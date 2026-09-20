# Excluir mensagem (recuperar contexto de um grupo)

Data: 2026-09-20
Status: Implementado

## Contexto e motivação

Não existe hoje nenhuma forma de remover uma mensagem individual de uma conversa. Conversas longas acumulam conteúdo que pesa no contexto enviado ao modelo a cada turno (`_build_history`, `app/queue_worker.py`, que lê a tabela `messages` sem filtrar por `hidden` — inclusive mensagens ocultas como texto extraído de PDF ou descrição de imagem entram inteiras no histórico de todo agente). Um PDF grande anexado por engano, uma pergunta que gerou uma resposta longa e inútil, ou simplesmente lixo acumulado — hoje não tem como "limpar" isso sem apagar a conversa inteira.

## Objetivo

- Permitir apagar uma mensagem específica, de forma definitiva (sem soft-delete/undo — decisão do usuário: simplicidade em vez de manter lixo indefinidamente no banco).
- A exclusão precisa remover a mensagem do **contexto real dos agentes**, não só da tela — como `_build_history` lê a tabela `messages` diretamente, isso significa um `DELETE` de verdade na tabela, não uma marcação.
- Permitir excluir também mensagens hoje **ocultas** (descrição de imagem, texto extraído de PDF) — são normalmente as que mais pesam em tokens, e é exatamente o que motivou o pedido ("recuperar contexto").
- Ao excluir uma mensagem com `image_path`/`pdf_path`, apagar também o arquivo correspondente em `data/uploads/` (evita acumular arquivo órfão).

Fora de escopo: exclusão em lote (selecionar várias mensagens de uma vez); desfazer/histórico de exclusões; qualquer confirmação server-side além do que a UI já faz (confirmação é responsabilidade do frontend, como já acontece pra apagar agente/grupo/conversa).

## Endpoint: `DELETE .../messages/{message_id}`

Em `app/routers/messages.py`, seguindo o mesmo padrão de `delete_agent` (`app/routers/agents.py:147-156`):

```python
@router.delete("/{message_id}", status_code=204)
def delete_message(conversation_id: int, message_id: int) -> Response:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image_path, pdf_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="message not found")

        # Best-effort cleanup: a locked/permission-denied file must not abort the delete —
        # the DB row going away is what matters (it's what leaves the LLM context), losing
        # an orphaned file on disk is a much smaller problem than a message that refuses
        # to delete because of an unrelated filesystem error.
        for path_value in (row["image_path"], row["pdf_path"]):
            if path_value:
                try:
                    Path(path_value).unlink(missing_ok=True)
                except OSError:
                    pass

        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

`Path.unlink(missing_ok=True)` cobre o caso do arquivo já não existir mais em disco. O `try/except OSError` ao redor (acrescentado durante a revisão de código da implementação) cobre o caso de um arquivo travado/sem permissão — sem isso, essa exceção subiria e a linha da mensagem sobreviveria no banco referenciando um arquivo que já pode ter sumido parcialmente; é mais seguro garantir que a exclusão da mensagem sempre completa. Nenhuma outra tabela referencia `messages.id` como chave estrangeira (`queue_jobs` guarda `message_id` só dentro do JSON de `payload`, sem `FOREIGN KEY`), então apagar a linha não quebra integridade referencial nem levanta erro de `sqlite3.IntegrityError`.

Efeitos colaterais aceitos e já cobertos pelo comportamento existente do sistema, sem código extra:
- Se a mensagem excluída tiver um job `agent_turn`/`describe_image`/`extract_pdf` ainda `pending`/`processing` referenciando-a, o job continua rodando normalmente (ele só usa `pdf_path`/`image_path` do payload, não consulta a linha da mensagem) e insere sua própria mensagem nova ao terminar — sem erro.
- Se a mensagem excluída for a mais recente e tiver `hidden_kind='wait_user'`, `_conversation_is_awaiting_user` (`app/queue_worker.py`) simplesmente vai olhar a nova mensagem mais recente na próxima checagem — é inclusive uma forma de destravar uma fila presa manualmente, sem precisar de nenhuma lógica nova.

## Endpoint: `GET .../messages` ganha `include_hidden`

Em `app/routers/messages.py`, `list_messages` (linha 157) ganha um parâmetro opcional:

```python
@router.get("", response_model=list[MessageOut])
def list_messages(
    conversation_id: int, since_id: int | None = None, include_hidden: bool = False
) -> list[MessageOut]:
    conn = get_connection()
    try:
        where = "conversation_id = ?" if include_hidden else _VISIBLE_MESSAGES_WHERE
        if since_id is None:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {where} ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {where} AND id > ? ORDER BY id",
                (conversation_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]
```

O polling normal da timeline (`pollMessages` em `app.js`) continua chamando sem `include_hidden` (comportamento inalterado). O novo bloco de "mensagens ocultas" no painel de contexto (ver frontend) é quem usa `include_hidden=true`, filtrando no cliente só as que têm `hidden === true` (pra não duplicar as que já aparecem na timeline, como `search_result`, que é `hidden=1` mas já visível hoje).

## Frontend: botão de apagar na timeline

Em `app/static/app.js`, `renderMessage` (linha 451) ganha um botão de exclusão em cada balão:

```javascript
const deleteBtn = document.createElement("button");
deleteBtn.type = "button";
deleteBtn.className = "message-delete-btn";
deleteBtn.textContent = "🗑";
deleteBtn.title = "Apagar mensagem";
deleteBtn.onclick = async () => {
  if (!confirm("Apagar esta mensagem? Ela some da conversa e do contexto dos agentes permanentemente.")) return;
  await api(`/api/conversations/${message.conversation_id}/messages/${message.id}`, { method: "DELETE" });
  row.remove();
};
bubble.appendChild(deleteBtn);
```

Mesmo padrão de confirmação já usado pra apagar agente/grupo/conversa (`app.js:265`, `:828`, `:874`). `row.remove()` tira a mensagem do DOM imediatamente, sem precisar esperar o próximo poll.

`renderSearchChip` (chamado antes desse ponto pra `hidden_kind === 'search_result'`, que usa uma UI diferente — um chip, não um balão completo) também ganha o mesmo botão de apagar, adaptado ao seu próprio elemento.

## Frontend: bloco "Mensagens ocultas" no painel de contexto

Em `app/static/index.html`, dentro de `#context-panel` (depois do bloco "Ferramentas ativas", linha ~100):

```html
<div class="context-block">
  <div class="context-block-title">Mensagens ocultas (peso no contexto)</div>
  <div id="hidden-messages-list"></div>
</div>
```

Em `app/static/app.js`, nova função chamada ao trocar de conversa e depois de qualquer exclusão. **Nota pós-implementação:** durante a revisão de código, duas correções foram aplicadas em relação ao rascunho original: (1) `search_result` também é `hidden=1`, mas já aparece como chip na timeline (via a exceção em `_VISIBLE_MESSAGES_WHERE`) — o filtro cliente precisa excluí-lo explicitamente, senão duplica; (2) a função precisa do mesmo guard de `pollGeneration` que `pollMessages` já usa, senão uma troca de conversa rápida pode deixar uma resposta antiga sobrescrever o painel da conversa nova (com botões de apagar fechando sobre IDs da conversa errada). O código abaixo já reflete a versão final corrigida:

```javascript
async function refreshHiddenMessages() {
  if (!state.activeConversationId) return;
  const generation = state.pollGeneration;
  const conversationId = state.activeConversationId;
  const messages = await api(
    `/api/conversations/${conversationId}/messages?include_hidden=true`
  );
  if (generation !== state.pollGeneration) return;
  const container = document.getElementById("hidden-messages-list");
  container.innerHTML = "";
  const hidden = messages.filter((m) => m.hidden && m.hidden_kind !== "search_result");
  if (hidden.length === 0) {
    container.textContent = "Nenhuma.";
    return;
  }
  for (const message of hidden) {
    const item = document.createElement("div");
    item.className = "hidden-message-item";

    const preview = document.createElement("span");
    preview.className = "hidden-message-preview";
    preview.textContent =
      `[${message.hidden_kind || "oculta"}] ` +
      (message.content.length > 80 ? message.content.slice(0, 80) + "…" : message.content);
    item.appendChild(preview);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "message-delete-btn";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Apagar mensagem";
    deleteBtn.onclick = async () => {
      if (!confirm("Apagar esta mensagem oculta? Ela some do contexto dos agentes permanentemente.")) return;
      await api(`/api/conversations/${conversationId}/messages/${message.id}`, { method: "DELETE" });
      await refreshHiddenMessages();
    };
    item.appendChild(deleteBtn);

    container.appendChild(item);
  }
}
```

Chamada em dois pontos já existentes no fluxo:
1. Dentro da função que troca de conversa (`selectConversation` ou equivalente), junto das outras chamadas de inicialização da conversa selecionada.
2. No final de `refreshHiddenMessages` já se auto-chama depois de um delete (via `onclick` acima) — não precisa de nenhum outro gatilho além desses dois, já que o bloco não muda sozinho por causa de novas mensagens visíveis (só muda quando algo novo fica oculto, o que já dispara um poll de mensagens; um refresh a cada troca de conversa é suficiente pro caso de uso).

## Testes

Backend (`tests/test_messages_api.py`):
- `DELETE .../messages/{id}` remove a mensagem: `GET` posterior não a lista mais (nem com `include_hidden=true`).
- `DELETE` de mensagem com `image_path` também apaga o arquivo em disco (`Path(...).exists()` vira `False`).
- `DELETE` de mensagem com `pdf_path` também apaga o arquivo em disco.
- `DELETE` de mensagem sem anexo funciona normalmente (não tenta apagar arquivo nenhum).
- `DELETE` de mensagem inexistente retorna 404.
- `DELETE` de mensagem que existe mas pertence a outra `conversation_id` retorna 404 (a query já filtra por `conversation_id = ?`, então id certo + conversa errada não deve casar).
- `GET .../messages` sem `include_hidden` continua não retornando mensagens ocultas (regressão do comportamento atual).
- `GET .../messages?include_hidden=true` retorna mensagens ocultas junto das visíveis.

Backend (`tests/test_queue_worker.py`):
- Excluir a mensagem `hidden_kind='pdf_extract'` (ou `image_description`) faz o conteúdo dela sumir do histórico passado a `chat_completion` num turno seguinte de `_process_agent_turn` — prova de que a exclusão realmente afeta o contexto do LLM, não só a listagem HTTP.

Sem teste de frontend automatizado (projeto não tem suíte de frontend hoje, mesma situação de features anteriores); verificação manual via `preview_start` cobre a UI (botão de apagar na timeline e no painel de mensagens ocultas).
