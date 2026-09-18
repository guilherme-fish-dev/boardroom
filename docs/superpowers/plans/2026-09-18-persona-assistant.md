# Persona Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Gerar com IA" button to the agent creation form that expands a short persona draft into a full system prompt, using a model chosen independently in Settings.

**Architecture:** A new `assistant_model` setting (same pattern as `default_vision_model`). A new `POST /api/agents/generate-persona` endpoint in the existing agents router calls the existing `chat_completion()` with a fixed system prompt and the user's draft, returning the expanded text. The frontend wires a button next to the persona textarea to call it and replace the field's content. A small shared `get_setting()` helper is extracted to `app/db.py` since this makes the third call site for the same one-line query.

**Tech Stack:** Python (FastAPI, pydantic), plain JS (no framework), pytest + monkeypatch (same mocking pattern already used for `chat_completion` and `list_models` throughout the test suite).

**Spec:** `docs/superpowers/specs/2026-09-18-persona-assistant-design.md`

---

## File Structure

```
boardroom/
  app/
    db.py                    # add shared get_setting() helper
    llm_client.py             # unchanged, reused as-is
    routers/
      agents.py               # add POST /generate-persona
      models.py                # switch to the shared get_setting()
      settings.py              # add assistant_model field
    static/
      index.html               # add button + settings field
      app.js                    # wire button + settings load/save
  tests/
    test_db.py                 # add get_setting() tests
    test_agents_api.py         # add generate-persona tests
    test_settings_api.py       # add assistant_model coverage
```

---

### Task 1: Shared `get_setting()` helper

**Files:**
- Modify: `app/db.py`
- Modify: `app/routers/models.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
from app.db import get_connection, get_setting


def test_get_setting_returns_seeded_default(db):
    conn = get_connection()
    try:
        value = get_setting(conn, "llama_swap_base_url")
    finally:
        conn.close()
    assert value == "http://localhost:8080"


def test_get_setting_returns_empty_string_for_unknown_key(db):
    conn = get_connection()
    try:
        value = get_setting(conn, "not_a_real_setting")
    finally:
        conn.close()
    assert value == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -k get_setting -v`
Expected: FAIL with `ImportError: cannot import name 'get_setting'`

- [ ] **Step 3: Add `get_setting()` to `app/db.py`**

Append this function at the end of `app/db.py`:

```python
def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: all tests in the file pass (2 previous + 2 new)

- [ ] **Step 5: Switch `app/routers/models.py` to the shared helper**

In `app/routers/models.py`, find:

```python
def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""
```

Delete that function entirely. Find the import line:

```python
from app.db import get_connection
```

Replace it with:

```python
from app.db import get_connection, get_setting
```

Find every call to `_get_setting(` in the same file and replace it with `get_setting(` (there is exactly one call site, inside `get_models()`).

- [ ] **Step 6: Run the full suite to confirm nothing broke**

Run: `pytest -v`
Expected: all tests pass (48 previous + 2 new = 50 passed)

- [ ] **Step 7: Commit**

```bash
git add app/db.py app/routers/models.py tests/test_db.py
git commit -m "refactor: extract shared get_setting() helper"
```

---

### Task 2: `assistant_model` setting

**Files:**
- Modify: `app/db.py`
- Modify: `app/routers/settings.py`
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_settings_api.py`:

```python
def test_get_settings_includes_assistant_model_default(db):
    client = make_client(db)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["assistant_model"] == ""


def test_update_settings_sets_assistant_model(db):
    client = make_client(db)
    resp = client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:8080",
            "default_vision_model": "",
            "max_pending_per_group": "20",
            "assistant_model": "qwen2.5-7b",
        },
    )
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["assistant_model"] == "qwen2.5-7b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_settings_api.py -k assistant_model -v`
Expected: FAIL — `test_get_settings_includes_assistant_model_default` fails with a `KeyError` (no `assistant_model` key in the response), since the field doesn't exist yet.

- [ ] **Step 3: Add `assistant_model` to `DEFAULT_SETTINGS` in `app/db.py`**

Find:

```python
DEFAULT_SETTINGS = {
    "llama_swap_base_url": "http://localhost:8080",
    "default_vision_model": "",
    "max_pending_per_group": "20",
}
```

Replace it with:

```python
DEFAULT_SETTINGS = {
    "llama_swap_base_url": "http://localhost:8080",
    "default_vision_model": "",
    "max_pending_per_group": "20",
    "assistant_model": "",
}
```

- [ ] **Step 4: Add the field to the `Settings` model in `app/routers/settings.py`**

Find:

```python
class Settings(BaseModel):
    llama_swap_base_url: str
    default_vision_model: str
    max_pending_per_group: str
```

Replace it with:

```python
class Settings(BaseModel):
    llama_swap_base_url: str
    default_vision_model: str
    max_pending_per_group: str
    assistant_model: str = ""
```

(The `= ""` default keeps existing `PUT` calls that don't mention `assistant_model` — like `test_update_settings` and `test_update_settings_rejects_invalid_max_pending`, already in this file — working exactly as before, instead of failing validation.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_settings_api.py -v`
Expected: all tests in the file pass (5 previous + 2 new = 7 passed)

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: 52 passed (50 from Task 1 + 2 new)

- [ ] **Step 7: Commit**

```bash
git add app/db.py app/routers/settings.py tests/test_settings_api.py
git commit -m "feat: add assistant_model setting"
```

---

### Task 3: `POST /api/agents/generate-persona`

**Files:**
- Modify: `app/routers/agents.py`
- Test: `tests/test_agents_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_agents_api.py`:

```python
def test_generate_persona_requires_assistant_model_configured(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": ""},
    )
    assert resp.status_code == 400


def test_generate_persona_returns_generated_text(db, monkeypatch):
    client = make_client(db)
    client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:8080",
            "default_vision_model": "",
            "max_pending_per_group": "20",
            "assistant_model": "qwen2.5-7b",
        },
    )

    captured = {}

    def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return "Você é um investidor cauteloso, avesso a risco, que pondera cada decisão."

    monkeypatch.setattr("app.routers.agents.chat_completion", fake_chat_completion)

    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": "bob"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "persona_prompt": "Você é um investidor cauteloso, avesso a risco, que pondera cada decisão."
    }
    assert captured["model"] == "qwen2.5-7b"
    assert captured["base_url"] == "http://localhost:8080"
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert "bob" in captured["messages"][1]["content"]
    assert "investidor cauteloso" in captured["messages"][1]["content"]


def test_generate_persona_returns_502_on_failure(db, monkeypatch):
    client = make_client(db)
    client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:8080",
            "default_vision_model": "",
            "max_pending_per_group": "20",
            "assistant_model": "qwen2.5-7b",
        },
    )

    def _raise(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.agents.chat_completion", _raise)

    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": ""},
    )

    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_api.py -k generate_persona -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement the endpoint in `app/routers/agents.py`**

Add these imports at the top of the file:

```python
from app.db import get_connection, get_setting
from app.llm_client import chat_completion
```

(Note: `app/routers/agents.py` currently imports `get_connection` from `app.db` already — merge into a single import line with `get_setting` added, don't duplicate the import.)

Add this constant and function near the end of `app/routers/agents.py`, after `update_agent`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_api.py -v`
Expected: all tests in the file pass (4 previous + 3 new = 7 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 55 passed (52 from Task 2 + 3 new)

- [ ] **Step 6: Commit**

```bash
git add app/routers/agents.py tests/test_agents_api.py
git commit -m "feat: add POST /api/agents/generate-persona endpoint"
```

---

### Task 4: Frontend — generate button and settings field

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/style.css`
- Modify: `app/static/app.js`

- [ ] **Step 1: Add the settings field**

In `app/static/index.html`, find:

```html
          <label>Limite de jobs por grupo
            <input id="setting-max-pending" type="number" min="1" required />
          </label>
          <button type="submit" class="btn-primary">Salvar</button>
```

Replace it with:

```html
          <label>Limite de jobs por grupo
            <input id="setting-max-pending" type="number" min="1" required />
          </label>
          <label>Modelo assistente (gerar personas)
            <input id="setting-assistant-model" placeholder="ex.: qwen2.5-7b" />
          </label>
          <button type="submit" class="btn-primary">Salvar</button>
```

- [ ] **Step 2: Add the generate button and error slot next to the persona field**

In `app/static/index.html`, find:

```html
          <label>Persona / system prompt
            <textarea id="agent-persona" placeholder="Descreva como esse agente deve pensar e responder" required></textarea>
          </label>
```

Replace it with:

```html
          <label>Persona / system prompt
            <textarea id="agent-persona" placeholder="Descreva como esse agente deve pensar e responder" required></textarea>
          </label>
          <div class="persona-assist">
            <button type="button" id="generate-persona-btn" class="btn-secondary" disabled>Gerar com IA</button>
            <span id="generate-persona-error" class="field-error"></span>
          </div>
```

- [ ] **Step 3: Style the new elements**

In `app/static/style.css`, append at the end of the file:

```css
.persona-assist { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }

.btn-secondary {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--ink);
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 600;
}
.btn-secondary:hover:not(:disabled) { background: var(--surface-hover); }

.field-error { font-size: 12px; color: var(--danger); }
```

- [ ] **Step 4: Wire the settings field in `app/static/app.js`**

Find `loadSettings()`:

```javascript
async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-vision-model").value = settings.default_vision_model;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;
}
```

Replace it with:

```javascript
async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-vision-model").value = settings.default_vision_model;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;
  document.getElementById("setting-assistant-model").value = settings.assistant_model;
}
```

Find the `settings-form` submit handler:

```javascript
document.getElementById("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      llama_swap_base_url: document.getElementById("setting-base-url").value,
      default_vision_model: document.getElementById("setting-vision-model").value,
      max_pending_per_group: document.getElementById("setting-max-pending").value,
    }),
  });
};
```

Replace it with:

```javascript
document.getElementById("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      llama_swap_base_url: document.getElementById("setting-base-url").value,
      default_vision_model: document.getElementById("setting-vision-model").value,
      max_pending_per_group: document.getElementById("setting-max-pending").value,
      assistant_model: document.getElementById("setting-assistant-model").value,
    }),
  });
};
```

- [ ] **Step 5: Wire the generate button in `app/static/app.js`**

Add this code right after the `document.getElementById("agent-form").onsubmit = ...` block (after its closing `};`):

```javascript
const personaTextarea = document.getElementById("agent-persona");
const generateBtn = document.getElementById("generate-persona-btn");
const generateError = document.getElementById("generate-persona-error");

personaTextarea.addEventListener("input", () => {
  generateBtn.disabled = personaTextarea.value.trim().length === 0;
});

generateBtn.onclick = async () => {
  generateError.textContent = "";
  generateBtn.disabled = true;
  const originalLabel = generateBtn.textContent;
  generateBtn.textContent = "Gerando...";
  try {
    const data = await api("/api/agents/generate-persona", {
      method: "POST",
      body: JSON.stringify({
        draft: personaTextarea.value,
        agent_name: document.getElementById("agent-name").value,
      }),
    });
    personaTextarea.value = data.persona_prompt;
  } catch (err) {
    generateError.textContent = err.message;
  } finally {
    generateBtn.textContent = originalLabel;
    generateBtn.disabled = personaTextarea.value.trim().length === 0;
  }
};
```

- [ ] **Step 6: Run the full automated suite**

Run: `pytest -v`
Expected: 55 passed (this task adds no automated tests — frontend has none in this project — but confirms nothing broke)

- [ ] **Step 7: Manual verification — button enable/disable and settings round-trip**

Run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000` (in one terminal).

Open `http://localhost:8000`, go to **Configurações**, set "Modelo assistente (gerar personas)" to any text (e.g. `qwen2.5-7b`) and click Salvar. Reload the page, go back to Configurações, confirm the value persisted.

Go to **Agentes**. Confirm "Gerar com IA" starts disabled. Type something in "Persona / system prompt" — confirm the button becomes enabled. Clear the field — confirm it becomes disabled again.

- [ ] **Step 8: Manual verification — generate flow with a mock llama-swap**

Create a throwaway mock server (do not commit this file) at the project root as `scratch_mock_llama_swap.py`:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(
            {"choices": [{"message": {"content": "Você é um investidor cauteloso e detalhista."}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


HTTPServer(("localhost", 8080), Handler).serve_forever()
```

Run: `python scratch_mock_llama_swap.py` (in a separate terminal — this fakes llama-swap on `http://localhost:8080`, matching the default `llama_swap_base_url`).

In the app: make sure Configurações has "Modelo assistente" set to any non-empty value (from Step 7). Go to Agentes, type `investidor cauteloso` in the persona field, click "Gerar com IA".

Expected: button shows "Gerando..." briefly, then the persona field is replaced with "Você é um investidor cauteloso e detalhista." and the button returns to "Gerar com IA", enabled.

Stop the mock server and delete `scratch_mock_llama_swap.py` when done.

- [ ] **Step 9: Manual verification — error path**

With the mock server from Step 8 stopped, and "Modelo assistente" still set in Configurações, click "Gerar com IA" again on a non-empty persona field.

Expected: an error message appears next to the button (something like `502 ...`), the button text returns to "Gerar com IA" and stays enabled (field still has text).

Now clear "Modelo assistente" in Configurações and save. Try generating again.

Expected: the error message shows the 400 "configure um modelo assistente..." message.

- [ ] **Step 10: Commit**

```bash
git add app/static/index.html app/static/style.css app/static/app.js
git commit -m "feat: add persona-generation button and assistant model setting to the UI"
```

---

## Self-Review Notes

- **Spec coverage:** `assistant_model` setting (Task 2), `POST /api/agents/generate-persona` with the fixed system prompt, 400 for unconfigured model, 502 for chat_completion failure (Task 3), button disabled on empty draft, replaces textarea content on success, inline error on failure (Task 4) — all covered.
- **Backward compatibility:** giving `assistant_model` a pydantic default of `""` (Task 2) means the two pre-existing `PUT /api/settings` tests that don't mention it keep passing unchanged, instead of needing to be rewritten.
- **DRY:** the `get_setting()` extraction (Task 1) is done first specifically so Task 3's endpoint doesn't introduce a third copy-pasted inline query function, consistent with the plan's YAGNI/DRY goals.
- **Error surface consistency:** the 502 error message format (`detail` string containing the underlying exception) matches the existing pattern from `GET /api/models`, so the frontend's generic `api()` error handling (`throw new Error(...)`) already surfaces it via `err.message` without any new client-side error-parsing logic.
