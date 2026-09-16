# Boardroom MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI + SQLite web app where the user defines AI agents (persona + model), groups them into channels, and chats with them using `@mentions` — processed one at a time through a priority queue (image descriptions first, agent replies second) against a llama-swap OpenAI-compatible endpoint.

**Architecture:** Single Python process. FastAPI serves both a JSON API and static frontend files. SQLite (file-based, one connection per call — this is a local single-user tool, no need for pooling) holds agents/groups/messages/jobs/settings. A single `asyncio` background task is the "worker": it polls `queue_jobs` for the next pending job (priority, then FIFO) and processes exactly one at a time, which is what keeps VRAM usage sequential. Frontend is plain HTML/JS with 2-second polling — no WebSockets, no build step.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, sqlite3 (stdlib), httpx (calls to llama-swap), pytest + FastAPI TestClient, python-multipart (image upload). No frontend framework.

**Spec:** `docs/superpowers/specs/2026-09-16-boardroom-design.md`

---

## File Structure

```
boardroom/
  app/
    __init__.py
    main.py                # FastAPI app: mounts routers + static files, starts worker on startup
    db.py                   # sqlite connection + schema + default settings seed
    mentions.py             # @mention text parsing
    llm_client.py           # httpx calls to llama-swap (text + vision)
    queue_worker.py         # background loop + single-job processing logic
    routers/
      __init__.py
      agents.py
      groups.py
      messages.py
      settings.py
    static/
      index.html
      style.css
      app.js
  data/                      # gitignored: sqlite db + uploaded images
  tests/
    conftest.py
    test_db.py
    test_mentions.py
    test_agents_api.py
    test_groups_api.py
    test_llm_client.py
    test_messages_api.py
    test_queue_worker.py
  requirements.txt
  README.md
```

---

### Task 1: Project scaffolding and database schema

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/db.py`
- Test: `tests/conftest.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
httpx==0.27.2
python-multipart==0.0.12
pytest==8.3.3
```

- [ ] **Step 2: Create empty package marker**

Create `app/__init__.py` with empty content.

- [ ] **Step 3: Write `app/db.py` with schema and connection helpers**

```python
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    persona_prompt TEXT NOT NULL,
    model_name TEXT NOT NULL,
    vision_capable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, agent_id)
);

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

CREATE TABLE IF NOT EXISTS queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image')),
    priority INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "llama_swap_base_url": "http://localhost:8080",
    "default_vision_model": "",
    "max_pending_per_group": "20",
}


def _db_path() -> Path:
    return Path(os.environ.get("BOARDROOM_DB_PATH", "./data/boardroom.db"))


def get_connection() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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

- [ ] **Step 4: Write `tests/conftest.py` with a temp-database fixture**

```python
import pytest

from app.db import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))
    init_db()
    yield db_file
```

- [ ] **Step 5: Write `tests/test_db.py`**

```python
from app.db import get_connection


def test_init_db_creates_all_tables(db):
    conn = get_connection()
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert {"agents", "groups", "group_members", "messages", "queue_jobs", "settings"} <= tables


def test_init_db_seeds_default_settings(db):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'llama_swap_base_url'"
        ).fetchone()
    finally:
        conn.close()
    assert row["value"] == "http://localhost:8080"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/__init__.py app/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: add project scaffolding and sqlite schema"
```

---

### Task 2: Mention parsing

**Files:**
- Create: `app/mentions.py`
- Test: `tests/test_mentions.py`

- [ ] **Step 1: Write failing tests**

```python
from app.mentions import extract_mentions


def test_extract_single_mention():
    assert extract_mentions("oi @bob, tudo bem?") == ["bob"]


def test_extract_multiple_mentions():
    assert extract_mentions("@alice e @bob, o que acham?") == ["alice", "bob"]


def test_extract_mentions_no_mentions():
    assert extract_mentions("mensagem sem menções") == []


def test_extract_mentions_deduplicates_preserving_order():
    assert extract_mentions("@bob @alice @bob") == ["bob", "alice"]


def test_extract_mentions_allows_hyphen_and_underscore():
    assert extract_mentions("@investidor-conservador e @dev_junior") == [
        "investidor-conservador",
        "dev_junior",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mentions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mentions'`

- [ ] **Step 3: Write `app/mentions.py`**

```python
import re

MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_-]+)")


def extract_mentions(text: str) -> list[str]:
    """Extract @mentioned names from text, lowercase, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for match in MENTION_PATTERN.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mentions.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/mentions.py tests/test_mentions.py
git commit -m "feat: add @mention parsing"
```

---

### Task 3: Agents data layer and API

**Files:**
- Create: `app/routers/__init__.py`
- Create: `app/routers/agents.py`
- Create: `app/main.py`
- Test: `tests/test_agents_api.py`

- [ ] **Step 1: Write failing tests**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_create_and_list_agent(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "Você é um investidor conservador.",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "bob"
    assert created["vision_capable"] is False

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert names == ["bob"]


def test_create_agent_duplicate_name_rejected(db):
    client = make_client(db)
    payload = {
        "name": "bob",
        "persona_prompt": "x",
        "model_name": "qwen2.5-7b",
        "vision_capable": False,
    }
    assert client.post("/api/agents", json=payload).status_code == 201
    resp = client.post("/api/agents", json=payload)
    assert resp.status_code == 409


def test_update_agent(db):
    client = make_client(db)
    created = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.put(
        f"/api/agents/{created['id']}",
        json={
            "name": "bob",
            "persona_prompt": "novo prompt",
            "model_name": "llava-7b",
            "vision_capable": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["persona_prompt"] == "novo prompt"
    assert body["vision_capable"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `app/routers/__init__.py`** (empty file)

- [ ] **Step 4: Write `app/routers/agents.py`**

```python
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str
    persona_prompt: str
    model_name: str
    vision_capable: bool = False


class AgentOut(AgentIn):
    id: int
    created_at: str


def _row_to_agent(row: sqlite3.Row) -> AgentOut:
    return AgentOut(
        id=row["id"],
        name=row["name"],
        persona_prompt=row["persona_prompt"],
        model_name=row["model_name"],
        vision_capable=bool(row["vision_capable"]),
        created_at=row["created_at"],
    )


@router.get("", response_model=list[AgentOut])
def list_agents() -> list[AgentOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
    finally:
        conn.close()
    return [_row_to_agent(r) for r in rows]


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO agents (name, persona_prompt, model_name, vision_capable) "
                "VALUES (?, ?, ?, ?)",
                (agent.name, agent.persona_prompt, agent.model_name, int(agent.vision_capable)),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="agent name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_agent(row)


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, agent: AgentIn) -> AgentOut:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE agents SET name = ?, persona_prompt = ?, model_name = ?, vision_capable = ? "
            "WHERE id = ?",
            (agent.name, agent.persona_prompt, agent.model_name, int(agent.vision_capable), agent_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="agent not found")
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_agent(row)
```

- [ ] **Step 5: Write `app/main.py`**

```python
from fastapi import FastAPI

from app.db import init_db
from app.routers import agents


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    return app


app = create_app()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_agents_api.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add app/routers/__init__.py app/routers/agents.py app/main.py tests/test_agents_api.py
git commit -m "feat: add agents CRUD API"
```

---

### Task 4: Groups data layer and API (create, list, members)

**Files:**
- Create: `app/routers/groups.py`
- Modify: `app/main.py`
- Test: `tests/test_groups_api.py`

- [ ] **Step 1: Write failing tests**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def _create_agent(client, name="bob"):
    return client.post(
        "/api/agents",
        json={
            "name": name,
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()


def test_create_and_list_group(db):
    client = make_client(db)
    resp = client.post("/api/groups", json={"name": "investidores"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "investidores"

    resp = client.get("/api/groups")
    assert [g["name"] for g in resp.json()] == ["investidores"]


def test_add_and_list_members(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    assert resp.status_code == 204

    resp = client.get(f"/api/groups/{group['id']}/members")
    assert resp.status_code == 200
    assert [m["name"] for m in resp.json()] == ["bob"]


def test_remove_member(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})

    resp = client.delete(f"/api/groups/{group['id']}/members/{agent['id']}")
    assert resp.status_code == 204

    resp = client.get(f"/api/groups/{group['id']}/members")
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_groups_api.py -v`
Expected: FAIL with 404 (router not mounted / module not found)

- [ ] **Step 3: Write `app/routers/groups.py`**

```python
import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupIn(BaseModel):
    name: str


class GroupOut(GroupIn):
    id: int
    created_at: str


class MemberIn(BaseModel):
    agent_id: int


class MemberOut(BaseModel):
    id: int
    name: str


@router.get("", response_model=list[GroupOut])
def list_groups() -> list[GroupOut]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM groups ORDER BY id").fetchall()
    finally:
        conn.close()
    return [GroupOut(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in rows]


@router.post("", response_model=GroupOut, status_code=201)
def create_group(group: GroupIn) -> GroupOut:
    conn = get_connection()
    try:
        try:
            cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (group.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="group name already exists")
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return GroupOut(id=row["id"], name=row["name"], created_at=row["created_at"])


@router.get("/{group_id}/members", response_model=list[MemberOut])
def list_members(group_id: int) -> list[MemberOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT agents.id, agents.name FROM group_members "
            "JOIN agents ON agents.id = group_members.agent_id "
            "WHERE group_members.group_id = ? ORDER BY agents.id",
            (group_id,),
        ).fetchall()
    finally:
        conn.close()
    return [MemberOut(id=r["id"], name=r["name"]) for r in rows]


@router.post("/{group_id}/members", status_code=204)
def add_member(group_id: int, member: MemberIn) -> Response:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, agent_id) VALUES (?, ?)",
            (group_id, member.agent_id),
        )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


@router.delete("/{group_id}/members/{agent_id}", status_code=204)
def remove_member(group_id: int, agent_id: int) -> Response:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND agent_id = ?",
            (group_id, agent_id),
        )
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)
```

- [ ] **Step 4: Wire router into `app/main.py`**

```python
from fastapi import FastAPI

from app.db import init_db
from app.routers import agents, groups


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_groups_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add app/routers/groups.py app/main.py tests/test_groups_api.py
git commit -m "feat: add groups CRUD and membership API"
```

---

### Task 5: Settings API

**Files:**
- Create: `app/routers/settings.py`
- Modify: `app/main.py`
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write failing tests**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_get_settings_returns_defaults(db):
    client = make_client(db)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llama_swap_base_url"] == "http://localhost:8080"
    assert body["default_vision_model"] == ""
    assert body["max_pending_per_group"] == "20"


def test_update_settings(db):
    client = make_client(db)
    resp = client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:9090",
            "default_vision_model": "qwen2-vl-7b",
            "max_pending_per_group": "10",
        },
    )
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["default_vision_model"] == "qwen2-vl-7b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_settings_api.py -v`
Expected: FAIL (404, route not found)

- [ ] **Step 3: Write `app/routers/settings.py`**

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/settings", tags=["settings"])


class Settings(BaseModel):
    llama_swap_base_url: str
    default_vision_model: str
    max_pending_per_group: str


@router.get("", response_model=Settings)
def get_settings() -> Settings:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    values = {r["key"]: r["value"] for r in rows}
    return Settings(**values)


@router.put("", response_model=Settings)
def update_settings(settings: Settings) -> Settings:
    conn = get_connection()
    try:
        for key, value in settings.model_dump().items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
    return get_settings()
```

- [ ] **Step 4: Wire router into `app/main.py`**

```python
from fastapi import FastAPI

from app.db import init_db
from app.routers import agents, groups, settings


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(settings.router)
    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_settings_api.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/routers/settings.py app/main.py tests/test_settings_api.py
git commit -m "feat: add settings API"
```

---

### Task 6: LLM client for llama-swap (text + vision)

**Files:**
- Create: `app/llm_client.py`
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Write failing tests (using a fake httpx transport, no real network)**

```python
import httpx
import pytest

from app.llm_client import chat_completion


def _client_with_transport(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_chat_completion_text_only_sends_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = httpx.Request.read(request) and __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "resposta do modelo"}}]},
        )

    client = _client_with_transport(handler)
    result = chat_completion(
        base_url="http://localhost:8080",
        model="qwen2.5-7b",
        messages=[{"role": "system", "content": "persona"}, {"role": "user", "content": "oi"}],
        http_client=client,
    )

    assert result == "resposta do modelo"
    assert captured["json"]["model"] == "qwen2.5-7b"
    assert captured["json"]["messages"][1]["content"] == "oi"


def test_chat_completion_with_image_builds_multimodal_content():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "vejo um gato"}}]},
        )

    client = _client_with_transport(handler)
    result = chat_completion(
        base_url="http://localhost:8080",
        model="llava-7b",
        messages=[{"role": "user", "content": "o que tem na imagem?"}],
        http_client=client,
        image_base64="ZmFrZS1pbWFnZS1ieXRlcw==",
    )

    assert result == "vejo um gato"
    last_message = captured["json"]["messages"][-1]
    assert last_message["role"] == "user"
    content_types = [part["type"] for part in last_message["content"]]
    assert content_types == ["text", "image_url"]
    assert "ZmFrZS1pbWFnZS1ieXRlcw==" in last_message["content"][1]["image_url"]["url"]


def test_chat_completion_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        chat_completion(
            base_url="http://localhost:8080",
            model="qwen2.5-7b",
            messages=[{"role": "user", "content": "oi"}],
            http_client=client,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm_client'`

- [ ] **Step 3: Write `app/llm_client.py`**

```python
from __future__ import annotations

import httpx


def chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict],
    http_client: httpx.Client | None = None,
    image_base64: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Call the llama-swap OpenAI-compatible /v1/chat/completions endpoint.

    If image_base64 is given, it is attached to the last message as a
    multimodal `image_url` content part alongside its existing text.
    """
    payload_messages = [dict(m) for m in messages]

    if image_base64 is not None and payload_messages:
        last = payload_messages[-1]
        text = last["content"]
        payload_messages[-1] = {
            "role": last["role"],
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                },
            ],
        }

    payload = {"model": model, "messages": payload_messages}

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    finally:
        if owns_client:
            client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/llm_client.py tests/test_llm_client.py
git commit -m "feat: add llama-swap chat completion client with vision support"
```

---

### Task 7: Messages API (post, list, mention-triggered job creation)

**Files:**
- Create: `app/routers/messages.py`
- Modify: `app/main.py`
- Test: `tests/test_messages_api.py`

- [ ] **Step 1: Write failing tests**

```python
import json

from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def _setup_group_with_agent(client, agent_name="bob"):
    agent = client.post(
        "/api/agents",
        json={
            "name": agent_name,
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    return group, agent


def test_post_message_without_mention_creates_no_job(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)

    resp = client.post(f"/api/groups/{group['id']}/messages", json={"content": "oi pessoal"})
    assert resp.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_post_message_with_mention_creates_agent_turn_job(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)

    resp = client.post(f"/api/groups/{group['id']}/messages", json={"content": "@bob o que acha?"})
    message = resp.json()

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "agent_turn"
    assert jobs[0]["agent_id"] == agent["id"]
    assert jobs[0]["priority"] == 1
    payload = json.loads(jobs[0]["payload"])
    assert payload["trigger_message_id"] == message["id"]


def test_post_message_mentioning_non_member_creates_no_job(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)

    client.post(f"/api/groups/{group['id']}/messages", json={"content": "@alguem-que-nao-existe oi"})

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_list_messages_excludes_hidden(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)
    client.post(f"/api/groups/{group['id']}/messages", json={"content": "visível"})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (group_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
            (group["id"], "oculta"),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get(f"/api/groups/{group['id']}/messages")
    contents = [m["content"] for m in resp.json()]
    assert contents == ["visível"]


def test_list_messages_since_id_returns_only_newer(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)
    first = client.post(f"/api/groups/{group['id']}/messages", json={"content": "primeira"}).json()
    second = client.post(f"/api/groups/{group['id']}/messages", json={"content": "segunda"}).json()

    resp = client.get(f"/api/groups/{group['id']}/messages", params={"since_id": first["id"]})
    contents = [m["content"] for m in resp.json()]
    assert contents == ["segunda"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_messages_api.py -v`
Expected: FAIL (404, route not found)

- [ ] **Step 3: Write `app/routers/messages.py`**

```python
import json
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection
from app.mentions import extract_mentions

router = APIRouter(prefix="/api/groups/{group_id}/messages", tags=["messages"])


class MessageIn(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: int
    group_id: int
    sender_type: str
    sender_id: int | None
    content: str
    image_path: str | None
    hidden: bool
    created_at: str


def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        group_id=row["group_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        hidden=bool(row["hidden"]),
        created_at=row["created_at"],
    )


def enqueue_mentions(conn: sqlite3.Connection, group_id: int, trigger_message_id: int, content: str) -> None:
    """Create agent_turn jobs for every mentioned agent that is a member of the group."""
    names = extract_mentions(content)
    if not names:
        return

    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT agents.id FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        WHERE group_members.group_id = ? AND lower(agents.name) IN ({placeholders})
        """,
        (group_id, *names),
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (group_id, row["id"], json.dumps({"trigger_message_id": trigger_message_id})),
        )


@router.get("", response_model=list[MessageOut])
def list_messages(group_id: int, since_id: int | None = None) -> list[MessageOut]:
    conn = get_connection()
    try:
        if since_id is None:
            rows = conn.execute(
                "SELECT * FROM messages WHERE group_id = ? AND hidden = 0 ORDER BY id",
                (group_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE group_id = ? AND hidden = 0 AND id > ? ORDER BY id",
                (group_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


@router.post("", response_model=MessageOut, status_code=201)
def post_message(group_id: int, message: MessageIn) -> MessageOut:
    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        cur = conn.execute(
            "INSERT INTO messages (group_id, sender_type, sender_id, content) "
            "VALUES (?, 'user', NULL, ?)",
            (group_id, message.content),
        )
        message_id = cur.lastrowid
        enqueue_mentions(conn, group_id, message_id, message.content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)
```

- [ ] **Step 4: Wire router into `app/main.py`**

```python
from fastapi import FastAPI

from app.db import init_db
from app.routers import agents, groups, messages, settings


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(settings.router)
    app.include_router(messages.router)
    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_messages_api.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add app/routers/messages.py app/main.py tests/test_messages_api.py
git commit -m "feat: add messages API with mention-triggered job creation"
```

---

### Task 8: Queue worker — single job processing (agent_turn and describe_image)

**Files:**
- Create: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

- [ ] **Step 1: Write failing tests**

```python
import json

from app.db import get_connection
from app.queue_worker import process_next_job


def _create_agent(conn, name="bob", vision_capable=0, model_name="qwen2.5-7b"):
    cur = conn.execute(
        "INSERT INTO agents (name, persona_prompt, model_name, vision_capable) VALUES (?, ?, ?, ?)",
        (name, f"Você é {name}.", model_name, vision_capable),
    )
    return cur.lastrowid


def _create_group(conn, name="investidores"):
    cur = conn.execute("INSERT INTO groups (name) VALUES (?)", (name,))
    return cur.lastrowid


def _add_member(conn, group_id, agent_id):
    conn.execute("INSERT INTO group_members (group_id, agent_id) VALUES (?, ?)", (group_id, agent_id))


def test_process_next_job_picks_highest_priority_first(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'describe_image', 0, ?)",
        (group_id, json.dumps({"image_path": "data/uploads/fake.png", "message_id": 1})),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "descrição gerada",
    )

    process_next_job()

    assert len(calls) == 1
    assert "image_base64" in calls[0] or calls[0].get("image_base64") is not None


def test_process_next_job_agent_turn_posts_reply_and_marks_done(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "olá, tudo bem?")

    process_next_job()

    conn = get_connection()
    try:
        messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert len(messages) == 1
    assert messages[0]["content"] == "olá, tudo bem?"
    assert messages[0]["sender_id"] == agent_id
    assert job["status"] == "done"


def test_process_next_job_reply_with_new_mention_enqueues_follow_up(db, monkeypatch):
    conn = get_connection()
    bob_id = _create_agent(conn, name="bob")
    alice_id = _create_agent(conn, name="alice")
    group_id = _create_group(conn)
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, bob_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.queue_worker.chat_completion", lambda **kwargs: "@alice o que acha?"
    )

    process_next_job()

    conn = get_connection()
    try:
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE status = 'pending'"
        ).fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["agent_id"] == alice_id


def test_process_next_job_no_pending_jobs_is_noop(db):
    assert process_next_job() is False


def test_process_next_job_respects_loop_limit(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (group_id,),
    )
    # Fill the queue up to the default limit (20) with pending jobs for this group.
    for _ in range(20):
        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (group_id, agent_id),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.queue_worker.chat_completion", lambda **kwargs: "@bob de novo?"
    )

    process_next_job()

    conn = get_connection()
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) AS c FROM queue_jobs WHERE status = 'pending'"
        ).fetchone()["c"]
        warning = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system'"
        ).fetchone()
    finally:
        conn.close()
    # 20 - 1 (just processed) = 19 remaining, no new job added for the self-mention.
    assert pending_count == 19
    assert warning is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queue_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.queue_worker'`

- [ ] **Step 3: Write `app/queue_worker.py`**

```python
from __future__ import annotations

import base64
import json
import sqlite3

from app.db import get_connection
from app.llm_client import chat_completion
from app.mentions import extract_mentions
from app.routers.messages import enqueue_mentions


def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _fetch_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM queue_jobs WHERE status = 'pending' "
        "ORDER BY priority ASC, id ASC LIMIT 1"
    ).fetchone()


def _build_history(conn: sqlite3.Connection, group_id: int, agent_persona: str) -> list[dict]:
    rows = conn.execute(
        "SELECT sender_type, sender_id, content FROM messages "
        "WHERE group_id = ? ORDER BY id",
        (group_id,),
    ).fetchall()
    messages = [{"role": "system", "content": agent_persona}]
    for row in rows:
        role = "assistant" if row["sender_type"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})
    return messages


def _process_describe_image(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    payload = json.loads(job["payload"])
    vision_model = _get_setting(conn, "default_vision_model")
    base_url = _get_setting(conn, "llama_swap_base_url")

    with open(payload["image_path"], "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("ascii")

    description = chat_completion(
        base_url=base_url,
        model=vision_model,
        messages=[{"role": "user", "content": "Descreva esta imagem com o máximo de detalhes e precisão possível."}],
        image_base64=image_base64,
    )

    conn.execute(
        "INSERT INTO messages (group_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (job["group_id"], description),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))


def _process_agent_turn(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (job["agent_id"],)).fetchone()
    base_url = _get_setting(conn, "llama_swap_base_url")
    history = _build_history(conn, job["group_id"], agent["persona_prompt"])

    reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)

    conn.execute(
        "INSERT INTO messages (group_id, sender_type, sender_id, content) "
        "VALUES (?, 'agent', ?, ?)",
        (job["group_id"], agent["id"], reply),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))

    max_pending = int(_get_setting(conn, "max_pending_per_group") or "20")
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM queue_jobs WHERE group_id = ? AND status IN ('pending','processing')",
        (job["group_id"],),
    ).fetchone()["c"]

    if active_count >= max_pending:
        if extract_mentions(reply):
            conn.execute(
                "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["group_id"], "Limite de fila atingido neste grupo — novas menções foram ignoradas até a fila esvaziar."),
            )
        return

    reply_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    # last_insert_rowid() above refers to the system warning insert only if it ran;
    # re-fetch the actual agent reply id explicitly instead.
    agent_message_id = conn.execute(
        "SELECT id FROM messages WHERE group_id = ? AND sender_type = 'agent' AND sender_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (job["group_id"], agent["id"]),
    ).fetchone()["id"]
    enqueue_mentions(conn, job["group_id"], agent_message_id, reply)


def process_next_job() -> bool:
    """Process exactly one pending job (highest priority, then oldest). Returns False if queue was empty."""
    conn = get_connection()
    try:
        job = _fetch_next_job(conn)
        if job is None:
            return False

        conn.execute("UPDATE queue_jobs SET status = 'processing' WHERE id = ?", (job["id"],))
        conn.commit()

        try:
            if job["job_type"] == "describe_image":
                _process_describe_image(conn, job)
            else:
                _process_agent_turn(conn, job)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - surface any LLM/IO failure as an error job + system message
            conn.execute("UPDATE queue_jobs SET status = 'error' WHERE id = ?", (job["id"],))
            conn.execute(
                "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["group_id"], f"Erro ao processar job {job['id']}: {exc}"),
            )
            conn.commit()
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: Fix the dead-code bug introduced above and simplify**

The `reply_id = conn.execute("SELECT last_insert_rowid() ...")` line in `_process_agent_turn` is unused and misleading — remove it. Replace the whole tail of `_process_agent_turn` (from `if active_count >= max_pending:` to the end) with:

```python
    agent_message_id = conn.execute(
        "SELECT id FROM messages WHERE group_id = ? AND sender_type = 'agent' AND sender_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (job["group_id"], agent["id"]),
    ).fetchone()["id"]

    if active_count >= max_pending:
        if extract_mentions(reply):
            conn.execute(
                "INSERT INTO messages (group_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["group_id"], "Limite de fila atingido neste grupo — novas menções foram ignoradas até a fila esvaziar."),
            )
        return

    enqueue_mentions(conn, job["group_id"], agent_message_id, reply)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_queue_worker.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: add queue worker for agent_turn and describe_image jobs"
```

---

### Task 9: Image upload endpoint and background worker loop

**Files:**
- Modify: `app/routers/messages.py`
- Modify: `app/main.py`
- Test: `tests/test_messages_api.py` (append)
- Test: `tests/test_queue_worker.py` (append)

- [ ] **Step 1: Write failing test for image upload, appended to `tests/test_messages_api.py`**

```python
def test_upload_image_creates_message_and_priority_job(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    resp = client.post(
        f"/api/groups/{group['id']}/messages/image",
        data={"content": "olha essa foto"},
        files=files,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["image_path"] is not None

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "describe_image"
    assert jobs[0]["priority"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_messages_api.py::test_upload_image_creates_message_and_priority_job -v`
Expected: FAIL with 404 (route not found)

- [ ] **Step 3: Add upload endpoint to `app/routers/messages.py`**

Add these imports at the top of the file:

```python
import os
import uuid
from pathlib import Path

from fastapi import File, Form, UploadFile
```

Append this function at the end of `app/routers/messages.py`:

```python
def _upload_dir() -> Path:
    path = Path(os.environ.get("BOARDROOM_UPLOAD_DIR", "./data/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/image", response_model=MessageOut, status_code=201)
async def post_image_message(
    group_id: int, content: str = Form(""), image: UploadFile = File(...)
) -> MessageOut:
    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        suffix = Path(image.filename or "upload.png").suffix or ".png"
        dest = _upload_dir() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(await image.read())

        cur = conn.execute(
            "INSERT INTO messages (group_id, sender_type, sender_id, content, image_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (group_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'describe_image', 0, ?)",
            (group_id, json.dumps({"image_path": str(dest), "message_id": message_id})),
        )
        enqueue_mentions(conn, group_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)
```

- [ ] **Step 4: Update `_process_describe_image` in `app/queue_worker.py` to read `image_path` (already does) — no change needed, but fix the hardcoded `"data/uploads/fake.png"` test path assumption**

In `tests/test_queue_worker.py`, update `test_process_next_job_picks_highest_priority_first` to actually create a real temp file instead of a fake path, since `_process_describe_image` opens the file. Replace the job-insert line for `describe_image` with:

```python
def test_process_next_job_picks_highest_priority_first(db, monkeypatch, tmp_path):
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"fake-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (group_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (group_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'describe_image', 0, ?)",
        (group_id, json.dumps({"image_path": str(image_path), "message_id": 1})),
    )
    conn.commit()
    conn.close()

    calls = []
    monkeypatch.setattr(
        "app.queue_worker.chat_completion",
        lambda **kwargs: calls.append(kwargs) or "descrição gerada",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None
```

- [ ] **Step 5: Run both test files to verify they pass**

Run: `pytest tests/test_messages_api.py tests/test_queue_worker.py -v`
Expected: all passed

- [ ] **Step 6: Add the background worker loop to `app/main.py`**

Replace the full content of `app/main.py` with:

```python
import asyncio

from fastapi import FastAPI

from app.db import init_db
from app.queue_worker import process_next_job
from app.routers import agents, groups, messages, settings

POLL_INTERVAL_SECONDS = 1.0


async def _worker_loop() -> None:
    while True:
        processed = await asyncio.to_thread(process_next_job)
        await asyncio.sleep(0.05 if processed else POLL_INTERVAL_SECONDS)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(settings.router)
    app.include_router(messages.router)

    @app.on_event("startup")
    async def _start_worker() -> None:
        app.state.worker_task = asyncio.create_task(_worker_loop())

    @app.on_event("shutdown")
    async def _stop_worker() -> None:
        app.state.worker_task.cancel()

    return app


app = create_app()
```

- [ ] **Step 7: Run the full test suite to verify nothing broke**

Run: `pytest -v`
Expected: all tests passed (the worker loop itself starts a background task, which TestClient's `with` context normally triggers; since existing tests instantiate `TestClient(create_app())` without a `with` block, startup/shutdown events do not fire during tests, so this is safe and won't interfere)

- [ ] **Step 8: Commit**

```bash
git add app/routers/messages.py app/main.py tests/test_messages_api.py tests/test_queue_worker.py
git commit -m "feat: add image upload endpoint and background worker loop"
```

---

### Task 10: Frontend shell — static files, groups sidebar, agents/settings pages

**Files:**
- Modify: `app/main.py`
- Create: `app/static/index.html`
- Create: `app/static/style.css`
- Create: `app/static/app.js`

- [ ] **Step 1: Mount static files in `app/main.py`**

Add these imports at the top:

```python
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

Add this right before `return app` inside `create_app()`:

```python
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")
```

- [ ] **Step 2: Write `app/static/index.html`**

```html
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8" />
  <title>Boardroom</title>
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <div id="app">
    <aside id="sidebar">
      <h1>Boardroom</h1>
      <nav>
        <button id="nav-agents">Agentes</button>
        <button id="nav-settings">Configurações</button>
      </nav>
      <h2>Grupos</h2>
      <ul id="group-list"></ul>
      <form id="new-group-form">
        <input id="new-group-name" placeholder="Novo grupo" required />
        <button type="submit">Criar</button>
      </form>
    </aside>
    <main id="main-panel">
      <div id="view-channel" class="view">
        <div id="channel-header"></div>
        <div id="queue-indicator"></div>
        <div id="message-list"></div>
        <form id="message-form">
          <input id="message-input" placeholder="Mensagem (@nome para mencionar)" autocomplete="off" />
          <input id="image-input" type="file" accept="image/*" />
          <button type="submit">Enviar</button>
        </form>
      </div>
      <div id="view-agents" class="view hidden">
        <h2>Agentes</h2>
        <ul id="agent-list"></ul>
        <form id="agent-form">
          <input id="agent-name" placeholder="Nome (sem espaços)" required />
          <textarea id="agent-persona" placeholder="Persona / system prompt" required></textarea>
          <input id="agent-model" placeholder="Nome do modelo no llama-swap" required />
          <label><input id="agent-vision" type="checkbox" /> Capaz de visão</label>
          <button type="submit">Salvar agente</button>
        </form>
      </div>
      <div id="view-settings" class="view hidden">
        <h2>Configurações</h2>
        <form id="settings-form">
          <label>URL base do llama-swap
            <input id="setting-base-url" required />
          </label>
          <label>Modelo de visão padrão
            <input id="setting-vision-model" />
          </label>
          <label>Limite de jobs por grupo
            <input id="setting-max-pending" type="number" min="1" required />
          </label>
          <button type="submit">Salvar</button>
        </form>
      </div>
    </main>
  </div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `app/static/style.css`**

```css
* { box-sizing: border-box; font-family: system-ui, sans-serif; }
body { margin: 0; background: #1e1f22; color: #dcddde; }
#app { display: flex; height: 100vh; }
#sidebar { width: 220px; background: #2b2d31; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
#sidebar h1 { font-size: 16px; }
#sidebar nav { display: flex; flex-direction: column; gap: 6px; }
#sidebar button { background: #383a40; color: #dcddde; border: none; padding: 8px; border-radius: 4px; cursor: pointer; text-align: left; }
#group-list { list-style: none; padding: 0; margin: 0; flex: 1; overflow-y: auto; }
#group-list li { padding: 6px 8px; border-radius: 4px; cursor: pointer; }
#group-list li.active { background: #404249; }
#main-panel { flex: 1; display: flex; flex-direction: column; padding: 16px; overflow: hidden; }
.view.hidden { display: none; }
#message-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.message { background: #2b2d31; padding: 8px 10px; border-radius: 6px; max-width: 70%; }
.message.user { align-self: flex-end; background: #3b4a6b; }
.message.system { align-self: center; font-style: italic; color: #999; }
.message img { max-width: 240px; display: block; margin-top: 6px; border-radius: 4px; }
#message-form { display: flex; gap: 6px; }
#message-input { flex: 1; padding: 8px; }
#queue-indicator { font-size: 12px; color: #a0a3a8; min-height: 16px; }
form input, form textarea { padding: 6px; margin-bottom: 6px; width: 100%; }
#agent-list, #group-list { list-style: none; }
</style>
```

Note: remove the stray trailing `</style>` line above — CSS files have no closing tag. The file should end right after the last real rule (`#agent-list, #group-list { list-style: none; }`).

- [ ] **Step 4: Write `app/static/app.js`**

```javascript
const state = {
  groups: [],
  activeGroupId: null,
  agents: [],
  lastMessageId: 0,
  pollTimer: null,
};

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  if (resp.status === 204) return null;
  return resp.json();
}

function showView(name) {
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("hidden", view.id !== `view-${name}`);
  }
}

async function loadGroups() {
  state.groups = await api("/api/groups");
  const list = document.getElementById("group-list");
  list.innerHTML = "";
  for (const group of state.groups) {
    const li = document.createElement("li");
    li.textContent = group.name;
    li.className = group.id === state.activeGroupId ? "active" : "";
    li.onclick = () => selectGroup(group.id);
    list.appendChild(li);
  }
}

async function selectGroup(groupId) {
  state.activeGroupId = groupId;
  state.lastMessageId = 0;
  document.getElementById("message-list").innerHTML = "";
  const group = state.groups.find((g) => g.id === groupId);
  document.getElementById("channel-header").textContent = group ? `# ${group.name}` : "";
  showView("channel");
  await loadGroups();
  await pollMessages();
}

function renderMessage(message) {
  const div = document.createElement("div");
  div.className = `message ${message.sender_type}`;
  div.textContent = message.content;
  if (message.image_path) {
    const img = document.createElement("img");
    img.src = `/api/groups/${message.group_id}/messages/${message.id}/image`;
    div.appendChild(img);
  }
  document.getElementById("message-list").appendChild(div);
}

async function pollMessages() {
  if (!state.activeGroupId) return;
  const messages = await api(
    `/api/groups/${state.activeGroupId}/messages?since_id=${state.lastMessageId}`
  );
  for (const message of messages) {
    renderMessage(message);
    state.lastMessageId = message.id;
  }
  if (messages.length > 0) {
    document.getElementById("message-list").scrollTop = 1e9;
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollMessages, 2000);
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  const list = document.getElementById("agent-list");
  list.innerHTML = "";
  for (const agent of state.agents) {
    const li = document.createElement("li");
    li.textContent = `${agent.name} (${agent.model_name}${agent.vision_capable ? ", visão" : ""})`;
    list.appendChild(li);
  }
}

async function loadSettings() {
  const settings = await api("/api/settings");
  document.getElementById("setting-base-url").value = settings.llama_swap_base_url;
  document.getElementById("setting-vision-model").value = settings.default_vision_model;
  document.getElementById("setting-max-pending").value = settings.max_pending_per_group;
}

document.getElementById("nav-agents").onclick = async () => {
  showView("agents");
  await loadAgents();
};

document.getElementById("nav-settings").onclick = async () => {
  showView("settings");
  await loadSettings();
};

document.getElementById("new-group-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("new-group-name");
  await api("/api/groups", { method: "POST", body: JSON.stringify({ name: input.value }) });
  input.value = "";
  await loadGroups();
};

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

document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeGroupId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/groups/${state.activeGroupId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
  } else {
    await api(`/api/groups/${state.activeGroupId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
};

(async function init() {
  await loadGroups();
  startPolling();
})();
```

- [ ] **Step 5: Fix the stray `</style>` left in `app/static/style.css`**

Open `app/static/style.css` and delete the trailing `</style>` line so the file ends cleanly with a CSS rule, not a stray HTML closing tag.

- [ ] **Step 6: Manually verify the app boots and serves the frontend**

Run: `uvicorn app.main:app --reload --port 8000` (in one terminal), then in another: `curl -s http://localhost:8000/ | head -5`
Expected: HTML output starting with `<!DOCTYPE html>`. Stop the server with Ctrl+C after checking.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/static
git commit -m "feat: add frontend shell (groups, agents, settings, chat view)"
```

---

### Task 11: Serve uploaded images and finish the image message flow

**Files:**
- Modify: `app/routers/messages.py`
- Test: `tests/test_messages_api.py` (append)

- [ ] **Step 1: Write failing test, appended to `tests/test_messages_api.py`**

```python
def test_get_message_image_returns_file_bytes(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    message = client.post(
        f"/api/groups/{group['id']}/messages/image",
        data={"content": "foto"},
        files=files,
    ).json()

    resp = client.get(f"/api/groups/{group['id']}/messages/{message['id']}/image")
    assert resp.status_code == 200
    assert resp.content == b"fake-png-bytes"


def test_get_message_image_404_when_no_image(db):
    client = make_client(db)
    group, agent = _setup_group_with_agent(client)
    message = client.post(f"/api/groups/{group['id']}/messages", json={"content": "sem imagem"}).json()

    resp = client.get(f"/api/groups/{group['id']}/messages/{message['id']}/image")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_messages_api.py -k image -v`
Expected: FAIL with 404 (route not found) for the first test, and a coincidental pass-looking 404 for the second — check the first one specifically fails on missing route, not just status code, by confirming via `-v` output that `test_get_message_image_returns_file_bytes` fails.

- [ ] **Step 3: Add the image-serving route to `app/routers/messages.py`**

Add this import at the top:

```python
from fastapi.responses import FileResponse
```

Append this function at the end of the file:

```python
@router.get("/{message_id}/image")
def get_message_image(group_id: int, message_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image_path FROM messages WHERE id = ? AND group_id = ?",
            (message_id, group_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["image_path"] is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(row["image_path"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_messages_api.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: serve uploaded images by message id"
```

---

### Task 12: README and manual end-to-end verification

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Boardroom

Chat local onde você cria agentes de IA (persona + modelo), agrupa em canais, e conversa
com eles usando `@menção`. As respostas são processadas uma de cada vez por uma fila
(descrição de imagem tem prioridade sobre resposta de agente), pra caber em GPUs com
VRAM limitada.

## Pré-requisitos

- Python 3.11+
- Um [llama-swap](https://github.com/mostlygeek/llama-swap) rodando localmente,
  proxyando um ou mais modelos llama.cpp (incluindo pelo menos um modelo de visão,
  se você quiser discutir imagens).

## Rodando localmente

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash; use .venv\Scripts\Activate.ps1 no PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Abra `http://localhost:8000`.

Antes de usar:
1. Vá em **Configurações** e ajuste a URL base do llama-swap e o modelo de visão padrão
   (o alias exatamente como configurado no `llama-swap`).
2. Vá em **Agentes** e crie pelo menos um agente (nome sem espaços, persona, modelo).
3. Crie um grupo, adicione o agente como membro (via API `/api/groups/{id}/members` por
   enquanto — UI de gestão de membros é um próximo passo).
4. No canal, mande uma mensagem mencionando `@nome-do-agente`.

## Rodando os testes

```bash
pytest -v
```
```

- [ ] **Step 2: Manual verification — run the test suite one final time**

Run: `pytest -v`
Expected: all tests passed, zero failures

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and usage instructions"
```

---

## Known gaps for a fast-follow (not in this plan's scope)

- No UI for adding/removing group members yet (spec requires it; do it as the next small plan once the core loop is verified against a real llama-swap instance).
- No `@mention` autocomplete dropdown in the message input (spec calls for it; plain text mentions work today).
- No moderator/consensus agent behavior — by design, deferred per the spec (create a "moderator" persona agent and mention it manually).
