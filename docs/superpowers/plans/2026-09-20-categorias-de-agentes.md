# Categorias de Agentes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar um conceito novo e independente de "categoria de agente" (N:N, opcional, sem cor) para organizar agentes, usado para filtrar o dropdown "+ adicionar agente" de um grupo de conversa.

**Architecture:** Backend FastAPI + SQLite: duas tabelas novas (`agent_categories`, `agent_category_members`) isoladas de `groups`/`group_members`, com um router CRUD próprio e uma extensão no router de agentes para sincronizar `category_ids`. Frontend vanilla JS/HTML: um bloco de gestão de categorias na tela de Agentes, um multi-select no formulário de agente, e um filtro de categoria no painel de membros do grupo.

**Tech Stack:** Python 3 / FastAPI / SQLite (`sqlite3` puro) / pytest / HTML+CSS+JS vanilla.

Spec de referência: [docs/superpowers/specs/2026-09-20-categorias-de-agentes-design.md](../specs/2026-09-20-categorias-de-agentes-design.md)

---

### Task 1: Tabelas `agent_categories` e `agent_category_members`

**Files:**
- Modify: `app/db.py:60-64` (SCHEMA)
- Test: `tests/test_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `tests/test_db.py`:

```python
def test_init_db_creates_agent_category_tables(db):
    conn = get_connection()
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"agent_categories", "agent_category_members"} <= tables


def test_agent_category_members_cascade_on_category_delete(db):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO agents (id, name, persona_prompt, model_name) VALUES (1, 'bob', 'x', 'm')")
        conn.execute("INSERT INTO agent_categories (id, name) VALUES (1, 'financeiro')")
        conn.execute("INSERT INTO agent_category_members (category_id, agent_id) VALUES (1, 1)")
        conn.commit()

        conn.execute("DELETE FROM agent_categories WHERE id = 1")
        conn.commit()

        members = conn.execute("SELECT * FROM agent_category_members").fetchall()
        agents = conn.execute("SELECT * FROM agents").fetchall()
    finally:
        conn.close()
    assert members == []
    assert len(agents) == 1  # apagar a categoria não apaga o agente


def test_agent_category_members_cascade_on_agent_delete(db):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO agents (id, name, persona_prompt, model_name) VALUES (1, 'bob', 'x', 'm')")
        conn.execute("INSERT INTO agent_categories (id, name) VALUES (1, 'financeiro')")
        conn.execute("INSERT INTO agent_category_members (category_id, agent_id) VALUES (1, 1)")
        conn.commit()

        conn.execute("DELETE FROM agents WHERE id = 1")
        conn.commit()

        members = conn.execute("SELECT * FROM agent_category_members").fetchall()
        categories = conn.execute("SELECT * FROM agent_categories").fetchall()
    finally:
        conn.close()
    assert members == []
    assert len(categories) == 1  # apagar o agente não apaga a categoria
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `pytest tests/test_db.py -k agent_category -v`
Expected: FAIL (tabelas não existem ainda)

- [ ] **Step 3: Adicionar as tabelas ao schema**

Em `app/db.py`, inserir logo antes de `CREATE TABLE IF NOT EXISTS settings` (linha 60):

```python
CREATE TABLE IF NOT EXISTS agent_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_category_members (
    category_id INTEGER NOT NULL REFERENCES agent_categories(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    PRIMARY KEY (category_id, agent_id)
);

```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `pytest tests/test_db.py -k agent_category -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Rodar a suíte inteira de `test_db.py` pra garantir que nada quebrou**

Run: `pytest tests/test_db.py -v`
Expected: todos os testes PASS

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: adiciona tabelas agent_categories e agent_category_members"
```

---

### Task 2: Router `agent_categories` (CRUD)

**Files:**
- Create: `app/routers/agent_categories.py`
- Modify: `app/main.py:13` (import), `app/main.py:46` (include_router)
- Test: `tests/test_agent_categories_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_agent_categories_api.py`:

```python
from app.db import get_connection
from app.main import create_app
from fastapi.testclient import TestClient


def make_client(db):
    return TestClient(create_app())


def test_create_and_list_agent_category(db):
    client = make_client(db)
    resp = client.post("/api/agent-categories", json={"name": "financeiro"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "financeiro"
    assert body["agent_count"] == 0

    resp = client.get("/api/agent-categories")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["financeiro"]


def test_create_agent_category_duplicate_name_rejected(db):
    client = make_client(db)
    client.post("/api/agent-categories", json={"name": "financeiro"})
    resp = client.post("/api/agent-categories", json={"name": "financeiro"})
    assert resp.status_code == 409


def test_list_agent_categories_includes_agent_count(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('bob', 'x', 'm')"
        )
        agent_id = conn.execute("SELECT id FROM agents WHERE name = 'bob'").fetchone()["id"]
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/agent-categories")
    assert resp.json()[0]["agent_count"] == 1


def test_delete_agent_category_removes_it(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.delete(f"/api/agent-categories/{category['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/agent-categories")
    assert resp.json() == []


def test_delete_agent_category_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/agent-categories/9999")
    assert resp.status_code == 404


def test_delete_agent_category_cascades_members(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('bob', 'x', 'm')"
        )
        agent_id = conn.execute("SELECT id FROM agents WHERE name = 'bob'").fetchone()["id"]
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.delete(f"/api/agent-categories/{category['id']}")
    assert resp.status_code == 204

    conn = get_connection()
    try:
        members = conn.execute("SELECT * FROM agent_category_members").fetchall()
        agents = conn.execute("SELECT * FROM agents").fetchall()
    finally:
        conn.close()
    assert members == []
    assert len(agents) == 1
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_agent_categories_api.py -v`
Expected: FAIL com erro 404 (rota não existe)

- [ ] **Step 3: Criar o router**

Criar `app/routers/agent_categories.py`:

```python
import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/agent-categories", tags=["agent-categories"])


class AgentCategoryIn(BaseModel):
    name: str


class AgentCategoryOut(AgentCategoryIn):
    id: int
    created_at: str
    agent_count: int


@router.get("", response_model=list[AgentCategoryOut])
def list_agent_categories() -> list[AgentCategoryOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT agent_categories.*, "
            "(SELECT COUNT(*) FROM agent_category_members "
            " WHERE agent_category_members.category_id = agent_categories.id) AS agent_count "
            "FROM agent_categories ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        AgentCategoryOut(
            id=r["id"], name=r["name"], created_at=r["created_at"], agent_count=r["agent_count"]
        )
        for r in rows
    ]


@router.post("", response_model=AgentCategoryOut, status_code=201)
def create_agent_category(category: AgentCategoryIn) -> AgentCategoryOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute("INSERT INTO agent_categories (name) VALUES (?)", (category.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="category name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM agent_categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return AgentCategoryOut(id=row["id"], name=row["name"], created_at=row["created_at"], agent_count=0)


@router.delete("/{category_id}", status_code=204)
def delete_agent_category(category_id: int) -> Response:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM agent_categories WHERE id = ?", (category_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="category not found")
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

- [ ] **Step 4: Registrar o router em `app/main.py`**

Em `app/main.py:13`, mudar:

```python
from app.routers import agents, conversations, groups, messages, models, settings, tts
```

para:

```python
from app.routers import agent_categories, agents, conversations, groups, messages, models, settings, tts
```

Em `app/main.py:46`, logo após `app.include_router(agents.router)`, adicionar:

```python
    app.include_router(agent_categories.router)
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_agent_categories_api.py -v`
Expected: 6 passed

- [ ] **Step 6: Rodar a suíte completa pra garantir que nada quebrou**

Run: `pytest -v`
Expected: todos os testes PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/agent_categories.py app/main.py tests/test_agent_categories_api.py
git commit -m "feat: adiciona endpoints CRUD de categorias de agente"
```

---

### Task 3: `category_ids` no router de agentes

**Files:**
- Modify: `app/routers/agents.py`
- Test: `tests/test_agents_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_agents_api.py`:

```python
def test_create_agent_with_categories(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
            "category_ids": [category["id"]],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["category_ids"] == [category["id"]]

    resp = client.get("/api/agents")
    assert resp.json()[0]["category_ids"] == [category["id"]]


def test_create_agent_without_categories_defaults_to_empty_list(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["category_ids"] == []


def test_update_agent_replaces_categories(db):
    client = make_client(db)
    cat_a = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    cat_b = client.post("/api/agent-categories", json={"name": "juridico"}).json()
    agent = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
            "category_ids": [cat_a["id"]],
        },
    ).json()

    resp = client.put(
        f"/api/agents/{agent['id']}",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
            "category_ids": [cat_b["id"]],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["category_ids"] == [cat_b["id"]]


def test_create_agent_with_unknown_category_id_returns_400(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
            "category_ids": [9999],
        },
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_agents_api.py -k category -v`
Expected: FAIL (`category_ids` não existe no payload/resposta)

- [ ] **Step 3: Modificar `app/routers/agents.py`**

Substituir todo o conteúdo do arquivo por:

```python
import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from app.db import get_connection, get_setting
from app.llm_client import chat_completion

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str
    persona_prompt: str
    subtitle: str = ""
    model_name: str
    vision_capable: bool = False
    category_ids: list[int] = []

    @field_validator("name")
    @classmethod
    def name_not_reserved(cls, value: str) -> str:
        # "all" is a reserved mention keyword (@all fans out to every group member, see
        # enqueue_mentions in app/routers/messages.py) — an agent with this literal name
        # would never be individually mentionable.
        if value.strip().lower() == "all":
            raise ValueError('"all" is a reserved name and can\'t be used for an agent')
        return value


class AgentOut(AgentIn):
    id: int
    created_at: str


def _category_ids_for_agent(conn: sqlite3.Connection, agent_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT category_id FROM agent_category_members WHERE agent_id = ? ORDER BY category_id",
        (agent_id,),
    ).fetchall()
    return [r["category_id"] for r in rows]


def _sync_agent_categories(conn: sqlite3.Connection, agent_id: int, category_ids: list[int]) -> None:
    conn.execute("DELETE FROM agent_category_members WHERE agent_id = ?", (agent_id,))
    for category_id in category_ids:
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category_id, agent_id),
        )


def _row_to_agent(conn: sqlite3.Connection, row: sqlite3.Row) -> AgentOut:
    return AgentOut(
        id=row["id"],
        name=row["name"],
        persona_prompt=row["persona_prompt"],
        subtitle=row["subtitle"],
        model_name=row["model_name"],
        vision_capable=bool(row["vision_capable"]),
        created_at=row["created_at"],
        category_ids=_category_ids_for_agent(conn, row["id"]),
    )


@router.get("", response_model=list[AgentOut])
def list_agents() -> list[AgentOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
        result = [_row_to_agent(conn, r) for r in rows]
    finally:
        conn.close()
    return result


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO agents (name, persona_prompt, subtitle, model_name, vision_capable) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    agent.name,
                    agent.persona_prompt,
                    agent.subtitle,
                    agent.model_name,
                    int(agent.vision_capable),
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        agent_id = cur.lastrowid
        try:
            _sync_agent_categories(conn, agent_id, agent.category_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="invalid category_id")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        result = _row_to_agent(conn, row)
    finally:
        conn.close()
    return result


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "UPDATE agents SET name = ?, persona_prompt = ?, subtitle = ?, model_name = ?, "
                "vision_capable = ? WHERE id = ?",
                (
                    agent.name,
                    agent.persona_prompt,
                    agent.subtitle,
                    agent.model_name,
                    int(agent.vision_capable),
                    agent_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            _sync_agent_categories(conn, agent_id, agent.category_ids)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="invalid category_id")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        result = _row_to_agent(conn, row)
    finally:
        conn.close()
    return result


PERSONA_SYSTEM_PROMPT = (
    "Você expande um esboço curto de persona num system prompt detalhado para um agente de "
    "IA que participa de conversas em grupo com outros agentes de IA. Mantenha o ponto de "
    "vista central do esboço, adicione traços de personalidade, forma de argumentar, e "
    "limites claros de comportamento. Responda só com o texto do system prompt final, sem "
    "comentários extras."
)


class GeneratePersonaIn(BaseModel):
    draft: str
    agent_name: str = ""


class GeneratePersonaOut(BaseModel):
    persona_prompt: str


@router.post("/generate-persona", response_model=GeneratePersonaOut)
def generate_persona(payload: GeneratePersonaIn) -> GeneratePersonaOut:
    conn = get_connection()
    try:
        assistant_model = get_setting(conn, "assistant_model")
        base_url = get_setting(conn, "llama_swap_base_url")
    finally:
        conn.close()

    if not assistant_model:
        raise HTTPException(
            status_code=400,
            detail="configure um modelo assistente em Configurações antes de usar essa função",
        )

    user_content = payload.draft
    if payload.agent_name:
        user_content = f"Nome do agente: {payload.agent_name}\n\nEsboço: {payload.draft}"

    try:
        persona_prompt = chat_completion(
            base_url=base_url,
            model=assistant_model,
            messages=[
                {"role": "system", "content": PERSONA_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"não foi possível gerar a persona: {exc}")

    return GeneratePersonaOut(persona_prompt=persona_prompt)


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

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_agents_api.py -v`
Expected: todos passed

- [ ] **Step 5: Rodar a suíte completa**

Run: `pytest -v`
Expected: todos os testes PASS

- [ ] **Step 6: Commit**

```bash
git add app/routers/agents.py tests/test_agents_api.py
git commit -m "feat: sincroniza category_ids ao criar/editar agente"
```

---

### Task 4: Bloco de gestão de categorias na tela de Agentes

**Files:**
- Modify: `app/static/index.html:116-143` (view-agents)
- Modify: `app/static/app.js` (state, novas funções, wiring)
- Modify: `app/static/style.css` (`.category-chip`)

Sem testes automatizados nesta task (frontend vanilla sem test runner no projeto) — validação é manual via preview do navegador na Task 7.

- [ ] **Step 1: Adicionar o bloco de categorias e o multi-select no HTML**

Em `app/static/index.html`, substituir o bloco `#view-agents` (linhas 116-143) por:

```html
      <div id="view-agents" class="view hidden">
        <h2>Agentes</h2>
        <div id="agent-categories-panel">
          <div class="context-block-title">Categorias</div>
          <div id="agent-category-list"></div>
          <form id="agent-category-form">
            <input id="agent-category-name" placeholder="Nova categoria" required />
            <button type="submit" class="btn-secondary">Criar categoria</button>
          </form>
        </div>
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
```

- [ ] **Step 2: Adicionar `agentCategories` ao estado**

Em `app/static/app.js:1-17`, no objeto `state`, adicionar a chave (após `agents: [],` na linha 7):

```javascript
  agents: [],
  agentCategories: [],
```

- [ ] **Step 3: Adicionar as funções de categoria**

Em `app/static/app.js`, logo antes da função `async function loadAgents() {` (linha 723), inserir:

```javascript
function currentlyCheckedCategoryIds() {
  return Array.from(
    document.querySelectorAll("#agent-category-checkboxes input[type=checkbox]:checked")
  ).map((cb) => Number(cb.value));
}

function renderAgentCategoryCheckboxes(selectedIds = []) {
  const container = document.getElementById("agent-category-checkboxes");
  container.innerHTML = "";
  if (state.agentCategories.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-hint";
    empty.textContent = "Nenhuma categoria criada ainda.";
    container.appendChild(empty);
    return;
  }
  for (const category of state.agentCategories) {
    const label = document.createElement("label");
    label.className = "checkbox-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = category.id;
    checkbox.checked = selectedIds.includes(category.id);
    label.appendChild(checkbox);
    label.append(` ${category.name}`);
    container.appendChild(label);
  }
}

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
    const label = document.createElement("span");
    label.textContent = category.name;
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
      state.agentCategories = await api("/api/agent-categories");
      renderAgentCategoryList();
      renderAgentCategoryCheckboxes(previouslyChecked.filter((id) => id !== category.id));
      await loadAgents();
    };
    chip.appendChild(removeBtn);
    container.appendChild(chip);
  }
}

async function loadAgentCategories() {
  state.agentCategories = await api("/api/agent-categories");
  renderAgentCategoryList();
  renderAgentCategoryCheckboxes([]);
}

```

- [ ] **Step 4: Marcar as categorias do agente ao editar/cancelar**

Em `app/static/app.js`, na função `startEditingAgent` (linha 750-760), adicionar uma linha antes do fechamento da função:

```javascript
function startEditingAgent(agent) {
  document.getElementById("agent-name").value = agent.name;
  document.getElementById("agent-subtitle").value = agent.subtitle || "";
  document.getElementById("agent-persona").value = agent.persona_prompt;
  document.getElementById("agent-vision").checked = agent.vision_capable;
  loadModels(agent.model_name);
  renderAgentCategoryCheckboxes(agent.category_ids || []);
  state.editingAgentId = agent.id;
  document.getElementById("agent-submit-btn").textContent = "Atualizar agente";
  document.getElementById("cancel-edit-agent-btn").classList.remove("hidden");
  document.getElementById("delete-agent-btn").classList.remove("hidden");
}
```

Na função `stopEditingAgent` (linha 762-769), adicionar `renderAgentCategoryCheckboxes([]);`:

```javascript
function stopEditingAgent() {
  document.getElementById("agent-form").reset();
  state.editingAgentId = null;
  document.getElementById("agent-submit-btn").textContent = "Salvar agente";
  document.getElementById("cancel-edit-agent-btn").classList.add("hidden");
  document.getElementById("delete-agent-btn").classList.add("hidden");
  renderAgentCategoryCheckboxes([]);
  loadModels();
}
```

- [ ] **Step 5: Enviar `category_ids` ao salvar o agente**

Em `app/static/app.js:926-948`, substituir o handler `agent-form`:

```javascript
document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("agent-name").value,
    persona_prompt: document.getElementById("agent-persona").value,
    subtitle: document.getElementById("agent-subtitle").value,
    model_name: document.getElementById("agent-model").value,
    vision_capable: document.getElementById("agent-vision").checked,
    category_ids: currentlyCheckedCategoryIds(),
  };
  if (state.editingAgentId) {
    await api(`/api/agents/${state.editingAgentId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  } else {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }
  stopEditingAgent();
  await loadAgents();
};
```

- [ ] **Step 6: Adicionar o handler de criar categoria e carregar categorias ao abrir a tela de Agentes**

Em `app/static/app.js`, logo após o bloco do Step 5 acima, adicionar:

```javascript
document.getElementById("agent-category-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("agent-category-name");
  const name = input.value.trim();
  if (!name) return;
  const previouslyChecked = currentlyCheckedCategoryIds();
  await api("/api/agent-categories", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  input.value = "";
  state.agentCategories = await api("/api/agent-categories");
  renderAgentCategoryList();
  renderAgentCategoryCheckboxes(previouslyChecked);
};
```

Em `app/static/app.js:884-886`, no handler `nav-agents`, adicionar `await loadAgentCategories();`:

```javascript
document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
  await loadAgentCategories();
};
```

- [ ] **Step 7: Estilizar o chip de categoria**

Em `app/static/style.css`, logo após a regra `.member-badge button:hover { color: var(--danger); }` (linha 265), adicionar:

```css
.category-chip {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 3px 6px 3px 8px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  margin: 0 6px 6px 0;
}
.category-chip button {
  background: none;
  border: none;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 0;
  font-size: 14px;
  line-height: 1;
  transition: color 150ms ease;
}
.category-chip button:hover { color: var(--danger); }

#agent-category-checkboxes { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-bottom: 12px; }
```

- [ ] **Step 8: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: adiciona gestão de categorias e atribuição no formulário de agente"
```

---

### Task 5: Filtro de categoria no dropdown de membro do grupo

**Files:**
- Modify: `app/static/index.html:90-96` (painel de Membros)
- Modify: `app/static/app.js:311-372` (`loadMembers`, `renderMembers`)

- [ ] **Step 1: Adicionar o select de filtro no HTML**

Em `app/static/index.html`, substituir as linhas 90-96:

```html
              <div class="context-block">
                <div class="context-block-title">Membros</div>
                <span id="member-list"></span>
                <select id="add-member-select">
                  <option value="">+ adicionar agente</option>
                </select>
              </div>
```

por:

```html
              <div class="context-block">
                <div class="context-block-title">Membros</div>
                <span id="member-list"></span>
                <select id="member-category-filter">
                  <option value="">Todas as categorias</option>
                </select>
                <select id="add-member-select">
                  <option value="">+ adicionar agente</option>
                </select>
              </div>
```

- [ ] **Step 2: Garantir que as categorias estão carregadas ao abrir o painel de membros**

Em `app/static/app.js:311-317`, substituir `loadMembers`:

```javascript
async function loadMembers(groupId) {
  if (state.agents.length === 0) {
    await loadAgents();
  }
  if (state.agentCategories.length === 0) {
    await loadAgentCategories();
  }
  state.members = await api(`/api/groups/${groupId}/members`);
  renderMembers(groupId);
}
```

- [ ] **Step 3: Separar a montagem do `add-member-select` numa função filtrável e popular o filtro**

Em `app/static/app.js:319-372`, substituir `renderMembers` inteira por:

```javascript
function renderMembers(groupId) {
  const list = document.getElementById("member-list");
  list.innerHTML = "";
  for (const member of state.members) {
    const badge = document.createElement("span");
    badge.className = "member-badge";
    const agent = state.agents.find((a) => a.id === member.id);
    const hint = agent?.subtitle || agent?.persona_prompt || "";
    if (hint) {
      badge.title = hint;
    }
    const dot = document.createElement("span");
    dot.className = "member-dot";
    dot.style.background = agentColor(member.name);
    badge.appendChild(dot);
    const name = document.createElement("span");
    name.textContent = member.name;
    badge.appendChild(name);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remover ${member.name} do grupo`);
    removeBtn.onclick = async () => {
      await api(`/api/groups/${groupId}/members/${member.id}`, { method: "DELETE" });
      await loadMembers(groupId);
    };
    badge.appendChild(removeBtn);
    list.appendChild(badge);
  }

  const categoryFilter = document.getElementById("member-category-filter");
  const previousFilterValue = categoryFilter.value;
  categoryFilter.innerHTML = "";
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "Todas as categorias";
  categoryFilter.appendChild(allOption);
  for (const category of state.agentCategories) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.name;
    categoryFilter.appendChild(option);
  }
  categoryFilter.value = previousFilterValue;
  categoryFilter.onchange = () => renderAddMemberOptions(groupId);

  renderAddMemberOptions(groupId);
}

function renderAddMemberOptions(groupId) {
  const select = document.getElementById("add-member-select");
  const categoryFilterValue = document.getElementById("member-category-filter").value;
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "+ adicionar agente";
  select.appendChild(placeholder);
  const memberIds = new Set(state.members.map((m) => m.id));
  for (const agent of state.agents) {
    if (memberIds.has(agent.id)) continue;
    if (categoryFilterValue && !agent.category_ids.includes(Number(categoryFilterValue))) continue;
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.name;
    select.appendChild(option);
  }
  select.value = "";
  select.onchange = async () => {
    if (!select.value) return;
    await api(`/api/groups/${groupId}/members`, {
      method: "POST",
      body: JSON.stringify({ agent_id: Number(select.value) }),
    });
    await loadMembers(groupId);
  };
}
```

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html app/static/app.js
git commit -m "feat: filtra o dropdown de adicionar membro por categoria de agente"
```

---

### Task 6: Verificação manual no navegador

**Files:** nenhum (apenas verificação, sem alterações de código)

- [ ] **Step 1: Subir o servidor**

Usar a ferramenta de preview do projeto (`preview_start`) para rodar o app FastAPI (uvicorn) e abrir `http://localhost:<porta configurada>`.

- [ ] **Step 2: Criar categorias e atribuir a um agente**

Na tela "Agentes": criar duas categorias (ex.: "Financeiro", "Jurídico"), criar/editar um agente marcando "Financeiro", salvar, reabrir o agente para edição e confirmar que o checkbox "Financeiro" continua marcado.

- [ ] **Step 3: Testar o filtro no grupo de conversa**

Abrir um grupo de conversa existente (ou criar um), selecionar "Financeiro" no novo select de categoria acima de "+ adicionar agente", e confirmar que só agentes daquela categoria aparecem na lista. Trocar para "Todas as categorias" e confirmar que a lista completa volta.

- [ ] **Step 4: Testar exclusão de categoria**

Apagar uma categoria sem agentes (sem confirmação) e uma categoria com agente vinculado (deve pedir confirmação com a contagem correta). Confirmar que apagar a categoria não apaga o agente.

- [ ] **Step 5: Checar o console do navegador**

Usar `read_console_messages` para garantir que não há erros JS durante o fluxo acima.

- [ ] **Step 6: Rodar a suíte de testes inteira uma última vez**

Run: `pytest -v`
Expected: todos os testes PASS
