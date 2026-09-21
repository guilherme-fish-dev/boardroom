# Menu de Categorias + Mover em Massa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar uma tela dedicada de "Categorias" (acessível por um submenu accordion sob "Agentes"), com um widget de mover-em-massa (dual-listbox) para adicionar/remover vários agentes de uma categoria de uma vez, substituindo o painel de categorias que hoje vive dentro da tela de Agentes.

**Architecture:** Backend FastAPI + SQLite: 3 endpoints novos em `app/routers/agent_categories.py` (listar membros de uma categoria, adicionar em lote, remover em lote). Frontend vanilla JS/HTML: a barra lateral ganha um accordion (`Agentes ▾` expande em `Agentes` / `Categorias`); o painel de gestão de categoria (criar/apagar) migra da tela de Agentes para a nova tela de Categorias, que ganha um dual-listbox com seleção múltipla nativa (`<select multiple>`) e busca por nome em cada coluna.

**Tech Stack:** Python 3 / FastAPI / SQLite (`sqlite3` puro) / pytest / HTML+CSS+JS vanilla.

Spec de referência: [docs/superpowers/specs/2026-09-21-menu-categorias-mover-em-massa-design.md](../specs/2026-09-21-menu-categorias-mover-em-massa-design.md)

---

### Task 1: Endpoints de membros em lote

**Files:**
- Modify: `app/routers/agent_categories.py`
- Test: `tests/test_agent_categories_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_agent_categories_api.py`:

```python
def _create_agent(client, name="bob"):
    return client.post(
        "/api/agents",
        json={
            "name": name,
            "persona_prompt": "x",
            "model_name": "m",
            "vision_capable": False,
        },
    ).json()


def test_list_agent_category_members_empty(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    resp = client.get("/api/agent-categories/9999/members")
    assert resp.status_code == 404


def test_add_agent_category_members_bulk(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")
    alice = _create_agent(client, "alice")

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add",
        json={"agent_ids": [bob["id"], alice["id"]]},
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert sorted(m["name"] for m in resp.json()) == ["alice", "bob"]


def test_add_agent_category_members_is_idempotent(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")

    client.post(f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [bob["id"]]})
    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert [m["name"] for m in resp.json()] == ["bob"]


def test_add_agent_category_members_invalid_agent_id_returns_400(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [9999]}
    )
    assert resp.status_code == 400

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert resp.json() == []


def test_add_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    bob = _create_agent(client, "bob")
    resp = client.post("/api/agent-categories/9999/members/add", json={"agent_ids": [bob["id"]]})
    assert resp.status_code == 404


def test_remove_agent_category_members_bulk(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")
    alice = _create_agent(client, "alice")
    client.post(
        f"/api/agent-categories/{category['id']}/members/add",
        json={"agent_ids": [bob["id"], alice["id"]]},
    )

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/remove", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert [m["name"] for m in resp.json()] == ["alice"]


def test_remove_agent_category_members_is_idempotent_for_non_member(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/remove", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204


def test_remove_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    bob = _create_agent(client, "bob")
    resp = client.post("/api/agent-categories/9999/members/remove", json={"agent_ids": [bob["id"]]})
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest tests/test_agent_categories_api.py -v`
Expected: os 9 testes novos falham com 404 (rotas não existem)

- [ ] **Step 3: Adicionar os 3 endpoints**

Em `app/routers/agent_categories.py`, adicionar (após a classe `AgentCategoryOut`, antes de `@router.get("", ...)`):

```python
class AgentCategoryMemberOut(BaseModel):
    id: int
    name: str


class AgentCategoryMemberIds(BaseModel):
    agent_ids: list[int]
```

E adicionar, ao final do arquivo, estas 3 rotas:

```python
@router.get("/{category_id}/members", response_model=list[AgentCategoryMemberOut])
def list_agent_category_members(category_id: int) -> list[AgentCategoryMemberOut]:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        rows = conn.execute(
            "SELECT agents.id, agents.name FROM agent_category_members "
            "JOIN agents ON agents.id = agent_category_members.agent_id "
            "WHERE agent_category_members.category_id = ? ORDER BY agents.id",
            (category_id,),
        ).fetchall()
    finally:
        conn.close()
    return [AgentCategoryMemberOut(id=r["id"], name=r["name"]) for r in rows]


@router.post("/{category_id}/members/add", status_code=204)
def add_agent_category_members(category_id: int, payload: AgentCategoryMemberIds) -> Response:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")

        # Validar a existência de cada agent_id ANTES de inserir, em vez de confiar em
        # "INSERT OR IGNORE" sozinho: OR IGNORE também suprime a violação de FK de um
        # agent_id inexistente (mesma armadilha já vista na sincronização de categorias
        # em app/routers/agents.py), o que faria um id inválido ser silenciosamente
        # ignorado em vez de retornar 400. Validando antes, o INSERT OR IGNORE abaixo só
        # precisa lidar com o caso inofensivo de "já é membro" (colisão de PK).
        agent_ids = list(dict.fromkeys(payload.agent_ids))
        for agent_id in agent_ids:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if agent is None:
                raise HTTPException(status_code=400, detail=f"agent {agent_id} not found")

        for agent_id in agent_ids:
            conn.execute(
                "INSERT OR IGNORE INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
                (category_id, agent_id),
            )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.post("/{category_id}/members/remove", status_code=204)
def remove_agent_category_members(category_id: int, payload: AgentCategoryMemberIds) -> Response:
    conn = get_connection()
    try:
        category = conn.execute(
            "SELECT id FROM agent_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        for agent_id in payload.agent_ids:
            conn.execute(
                "DELETE FROM agent_category_members WHERE category_id = ? AND agent_id = ?",
                (category_id, agent_id),
            )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest tests/test_agent_categories_api.py -v`
Expected: todos passed (deve dar 15 passed: 6 já existentes + 9 novos)

- [ ] **Step 5: Rodar a suíte completa**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest -v`
Expected: todos os testes PASS

- [ ] **Step 6: Commit**

```bash
git add app/routers/agent_categories.py tests/test_agent_categories_api.py
git commit -m "feat: adiciona endpoints de membros em lote para categorias de agente"
```

---

### Task 2: Submenu accordion + tela dedicada de Categorias (estrutura)

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

Sem testes automatizados (frontend vanilla sem test runner) — validação manual na Task 4.

- [ ] **Step 1: Reestruturar a navegação e mover o painel de categoria para uma view própria**

Em `app/static/index.html`, localizar o bloco:

```html
        <nav>
          <button id="nav-agents" type="button"><span class="nav-icon">👥</span> Agentes</button>
          <button id="nav-settings" type="button"><span class="nav-icon">⚙️</span> Configurações</button>
        </nav>
```

e substituir por:

```html
        <nav>
          <button id="nav-agents-toggle" type="button" aria-expanded="false">
            <span class="nav-icon">👥</span> Agentes <span class="nav-caret">▾</span>
          </button>
          <div id="nav-agents-submenu" class="nav-submenu hidden">
            <button id="nav-agents" type="button">Agentes</button>
            <button id="nav-agent-categories" type="button">Categorias</button>
          </div>
          <button id="nav-settings" type="button"><span class="nav-icon">⚙️</span> Configurações</button>
        </nav>
```

Depois, localizar o bloco `#view-agents` (que hoje contém `#agent-categories-panel` logo no início) e substituir o `#view-agents` inteiro por (removendo `#agent-categories-panel` de dentro dele) mais uma nova view `#view-agent-categories` logo em seguida:

```html
      <div id="view-agents" class="view hidden">
        <h2>Agentes</h2>
        <ul id="agent-list"></ul>
        <form id="agent-form">
          <label>Nome (sem espaços)
            <input id="agent-name" placeholder="ex.: investidor-conservador" required />
          </label>
          <label>Subtítulo (resumo curto, aparece como dica ao passar o mouse)
            <input id="agent-subtitle" placeholder="ex.: analista cauteloso de risco" maxlength="140" />
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
          <label>Categorias
            <div id="agent-category-checkboxes"></div>
          </label>
          <div class="agent-form-actions">
            <button type="submit" class="btn-primary" id="agent-submit-btn">Salvar agente</button>
            <button type="button" id="cancel-edit-agent-btn" class="btn-secondary hidden">Cancelar edição</button>
            <button type="button" id="delete-agent-btn" class="btn-secondary hidden">Apagar agente</button>
          </div>
        </form>
      </div>
      <div id="view-agent-categories" class="view hidden">
        <h2>Categorias</h2>
        <div id="agent-categories-panel">
          <div id="agent-category-list"></div>
          <form id="agent-category-form">
            <input id="agent-category-name" placeholder="Nova categoria" required />
            <button type="submit" class="btn-secondary">Criar categoria</button>
          </form>
        </div>
      </div>
```

(O conteúdo do dual-listbox dentro de `#view-agent-categories` é adicionado na Task 3 — por enquanto essa view só tem a lista/criação/exclusão de categoria, que é exatamente o `#agent-categories-panel` que saiu de `#view-agents`.)

- [ ] **Step 2: Atualizar `showView` para reconhecer a nova view e controlar o submenu**

Em `app/static/app.js`, localizar a função `showView` (procure por `function showView(name) {`) e substituir por:

```javascript
function showView(name) {
  state.activeView = name;
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  }
  document.getElementById("nav-agents").classList.toggle("active", name === "agents");
  document.getElementById("nav-agent-categories").classList.toggle("active", name === "agent-categories");
  document.getElementById("nav-settings").classList.toggle("active", name === "settings");
  updateAgentsSubmenu();
  if (name !== "channel") {
    for (const li of document.querySelectorAll("#group-list li")) {
```

(mantenha o resto do corpo da função exatamente como está hoje — só as linhas acima do `if (name !== "channel")` mudam)

- [ ] **Step 3: Adicionar o estado e a função do accordion**

Em `app/static/app.js`, no objeto `state` (topo do arquivo), adicionar a chave (após `editingGroupId: null,`):

```javascript
  editingGroupId: null,
  agentsSubmenuOpen: false,
```

Logo antes da função `function showView(name) {`, adicionar:

```javascript
function updateAgentsSubmenu() {
  const isAgentsSection = state.activeView === "agents" || state.activeView === "agent-categories";
  const shouldShow = state.agentsSubmenuOpen || isAgentsSection;
  document.getElementById("nav-agents-submenu").classList.toggle("hidden", !shouldShow);
  document.getElementById("nav-agents-toggle").setAttribute("aria-expanded", String(shouldShow));
  document.getElementById("nav-agents-toggle").classList.toggle("active", isAgentsSection);
}

```

- [ ] **Step 4: Ligar o botão do accordion e o novo item de menu "Categorias"**

Em `app/static/app.js`, localizar:

```javascript
document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
  await loadModels();
  await loadAgentCategories();
};
```

e adicionar logo depois (mantendo o bloco acima intacto):

```javascript
document.getElementById("nav-agents-toggle").onclick = () => {
  state.agentsSubmenuOpen = !state.agentsSubmenuOpen;
  updateAgentsSubmenu();
};

document.getElementById("nav-agent-categories").onclick = async () => {
  showView("agent-categories");
  await loadAgentCategories();
};
```

- [ ] **Step 5: Estilizar o accordion**

Em `app/static/style.css`, logo após a regra `.nav-icon { font-size: 14px; }` (procure por essa linha), adicionar:

```css
.nav-caret { margin-left: auto; font-size: 11px; opacity: .7; }
#nav-agents-toggle { display: flex; align-items: center; width: 100%; }
.nav-submenu { display: flex; flex-direction: column; padding-left: 24px; }
.nav-submenu button { text-align: left; }
```

- [ ] **Step 6: Rodar a suíte de testes Python (deve continuar 100% verde, já que nada Python foi tocado)**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest -q`
Expected: todos os testes PASS

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: move gestão de categoria para tela própria com submenu accordion"
```

---

### Task 3: Dual-listbox de mover em massa

**Files:**
- Modify: `app/static/index.html` (`#view-agent-categories`)
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

- [ ] **Step 1: Adicionar o markup do dual-listbox**

Em `app/static/index.html`, dentro de `#view-agent-categories` (criado na Task 2), logo depois do `</div>` que fecha `#agent-categories-panel` e antes do `</div>` que fecha `#view-agent-categories`, adicionar:

```html
        <div id="category-bulk-move" class="hidden">
          <div class="label">Mover agentes — categoria selecionada: <strong id="active-category-name"></strong></div>
          <div class="dual-listbox">
            <div class="dual-listbox-column">
              <div class="label">Disponíveis</div>
              <input id="available-agents-search" placeholder="Buscar agente..." />
              <select id="available-agents-select" multiple></select>
            </div>
            <div class="dual-listbox-actions">
              <button type="button" id="add-to-category-btn" class="btn-secondary">Adicionar →</button>
              <button type="button" id="remove-from-category-btn" class="btn-secondary">← Remover</button>
            </div>
            <div class="dual-listbox-column">
              <div class="label">Nesta categoria</div>
              <input id="category-members-search" placeholder="Buscar agente..." />
              <select id="category-members-select" multiple></select>
            </div>
          </div>
        </div>
```

- [ ] **Step 2: Adicionar estado e seleção de categoria ativa**

Em `app/static/app.js`, no objeto `state`, adicionar (após `agentsSubmenuOpen: false,`):

```javascript
  agentsSubmenuOpen: false,
  activeAgentCategoryId: null,
  categoryMembers: [],
```

Localizar a função `renderAgentCategoryList` (procure por `function renderAgentCategoryList() {`) e substituir por (a única mudança é: destacar o chip ativo, tornar o nome clicável para selecionar a categoria, e desmarcar a categoria ativa se ela for apagada):

```javascript
function renderAgentCategoryList() {
  const container = document.getElementById("agent-category-list");
  container.innerHTML = "";
  if (state.agentCategories.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-hint";
    empty.textContent = "Nenhuma categoria ainda.";
    container.appendChild(empty);
    return;
  }
  for (const category of state.agentCategories) {
    const chip = document.createElement("span");
    chip.className = "category-chip";
    chip.classList.toggle("active", category.id === state.activeAgentCategoryId);
    const label = document.createElement("span");
    label.textContent = category.name;
    label.style.cursor = "pointer";
    label.onclick = () => selectActiveCategory(category.id);
    chip.appendChild(label);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Apagar categoria ${category.name}`);
    removeBtn.onclick = async () => {
      if (
        category.agent_count > 0 &&
        !confirm(`Esta categoria tem ${category.agent_count} agente(s). Apagar mesmo assim?`)
      ) {
        return;
      }
      const previouslyChecked = currentlyCheckedCategoryIds();
      await api(`/api/agent-categories/${category.id}`, { method: "DELETE" });
      if (state.activeAgentCategoryId === category.id) {
        await selectActiveCategory(null);
      }
      state.agentCategories = await api("/api/agent-categories");
      renderAgentCategoryList();
      renderAgentCategoryCheckboxes(previouslyChecked.filter((id) => id !== category.id));
      await loadAgents();
    };
    chip.appendChild(removeBtn);
    container.appendChild(chip);
  }
}
```

- [ ] **Step 3: Adicionar as funções do dual-listbox**

Em `app/static/app.js`, logo depois da função `renderAgentCategoryList` (do Step 2 acima), adicionar:

```javascript
async function selectActiveCategory(categoryId) {
  state.activeAgentCategoryId = categoryId;
  renderAgentCategoryList();
  const bulkPanel = document.getElementById("category-bulk-move");
  if (categoryId === null) {
    bulkPanel.classList.add("hidden");
    return;
  }
  bulkPanel.classList.remove("hidden");
  const category = state.agentCategories.find((c) => c.id === categoryId);
  document.getElementById("active-category-name").textContent = category ? category.name : "";
  await loadCategoryMembers(categoryId);
}

async function loadCategoryMembers(categoryId) {
  if (state.agents.length === 0) {
    await loadAgents();
  }
  state.categoryMembers = await api(`/api/agent-categories/${categoryId}/members`);
  renderCategoryBulkMove();
}

function renderCategoryBulkMove() {
  const memberIds = new Set(state.categoryMembers.map((m) => m.id));
  const availableSearch = document.getElementById("available-agents-search").value.trim().toLowerCase();
  const memberSearch = document.getElementById("category-members-search").value.trim().toLowerCase();

  const availableSelect = document.getElementById("available-agents-select");
  availableSelect.innerHTML = "";
  for (const agent of state.agents) {
    if (memberIds.has(agent.id)) continue;
    if (availableSearch && !agent.name.toLowerCase().includes(availableSearch)) continue;
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.name;
    availableSelect.appendChild(option);
  }

  const memberSelect = document.getElementById("category-members-select");
  memberSelect.innerHTML = "";
  for (const member of state.categoryMembers) {
    if (memberSearch && !member.name.toLowerCase().includes(memberSearch)) continue;
    const option = document.createElement("option");
    option.value = member.id;
    option.textContent = member.name;
    memberSelect.appendChild(option);
  }
}

```

- [ ] **Step 4: Ligar os campos de busca e os botões Adicionar/Remover**

Em `app/static/app.js`, logo após o bloco `document.getElementById("nav-agent-categories").onclick = ...` (adicionado na Task 2), adicionar:

```javascript
document.getElementById("available-agents-search").addEventListener("input", renderCategoryBulkMove);
document.getElementById("category-members-search").addEventListener("input", renderCategoryBulkMove);

document.getElementById("add-to-category-btn").onclick = async () => {
  const select = document.getElementById("available-agents-select");
  const agentIds = Array.from(select.selectedOptions).map((o) => Number(o.value));
  if (agentIds.length === 0 || state.activeAgentCategoryId === null) return;
  await api(`/api/agent-categories/${state.activeAgentCategoryId}/members/add`, {
    method: "POST",
    body: JSON.stringify({ agent_ids: agentIds }),
  });
  state.agentCategories = await api("/api/agent-categories");
  renderAgentCategoryList();
  await loadAgents();
  await loadCategoryMembers(state.activeAgentCategoryId);
};

document.getElementById("remove-from-category-btn").onclick = async () => {
  const select = document.getElementById("category-members-select");
  const agentIds = Array.from(select.selectedOptions).map((o) => Number(o.value));
  if (agentIds.length === 0 || state.activeAgentCategoryId === null) return;
  await api(`/api/agent-categories/${state.activeAgentCategoryId}/members/remove`, {
    method: "POST",
    body: JSON.stringify({ agent_ids: agentIds }),
  });
  state.agentCategories = await api("/api/agent-categories");
  renderAgentCategoryList();
  await loadAgents();
  await loadCategoryMembers(state.activeAgentCategoryId);
};
```

- [ ] **Step 5: Garantir que trocar de categoria ativa ao navegar limpa a seleção anterior**

Em `app/static/app.js`, localizar o handler `nav-agent-categories` (adicionado na Task 2):

```javascript
document.getElementById("nav-agent-categories").onclick = async () => {
  showView("agent-categories");
  await loadAgentCategories();
};
```

e substituir por:

```javascript
document.getElementById("nav-agent-categories").onclick = async () => {
  showView("agent-categories");
  await loadAgentCategories();
  await selectActiveCategory(null);
};
```

- [ ] **Step 6: Estilizar o dual-listbox**

Em `app/static/style.css`, logo após a regra `#agent-category-checkboxes { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-bottom: 12px; }` (procure por essa linha), adicionar:

```css
.category-chip.active { border-color: var(--accent); }

.dual-listbox { display: flex; align-items: stretch; gap: 14px; margin-top: 10px; }
.dual-listbox-column { flex: 1; display: flex; flex-direction: column; gap: 6px; }
.dual-listbox-column select {
  height: 220px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--ink);
  border-radius: var(--radius-sm);
}
.dual-listbox-actions { display: flex; flex-direction: column; justify-content: center; gap: 8px; }
```

- [ ] **Step 7: Rodar a suíte de testes Python (deve continuar 100% verde)**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest -q`
Expected: todos os testes PASS

- [ ] **Step 8: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: adiciona mover em massa (dual-listbox) na tela de categorias"
```

---

### Task 4: Verificação manual no navegador

**Files:** nenhum (apenas verificação, sem alterações de código)

- [ ] **Step 1: Subir uma instância isolada do app**

Suba o servidor numa porta separada com um banco de dados temporário (não use a porta 8000 se o app real do usuário já estiver rodando nela — confirme antes). Ex.: `BOARDROOM_DB_PATH=/tmp/categorias-v2-test.db .venv/Scripts/python.exe -m uvicorn app.main:app --port 8020`.

- [ ] **Step 2: Testar o accordion**

Clicar em "Agentes ▾" na barra lateral: deve expandir mostrando "Agentes" e "Categorias". Clicar de novo deve recolher. Clicar em "Categorias" deve navegar e manter o submenu expandido com "Categorias" destacado.

- [ ] **Step 3: Testar a tela de Categorias sem painel duplicado**

Confirmar que a tela de Agentes não tem mais o painel de criar/apagar categoria (só o multi-select de checkboxes no formulário do agente). Confirmar que a tela de Categorias tem criar/listar/apagar categoria funcionando como antes.

- [ ] **Step 4: Testar o dual-listbox**

Criar 2 categorias e 3+ agentes. Clicar no nome de uma categoria (não no "×") para selecioná-la como ativa — o dual-listbox deve aparecer com todos os agentes em "Disponíveis". Selecionar 2 agentes com ctrl+clique (ou shift+clique) em "Disponíveis" e clicar "Adicionar →": ambos devem sumir de "Disponíveis" e aparecer em "Nesta categoria" (via `GET /api/agent-categories/{id}/members` — checar na aba Network). Testar a busca em ambas as colunas. Selecionar 1 agente em "Nesta categoria" e clicar "← Remover": deve voltar para "Disponíveis".

- [ ] **Step 5: Testar exclusão da categoria ativa**

Com uma categoria selecionada como ativa (dual-listbox visível), apagar essa mesma categoria (ícone "×" no chip). O dual-listbox deve desaparecer (nenhuma categoria mais selecionada).

- [ ] **Step 6: Checar o console do navegador**

Usar `read_console_messages` para garantir que não há erros JS durante todo o fluxo acima.

- [ ] **Step 7: Rodar a suíte de testes inteira uma última vez**

Run: `F:/Projetos/boardroom/.venv/Scripts/python.exe -m pytest -v`
Expected: todos os testes PASS
