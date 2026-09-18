# CRUD completo de agentes e grupos

Data: 2026-09-18
Status: Aprovado para planejamento

## Contexto e motivação

O backend já tem `GET`/`POST`/`PUT` pra agentes e `GET`/`POST` pra grupos (mais gestão de membros), mas faltam as operações de apagar (agentes e grupos) e renomear (grupos). O frontend é ainda mais limitado: a tela de Agentes só cria (a lista é só leitura) e não existe nenhuma forma de renomear ou apagar um grupo pela interface.

## Objetivo

- `DELETE /api/agents/{agent_id}`.
- `PUT /api/groups/{group_id}` (renomear) e `DELETE /api/groups/{group_id}`.
- Frontend: clicar num agente da lista carrega ele no formulário pra edição (reaproveitando o formulário de criação existente); botões de "Renomear"/"Apagar" no cabeçalho do canal aberto, pra grupos.

Fora de escopo: edição de membros de grupo pela tela de agente (já existe, separado, na view de canal); undo/lixeira pra itens apagados; edição em lote.

## Backend

### `DELETE /api/agents/{agent_id}` (`app/routers/agents.py`)

```python
@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: int) -> Response:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

As FKs `group_members.agent_id` e `queue_jobs.agent_id` já têm `ON DELETE CASCADE` — apagar um agente automaticamente remove sua participação em grupos e qualquer job pendente dele na fila, sem SQL extra. `messages.sender_id` não tem FK (decisão de design já documentada anteriormente) — mensagens antigas do agente apagado permanecem no histórico com um `sender_id` órfão; o frontend já trata isso graciosamente (cai no fallback `"agente"` quando não encontra o agente na lista carregada).

### `PUT /api/groups/{group_id}` e `DELETE /api/groups/{group_id}` (`app/routers/groups.py`)

```python
@router.put("/{group_id}", response_model=GroupOut)
def update_group(group_id: int, group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "UPDATE groups SET name = ? WHERE id = ?", (group.name, group_id)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="group not found")
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], created_at=row["created_at"])


@router.delete("/{group_id}", status_code=204)
def delete_group(group_id: int) -> Response:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="group not found")
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

`group_members.group_id`, `messages.group_id` e `queue_jobs.group_id` já têm `ON DELETE CASCADE` — apagar um grupo remove automaticamente todo o histórico de mensagens, membros e jobs pendentes associados a ele. Isso é intencional (é exatamente por isso que a confirmação no frontend avisa explicitamente sobre a perda do histórico).

Nenhuma colisão de rota: `PUT`/`DELETE /{group_id}` (sem sufixo) e as rotas existentes `/{group_id}/members` continuam distintas por terem segmentos de path diferentes.

## Frontend

### Agentes — editar/apagar

O formulário de criação (`#agent-form`) passa a servir tanto criação quanto edição, controlado por `state.editingAgentId` (novo campo no estado, `null` = modo criação).

`app/static/index.html`: agrupar os botões do formulário:
```html
<div class="agent-form-actions">
  <button type="submit" class="btn-primary" id="agent-submit-btn">Salvar agente</button>
  <button type="button" id="cancel-edit-agent-btn" class="btn-secondary hidden">Cancelar edição</button>
  <button type="button" id="delete-agent-btn" class="btn-secondary hidden">Apagar agente</button>
</div>
```

`app/static/app.js`:
- `loadModels()` ganha um parâmetro opcional `currentValue` (default `""`), repassado pro `populateModelSelect` já existente — isso permite pré-selecionar o modelo do agente sendo editado (com a mesma proteção de "valor órfão preservado" que já existe pra Configurações).
- Cada `<li>` de `#agent-list` fica clicável (`cursor: pointer`, hover) e, ao clicar, chama uma função que preenche o formulário com os dados do agente, seleciona o modelo correto (via `loadModels(agent.model_name)`), seta `state.editingAgentId`, muda o texto do botão de submit pra "Atualizar agente", e mostra "Cancelar edição"/"Apagar agente".
- Submeter o formulário faz `PUT /api/agents/{id}` se `state.editingAgentId` estiver setado, ou `POST /api/agents` caso contrário (mesmo formulário, dois destinos).
- "Cancelar edição" limpa o formulário, zera `state.editingAgentId`, volta o botão pra "Salvar agente", esconde os dois botões extras, e recarrega o dropdown de modelo pro estado vazio padrão.
- "Apagar agente" pede `confirm()` com o nome do agente, chama `DELETE /api/agents/{id}`, e volta ao estado de criação.

### Grupos — renomear/apagar

`app/static/index.html`: `#channel-header` ganha estrutura interna:
```html
<div id="channel-header">
  <span id="channel-header-name"></span>
  <button type="button" id="rename-group-btn" class="btn-secondary">Renomear</button>
  <button type="button" id="delete-group-btn" class="btn-secondary">Apagar</button>
</div>
```
(`#channel-header` só é visível quando `#channel-content` está visível, ou seja, com um grupo selecionado — os botões não aparecem na tela vazia.)

`app/static/app.js`:
- `selectGroup()` passa a escrever o nome em `#channel-header-name` em vez de `#channel-header` diretamente.
- "Renomear": `prompt("Novo nome do grupo:", nomeAtual)` — se o usuário confirmar com um nome não vazio e diferente do atual, chama `PUT /api/groups/{id}`, recarrega a lista de grupos e atualiza o texto do cabeçalho.
- "Apagar": `confirm()` avisando explicitamente que todo o histórico de mensagens será perdido — se confirmado, chama `DELETE /api/groups/{id}`, zera `state.activeGroupId`, esconde `#channel-content`, mostra `#channel-empty` de novo, e recarrega a lista de grupos.

### CSS

`#channel-header` vira um flex container (nome + botões lado a lado); `#agent-list li` ganha `cursor: pointer` e um hover state consistente com o resto da lista.

## Testes

Backend, seguindo o padrão já estabelecido em `tests/test_agents_api.py` e `tests/test_groups_api.py`:
- `DELETE /api/agents/{id}`: sucesso (204, agente some da listagem), 404 pra id inexistente, e confirma via SQL direto que `group_members`/`queue_jobs` referenciando o agente foram removidos em cascata quando ele pertencia a um grupo com um job pendente.
- `PUT /api/groups/{id}`: sucesso (renomeia, 200), 409 pra nome duplicado, 404 pra id inexistente.
- `DELETE /api/groups/{id}`: sucesso (204), 404 pra id inexistente, confirma via SQL que mensagens/membros/jobs do grupo foram removidos em cascata.

Frontend: sem teste automatizado (mesmo padrão já usado no resto do projeto) — verificação manual: editar um agente existente e confirmar que os dados persistem; apagar um agente e confirmar que ele some da lista e de qualquer grupo que participava; renomear um grupo e confirmar que o nome novo aparece na sidebar e no cabeçalho; apagar um grupo ativo e confirmar que a tela volta ao estado "nenhum grupo selecionado" e que ele some da sidebar.
