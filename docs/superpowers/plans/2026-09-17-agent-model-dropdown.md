# Agent Model Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-text "model name" field on the agent creation form with a dropdown populated from the llama-swap server's `/v1/models` endpoint, and fix the CSS bug that makes the "vision capable" checkbox visually detached from its label.

**Architecture:** A new `list_models()` function in `app/llm_client.py` calls llama-swap's OpenAI-compatible `/v1/models` endpoint (same pattern as the existing `chat_completion()`). A new `GET /api/models` router wraps it, reading the configured `llama_swap_base_url` from settings and translating any failure into a clean `502`. The frontend fetches this endpoint whenever the Agents view opens and either populates a `<select>` or shows a disabled error state. The checkbox fix is a standalone CSS change.

**Tech Stack:** Python (FastAPI, httpx), plain HTML/CSS/JS (no framework), pytest + `httpx.MockTransport` / FastAPI `TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-17-agent-model-dropdown-design.md`

---

## File Structure

```
boardroom/
  app/
    llm_client.py           # add list_models()
    routers/
      models.py             # new: GET /api/models
    main.py                 # register models router
    static/
      index.html            # agent-model becomes <select>, checkbox gets a class
      style.css              # checkbox fix + <select> styling
      app.js                  # loadModels(), wired into nav-agents click
  tests/
    test_llm_client.py       # add list_models tests
    test_models_api.py       # new: GET /api/models tests
```

---

### Task 1: Fix the vision-capable checkbox CSS

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/style.css`

- [ ] **Step 1: Add a class to the checkbox's label**

In `app/static/index.html`, find this line:

```html
          <label><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
```

Replace it with:

```html
          <label class="checkbox-label"><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
```

- [ ] **Step 2: Exclude checkboxes from the full-width input rule and style the label**

In `app/static/style.css`, find this line:

```css
form input, form textarea { padding: 6px; margin-bottom: 6px; width: 100%; }
```

Replace it with:

```css
form input:not([type="checkbox"]), form textarea { padding: 6px; margin-bottom: 6px; width: 100%; }
.checkbox-label { display: inline-flex; align-items: center; gap: 6px; margin-bottom: 6px; }
```

- [ ] **Step 3: Manual verification**

Run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000` (in one terminal)

Open `http://localhost:8000` in a browser, click "Agentes" in the sidebar.

Expected: the "Capaz de visão" checkbox sits inline right before its own text, both vertically centered together, no longer stretched across the row or visually separated from the label. Stop the server with Ctrl+C after checking.

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html app/static/style.css
git commit -m "fix: align vision-capable checkbox with its label"
```

---

### Task 2: `list_models()` in the LLM client

**Files:**
- Modify: `app/llm_client.py`
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_llm_client.py`:

```python
from app.llm_client import list_models


def test_list_models_returns_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen2.5-7b"}, {"id": "llava-7b"}]},
        )

    client = _client_with_transport(handler)
    result = list_models(base_url="http://localhost:8080", http_client=client)

    assert result == ["qwen2.5-7b", "llava-7b"]


def test_list_models_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        list_models(base_url="http://localhost:8080", http_client=client)


def test_list_models_raises_value_error_on_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not_data": []})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        list_models(base_url="http://localhost:8080", http_client=client)


def test_list_models_raises_value_error_when_item_has_no_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"not_id": "x"}]})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        list_models(base_url="http://localhost:8080", http_client=client)
```

(`httpx`, `pytest`, and `_client_with_transport` are already imported/defined at the top of this file from the existing `chat_completion` tests — no new imports needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_client.py -k list_models -v`
Expected: FAIL with `ImportError: cannot import name 'list_models'`

- [ ] **Step 3: Implement `list_models()` in `app/llm_client.py`**

Append this function at the end of `app/llm_client.py`:

```python
def list_models(
    *,
    base_url: str,
    http_client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """Call the llama-swap OpenAI-compatible /v1/models endpoint and return model ids.

    Raises:
        httpx.ConnectError: if the server is unreachable.
        httpx.TimeoutException: if the request times out.
        httpx.HTTPStatusError: if the server responds with an error status.
        ValueError: if the response body doesn't have the expected shape.
    """
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.get(f"{base_url}/v1/models", timeout=timeout)
        response.raise_for_status()
        data = response.json()
        try:
            return [item["id"] for item in data["data"]]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"resposta inesperada do llama-swap: {data}") from exc
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_client.py -v`
Expected: all tests in the file pass (7 previous + 4 new = 11 passed)

- [ ] **Step 5: Commit**

```bash
git add app/llm_client.py tests/test_llm_client.py
git commit -m "feat: add list_models() to fetch available models from llama-swap"
```

---

### Task 3: `GET /api/models` endpoint

**Files:**
- Create: `app/routers/models.py`
- Modify: `app/main.py`
- Test: `tests/test_models_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_models_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_get_models_returns_list(db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.models.list_models",
        lambda **kwargs: ["qwen2.5-7b", "llava-7b"],
    )
    client = make_client(db)

    resp = client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == {"models": ["qwen2.5-7b", "llava-7b"]}


def test_get_models_returns_502_on_failure(db, monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.models.list_models", _raise)
    client = make_client(db)

    resp = client.get("/api/models")

    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers.models'`

- [ ] **Step 3: Write `app/routers/models.py`**

```python
import sqlite3

from fastapi import APIRouter, HTTPException

from app.db import get_connection
from app.llm_client import list_models

router = APIRouter(prefix="/api/models", tags=["models"])


def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


@router.get("")
def get_models() -> dict:
    conn = get_connection()
    try:
        base_url = _get_setting(conn, "llama_swap_base_url")
    finally:
        conn.close()

    try:
        models = list_models(base_url=base_url)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"não foi possível buscar modelos do llama-swap: {exc}",
        )

    return {"models": models}
```

- [ ] **Step 4: Wire the router into `app/main.py`**

In `app/main.py`, find the import line:

```python
from app.routers import agents, groups, messages, settings
```

Replace it with:

```python
from app.routers import agents, groups, messages, models, settings
```

Then find:

```python
    app.include_router(messages.router)
```

Add right after it:

```python
    app.include_router(models.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models_api.py -v`
Expected: 2 passed

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: all tests pass (42 previous + 4 from Task 2 + 2 from this task = 48 passed)

- [ ] **Step 7: Commit**

```bash
git add app/routers/models.py app/main.py tests/test_models_api.py
git commit -m "feat: add GET /api/models endpoint"
```

---

### Task 4: Frontend — dropdown with error state

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/style.css`
- Modify: `app/static/app.js`

- [ ] **Step 1: Replace the free-text model field with a `<select>`**

In `app/static/index.html`, find:

```html
          <input id="agent-model" placeholder="Nome do modelo no llama-swap" required />
```

Replace it with:

```html
          <select id="agent-model" required></select>
```

- [ ] **Step 2: Style the new `<select>` like the other form fields**

In `app/static/style.css`, find the line you edited in Task 1:

```css
form input:not([type="checkbox"]), form textarea { padding: 6px; margin-bottom: 6px; width: 100%; }
```

Replace it with:

```css
form input:not([type="checkbox"]), form textarea, form select { padding: 6px; margin-bottom: 6px; width: 100%; }
```

- [ ] **Step 3: Add `loadModels()` to `app/static/app.js`**

Add this function to `app/static/app.js`, right after the existing `loadAgents()` function:

```javascript
async function loadModels() {
  const modelSelect = document.getElementById("agent-model");
  const submitButton = document.querySelector("#agent-form button[type=submit]");
  try {
    const data = await api("/api/models");
    modelSelect.innerHTML = "";
    modelSelect.disabled = false;
    submitButton.disabled = false;

    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "selecione um modelo";
    modelSelect.appendChild(emptyOption);

    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.appendChild(option);
    }
  } catch (err) {
    modelSelect.innerHTML = "";
    const errorOption = document.createElement("option");
    errorOption.value = "";
    errorOption.textContent = "Erro ao carregar modelos (verifique o llama-swap)";
    errorOption.disabled = true;
    errorOption.selected = true;
    modelSelect.appendChild(errorOption);
    modelSelect.disabled = true;
    submitButton.disabled = true;
  }
}
```

- [ ] **Step 4: Call `loadModels()` when the Agents view opens**

In `app/static/app.js`, find:

```javascript
document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
};
```

Replace it with:

```javascript
document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
  await loadModels();
};
```

- [ ] **Step 5: Manual verification — success path**

Create a throwaway mock llama-swap server for this test only (do not commit this file). Save it as `scratch_mock_llama_swap.py` in the project root:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps(
                {"data": [{"id": "qwen2.5-7b"}, {"id": "llava-7b"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


HTTPServer(("localhost", 8080), Handler).serve_forever()
```

Run: `python scratch_mock_llama_swap.py` (in one terminal — this fakes llama-swap on `http://localhost:8080`, which matches the default `llama_swap_base_url` setting).

In another terminal: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000`.

Open `http://localhost:8000`, click "Agentes".

Expected: the model `<select>` shows "selecione um modelo" plus two options, `qwen2.5-7b` and `llava-7b`. The "Salvar agente" button is enabled.

Stop both processes (Ctrl+C in each terminal) and delete `scratch_mock_llama_swap.py` when done — it's a throwaway verification tool, not part of the app.

- [ ] **Step 6: Manual verification — failure path**

With the mock server from Step 5 stopped (or never started), run: `source .venv/Scripts/activate && uvicorn app.main:app --port 8000`.

Open `http://localhost:8000`, click "Agentes".

Expected: the model `<select>` shows a single disabled option, "Erro ao carregar modelos (verifique o llama-swap)", the `<select>` itself is disabled, and the "Salvar agente" button is disabled. Other parts of the page (sidebar, other views) still work normally. Stop the server with Ctrl+C after checking.

- [ ] **Step 7: Run the full automated suite one more time**

Run: `pytest -v`
Expected: 48 passed (this task doesn't add automated tests — frontend has none in this project — but confirms nothing broke)

- [ ] **Step 8: Commit**

```bash
git add app/static/index.html app/static/style.css app/static/app.js
git commit -m "feat: replace agent model text field with a dropdown from llama-swap"
```

---

## Self-Review Notes

- **Spec coverage:** checkbox CSS fix (Task 1), `list_models()` with the same error contract as `chat_completion()` (Task 2), `GET /api/models` translating any failure into 502 (Task 3), frontend dropdown with populated/error states and no caching between visits (Task 4) — all requirements from `docs/superpowers/specs/2026-09-17-agent-model-dropdown-design.md` are covered.
- **Out of scope confirmed:** no agent-edit UI exists yet (unaffected by this change — `agent-model` is only used on the create form), no caching, no multi-endpoint support — matches the spec's explicit exclusions.
- **Type/name consistency:** `list_models()` signature (`base_url`, `http_client`, `timeout`) mirrors the existing `chat_completion()` convention; `app/routers/models.py` monkeypatch target in tests (`app.routers.models.list_models`) matches the actual import in that file; `GET /api/models` response shape (`{"models": [...]}`) is used identically in the Task 3 tests and the Task 4 frontend code (`data.models`).
