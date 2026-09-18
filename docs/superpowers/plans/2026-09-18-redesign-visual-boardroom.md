# Redesign visual do Boardroom — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganizar a UI do Boardroom em 4 colunas (grupos, conversas do grupo ativo, chat, painel de contexto), cada coluna colapsável independentemente, com um ícone emoji por grupo e um tema visual mais próximo do laranja/dark do mockup de referência — sem remover nenhuma funcionalidade existente (fila de mensagens, parar fila, menções, markdown, chip de busca, upload de imagem, agentes, configurações).

**Architecture:** Puramente frontend + uma adição pequena de schema (`groups.icon`). `app/static/index.html` ganha 4 blocos de layout dentro de `#app` (mais um modal de formulário de grupo fora dele); `app/static/app.js` ganha funções de renderização/estado por coluna e troca os fluxos de `prompt()` de criar/renomear grupo por um formulário próprio com grade de emojis; `app/static/style.css` ganha as regras de layout, colapso (handle sempre visível + `.collapsed` no desktop, overlay fixo + `.open` no mobile) e os novos componentes visuais (cards, painel de contexto, grade de emoji).

**Tech Stack:** FastAPI + sqlite3 (backend), HTML/CSS/JS vanilla sem build step (frontend), pytest (testes de backend).

---

## Referência do estado atual (antes deste plano)

- `app/db.py`: `SCHEMA` cria `groups(id, name, created_at)`. `init_db()` roda `_ensure_hidden_kind_column` e `_ensure_conversations_table` como migrações aditivas.
- `app/routers/groups.py`: `GroupIn(name)`, `GroupOut(GroupIn) + id, created_at`. `create_group`/`update_group` fazem `INSERT`/`UPDATE` só com `name`.
- `app/static/index.html`: `#sidebar` (grupos) → `#main-panel` com `#view-channel` contendo `#channel-header`, `#conversation-tabs`, `#channel-members` (lista de membros + adicionar agente), `#queue-bar`, `#message-list`, `#message-form`.
- `app/static/app.js`: `loadGroups()`/`selectGroup()` renderizam `#group-list`; `renderConversationTabs()` renderiza abas horizontais em `#conversation-tabs`; `loadMembers()`/`renderMembers()` renderizam `#member-list`/`#add-member-select`; criar/renomear grupo e conversa usam `prompt()`.
- `app/static/style.css`: tokens `oklch` em `:root` (já dark theme, `--hue: 55`), breakpoint mobile único em `#sidebar`/`#sidebar-toggle`.

Este plano assume que `git status` está limpo nesses arquivos antes de começar (confirmado antes de escrever este plano). Se algo mudou nesses arquivos desde então, releia o arquivo atual antes de aplicar o `old_string` de qualquer step — não assuma que o conteúdo abaixo ainda bate byte a byte.

---

### Task 1: Coluna `icon` em `groups`

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Escrever o teste da migração aditiva**

Adicionar ao final de `tests/test_db.py`:

```python
def test_init_db_adds_icon_column_to_groups_with_default(db):
    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)")}
        cur = conn.execute("INSERT INTO groups (name) VALUES ('sem-icone')")
        conn.commit()
        row = conn.execute("SELECT icon FROM groups WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    assert "icon" in columns
    assert row["icon"] == "💬"


def test_init_db_group_icon_migration_is_idempotent(db):
    from app.db import init_db

    init_db()
    init_db()

    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)")}
    finally:
        conn.close()
    assert "icon" in columns
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -k icon_column -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: icon` (a tabela `groups` ainda não tem essa coluna, e o `INSERT` sem ela nem chega a rodar porque a query de teste já assume que existe).

- [ ] **Step 3: Adicionar a coluna ao schema e à migração**

Em `app/db.py`, no bloco `SCHEMA`, trocar:

```python
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

por:

```python
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    icon TEXT NOT NULL DEFAULT '💬',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Adicionar, logo depois de `_ensure_hidden_kind_column`:

```python
def _ensure_group_icon_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)")}
    if "icon" not in columns:
        conn.execute("ALTER TABLE groups ADD COLUMN icon TEXT NOT NULL DEFAULT '💬'")
```

E em `init_db()`, chamar a nova função junto das outras migrações:

```python
    try:
        conn.executescript(SCHEMA)
        _ensure_hidden_kind_column(conn)
        _ensure_group_icon_column(conn)
        _ensure_conversations_table(conn)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: PASS (todos, incluindo os dois novos)

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add icon column to groups, defaulting to 💬"
```

---

### Task 2: Expor `icon` na API de grupos

**Files:**
- Modify: `app/routers/groups.py`
- Test: `tests/test_groups_api.py`

- [ ] **Step 1: Escrever os testes**

Adicionar ao final de `tests/test_groups_api.py`:

```python
def test_create_group_with_custom_icon(db):
    client = make_client(db)
    resp = client.post("/api/groups", json={"name": "investidores", "icon": "📈"})
    assert resp.status_code == 201
    assert resp.json()["icon"] == "📈"


def test_create_group_without_icon_defaults_to_speech_bubble(db):
    client = make_client(db)
    resp = client.post("/api/groups", json={"name": "investidores"})
    assert resp.status_code == 201
    assert resp.json()["icon"] == "💬"


def test_update_group_changes_icon(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.put(f"/api/groups/{group['id']}", json={"name": "investidores", "icon": "💰"})
    assert resp.status_code == 200
    assert resp.json()["icon"] == "💰"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/Scripts/python.exe -m pytest tests/test_groups_api.py -k icon -v`
Expected: FAIL — `KeyError: 'icon'` (o response ainda não inclui o campo).

- [ ] **Step 3: Implementar**

Em `app/routers/groups.py`, trocar:

```python
class GroupIn(BaseModel):
    name: str


class GroupOut(GroupIn):
    id: int
    created_at: str
```

por:

```python
class GroupIn(BaseModel):
    name: str
    icon: str = "💬"


class GroupOut(GroupIn):
    id: int
    created_at: str
```

Trocar `create_group`:

```python
@router.post("", response_model=GroupOut, status_code=201)
def create_group(group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (group.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        group_id = cur.lastrowid
        conn.execute("INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (group_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], created_at=row["created_at"])
```

por:

```python
@router.post("", response_model=GroupOut, status_code=201)
def create_group(group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO groups (name, icon) VALUES (?, ?)", (group.name, group.icon)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        group_id = cur.lastrowid
        conn.execute("INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (group_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], icon=row["icon"], created_at=row["created_at"])
```

Trocar `list_groups` (última linha):

```python
    return [GroupOut(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in rows]
```

por:

```python
    return [GroupOut(id=r["id"], name=r["name"], icon=r["icon"], created_at=r["created_at"]) for r in rows]
```

Trocar `update_group`:

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
```

por:

```python
@router.put("/{group_id}", response_model=GroupOut)
def update_group(group_id: int, group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "UPDATE groups SET name = ?, icon = ? WHERE id = ?",
                (group.name, group.icon, group_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="group not found")
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], icon=row["icon"], created_at=row["created_at"])
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/Scripts/python.exe -m pytest tests/test_groups_api.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Rodar a suíte inteira antes de seguir pro frontend**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `... passed` sem falhas (mesmo total de antes + 5 novos testes)

- [ ] **Step 6: Commit**

```bash
git add app/routers/groups.py tests/test_groups_api.py
git commit -m "feat: expose group icon in the groups API"
```

---

### Task 3: Esqueleto HTML de 4 colunas

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: Reescrever o `<body>`**

Substituir o conteúdo de `app/static/index.html` (mantendo `<!DOCTYPE html>` … `<head>` como estão) pelo seguinte `<body>`:

```html
<body>
  <div id="app">
    <button id="sidebar-toggle" type="button" aria-label="Abrir grupos" aria-expanded="false">☰</button>

    <aside id="groups-col">
      <button type="button" id="groups-collapse-btn" class="col-handle" aria-label="Recolher grupos" aria-expanded="true">‹</button>
      <div class="col-body">
        <h1>Boardroom</h1>
        <nav>
          <button id="nav-agents" type="button">Agentes</button>
          <button id="nav-settings" type="button">Configurações</button>
        </nav>
        <div class="sidebar-section">
          <div class="sidebar-section-title">Grupos</div>
          <input id="group-search" type="search" placeholder="Buscar grupos" />
          <ul id="group-list"></ul>
          <button type="button" id="new-group-btn" class="btn-primary">+ Novo grupo</button>
        </div>
      </div>
    </aside>

    <aside id="conversations-col">
      <button type="button" id="conversations-toggle-btn" class="mobile-only-toggle" aria-label="Abrir conversas" aria-expanded="false">☰</button>
      <button type="button" id="conversations-collapse-btn" class="col-handle" aria-label="Recolher conversas" aria-expanded="true">‹</button>
      <div class="col-body">
        <div class="col-header">
          <span>Conversas</span>
        </div>
        <button type="button" id="new-conversation-btn" class="btn-primary">+ Nova conversa</button>
        <ul id="conversation-list"></ul>
      </div>
    </aside>

    <main id="main-panel">
      <div id="view-channel" class="view">
        <div id="channel-empty" class="empty-state">
          <p>Selecione um grupo na barra lateral, ou crie um novo pra começar uma conversa.</p>
        </div>
        <div id="channel-content" class="hidden">
          <div class="chat-main">
            <div id="channel-header">
              <span id="channel-header-name"></span>
              <button type="button" id="rename-group-btn" class="btn-secondary">Renomear</button>
              <button type="button" id="delete-group-btn" class="btn-secondary">Apagar</button>
              <button type="button" id="context-toggle-btn" class="mobile-only-toggle btn-secondary" aria-label="Abrir contexto" aria-expanded="false">i</button>
            </div>
            <div id="queue-bar">
              <div id="queue-indicator"></div>
              <button type="button" id="stop-queue-btn" class="btn-secondary hidden">Parar</button>
            </div>
            <div id="message-list"></div>
            <form id="message-form">
              <input id="message-input" placeholder="Mensagem (@nome para mencionar)" autocomplete="off" />
              <label class="file-btn" for="image-input">Anexar imagem</label>
              <input id="image-input" type="file" accept="image/*" class="visually-hidden" />
              <span id="image-filename" class="file-name"></span>
              <button type="submit" class="btn-primary">Enviar</button>
            </form>
          </div>
          <aside id="context-panel">
            <div class="col-body">
              <div class="col-header">
                <span>Contexto</span>
              </div>
              <div class="context-block">
                <div class="context-block-title">Contexto da conversa</div>
                <div id="context-conversation-info"></div>
              </div>
              <div class="context-block">
                <div class="context-block-title">Membros</div>
                <span id="member-list"></span>
                <select id="add-member-select">
                  <option value="">+ adicionar agente</option>
                </select>
              </div>
              <div class="context-block">
                <div class="context-block-title">Ferramentas ativas</div>
                <div class="tool-badge">🌐 Busca na Web</div>
              </div>
            </div>
            <button type="button" id="context-collapse-btn" class="col-handle" aria-label="Recolher contexto" aria-expanded="true">›</button>
          </aside>
        </div>
      </div>
      <div id="view-agents" class="view hidden">
        <h2>Agentes</h2>
        <ul id="agent-list"></ul>
        <form id="agent-form">
          <label>Nome (sem espaços)
            <input id="agent-name" placeholder="ex.: investidor-conservador" required />
          </label>
          <label>Persona / system prompt
            <textarea id="agent-persona" placeholder="Descreva como esse agente deve pensar e responder" required></textarea>
          </label>
          <div class="persona-assist">
            <button type="button" id="generate-persona-btn" class="btn-secondary" disabled>Gerar com IA</button>
            <span id="generate-persona-error" class="field-error"></span>
          </div>
          <label>Modelo (llama-swap)
            <select id="agent-model" required></select>
          </label>
          <label class="checkbox-label"><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
          <div class="agent-form-actions">
            <button type="submit" class="btn-primary" id="agent-submit-btn">Salvar agente</button>
            <button type="button" id="cancel-edit-agent-btn" class="btn-secondary hidden">Cancelar edição</button>
            <button type="button" id="delete-agent-btn" class="btn-secondary hidden">Apagar agente</button>
          </div>
        </form>
      </div>
      <div id="view-settings" class="view hidden">
        <h2>Configurações</h2>
        <form id="settings-form">
          <label>URL base do llama-swap
            <input id="setting-base-url" required />
          </label>
          <label>Modelo de visão padrão
            <select id="setting-vision-model"></select>
          </label>
          <label>Limite de jobs por grupo
            <input id="setting-max-pending" type="number" min="1" required />
          </label>
          <label>Modelo assistente (gerar personas)
            <select id="setting-assistant-model"></select>
          </label>
          <button type="submit" class="btn-primary">Salvar</button>
        </form>
      </div>
    </main>
  </div>

  <div id="group-form-backdrop" class="modal-backdrop hidden">
    <div id="group-form-modal" class="modal">
      <h3 id="group-form-title">Novo grupo</h3>
      <form id="group-form">
        <input id="group-form-name" placeholder="Nome do grupo" required />
        <div id="group-icon-grid"></div>
        <div class="modal-actions">
          <button type="button" id="group-form-cancel" class="btn-secondary">Cancelar</button>
          <button type="submit" class="btn-primary">Salvar</button>
        </div>
      </form>
    </div>
  </div>

  <script src="/static/app.js"></script>
</body>
```

Removido nesta troca (de propósito, cobertos em tasks seguintes): `#sidebar` (renomeado `#groups-col`), `#conversation-tabs` (substituído por `#conversation-list` em `#conversations-col`), `#channel-members` (seu conteúdo — `#member-list`/`#add-member-select` — foi realocado dentro de `#context-panel`, mesmos ids).

- [ ] **Step 2: Verificar visualmente que a página ainda carrega (mesmo sem JS ajustado ainda)**

Suba o servidor (`uvicorn app.main:app --reload` ou o `start.bat` do projeto) e abra `http://localhost:8000` no browser. Nesta etapa é esperado que a página pareça quebrada/desorganizada (JS e CSS ainda não sabem dos novos ids) — o objetivo aqui é só confirmar que o HTML é válido e não trava o carregamento (sem erros de parsing no console). Não precisa corrigir nada ainda.

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "refactor: restructure index.html into 4 collapsible columns"
```

---

### Task 4: CSS base do layout de 4 colunas e mecânica de colapso

**Files:**
- Modify: `app/static/style.css`

- [ ] **Step 1: Substituir as regras de `#sidebar`/`#sidebar-toggle` pela mecânica genérica de coluna**

Em `app/static/style.css`, trocar o bloco (de `#sidebar-toggle {` até o fim de `#sidebar h1 { ... }`, ou seja as regras atuais de `#sidebar-toggle` e `#sidebar`):

```css
#sidebar-toggle {
  display: none;
  position: fixed;
  top: 10px;
  left: 10px;
  z-index: 20;
  width: 36px;
  height: 36px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--ink);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 16px;
}

#sidebar {
  width: 240px;
  flex: 0 0 auto;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

#sidebar h1 {
  font-size: 16px;
  font-weight: 700;
  margin: 4px 4px 0;
  letter-spacing: -0.01em;
}
```

por:

```css
#sidebar-toggle {
  display: none;
  position: fixed;
  top: 10px;
  left: 10px;
  z-index: 20;
  width: 36px;
  height: 36px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--ink);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 16px;
}

.mobile-only-toggle { display: none; }

/* Mecânica de colapso genérica para as 3 colunas laterais (grupos, conversas,
   contexto): cada uma tem um "handle" (a faixa estreita com a setinha) que fica
   sempre visível, e um `.col-body` que existe ou não conforme `.collapsed`. */
#groups-col, #conversations-col, #context-panel {
  flex: 0 0 auto;
  background: var(--bg-elevated);
  display: flex;
  transition: width 200ms ease;
  overflow: hidden;
}
#groups-col { width: 240px; border-right: 1px solid var(--border); }
#conversations-col { width: 260px; border-right: 1px solid var(--border); }
#context-panel { width: 260px; border-left: 1px solid var(--border); flex-direction: row-reverse; }

#groups-col.collapsed, #conversations-col.collapsed, #context-panel.collapsed { width: 32px; }
.collapsed .col-body { display: none; }

.col-handle {
  flex: 0 0 auto;
  width: 32px;
  background: transparent;
  border: none;
  color: var(--ink-faint);
  cursor: pointer;
  font-size: 14px;
  align-self: stretch;
}
.col-handle:hover { background: var(--surface); color: var(--ink); }

#groups-col .col-body, #conversations-col .col-body, #context-panel .col-body {
  flex: 1;
  min-width: 0;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

#groups-col h1 {
  font-size: 16px;
  font-weight: 700;
  margin: 4px 4px 0;
  letter-spacing: -0.01em;
}

.col-header { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-faint); }
```

- [ ] **Step 2: Adaptar `#sidebar nav`/`.sidebar-section`/`#group-list` para o novo id `#groups-col`**

Trocar todas as ocorrências de `#sidebar nav`, `#sidebar nav button` (e seus `:hover`/`.active`) por `#groups-col nav`, `#groups-col nav button` — é uma renomeação 1:1, o resto da regra fica igual. O restante das regras de `.sidebar-section`, `.sidebar-section-title`, `#group-list` e seus `li` continuam válidas como estão (não referenciam `#sidebar` diretamente).

Adicionar, perto de `#group-list li`:

```css
#group-search { margin-bottom: 4px; }
.group-icon { margin-right: 6px; }
```

- [ ] **Step 3: Ajustar `#main-panel` pra sobrar espaço pras novas colunas e reestruturar `#channel-content`/`#channel-header` como flex row**

Trocar:

```css
#main-panel { flex: 1; display: flex; flex-direction: column; padding: 20px 24px; overflow: hidden; min-width: 0; }
```

(fica igual — `#main-panel` já é `flex: 1`, que é o que precisamos: ele naturalmente ocupa o espaço restante ao lado das colunas laterais dentro de `#app { display: flex; }`).

Trocar:

```css
#channel-content { display: flex; flex-direction: column; flex: 1; min-height: 0; }
```

por:

```css
#channel-content { display: flex; flex-direction: row; flex: 1; min-height: 0; gap: 20px; }
.chat-main { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; }
```

- [ ] **Step 4: Remover as regras de `#channel-members`/`.member-badge` do fluxo do chat e adicionar as do painel de contexto**

Remover (serão recriadas dentro do escopo `.context-block` na Task 9, mas os ids `#member-list`/`.member-badge` continuam os mesmos — só o container muda):

```css
#channel-members {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  padding: 0 0 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
```

(o `#member-list { display: inline-flex; ... }` e as regras `.member-badge` continuam, sem mudança — ainda se aplicam dentro do novo container).

- [ ] **Step 5: Remover as regras de `#conversation-tabs`/`.conversation-tab` do chat**

As regras `#conversation-tabs { ... }`, `.conversation-tab { ... }`, `.conversation-tab:hover`, `.conversation-tab.active`, `.conversation-tab-rename`/`.conversation-tab-delete` e `#new-conversation-btn { ... }` no final do arquivo não se aplicam mais (o markup de abas foi substituído por cards na Task 7) — apague esse bloco inteiro (da linha `#conversation-tabs {` até o final de `#new-conversation-btn:hover { ... }`). A Task 7 vai adicionar as regras `.conversation-card` no lugar.

- [ ] **Step 6: Mobile — trocar o breakpoint único de `#sidebar` pelas 3 colunas + toggles**

Trocar:

```css
@media (max-width: 760px) {
  #sidebar-toggle { display: block; }
  #app { position: relative; }
  #sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 15;
    transform: translateX(-100%);
    transition: transform 200ms ease;
    box-shadow: 2px 0 16px rgba(0, 0, 0, 0.4);
  }
  #sidebar.open { transform: translateX(0); }
  #sidebar { padding-top: 60px; }
  #main-panel { padding: 56px 16px 16px; }
  .message-row { max-width: 88%; }
  #agent-form, #settings-form { max-width: none; }
}
```

por:

```css
@media (max-width: 760px) {
  #sidebar-toggle { display: block; }
  .mobile-only-toggle { display: inline-flex; align-items: center; justify-content: center; }
  .col-handle { display: none; }
  #app { position: relative; }

  #groups-col, #conversations-col, #context-panel {
    position: fixed;
    top: 0;
    bottom: 0;
    width: 260px;
    z-index: 15;
    transition: transform 200ms ease;
    box-shadow: 2px 0 16px rgba(0, 0, 0, 0.4);
  }
  #groups-col, #conversations-col { left: 0; transform: translateX(-100%); }
  #context-panel { right: 0; left: auto; transform: translateX(100%); flex-direction: row; }
  #groups-col.open, #conversations-col.open, #context-panel.open { transform: translateX(0); }
  /* No mobile, .collapsed (regra de desktop) não deve zerar a largura — a visibilidade
     aqui é só .open/fechada por transform. */
  #groups-col.collapsed, #conversations-col.collapsed, #context-panel.collapsed { width: 260px; }
  .collapsed .col-body { display: flex; }

  #groups-col { padding-top: 60px; }
  #main-panel { padding: 56px 16px 16px; }
  .message-row { max-width: 88%; }
  #agent-form, #settings-form { max-width: none; }
  #channel-content { flex-direction: column; }
}
```

- [ ] **Step 7: Verificar no browser**

Suba o servidor, abra a página, redimensione a janela abaixo de 760px e confirme (via `read_page`/screenshot) que: (a) em desktop as 3 colunas aparecem lado a lado com uma seta de colapso visível em cada uma; (b) em mobile elas ficam ocultas por padrão e os botões `☰`/`i` (ainda sem funcionalidade JS — ok por agora) pelo menos aparecem no layout.

- [ ] **Step 8: Commit**

```bash
git add app/static/style.css
git commit -m "feat: add 4-column layout CSS with independent collapse per column"
```

---

### Task 5: JS — coluna de grupos (ícone, busca, colapso)

**Files:**
- Modify: `app/static/app.js`

- [ ] **Step 1: Trocar `loadGroups()` pra renderizar ícone + aplicar o filtro de busca**

Adicionar ao `state` (topo do arquivo), junto dos outros campos:

```js
const state = {
  groups: [],
  activeGroupId: null,
  conversations: [],
  activeConversationId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
  pollGeneration: 0,
  pollInFlight: false,
  editingAgentId: null,
  groupSearchTerm: "",
  editingGroupId: null,
};
```

Trocar `loadGroups()`:

```js
async function loadGroups() {
  state.groups = await api("/api/groups");
  renderGroupList();
}
```

(a função passa a só buscar; a renderização — que agora também precisa reagir ao filtro de busca sem re-buscar — vira uma função própria `renderGroupList()`, chamada por `loadGroups()` e pelo listener do campo de busca).

Adicionar, no lugar de onde estava o corpo antigo de `loadGroups()`:

```js
function renderGroupList() {
  const list = document.getElementById("group-list");
  list.innerHTML = "";

  const term = state.groupSearchTerm.trim().toLowerCase();
  const visible = term
    ? state.groups.filter((g) => g.name.toLowerCase().includes(term))
    : state.groups;

  if (state.groups.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum grupo ainda";
    list.appendChild(li);
    return;
  }

  if (visible.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = "Nenhum grupo encontrado";
    list.appendChild(li);
    return;
  }

  for (const group of visible) {
    const li = document.createElement("li");
    li.className = state.activeView === "channel" && group.id === state.activeGroupId ? "active" : "";
    li.onclick = () => selectGroup(group.id);

    const icon = document.createElement("span");
    icon.className = "group-icon";
    icon.textContent = group.icon;
    li.appendChild(icon);

    const name = document.createElement("span");
    name.textContent = group.name;
    li.appendChild(name);

    list.appendChild(li);
  }
}
```

- [ ] **Step 2: Ligar o campo de busca**

Adicionar junto dos outros `document.getElementById(...).onclick =` (perto do fim do arquivo):

```js
document.getElementById("group-search").addEventListener("input", (e) => {
  state.groupSearchTerm = e.target.value;
  renderGroupList();
});
```

- [ ] **Step 3: Ligar o colapso desktop e os toggles mobile**

Adicionar uma função genérica de toggle (perto de `closeSidebarOnMobile`):

```js
function wireColumnToggle(collapseBtnId, mobileToggleBtnId, colId) {
  const col = document.getElementById(colId);
  const collapseBtn = document.getElementById(collapseBtnId);
  if (collapseBtn) {
    collapseBtn.onclick = () => {
      const collapsed = !col.classList.contains("collapsed");
      col.classList.toggle("collapsed", collapsed);
      collapseBtn.setAttribute("aria-expanded", String(!collapsed));
    };
  }
  if (mobileToggleBtnId) {
    const mobileBtn = document.getElementById(mobileToggleBtnId);
    mobileBtn.onclick = () => {
      const opening = !col.classList.contains("open");
      col.classList.toggle("open", opening);
      mobileBtn.setAttribute("aria-expanded", String(opening));
    };
  }
}

wireColumnToggle("groups-collapse-btn", null, "groups-col");
wireColumnToggle("conversations-collapse-btn", "conversations-toggle-btn", "conversations-col");
wireColumnToggle("context-collapse-btn", "context-toggle-btn", "context-panel");
```

`#groups-col` continua usando o `#sidebar-toggle` já existente pra abrir no mobile — ajustar seu handler (já existe, só trocar o id do elemento alvo):

```js
document.getElementById("sidebar-toggle").onclick = () => {
  const sidebar = document.getElementById("groups-col");
  const opening = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", opening);
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", String(opening));
};
```

E `closeSidebarOnMobile()`:

```js
function closeSidebarOnMobile() {
  document.getElementById("groups-col").classList.remove("open");
  document.getElementById("sidebar-toggle").setAttribute("aria-expanded", "false");
}
```

- [ ] **Step 4: Atualizar as demais referências a `#sidebar`/`#group-list li` que dependiam do markup antigo**

`showView()` já usa `#group-list li` (sem mudança de id, continua igual). Nenhuma outra função referenciava `#sidebar` diretamente.

- [ ] **Step 5: Verificar no browser**

Suba o servidor, crie 2-3 grupos (via `new-group-btn`, que ainda abre o modal só na Task 6 — por ora, teste digitando na URL da API ou temporariamente religando o form antigo não é necessário: a lista deve renderizar vazia/"Nenhum grupo ainda" corretamente até a Task 6 entregar a criação). Confirme visualmente que a busca filtra a lista quando há grupos (pode inserir grupos direto via `curl -X POST http://localhost:8000/api/groups -H "Content-Type: application/json" -d "{\"name\":\"teste\",\"icon\":\"📊\"}"` pra popular dados de teste nesta etapa) e que a setinha de colapso encolhe/expande a coluna.

- [ ] **Step 6: Commit**

```bash
git add app/static/app.js
git commit -m "feat: render group icons, add group search filter and column collapse"
```

---

### Task 6: JS — criar/renomear grupo com grade de emoji (substitui `prompt()`)

**Files:**
- Modify: `app/static/app.js`

- [ ] **Step 1: Definir a paleta fixa de emojis e a função que desenha a grade**

Adicionar, próximo ao topo do arquivo (depois de `AGENT_HUES`):

```js
const GROUP_ICON_OPTIONS = ["💬", "📊", "📁", "🧑", "📚", "🎨", "💰", "⚙️", "🧪", "🚀", "📈", "🗑️"];

function renderGroupIconGrid(selectedIcon) {
  const grid = document.getElementById("group-icon-grid");
  grid.innerHTML = "";
  for (const icon of GROUP_ICON_OPTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "icon-grid-option" + (icon === selectedIcon ? " selected" : "");
    btn.textContent = icon;
    btn.setAttribute("aria-label", `Usar ícone ${icon}`);
    btn.onclick = () => {
      grid.dataset.selected = icon;
      for (const other of grid.querySelectorAll(".icon-grid-option")) {
        other.classList.toggle("selected", other === btn);
      }
    };
    grid.appendChild(btn);
  }
  grid.dataset.selected = selectedIcon;
}
```

- [ ] **Step 2: Funções de abrir/fechar o modal**

```js
function openGroupForm({ groupId = null, name = "", icon = GROUP_ICON_OPTIONS[0] } = {}) {
  state.editingGroupId = groupId;
  document.getElementById("group-form-title").textContent = groupId ? "Renomear grupo" : "Novo grupo";
  document.getElementById("group-form-name").value = name;
  renderGroupIconGrid(icon);
  document.getElementById("group-form-backdrop").classList.remove("hidden");
  document.getElementById("group-form-name").focus();
}

function closeGroupForm() {
  document.getElementById("group-form-backdrop").classList.add("hidden");
  document.getElementById("group-form").reset();
  state.editingGroupId = null;
}
```

- [ ] **Step 3: Ligar os botões que abrem o modal (`+ Novo grupo` e `Renomear`)**

Remover o handler antigo de `#new-group-form` (o form de criação inline não existe mais no HTML) e o corpo antigo de `#rename-group-btn`. No lugar, adicionar:

```js
document.getElementById("new-group-btn").onclick = () => openGroupForm();

document.getElementById("rename-group-btn").onclick = () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  if (!group) return;
  openGroupForm({ groupId: group.id, name: group.name, icon: group.icon });
};

document.getElementById("group-form-cancel").onclick = () => closeGroupForm();

document.getElementById("group-form-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "group-form-backdrop") closeGroupForm();
});

document.getElementById("group-form").onsubmit = async (e) => {
  e.preventDefault();
  const name = document.getElementById("group-form-name").value.trim();
  if (!name) return;
  const icon = document.getElementById("group-icon-grid").dataset.selected || GROUP_ICON_OPTIONS[0];

  if (state.editingGroupId) {
    await api(`/api/groups/${state.editingGroupId}`, {
      method: "PUT",
      body: JSON.stringify({ name, icon }),
    });
  } else {
    await api("/api/groups", { method: "POST", body: JSON.stringify({ name, icon }) });
  }
  closeGroupForm();
  await loadGroups();
  if (state.activeGroupId) {
    const updated = state.groups.find((g) => g.id === state.activeGroupId);
    document.getElementById("channel-header-name").textContent = updated ? `# ${updated.name}` : "";
  }
};
```

- [ ] **Step 4: Verificar no browser**

Suba o servidor. Clique em "+ Novo grupo", escolha um emoji da grade, digite um nome, salve — o grupo deve aparecer na lista com o ícone escolhido. Selecione o grupo, clique "Renomear", troque nome e ícone — confirme que ambos atualizam na lista e no header do chat.

- [ ] **Step 5: Commit**

```bash
git add app/static/app.js
git commit -m "feat: create/rename groups via an emoji-grid form instead of prompt()"
```

---

### Task 7: JS — coluna de conversas como cards (substitui as abas)

**Files:**
- Modify: `app/static/app.js`

- [ ] **Step 1: Trocar `renderConversationTabs()` por `renderConversationList()`**

Remover a função `renderConversationTabs()` inteira e substituir por:

```js
function renderConversationList() {
  const list = document.getElementById("conversation-list");
  list.innerHTML = "";

  for (const conversation of state.conversations) {
    const card = document.createElement("li");
    card.className = "conversation-card" + (conversation.id === state.activeConversationId ? " active" : "");
    card.onclick = () => selectConversation(conversation.id);

    const name = document.createElement("span");
    name.className = "conversation-card-name";
    name.textContent = conversation.name;
    card.appendChild(name);

    const actions = document.createElement("span");
    actions.className = "conversation-card-actions";

    const renameBtn = document.createElement("span");
    renameBtn.className = "conversation-card-rename";
    renameBtn.textContent = "✎";
    renameBtn.tabIndex = 0;
    renameBtn.setAttribute("role", "button");
    renameBtn.setAttribute("aria-label", `Renomear conversa ${conversation.name}`);
    const renameConversation = async (e) => {
      e.stopPropagation();
      const newName = prompt("Novo nome da conversa:", conversation.name);
      if (!newName || !newName.trim() || newName.trim() === conversation.name) return;
      const updated = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "PUT", body: JSON.stringify({ name: newName.trim() }) }
      );
      const target = state.conversations.find((c) => c.id === conversation.id);
      if (target) target.name = updated.name;
      renderConversationList();
    };
    renameBtn.onclick = renameConversation;
    renameBtn.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        renameConversation(e);
      }
    };
    actions.appendChild(renameBtn);

    const closeBtn = document.createElement("span");
    closeBtn.className = "conversation-card-delete";
    closeBtn.textContent = "×";
    closeBtn.tabIndex = 0;
    closeBtn.setAttribute("role", "button");
    closeBtn.setAttribute("aria-label", `Apagar conversa ${conversation.name}`);
    const deleteConversation = async (e) => {
      e.stopPropagation();
      if (!confirm(`Apagar a conversa "${conversation.name}"? As mensagens dela serão perdidas permanentemente.`)) return;
      const remaining = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "DELETE" }
      );
      state.conversations = remaining;
      if (state.activeConversationId === conversation.id) {
        await selectConversation(remaining[0].id);
      } else {
        renderConversationList();
      }
    };
    closeBtn.onclick = deleteConversation;
    closeBtn.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        deleteConversation(e);
      }
    };
    actions.appendChild(closeBtn);

    card.appendChild(actions);
    list.appendChild(card);
  }
}
```

- [ ] **Step 2: Atualizar quem chamava `renderConversationTabs()`**

Em `loadConversations()`, trocar a chamada `renderConversationTabs();` por `renderConversationList();`. Em `selectConversation()`, trocar `renderConversationTabs();` por `renderConversationList();`.

- [ ] **Step 3: Mover o botão "+ Nova conversa" pro seu novo lugar (fora da lista, no header da coluna)**

O botão `#new-conversation-btn` já existe fixo no HTML (Task 3), fora de `renderConversationList()` — então em vez de criá-lo dinamicamente dentro da função de render (como no código antigo), ele só precisa de um handler fixo. Adicionar, junto dos outros handlers fixos:

```js
document.getElementById("new-conversation-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const name = prompt("Nome da nova conversa:");
  if (!name || !name.trim()) return;
  const conversation = await api(`/api/groups/${state.activeGroupId}/conversations`, {
    method: "POST",
    body: JSON.stringify({ name: name.trim() }),
  });
  state.conversations.push(conversation);
  await selectConversation(conversation.id);
};
```

- [ ] **Step 4: Verificar no browser**

Selecione um grupo, confirme que a coluna de conversas mostra os cards (não mais abas horizontais), que "+ Nova conversa" cria e seleciona uma nova conversa, que ✎/× continuam renomeando/apagando, e que a seta de colapso da coluna de conversas encolhe/expande independente da coluna de grupos.

- [ ] **Step 5: Commit**

```bash
git add app/static/app.js
git commit -m "refactor: replace horizontal conversation tabs with a card list column"
```

---

### Task 8: JS — painel de contexto (info da conversa + membros realocados)

**Files:**
- Modify: `app/static/app.js`

- [ ] **Step 1: Preencher "Contexto da conversa" ao trocar de conversa**

Adicionar uma função:

```js
function renderConversationContext() {
  const el = document.getElementById("context-conversation-info");
  const conversation = state.conversations.find((c) => c.id === state.activeConversationId);
  if (!conversation) {
    el.textContent = "";
    return;
  }
  const created = new Date(conversation.created_at.replace(" ", "T") + "Z");
  const formatted = created.toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  el.textContent = `${conversation.name} · criada em ${formatted}`;
}
```

Chamar essa função em `selectConversation()`, junto das outras chamadas de reset — adicionar `renderConversationContext();` logo depois de `renderConversationList();`:

```js
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

E também depois que `loadConversations()` decide manter a conversa ativa (já que nesse caso `selectConversation` não é chamado de novo):

```js
async function loadConversations(groupId) {
  state.conversations = await api(`/api/groups/${groupId}/conversations`);
  const stillActive = state.conversations.some((c) => c.id === state.activeConversationId);
  if (stillActive) {
    renderConversationList();
    renderConversationContext();
  } else {
    await selectConversation(state.conversations[0].id);
  }
}
```

- [ ] **Step 2: Confirmar que `renderMembers()`/`loadMembers()` não precisam de mudança**

`renderMembers()` e `loadMembers()` já usam `document.getElementById("member-list")` e `document.getElementById("add-member-select")` — esses ids continuam existindo, só mudaram de container (agora dentro de `#context-panel .context-block`, ver Task 3). Nenhuma linha de JS precisa mudar aqui.

- [ ] **Step 3: Verificar no browser**

Selecione uma conversa e confirme que o painel de contexto (coluna da direita) mostra "nome da conversa · criada em ...", a lista de membros com o seletor de adicionar agente, e o card estático "🌐 Busca na Web". Confirme que adicionar/remover membro ainda funciona normalmente a partir desse painel. Confirme que a seta de colapso do painel de contexto funciona independente das outras duas colunas.

- [ ] **Step 4: Commit**

```bash
git add app/static/app.js
git commit -m "feat: show conversation info in the context panel on conversation switch"
```

---

### Task 9: CSS — acabamento visual (cards, painel de contexto, grade de emoji, acento laranja)

**Files:**
- Modify: `app/static/style.css`

- [ ] **Step 1: Ajustar o acento pra mais próximo do laranja do mockup**

Trocar, em `:root`:

```css
  --hue: 55;
```

por:

```css
  --hue: 48;
```

(mantém a mesma família de tokens `oklch` — só desloca o hue de amarelo-verde pra mais laranja; todo o resto do arquivo já referencia `var(--hue)`, então essa é a única linha que precisa mudar pra afetar o tema inteiro.)

- [ ] **Step 2: Estilo dos cards de conversa**

Adicionar (substitui as regras de `.conversation-tab*` já removidas na Task 4):

```css
#conversation-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.conversation-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink-muted);
  transition: background-color 150ms ease, color 150ms ease, border-color 150ms ease;
}
.conversation-card:hover { background: var(--surface-hover); color: var(--ink); }
.conversation-card.active { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }
.conversation-card-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.conversation-card-actions { display: inline-flex; gap: 6px; flex: 0 0 auto; }
.conversation-card-rename, .conversation-card-delete { color: var(--ink-faint); font-size: 13px; line-height: 1; transition: color 150ms ease; }
.conversation-card.active .conversation-card-rename, .conversation-card.active .conversation-card-delete { color: var(--accent-ink); opacity: 0.75; }
.conversation-card-rename:hover { color: var(--ink); }
.conversation-card-delete:hover { color: var(--danger); }
```

- [ ] **Step 3: Estilo do painel de contexto**

```css
.context-block { display: flex; flex-direction: column; gap: 8px; }
.context-block-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-faint); }
#context-conversation-info { font-size: 13px; color: var(--ink-muted); }
.tool-badge {
  display: inline-flex;
  align-self: flex-start;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 12px;
  color: var(--ink-muted);
}
```

- [ ] **Step 4: Estilo do modal de grupo e da grade de emoji**

```css
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 30;
}
.modal {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 20px;
  width: min(360px, calc(100vw - 32px));
}
.modal h3 { margin: 0 0 14px; font-size: 15px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }

#group-icon-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; margin-bottom: 14px; }
.icon-grid-option {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 18px;
  padding: 6px 0;
  cursor: pointer;
}
.icon-grid-option:hover { background: var(--surface-hover); }
.icon-grid-option.selected { border-color: var(--accent); background: var(--surface-hover); }
```

- [ ] **Step 5: Verificar no browser**

Confirme visualmente (screenshot) que: o acento (botões primários, aba/card ativo) está mais laranja; os cards de conversa têm cantos arredondados e destacam o ativo; o painel de contexto tem títulos de seção em uppercase discreto; o modal de grupo abre centralizado com fundo escurecido e a grade de emoji em 6 colunas.

- [ ] **Step 6: Commit**

```bash
git add app/static/style.css
git commit -m "polish: card styling, context panel, emoji grid, and warmer accent hue"
```

---

### Task 10: Verificação final

**Files:** nenhum (só verificação)

- [ ] **Step 1: Rodar a suíte de backend inteira**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `... passed` — mesmo total de antes mais os 5 testes novos da Task 1/2, sem nenhuma falha.

- [ ] **Step 2: Roteiro manual completo no browser (desktop)**

Com o servidor rodando, usando o browser: criar um grupo com ícone pela grade; renomear esse grupo trocando nome e ícone; criar uma segunda conversa nesse grupo e alternar entre elas pelos cards; adicionar um agente como membro pelo painel de contexto e enviar uma mensagem mencionando-o (`@nome`), confirmando que a resposta chega, que o indicador de fila e o botão "Parar" aparecem/desaparecem corretamente, e que o markdown/chip de busca (se o agente buscar algo) ainda renderizam certo; colapsar e expandir cada uma das 3 colunas laterais independentemente; usar a busca de grupos pra filtrar a lista.

- [ ] **Step 3: Roteiro manual no mobile**

Com `resize_window` (preset mobile) ou a janela do browser abaixo de 760px: confirmar que só a área de chat aparece por padrão, que o `☰` abre a coluna de grupos como overlay, que o botão de conversas (no header do chat, mobile-only) abre a coluna de conversas como overlay, e que o botão "i" (mobile-only) abre o painel de contexto como overlay pela direita — cada um fechando ao tocar fora ou selecionar um item.

- [ ] **Step 4: Confirmar que nada da Task 1 (mentions/queue) e Task 2 (markdown/stop) comitadas antes deste plano regrediu**

Reenviar uma mensagem com múltiplas menções (`@a @b @c`) e confirmar que as respostas ainda chegam na ordem certa; clicar em "Parar" com uma resposta pendente na fila e confirmar que ela é cancelada e a mensagem de sistema aparece.

Nenhum commit nesta task — é só a checagem final antes de considerar o plano concluído.
