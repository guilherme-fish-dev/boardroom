# Excluir mensagem — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir apagar uma mensagem individual (visível ou oculta) de uma conversa, de forma definitiva — removendo-a tanto da tela quanto do contexto real enviado aos agentes — e dar à UI um jeito de listar e apagar as mensagens ocultas que hoje pesam no contexto sem aparecer em lugar nenhum.

**Architecture:** Um endpoint `DELETE` novo em `app/routers/messages.py` faz o `DELETE` de verdade na tabela `messages` (e apaga o arquivo de imagem/PDF anexado, se houver). O endpoint `GET` de listagem ganha um parâmetro `include_hidden` pra expor as mensagens ocultas à UI. No frontend, um botão de lixeira aparece em cada mensagem da timeline, e um novo bloco no painel de contexto lista as mensagens ocultas com o mesmo botão.

**Tech Stack:** Python, FastAPI, SQLite (`sqlite3` stdlib), vanilla JS, pytest.

Spec de referência: `docs/superpowers/specs/2026-09-20-delete-message-design.md`.

---

### Task 1: endpoint `DELETE` de mensagem + `include_hidden` no `GET`

**Files:**
- Modify: `app/routers/messages.py`
- Test: `tests/test_messages_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_messages_api.py`:

```python
def test_delete_message_removes_it(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "apague-me"}
    ).json()

    resp = client.delete(f"/api/conversations/{conversation['id']}/messages/{message['id']}")
    assert resp.status_code == 204

    remaining = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert message["id"] not in [m["id"] for m in remaining]

    remaining_with_hidden = client.get(
        f"/api/conversations/{conversation['id']}/messages", params={"include_hidden": "true"}
    ).json()
    assert message["id"] not in [m["id"] for m in remaining_with_hidden]


def test_delete_message_404_for_unknown_message(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    resp = client.delete(f"/api/conversations/{conversation['id']}/messages/9999")
    assert resp.status_code == 404


def test_delete_message_404_when_message_belongs_to_other_conversation(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    other_conv = client.post(
        f"/api/groups/{group['id']}/conversations", json={"name": "outra"}
    ).json()
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "oi"}
    ).json()

    resp = client.delete(f"/api/conversations/{other_conv['id']}/messages/{message['id']}")
    assert resp.status_code == 404

    # a mensagem continua existindo na conversa certa
    remaining = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert message["id"] in [m["id"] for m in remaining]


def test_delete_message_removes_attached_image_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "foto"},
        files=files,
    ).json()
    image_path = tmp_path / "uploads" / Path(message["image_path"]).name
    assert image_path.exists()

    resp = client.delete(f"/api/conversations/{conversation['id']}/messages/{message['id']}")
    assert resp.status_code == 204
    assert not image_path.exists()


def test_delete_message_removes_attached_pdf_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"pdf": ("relatorio.pdf", b"fake-pdf-bytes", "application/pdf")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/pdf",
        data={"content": "relatorio"},
        files=files,
    ).json()
    pdf_path = tmp_path / "uploads" / Path(message["pdf_path"]).name
    assert pdf_path.exists()

    resp = client.delete(f"/api/conversations/{conversation['id']}/messages/{message['id']}")
    assert resp.status_code == 204
    assert not pdf_path.exists()


def test_delete_message_without_attachment_does_not_error(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "texto puro"}
    ).json()

    resp = client.delete(f"/api/conversations/{conversation['id']}/messages/{message['id']}")
    assert resp.status_code == 204


def test_list_messages_excludes_hidden_by_default(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
            "VALUES (?, 'system', ?, 1, 'pdf_extract')",
            (conversation["id"], "texto extraido oculto"),
        )
        conn.commit()
    finally:
        conn.close()

    visible = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert "texto extraido oculto" not in [m["content"] for m in visible]


def test_list_messages_include_hidden_returns_hidden_messages(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
            "VALUES (?, 'system', ?, 1, 'pdf_extract')",
            (conversation["id"], "texto extraido oculto"),
        )
        conn.commit()
    finally:
        conn.close()

    with_hidden = client.get(
        f"/api/conversations/{conversation['id']}/messages", params={"include_hidden": "true"}
    ).json()
    assert "texto extraido oculto" in [m["content"] for m in with_hidden]
```

Adicionar também o import de `Path` no topo do arquivo de teste, junto dos já existentes:

```python
from pathlib import Path
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_messages_api.py -k "delete_message or include_hidden or excludes_hidden_by_default" -v`
Expected: FAIL — os testes de `delete_message` falham com 404/405 (rota ainda não existe); os de `include_hidden` falham porque o parâmetro é ignorado hoje (a mensagem oculta nunca aparece, mesmo pedindo `include_hidden=true`).

- [ ] **Step 3: Implementar o endpoint `DELETE` e o parâmetro `include_hidden`**

Em `app/routers/messages.py`, substituir `list_messages` (por volta da linha 157):

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

Adicionar o novo endpoint logo depois de `get_message_pdf` (final do arquivo):

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

        for path_value in (row["image_path"], row["pdf_path"]):
            if path_value:
                Path(path_value).unlink(missing_ok=True)

        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

O import de `fastapi` no topo do arquivo (linha 7) hoje é:

```python
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
```

`Response` ainda não está importado neste arquivo (é usado em `app/routers/agents.py`, mas não aqui). Trocar por:

```python
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_messages_api.py -v`
Expected: PASS (todos os testes de `test_messages_api.py`, incluindo os novos)

- [ ] **Step 5: Rodar a suíte completa**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS (todos os testes do projeto)

- [ ] **Step 6: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: add endpoint to delete a message and list hidden messages"
```

---

### Task 2: excluir mensagem realmente some do contexto do LLM

**Files:**
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Escrever o teste**

Adicionar ao final de `tests/test_queue_worker.py`:

```python
def test_deleting_hidden_message_removes_it_from_agent_context(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob olha o pdf')",
        (conversation_id,),
    )
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'pdf_extract')",
        (conversation_id, "conteudo sensivel que deve sumir"),
    )
    pdf_message_id = cur.lastrowid
    conn.commit()

    # Simula o usuário apagando a mensagem oculta antes do agente responder
    conn.execute("DELETE FROM messages WHERE id = ?", (pdf_message_id,))
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "ok",
    )

    process_next_job()

    contents = [m["content"] for m in calls[0]["messages"]]
    assert not any("conteudo sensivel que deve sumir" in c for c in contents)
```

- [ ] **Step 2: Rodar o teste e confirmar que passa**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest tests/test_queue_worker.py -k deleting_hidden_message -v`
Expected: PASS — como `_build_history` lê a tabela `messages` diretamente e a linha já não existe mais (foi apagada antes do `INSERT` do job), o conteúdo nunca chega ao histórico. Este teste não exige nenhuma mudança de código de produção — ele só comprova, de forma explícita, que o `DELETE` da Task 1 já resolve o objetivo central da feature (remover algo do contexto de verdade).

Se esse teste FALHAR de alguma forma inesperada, é sinal de que algo em `_build_history` está cacheando ou reconstruindo conteúdo de mensagens apagadas — investigar antes de prosseguir, não é esperado dado como a função já funciona hoje.

- [ ] **Step 3: Commit**

```bash
git add tests/test_queue_worker.py
git commit -m "test: verify deleting a hidden message removes it from agent context"
```

---

### Task 3: botão de apagar mensagem na timeline

**Files:**
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

Sem testes automatizados (projeto não tem suíte de frontend). Verificação manual no Step 4.

- [ ] **Step 1: Adicionar o botão em `renderMessage`**

Em `app/static/app.js`, a função `renderMessage` (por volta da linha 451) hoje termina assim:

```javascript
  if (message.pdf_path) {
    const link = document.createElement("a");
    link.href = `/api/conversations/${message.conversation_id}/messages/${message.id}/pdf`;
    link.textContent = "📄 PDF anexado";
    link.target = "_blank";
    bubble.appendChild(link);
  }

  document.getElementById("message-list").appendChild(row);
}
```

Trocar por (adiciona o botão de apagar como último elemento do balão, antes de inserir a linha na lista):

```javascript
  if (message.pdf_path) {
    const link = document.createElement("a");
    link.href = `/api/conversations/${message.conversation_id}/messages/${message.id}/pdf`;
    link.textContent = "📄 PDF anexado";
    link.target = "_blank";
    bubble.appendChild(link);
  }

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

  document.getElementById("message-list").appendChild(row);
}
```

- [ ] **Step 2: Adicionar o botão em `renderSearchChip`**

A função `renderSearchChip` (por volta da linha 437) hoje é:

```javascript
function renderSearchChip(message) {
  const row = document.createElement("div");
  row.className = "message-row search-chip-row";

  const chip = document.createElement("div");
  chip.className = "search-chip";
  const firstLine = message.content.split("\n")[0] || "Pesquisou na internet";
  chip.textContent = `🔍 ${firstLine}`;
  chip.title = message.content;

  row.appendChild(chip);
  document.getElementById("message-list").appendChild(row);
}
```

Trocar por:

```javascript
function renderSearchChip(message) {
  const row = document.createElement("div");
  row.className = "message-row search-chip-row";

  const chip = document.createElement("div");
  chip.className = "search-chip";
  const firstLine = message.content.split("\n")[0] || "Pesquisou na internet";
  chip.textContent = `🔍 ${firstLine}`;
  chip.title = message.content;
  row.appendChild(chip);

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
  row.appendChild(deleteBtn);

  document.getElementById("message-list").appendChild(row);
}
```

- [ ] **Step 3: Adicionar o CSS do botão**

Em `app/static/style.css`, logo depois do bloco `.member-badge button:hover { color: var(--danger); }` (por volta da linha 265):

```css
.message-delete-btn {
  background: none;
  border: none;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 0;
  margin-left: 8px;
  font-size: 12px;
  line-height: 1;
  opacity: 0;
  transition: opacity 150ms ease, color 150ms ease;
}
.message-bubble:hover .message-delete-btn,
.search-chip:hover .message-delete-btn { opacity: 1; }
.message-delete-btn:hover { color: var(--danger); }
```

`opacity: 0` por padrão e `1` no hover evita poluir a timeline com um ícone de lixeira visível o tempo todo em cada mensagem — só aparece quando o mouse passa em cima, mesmo padrão discreto já usado pelo botão de remover membro.

- [ ] **Step 4: Verificação manual no navegador**

1. Rodar o servidor: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m uvicorn app.main:app --reload` a partir da raiz do projeto (ou `preview_start` se disponível).
2. Abrir a aplicação, entrar numa conversa com pelo menos uma mensagem.
3. Passar o mouse sobre uma mensagem e confirmar que o ícone 🗑 aparece; clicar, confirmar o `confirm()`, e verificar que a mensagem some da tela imediatamente.
4. Recarregar a página e confirmar que a mensagem continua ausente (não veio de volta por engano).
5. Se houver alguma mensagem com chip de busca (🔍) na conversa de teste, repetir o mesmo teste nela.
6. Checar o console do navegador por erros JS.

- [ ] **Step 5: Commit**

```bash
git add app/static/app.js app/static/style.css
git commit -m "feat: add delete button to messages in the timeline"
```

---

### Task 4: painel de mensagens ocultas

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

Sem testes automatizados. Verificação manual no Step 4.

- [ ] **Step 1: Adicionar o bloco HTML**

Em `app/static/index.html`, dentro de `#context-panel`, depois do bloco "Ferramentas ativas" (por volta da linha 100) e antes do `.quote-block`:

```html
<div class="context-block">
  <div class="context-block-title">Mensagens ocultas (peso no contexto)</div>
  <div id="hidden-messages-list"></div>
</div>
```

- [ ] **Step 2: Implementar `refreshHiddenMessages` em `app.js`**

Adicionar a função logo depois de `renderMembers` (por volta da linha 339, depois do fechamento de `}` daquela função):

```javascript
async function refreshHiddenMessages() {
  if (!state.activeConversationId) return;
  const messages = await api(
    `/api/conversations/${state.activeConversationId}/messages?include_hidden=true`
  );
  const container = document.getElementById("hidden-messages-list");
  container.innerHTML = "";
  const hidden = messages.filter((m) => m.hidden);
  if (hidden.length === 0) {
    container.textContent = "Nenhuma.";
    return;
  }
  for (const message of hidden) {
    const item = document.createElement("div");
    item.className = "hidden-message-item";

    const preview = document.createElement("span");
    preview.className = "hidden-message-preview";
    const label = message.hidden_kind || "oculta";
    const text = message.content.length > 80 ? message.content.slice(0, 80) + "…" : message.content;
    preview.textContent = `[${label}] ${text}`;
    preview.title = message.content;
    item.appendChild(preview);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "message-delete-btn";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = "Apagar mensagem";
    deleteBtn.onclick = async () => {
      if (!confirm("Apagar esta mensagem oculta? Ela some do contexto dos agentes permanentemente.")) return;
      await api(`/api/conversations/${state.activeConversationId}/messages/${message.id}`, { method: "DELETE" });
      refreshHiddenMessages();
    };
    item.appendChild(deleteBtn);

    container.appendChild(item);
  }
}
```

- [ ] **Step 3: Chamar `refreshHiddenMessages` ao trocar de conversa**

Em `app.js`, `selectConversation` (por volta da linha 291) hoje é:

```javascript
async function selectConversation(conversationId) {
  state.activeConversationId = conversationId;
  state.lastMessageId = 0;
  state.pollGeneration += 1;
  state.pollInFlight = false;
  document.getElementById("message-list").innerHTML = "";
  document.getElementById("queue-indicator").textContent = "";
  document.getElementById("stop-queue-btn").classList.add("hidden");
  renderConversationList();
  renderConversationContext();
  await pollMessages();
  await pollPendingStatus();
}
```

Trocar por (adiciona a chamada logo depois de `renderConversationContext()`):

```javascript
async function selectConversation(conversationId) {
  state.activeConversationId = conversationId;
  state.lastMessageId = 0;
  state.pollGeneration += 1;
  state.pollInFlight = false;
  document.getElementById("message-list").innerHTML = "";
  document.getElementById("queue-indicator").textContent = "";
  document.getElementById("stop-queue-btn").classList.add("hidden");
  renderConversationList();
  renderConversationContext();
  await refreshHiddenMessages();
  await pollMessages();
  await pollPendingStatus();
}
```

- [ ] **Step 4: Adicionar o CSS do bloco**

Em `app/static/style.css`, logo depois do bloco `.context-block-title` (por volta da linha 556):

```css
#hidden-messages-list { display: flex; flex-direction: column; gap: 6px; }
.hidden-message-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-muted);
}
.hidden-message-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

- [ ] **Step 5: Verificação manual no navegador**

1. Rodar o servidor (mesmo comando da Task 3).
2. Anexar um PDF ou uma imagem numa conversa de teste e esperar o job de extração/descrição terminar.
3. Abrir o painel de contexto e confirmar que o bloco "Mensagens ocultas" mostra a mensagem correspondente (ex.: `[pdf_extract] Relatório...`).
4. Clicar no 🗑 do item, confirmar, e verificar que ele some da lista.
5. Mencionar o agente de novo e confirmar (via inspeção da conversa ou do banco, se necessário) que o conteúdo apagado não aparece mais na resposta dele.
6. Trocar de conversa e voltar, confirmando que o bloco atualiza corretamente para cada conversa (sem misturar mensagens ocultas de conversas diferentes).

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: add hidden messages panel to context sidebar"
```

---

### Task 5: fechar a spec

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-delete-message-design.md`

- [ ] **Step 1: Rodar a suíte de testes inteira**

Run: `F:\Projetos\boardroom\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 2: Atualizar o status no topo da spec**

Trocar:

```
Status: Aprovado para planejamento
```

por:

```
Status: Implementado
```

- [ ] **Step 3: Commit final**

```bash
git add docs/superpowers/specs/2026-09-20-delete-message-design.md
git commit -m "docs: mark delete-message spec as implemented"
```
