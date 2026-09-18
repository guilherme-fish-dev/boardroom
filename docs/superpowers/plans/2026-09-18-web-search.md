# Agent Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every agent the ability to search the web during its turn, using a `BUSCAR: <query>` text pattern (not native tool-calling) backed by Exa's free public MCP endpoint, capped at 3 searches per turn.

**Architecture:** A new `app/web_search.py` module calls Exa's public MCP endpoint via JSON-RPC, following the same client-lifecycle/error conventions as `chat_completion`/`list_models`. `app/queue_worker.py` gains a `hidden_kind` column-aware history builder (so search results and image descriptions can be filtered independently) and a search loop inside `_process_agent_turn` that intercepts `BUSCAR:`-only replies, runs the search, feeds the result back, and repeats up to 3 times before forcing a final answer.

**Tech Stack:** Python (httpx, sqlite3), pytest + `httpx.MockTransport` (same mocking pattern as `tests/test_llm_client.py`).

**Spec:** `docs/superpowers/specs/2026-09-18-web-search-design.md`

---

## File Structure

```
boardroom/
  app/
    db.py                    # add hidden_kind column (schema + migration)
    web_search.py             # new: web_search() via Exa MCP
    queue_worker.py            # _build_history renamed param, describe_image tags hidden_kind, search loop
  tests/
    test_db.py                 # hidden_kind migration tests
    test_web_search.py          # new: web_search() tests
    test_queue_worker.py         # update 2 existing tests (hidden_kind), add search-loop tests
```

---

### Task 1: `hidden_kind` column and migration

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
def test_init_db_adds_hidden_kind_column_to_messages(db):
    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()
    assert "hidden_kind" in columns


def test_init_db_migration_is_idempotent(db):
    from app.db import init_db

    init_db()
    init_db()

    conn = get_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()
    assert "hidden_kind" in columns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -k hidden_kind -v`
Expected: FAIL — `test_init_db_adds_hidden_kind_column_to_messages` fails because `hidden_kind` isn't a column yet.

- [ ] **Step 3: Add the column to the schema and a migration helper in `app/db.py`**

Find the `messages` table in `SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK (sender_type IN ('user','agent','system')),
    sender_id INTEGER,
    content TEXT NOT NULL,
    image_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Replace it with:

```python
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK (sender_type IN ('user','agent','system')),
    sender_id INTEGER,
    content TEXT NOT NULL,
    image_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    hidden_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Then find `init_db()`:

```python
def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
```

Replace it with:

```python
def _ensure_hidden_kind_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "hidden_kind" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN hidden_kind TEXT")


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _ensure_hidden_kind_column(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: all tests in the file pass (6 previous + 2 new = 8 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 57 passed (55 previous + 2 new)

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add hidden_kind column to messages with idempotent migration"
```

---

### Task 2: `web_search()` via Exa's public MCP endpoint

**Files:**
- Create: `app/web_search.py`
- Test: `tests/test_web_search.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_web_search.py`:

```python
import json

import httpx
import pytest

from app.web_search import web_search


def _client_with_transport(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_web_search_returns_text_from_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "resultado da busca em JSON"}]},
            },
        )

    client = _client_with_transport(handler)
    result = web_search("clima em São Paulo", http_client=client)

    assert result == "resultado da busca em JSON"


def test_web_search_returns_text_from_sse_response():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "resultado da busca em SSE"}]},
            }
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {body}\n\n",
        )

    client = _client_with_transport(handler)
    result = web_search("clima em São Paulo", http_client=client)

    assert result == "resultado da busca em SSE"


def test_web_search_sends_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"result": {"content": [{"text": "ok"}]}},
        )

    client = _client_with_transport(handler)
    web_search("consulta de teste", num_results=3, http_client=client)

    assert captured["json"]["method"] == "tools/call"
    assert captured["json"]["params"]["name"] == "web_search_exa"
    assert captured["json"]["params"]["arguments"]["query"] == "consulta de teste"
    assert captured["json"]["params"]["arguments"]["numResults"] == 3


def test_web_search_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        web_search("consulta", http_client=client)


def test_web_search_raises_value_error_on_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"content": []}})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        web_search("consulta", http_client=client)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web_search'`

- [ ] **Step 3: Write `app/web_search.py`**

```python
from __future__ import annotations

import json

import httpx

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def _parse_mcp_response(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError(f"resposta SSE sem linha 'data:': {response.text}")
    return response.json()


def web_search(
    query: str,
    *,
    num_results: int = 5,
    http_client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> str:
    """Search the web via Exa's public MCP endpoint (no API key required).

    Raises:
        httpx.ConnectError: if the endpoint is unreachable.
        httpx.TimeoutException: if the request times out.
        httpx.HTTPStatusError: if the endpoint responds with an error status.
        ValueError: if the response body doesn't have the expected shape.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {"query": query, "numResults": num_results},
        },
    }

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.post(
            EXA_MCP_URL,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = _parse_mcp_response(response)
        try:
            return data["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"resposta inesperada da busca: {data}") from exc
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web_search.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 63 passed (57 from Task 1 + 6 new)

- [ ] **Step 6: Commit**

```bash
git add app/web_search.py tests/test_web_search.py
git commit -m "feat: add web_search() via Exa's public MCP endpoint"
```

---

### Task 3: Rename `exclude_hidden` to `exclude_image_descriptions`, tag image descriptions

**Files:**
- Modify: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Update the two existing tests that simulate a hidden image description**

In `tests/test_queue_worker.py`, find `test_process_next_job_vision_agent_history_excludes_hidden_description` and locate this line inside it:

```python
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (group_id, "descrição oculta gerada pelo describe_image"),
    )
```

Replace it with:

```python
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (group_id, "descrição oculta gerada pelo describe_image"),
    )
```

Find the identical block inside `test_process_next_job_non_vision_agent_history_includes_hidden_description` (same INSERT statement, different test function) and apply the same replacement there too.

(This matters because after this task, `_build_history`'s exclusion filter only excludes hidden rows tagged `hidden_kind = 'image_description'` — without this update, these two pre-existing tests would silently start testing the wrong thing, since a hidden row with `hidden_kind = NULL` would no longer be excluded.)

- [ ] **Step 2: Run these two tests to verify they still pass with the old code (sanity check before refactoring)**

Run: `pytest tests/test_queue_worker.py -k history_excludes_hidden_description -v`
Expected: still passes at this point (the production code hasn't changed yet, and the SQL `hidden = 0` filter doesn't care about `hidden_kind`, so tagging the row doesn't affect the old behavior)

- [ ] **Step 3: Rename the parameter and update the SQL filter in `_build_history`**

Find:

```python
def _build_history(
    conn: sqlite3.Connection, group_id: int, agent_persona: str, *, exclude_hidden: bool = False
) -> list[dict]:
    query = "SELECT sender_type, sender_id, content FROM messages WHERE group_id = ?"
    if exclude_hidden:
        query += " AND hidden = 0"
    query += " ORDER BY id"
    rows = conn.execute(query, (group_id,)).fetchall()
    messages = [{"role": "system", "content": agent_persona}]
    for row in rows:
        role = "assistant" if row["sender_type"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})
    return messages
```

Replace it with:

```python
WEB_SEARCH_INSTRUCTIONS = (
    "\n\nVocê pode pesquisar na internet quando precisar de informação atual ou que não sabe. "
    "Para isso, responda usando SOMENTE esta linha, nada mais: BUSCAR: sua consulta aqui. "
    "Você vai receber os resultados da busca e poderá responder normalmente em seguida, "
    "ou buscar de novo (no máximo 3 vezes) se ainda precisar de mais informação."
)


def _build_history(
    conn: sqlite3.Connection,
    group_id: int,
    agent_persona: str,
    *,
    exclude_image_descriptions: bool = False,
) -> list[dict]:
    query = "SELECT sender_type, sender_id, content FROM messages WHERE group_id = ?"
    if exclude_image_descriptions:
        query += " AND NOT (hidden = 1 AND hidden_kind = 'image_description')"
    query += " ORDER BY id"
    rows = conn.execute(query, (group_id,)).fetchall()
    messages = [{"role": "system", "content": agent_persona + WEB_SEARCH_INSTRUCTIONS}]
    for row in rows:
        role = "assistant" if row["sender_type"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})
    return messages
```

- [ ] **Step 4: Update the call site in `_process_agent_turn`**

Find:

```python
    history = _build_history(
        conn, job["group_id"], agent["persona_prompt"], exclude_hidden=image_base64 is not None
    )
```

Replace it with:

```python
    history = _build_history(
        conn,
        job["group_id"],
        agent["persona_prompt"],
        exclude_image_descriptions=image_base64 is not None,
    )
```

- [ ] **Step 5: Tag image-description messages with `hidden_kind` in `_process_describe_image`**

Find:

```python
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (job["group_id"], description),
    )
```

Replace it with:

```python
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'image_description')",
        (job["group_id"], description),
    )
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: 63 passed (no new tests in this task — it's a rename + a real-world describe_image behavior change verified by the two tests updated in Step 1)

- [ ] **Step 7: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "refactor: rename exclude_hidden to exclude_image_descriptions, tag image descriptions with hidden_kind"
```

---

### Task 4: Search loop in `_process_agent_turn`

**Files:**
- Modify: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queue_worker.py`:

```python
def test_process_next_job_agent_searches_once_then_answers(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', 'que dia é hoje?')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["BUSCAR: data de hoje", "Hoje é 18 de setembro de 2026."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))
    monkeypatch.setattr("app.queue_worker.web_search", lambda query, **kwargs: "Hoje é 18/09/2026.")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'search_result'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Hoje é 18 de setembro de 2026."
    assert len(hidden_messages) == 1
    assert "data de hoje" in hidden_messages[0]["content"]
    assert "18/09/2026" in hidden_messages[0]["content"]


def test_process_next_job_agent_hits_search_limit_and_is_forced_to_answer(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', 'pesquise sem parar')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(
        [
            "BUSCAR: um",
            "BUSCAR: dois",
            "BUSCAR: tres",
            "BUSCAR: quatro",
            "Não encontrei nada definitivo, mas aqui está o que sei.",
        ]
    )
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))
    monkeypatch.setattr("app.queue_worker.web_search", lambda query, **kwargs: f"resultado de {query}")

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        hidden_messages = conn.execute(
            "SELECT * FROM messages WHERE hidden = 1 AND hidden_kind = 'search_result'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Não encontrei nada definitivo, mas aqui está o que sei."
    assert len(hidden_messages) == 3


def test_process_next_job_search_failure_does_not_crash_the_job(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', 'oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["BUSCAR: algo", "Sem internet, mas posso ajudar de outra forma."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))

    def _raise(query, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.queue_worker.web_search", _raise)

    process_next_job()

    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert job["status"] == "done"
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Sem internet, mas posso ajudar de outra forma."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queue_worker.py -k "searches_once or search_limit or search_failure" -v`
Expected: FAIL — `AttributeError` or `StopIteration`-shaped failures, since `_process_agent_turn` doesn't call `web_search` or loop on `BUSCAR:` yet, so the mocked `chat_completion` iterator never advances past the first reply and the first (only) reply — `"BUSCAR: ..."` — gets saved as the literal agent message instead of being intercepted.

- [ ] **Step 3: Add the search loop to `_process_agent_turn`**

Add this import near the top of `app/queue_worker.py`, alongside the existing imports:

```python
import re
```

Add this import alongside `from app.mentions import extract_mentions`:

```python
from app.web_search import web_search
```

Add this constant near `WEB_SEARCH_INSTRUCTIONS` (defined in Task 3):

```python
SEARCH_PATTERN = re.compile(r"^\s*BUSCAR:\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
MAX_SEARCHES_PER_TURN = 3
```

Find this part of `_process_agent_turn`:

```python
    history = _build_history(
        conn,
        job["group_id"],
        agent["persona_prompt"],
        exclude_image_descriptions=image_base64 is not None,
    )

    reply = chat_completion(
        base_url=base_url,
        model=agent["model_name"],
        messages=history,
        image_base64=image_base64,
    )

    cur = conn.execute(
```

Replace it with:

```python
    history = _build_history(
        conn,
        job["group_id"],
        agent["persona_prompt"],
        exclude_image_descriptions=image_base64 is not None,
    )

    reply = chat_completion(
        base_url=base_url,
        model=agent["model_name"],
        messages=history,
        image_base64=image_base64,
    )

    searches_done = 0
    match = SEARCH_PATTERN.match(reply)
    while match and searches_done < MAX_SEARCHES_PER_TURN:
        query = match.group(1).strip()
        try:
            results = web_search(query)
        except Exception as exc:
            results = f"Erro ao buscar: {exc}"

        conn.execute(
            "INSERT INTO messages (group_id, sender_type, content, hidden, hidden_kind) "
            "VALUES (?, 'system', ?, 1, 'search_result')",
            (job["group_id"], f'Busca por "{query}":\n{results}'),
        )

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": f'Resultados da busca por "{query}":\n{results}'})
        reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)
        searches_done += 1
        match = SEARCH_PATTERN.match(reply)

    if match:
        history.append({"role": "assistant", "content": reply})
        history.append(
            {
                "role": "user",
                "content": "Você atingiu o limite de buscas para esta resposta. Responda com base "
                "no que você já sabe, sem buscar de novo.",
            }
        )
        reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)

    cur = conn.execute(
```

(The rest of `_process_agent_turn` — the `INSERT INTO messages ... sender_type = 'agent'`, the loop-limit check, `enqueue_mentions` — stays exactly as it is; `reply` at that point now always holds the final, non-`BUSCAR:` answer.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queue_worker.py -v`
Expected: all tests in the file pass

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: 66 passed (63 from Task 2 + 3 new from this task; Task 3 added no new tests)

- [ ] **Step 6: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: add BUSCAR: search loop to agent turns, capped at 3 per turn"
```

---

## Self-Review Notes

- **Spec coverage:** `hidden_kind` migration (Task 1), `web_search()` matching the Exa MCP call shape from the spec including SSE parsing (Task 2), `_build_history` filtering only image descriptions (Task 3), the full search loop with the 3-search cap and forced final answer plus search-failure resilience (Task 4) — all requirements from `docs/superpowers/specs/2026-09-18-web-search-design.md` are covered.
- **Regression risk called out explicitly:** Task 3 Step 1 exists specifically because two pre-existing tests construct a hidden image-description row by raw `INSERT` rather than by calling `_process_describe_image`, so they need to be updated in lockstep with the production code change or they'd silently stop testing the real behavior.
- **Type/name consistency:** `exclude_hidden` → `exclude_image_descriptions` is renamed consistently at both its definition (`_build_history`) and its one call site (`_process_agent_turn`) in the same task, so there's no intermediate broken state committed. `web_search` is imported into `app.queue_worker` under that exact name in Task 4, matching the `monkeypatch.setattr("app.queue_worker.web_search", ...)` target used in that task's tests.
- **Sequencing:** Task 4 depends on both Task 1 (`hidden_kind` column must exist before the search loop can insert rows tagging it) and Task 3 (`WEB_SEARCH_INSTRUCTIONS` is defined in Task 3's `_build_history` replacement, reused as-is by Task 4). Tasks must run in order 1 → 2 → 3 → 4.
