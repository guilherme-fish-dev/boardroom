# Agent and Group CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the CRUD surface for agents (add delete) and groups (add rename and delete), both in the API and in the UI, relying entirely on the FK cascades already defined in the schema.

**Architecture:** Two small backend tasks add `DELETE /api/agents/{id}` and `PUT`/`DELETE /api/groups/{id}`, following the exact same connection/error-handling pattern already used by every other endpoint in these two routers. Two frontend tasks wire them up: the agent list becomes clickable to load an agent into the existing create form for editing (with a delete button that appears only in edit mode), and the channel header gains rename/delete buttons for the currently open group.

**Tech Stack:** FastAPI, sqlite3 (existing `ON DELETE CASCADE` FKs — no new migration needed), plain JS, pytest + FastAPI `TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-18-agent-group-crud-design.md`

---

## File Structure

```
boardroom/
  app/
    routers/
      agents.py    # add DELETE /{agent_id}
      groups.py     # add PUT /{group_id}, DELETE /{group_id}
    static/
      index.html     # agent form action buttons, channel header buttons
      app.js          # edit-mode state machine for agents, rename/delete handlers for groups
      style.css        # .agent-form-actions, #channel-header layout, #agent-list li hover/cursor
  tests/
    test_agents_api.py   # DELETE tests
    test_groups_api.py    # PUT/DELETE tests
```

---

### Task 1: `DELETE /api/agents/{agent_id}`

**Files:**
- Modify: `app/routers/agents.py`
- Test: `tests/test_agents_api.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_agents_api.py`, add this import at the top of the file (alongside the existing ones):

```python
from app.db import get_connection
```

Append these tests to the end of the file:

```python
def test_delete_agent_removes_it(db):
    client = make_client(db)
    agent = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.delete(f"/api/agents/{agent['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/agents")
    assert resp.json() == []


def test_delete_agent_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/agents/9999")
    assert resp.status_code == 404


def test_delete_agent_cascades_group_membership_and_jobs(db):
    client = make_client(db)
    agent = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (group["id"], agent["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.delete(f"/api/agents/{agent['id']}")
    assert resp.status_code == 204

    conn = get_connection()
    try:
        members = conn.execute(
            "SELECT * FROM group_members WHERE agent_id = ?", (agent["id"],)
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE agent_id = ?", (agent["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert members == []
    assert jobs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_api.py -k delete_agent -v`
Expected: FAIL with 405 Method Not Allowed (no `DELETE` route exists yet)

- [ ] **Step 3: Implement the endpoint**

In `app/routers/agents.py`, find the import line:

```python
from fastapi import APIRouter, HTTPException
```

Replace it with:

```python
from fastapi import APIRouter, HTTPException, Response
```

Append this function at the end of the file:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_api.py -v`
Expected: all tests in the file pass (7 previous + 3 new = 10 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 73 passed (70 previous + 3 new)

- [ ] **Step 6: Commit**

```bash
git add app/routers/agents.py tests/test_agents_api.py
git commit -m "feat: add DELETE /api/agents/{agent_id}"
```

---

### Task 2: `PUT`/`DELETE /api/groups/{group_id}`

**Files:**
- Modify: `app/routers/groups.py`
- Test: `tests/test_groups_api.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_groups_api.py`, add this import at the top of the file:

```python
from app.db import get_connection
```

Append these tests to the end of the file:

```python
def test_update_group_renames_it(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.put(f"/api/groups/{group['id']}", json={"name": "financas"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "financas"

    resp = client.get("/api/groups")
    assert [g["name"] for g in resp.json()] == ["financas"]


def test_update_group_duplicate_name_rejected(db):
    client = make_client(db)
    client.post("/api/groups", json={"name": "investidores"})
    produto = client.post("/api/groups", json={"name": "produto"}).json()

    resp = client.put(f"/api/groups/{produto['id']}", json={"name": "investidores"})
    assert resp.status_code == 409


def test_update_group_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.put("/api/groups/9999", json={"name": "novo-nome"})
    assert resp.status_code == 404


def test_delete_group_removes_it(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.delete(f"/api/groups/{group['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/groups")
    assert resp.json() == []


def test_delete_group_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/groups/9999")
    assert resp.status_code == 404


def test_delete_group_cascades_members_messages_and_jobs(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    client.post(f"/api/groups/{group['id']}/messages", json={"content": "oi"})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (group["id"], agent["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.delete(f"/api/groups/{group['id']}")
    assert resp.status_code == 204

    conn = get_connection()
    try:
        members = conn.execute(
            "SELECT * FROM group_members WHERE group_id = ?", (group["id"],)
        ).fetchall()
        messages = conn.execute(
            "SELECT * FROM messages WHERE group_id = ?", (group["id"],)
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE group_id = ?", (group["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert members == []
    assert messages == []
    assert jobs == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_groups_api.py -k "update_group or delete_group" -v`
Expected: FAIL — `update_group` tests fail with 405 (no `PUT` route), `delete_group` tests fail with 405 (no `DELETE /{group_id}` route; only `/{group_id}/members/{agent_id}` exists today)

- [ ] **Step 3: Implement the endpoints**

Append these two functions at the end of `app/routers/groups.py`:

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

(`Response`, `HTTPException`, and `sqlite3` are already imported in this file — no import changes needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_groups_api.py -v`
Expected: all tests in the file pass (4 previous + 6 new = 10 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 79 passed (73 from Task 1 + 6 new)

- [ ] **Step 6: Commit**

```bash
git add app/routers/groups.py tests/test_groups_api.py
git commit -m "feat: add PUT and DELETE /api/groups/{group_id}"
```

---

### Task 3: Frontend — edit and delete agents

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

- [ ] **Step 1: Add the edit/delete buttons to the agent form**

In `app/static/index.html`, find:

```html
          <label class="checkbox-label"><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
          <button type="submit" class="btn-primary">Salvar agente</button>
        </form>
```

Replace it with:

```html
          <label class="checkbox-label"><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
          <div class="agent-form-actions">
            <button type="submit" class="btn-primary" id="agent-submit-btn">Salvar agente</button>
            <button type="button" id="cancel-edit-agent-btn" class="btn-secondary hidden">Cancelar edição</button>
            <button type="button" id="delete-agent-btn" class="btn-secondary hidden">Apagar agente</button>
          </div>
        </form>
```

- [ ] **Step 2: Style the new elements**

In `app/static/style.css`, find:

```css
#agent-list li {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--ink-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}
```

Replace it with:

```css
#agent-list li {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--ink-muted);
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  transition: background-color 150ms ease, border-color 150ms ease;
}
#agent-list li:hover { background: var(--surface-hover); border-color: var(--border-strong); }
#agent-list li.empty-hint { cursor: default; }
#agent-list li.empty-hint:hover { background: var(--surface); border-color: var(--border); }
```

Append at the end of `app/static/style.css`:

```css
.agent-form-actions { display: flex; gap: 8px; align-items: center; }
```

- [ ] **Step 3: Add `editingAgentId` to state**

In `app/static/app.js`, find:

```javascript
const state = {
  groups: [],
  activeGroupId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
};
```

Replace it with:

```javascript
const state = {
  groups: [],
  activeGroupId: null,
  activeView: "channel",
  agents: [],
  members: [],
  lastMessageId: 0,
  pollTimer: null,
  editingAgentId: null,
};
```

- [ ] **Step 4: Make `loadModels()` accept a current value, and wire the agent list to start editing**

Find:

```javascript
async function loadModels() {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, "", {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => {
      submitButton.disabled = true;
    },
  });
  if (ok) submitButton.disabled = false;
}
```

Replace it with:

```javascript
async function loadModels(currentValue = "") {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  const ok = await populateModelSelect(modelSelect, currentValue, {
    allowEmpty: true,
    emptyLabel: "selecione um modelo",
    onError: () => {
      submitButton.disabled = true;
    },
  });
  if (ok) submitButton.disabled = false;
}


async function startEditingAgent(agent) {
  state.editingAgentId = agent.id;
  document.getElementById("agent-name").value = agent.name;
  document.getElementById("agent-persona").value = agent.persona_prompt;
  document.getElementById("agent-vision").checked = agent.vision_capable;
  await loadModels(agent.model_name);
  document.getElementById("agent-submit-btn").textContent = "Atualizar agente";
  document.getElementById("cancel-edit-agent-btn").classList.remove("hidden");
  document.getElementById("delete-agent-btn").classList.remove("hidden");
}

function stopEditingAgent() {
  state.editingAgentId = null;
  document.getElementById("agent-form").reset();
  document.getElementById("agent-submit-btn").textContent = "Salvar agente";
  document.getElementById("cancel-edit-agent-btn").classList.add("hidden");
  document.getElementById("delete-agent-btn").classList.add("hidden");
  loadModels();
}
```

- [ ] **Step 5: Make each agent list item clickable**

Find, inside `loadAgents()`:

```javascript
  for (const agent of state.agents) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "agent-avatar-dot";
    dot.style.background = agentColor(agent.name);
    li.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
    li.appendChild(label);
    list.appendChild(li);
  }
```

Replace it with:

```javascript
  for (const agent of state.agents) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "agent-avatar-dot";
    dot.style.background = agentColor(agent.name);
    li.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
    li.appendChild(label);
    li.onclick = () => startEditingAgent(agent);
    list.appendChild(li);
  }
```

- [ ] **Step 6: Make the form submit PUT or POST depending on edit mode, and wire the cancel/delete buttons**

Find:

```javascript
document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/agents", {
    method: "POST",
    body: JSON.stringify({
      name: document.getElementById("agent-name").value,
      persona_prompt: document.getElementById("agent-persona").value,
      model_name: document.getElementById("agent-model").value,
      vision_capable: document.getElementById("agent-vision").checked,
    }),
  });
  e.target.reset();
  await loadAgents();
};
```

Replace it with:

```javascript
document.getElementById("agent-form").onsubmit = async (e) => {
  e.preventDefault();
  const payload = {
    name: document.getElementById("agent-name").value,
    persona_prompt: document.getElementById("agent-persona").value,
    model_name: document.getElementById("agent-model").value,
    vision_capable: document.getElementById("agent-vision").checked,
  };
  if (state.editingAgentId) {
    await api(`/api/agents/${state.editingAgentId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  } else {
    await api("/api/agents", { method: "POST", body: JSON.stringify(payload) });
  }
  stopEditingAgent();
  await loadAgents();
};

document.getElementById("cancel-edit-agent-btn").onclick = () => stopEditingAgent();

document.getElementById("delete-agent-btn").onclick = async () => {
  if (!state.editingAgentId) return;
  const agent = state.agents.find((a) => a.id === state.editingAgentId);
  const name = agent ? agent.name : "este agente";
  if (!confirm(`Apagar o agente "${name}"? Essa ação não pode ser desfeita.`)) return;
  await api(`/api/agents/${state.editingAgentId}`, { method: "DELETE" });
  stopEditingAgent();
  await loadAgents();
};
```

- [ ] **Step 7: Run the full automated suite**

Run: `pytest -v`
Expected: 79 passed (this task adds no automated tests — frontend has none in this project — but confirms nothing broke)

- [ ] **Step 8: Manual verification — edit flow**

Run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000` (with llama-swap reachable so the model dropdown populates).

Open `http://localhost:8000`, go to **Agentes**, create an agent if none exist.

Click the agent in the list. Expected: the form fills with its data, the model select shows its current model selected, the submit button says "Atualizar agente", and "Cancelar edição"/"Apagar agente" appear.

Change the persona text and click "Atualizar agente". Expected: the form clears back to create-mode (button says "Salvar agente" again, extra buttons hidden), and the list shows the updated persona reflected the next time you click that agent again.

- [ ] **Step 9: Manual verification — cancel and delete**

Click an agent again, then click "Cancelar edição". Expected: form clears, back to create-mode, nothing was changed.

Click an agent, click "Apagar agente", confirm the browser dialog. Expected: the agent disappears from the list, the form resets to create-mode. If that agent was a member of any group, open that group and confirm it's no longer listed as a member (member removal happens automatically via the FK cascade — you don't need to remove it manually first).

Stop the server.

- [ ] **Step 10: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: add agent editing and deletion to the UI"
```

---

### Task 4: Frontend — rename and delete groups

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/style.css`

- [ ] **Step 1: Add rename/delete buttons to the channel header**

In `app/static/index.html`, find:

```html
          <div id="channel-header"></div>
```

Replace it with:

```html
          <div id="channel-header">
            <span id="channel-header-name"></span>
            <button type="button" id="rename-group-btn" class="btn-secondary">Renomear</button>
            <button type="button" id="delete-group-btn" class="btn-secondary">Apagar</button>
          </div>
```

- [ ] **Step 2: Restyle `#channel-header` as a flex row**

In `app/static/style.css`, find:

```css
#channel-header { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
```

Replace it with:

```css
#channel-header { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
#channel-header-name {
  font-size: 16px;
  font-weight: 700;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

- [ ] **Step 3: Write the group name into the new inner span**

In `app/static/app.js`, find, inside `selectGroup()`:

```javascript
  document.getElementById("channel-header").textContent = group ? `# ${group.name}` : "";
```

Replace it with:

```javascript
  document.getElementById("channel-header-name").textContent = group ? `# ${group.name}` : "";
```

- [ ] **Step 4: Wire the rename and delete buttons**

Append this to the end of `app/static/app.js`, right before the final `(async function init() { ... })();` block:

```javascript
document.getElementById("rename-group-btn").onclick = async () => {
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  if (!group) return;
  const newName = prompt("Novo nome do grupo:", group.name);
  if (!newName || newName.trim() === "" || newName.trim() === group.name) return;
  await api(`/api/groups/${state.activeGroupId}`, {
    method: "PUT",
    body: JSON.stringify({ name: newName.trim() }),
  });
  await loadGroups();
  const updated = state.groups.find((g) => g.id === state.activeGroupId);
  document.getElementById("channel-header-name").textContent = updated ? `# ${updated.name}` : "";
};

document.getElementById("delete-group-btn").onclick = async () => {
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  if (!group) return;
  if (
    !confirm(
      `Apagar o grupo "${group.name}"? Isso vai apagar todo o histórico de mensagens dele. Essa ação não pode ser desfeita.`
    )
  ) {
    return;
  }
  await api(`/api/groups/${state.activeGroupId}`, { method: "DELETE" });
  state.activeGroupId = null;
  document.getElementById("channel-content").classList.add("hidden");
  document.getElementById("channel-empty").classList.remove("hidden");
  await loadGroups();
};
```

- [ ] **Step 5: Run the full automated suite**

Run: `pytest -v`
Expected: 79 passed (no new automated tests in this task)

- [ ] **Step 6: Manual verification — rename**

Run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000`.

Open `http://localhost:8000`, select a group (create one if needed).

Click "Renomear", enter a new name in the prompt, confirm. Expected: the channel header updates immediately to the new name, and the sidebar group list also shows the new name.

Click "Renomear" again and cancel the prompt (or submit the exact same name). Expected: nothing changes, no request errors.

- [ ] **Step 7: Manual verification — delete**

With a group open, click "Apagar", confirm the browser dialog. Expected: the screen returns to the "select a group" empty state, and the group disappears from the sidebar list.

Reload the page to confirm the deletion persisted (the group should not reappear).

Stop the server.

- [ ] **Step 8: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: add group renaming and deletion to the UI"
```

---

## Self-Review Notes

- **Spec coverage:** `DELETE /api/agents/{id}` with cascade verification (Task 1), `PUT`/`DELETE /api/groups/{id}` with cascade verification (Task 2), click-to-edit agent flow with cancel/delete (Task 3), rename/delete buttons in the channel header (Task 4) — all requirements from `docs/superpowers/specs/2026-09-18-agent-group-crud-design.md` are covered.
- **No route collisions:** `PUT`/`DELETE /api/groups/{group_id}` (Task 2) coexist with the pre-existing `/{group_id}/members` and `/{group_id}/members/{agent_id}` routes because they're distinct path patterns (different number of segments) — verified by reading the existing router before adding the new routes, not assumed.
- **Reuses the existing `populateModelSelect`/`loadModels` machinery** (Task 3) instead of duplicating dropdown-population logic for the edit case — `loadModels(currentValue)` already knows how to preserve an orphaned value (a model no longer in llama-swap's list) via the helper built for the Settings screen, so editing an agent whose model was since renamed/removed in llama-swap doesn't silently lose that value.
- **Type/name consistency:** `state.editingAgentId` is introduced once (Task 3, Step 3) and consumed consistently by `startEditingAgent`/`stopEditingAgent`/the submit handler/the delete handler, all defined or modified within the same task.
