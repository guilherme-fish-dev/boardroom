# Múltiplas conversas por grupo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que um grupo tenha várias conversas independentes (cada uma com seu próprio histórico de mensagens), com suporte a criar, renomear e apagar conversas, sem perder nenhuma mensagem já existente.

**Architecture:** Nova tabela `conversations` (FK para `groups`); `messages.group_id` e `queue_jobs.group_id` são substituídos por `conversation_id`. Uma migração automática em `app/db.py`, executada no startup, cria uma conversa "Geral" por grupo existente e realoca as mensagens/jobs antigos para ela. Novo router `app/routers/conversations.py` expõe CRUD de conversas. O frontend ganha uma faixa de abas de conversas dentro do grupo selecionado.

**Tech Stack:** Python 3.11, FastAPI, SQLite (`sqlite3`, sem ORM), pytest + `fastapi.testclient.TestClient`; frontend HTML/CSS/JS vanilla sem build step.

**Spec:** [docs/superpowers/specs/2026-09-18-multiple-conversations-design.md](../specs/2026-09-18-multiple-conversations-design.md)

---

## Nota sobre decisão não coberta explicitamente pelo spec

O spec não fala sobre o que acontece quando um **grupo novo** é criado. Para manter o invariante "um grupo nunca fica sem conversa ativa" (já definido pelo spec para o caso de apagar a última conversa), a Task 2 abaixo faz `POST /api/groups` criar automaticamente uma conversa "Geral" junto com o grupo. Sem isso, um grupo recém-criado ficaria sem nenhuma conversa e o chat não teria onde postar mensagens.

---

### Task 1: Tabela `conversations` e migração de `messages`/`queue_jobs`

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_db.py`:

```python
def test_init_db_creates_conversations_table(db):
    conn = get_connection()
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "conversations" in tables


def test_init_db_migrates_group_id_messages_to_conversations(tmp_path, monkeypatch):
    from app.db import get_connection, init_db

    db_file = tmp_path / "old.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages (
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
        CREATE TABLE queue_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            agent_id INTEGER,
            job_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO groups (id, name) VALUES (1, 'investidores')")
    conn.execute(
        "INSERT INTO messages (id, group_id, sender_type, content) VALUES (1, 1, 'user', 'oi')"
    )
    conn.execute(
        "INSERT INTO queue_jobs (id, group_id, agent_id, job_type, priority, payload) "
        "VALUES (1, 1, NULL, 'agent_turn', 1, '{}')"
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_connection()
    try:
        conversations = conn.execute("SELECT * FROM conversations").fetchall()
        message = conn.execute("SELECT * FROM messages WHERE id = 1").fetchone()
        job = conn.execute("SELECT * FROM queue_jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert len(conversations) == 1
    assert conversations[0]["name"] == "Geral"
    assert conversations[0]["group_id"] == 1
    assert message["conversation_id"] == conversations[0]["id"]
    assert job["conversation_id"] == conversations[0]["id"]


def test_init_db_migration_is_idempotent_for_conversations(tmp_path, monkeypatch):
    from app.db import get_connection, init_db

    db_file = tmp_path / "old2.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages (
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
        CREATE TABLE queue_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            agent_id INTEGER,
            job_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute("INSERT INTO groups (id, name) VALUES (1, 'investidores')")
    conn.commit()
    conn.close()

    init_db()
    init_db()

    conn = get_connection()
    try:
        conversations = conn.execute("SELECT * FROM conversations").fetchall()
    finally:
        conn.close()
    assert len(conversations) == 1
```

Também atualizar `test_init_db_creates_all_tables` para exigir `conversations`:

```python
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
    assert {"agents", "groups", "group_members", "conversations", "messages", "queue_jobs", "settings"} <= tables
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `pytest tests/test_db.py -v`
Expected: FAIL (tabela `conversations` não existe; colunas `group_id`/`conversation_id` incompatíveis)

- [ ] **Step 3: Implementar o schema e a migração em `app/db.py`**

Substituir o conteúdo de `app/db.py` por:

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

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL CHECK (sender_type IN ('user','agent','system')),
    sender_id INTEGER,
    content TEXT NOT NULL,
    image_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    hidden_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
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
    "assistant_model": "",
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


# Não é seguro contra chamadas concorrentes de init_db() (checagem + ALTER não é atômico) —
# aceitável hoje porque o app roda em um único processo e init_db() só é chamado uma vez, no startup.
def _ensure_hidden_kind_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "hidden_kind" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN hidden_kind TEXT")


def _ensure_conversations_table(conn: sqlite3.Connection) -> None:
    """Migrate a pre-conversations database: create one 'Geral' conversation per existing
    group and move messages/queue_jobs from group_id to conversation_id. No-op on a fresh
    install (SCHEMA already creates messages/queue_jobs with conversation_id, so the
    `group_id` column never exists) and no-op on an already-migrated database."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "group_id" not in columns:
        return

    conversation_id_by_group: dict[int, int] = {}
    for row in conn.execute("SELECT id FROM groups"):
        cur = conn.execute(
            "INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (row["id"],)
        )
        conversation_id_by_group[row["id"]] = cur.lastrowid

    conn.execute("ALTER TABLE messages RENAME TO messages_old")
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        "sender_type TEXT NOT NULL CHECK (sender_type IN ('user','agent','system')),"
        "sender_id INTEGER,"
        "content TEXT NOT NULL,"
        "image_path TEXT,"
        "hidden INTEGER NOT NULL DEFAULT 0,"
        "hidden_kind TEXT,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    for row in conn.execute("SELECT * FROM messages_old"):
        conn.execute(
            "INSERT INTO messages (id, conversation_id, sender_type, sender_id, content, "
            "image_path, hidden, hidden_kind, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                conversation_id_by_group[row["group_id"]],
                row["sender_type"],
                row["sender_id"],
                row["content"],
                row["image_path"],
                row["hidden"],
                row["hidden_kind"],
                row["created_at"],
            ),
        )
    conn.execute("DROP TABLE messages_old")

    conn.execute("ALTER TABLE queue_jobs RENAME TO queue_jobs_old")
    conn.execute(
        "CREATE TABLE queue_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        "agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,"
        "job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image')),"
        "priority INTEGER NOT NULL,"
        "payload TEXT NOT NULL,"
        "status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),"
        "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    for row in conn.execute("SELECT * FROM queue_jobs_old"):
        conn.execute(
            "INSERT INTO queue_jobs (id, conversation_id, agent_id, job_type, priority, "
            "payload, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                conversation_id_by_group[row["group_id"]],
                row["agent_id"],
                row["job_type"],
                row["priority"],
                row["payload"],
                row["status"],
                row["created_at"],
            ),
        )
    conn.execute("DROP TABLE queue_jobs_old")


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _ensure_hidden_kind_column(conn)
        _ensure_conversations_table(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_db.py -v`
Expected: PASS (todos os testes de `tests/test_db.py`)

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add conversations table and migrate messages/queue_jobs to conversation_id"
```

---

### Task 2: Grupo novo cria uma conversa "Geral" automaticamente

**Files:**
- Modify: `app/routers/groups.py:39-51`
- Test: `tests/test_groups_api.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar a `tests/test_groups_api.py`:

```python
def test_create_group_creates_default_conversation(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_groups_api.py::test_create_group_creates_default_conversation -v`
Expected: FAIL com 404 ou erro de rota (`/api/groups/{id}/conversations` ainda não existe — vai passar a existir na Task 3; por ora falha por rota inexistente, o que já é esperado)

- [ ] **Step 3: Atualizar `create_group` em `app/routers/groups.py`**

Substituir a função `create_group` (linhas 39-51 do arquivo atual):

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

- [ ] **Step 4: Atualizar o teste de cascade para usar conversas**

Em `tests/test_groups_api.py`, substituir `test_delete_group_cascades_members_messages_and_jobs` por:

```python
def test_delete_group_cascades_members_messages_and_jobs(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "oi"})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation["id"], agent["id"]),
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
        conversations = conn.execute(
            "SELECT * FROM conversations WHERE group_id = ?", (group["id"],)
        ).fetchall()
        messages = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ?", (conversation["id"],)
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE conversation_id = ?", (conversation["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert members == []
    assert conversations == []
    assert messages == []
    assert jobs == []
```

This test still won't pass until Task 3 (needs `POST /api/conversations/{id}/messages`) and Task 4 lands — that's expected; move on to Task 3 without running the full suite yet.

- [ ] **Step 5: Commit**

```bash
git add app/routers/groups.py tests/test_groups_api.py
git commit -m "feat: create a default 'Geral' conversation when a group is created"
```

---

### Task 3: Router de conversas (CRUD)

**Files:**
- Create: `app/routers/conversations.py`
- Modify: `app/main.py:13,49` (registrar o router)
- Test: `tests/test_conversations_api.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/test_conversations_api.py`:

```python
from app.db import get_connection
from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def _create_group(client, name="investidores"):
    return client.post("/api/groups", json={"name": name}).json()


def test_list_conversations_includes_default_geral(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]


def test_create_conversation(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "Due diligence"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Due diligence"
    assert body["group_id"] == group["id"]

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert sorted(c["name"] for c in resp.json()) == ["Due diligence", "Geral"]


def test_create_conversation_rejects_empty_name(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "   "})
    assert resp.status_code == 400


def test_create_conversation_404_for_unknown_group(db):
    client = make_client(db)
    resp = client.post("/api/groups/9999/conversations", json={"name": "x"})
    assert resp.status_code == 404


def test_rename_conversation(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    resp = client.put(
        f"/api/groups/{group['id']}/conversations/{conversation['id']}",
        json={"name": "Renomeada"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renomeada"


def test_rename_conversation_404_for_unknown_id(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.put(f"/api/groups/{group['id']}/conversations/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_non_last_conversation_leaves_the_rest(db):
    client = make_client(db)
    group = _create_group(client)
    geral = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    extra = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "extra"}).json()

    resp = client.delete(f"/api/groups/{group['id']}/conversations/{extra['id']}")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]
    assert resp.json()[0]["id"] == geral["id"]


def test_delete_last_conversation_recreates_geral(db):
    client = make_client(db)
    group = _create_group(client)
    geral = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    resp = client.delete(f"/api/groups/{group['id']}/conversations/{geral['id']}")
    assert resp.status_code == 200
    remaining = resp.json()
    assert [c["name"] for c in remaining] == ["Geral"]
    assert remaining[0]["id"] != geral["id"]  # é uma conversa nova, recriada


def test_delete_conversation_404_for_unknown_id(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.delete(f"/api/groups/{group['id']}/conversations/9999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `pytest tests/test_conversations_api.py -v`
Expected: FAIL com 404 (rota `/api/groups/{group_id}/conversations` não registrada)

- [ ] **Step 3: Implementar `app/routers/conversations.py`**

```python
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/groups/{group_id}/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    name: str


class ConversationOut(BaseModel):
    id: int
    group_id: int
    name: str
    created_at: str


def _row_to_conversation(row: sqlite3.Row) -> ConversationOut:
    return ConversationOut(
        id=row["id"], group_id=row["group_id"], name=row["name"], created_at=row["created_at"]
    )


@router.get("", response_model=list[ConversationOut])
def list_conversations(group_id: int) -> list[ConversationOut]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE group_id = ? ORDER BY id", (group_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_conversation(r) for r in rows]


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(group_id: int, conversation: ConversationIn) -> ConversationOut:
    name = conversation.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="conversation name is required")

    conn = get_connection()
    try:
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")

        cur = conn.execute(
            "INSERT INTO conversations (group_id, name) VALUES (?, ?)", (group_id, name)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_conversation(row)


@router.put("/{conversation_id}", response_model=ConversationOut)
def update_conversation(group_id: int, conversation_id: int, conversation: ConversationIn) -> ConversationOut:
    name = conversation.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="conversation name is required")

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE conversations SET name = ? WHERE id = ? AND group_id = ?",
            (name, conversation_id, group_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation not found")
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_conversation(row)


@router.delete("/{conversation_id}", response_model=list[ConversationOut])
def delete_conversation(group_id: int, conversation_id: int) -> list[ConversationOut]:
    """Delete a conversation. If it was the group's last one, a new empty 'Geral'
    conversation is created automatically so the group is never left without one."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND group_id = ?",
            (conversation_id, group_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation not found")

        remaining = conn.execute(
            "SELECT * FROM conversations WHERE group_id = ? ORDER BY id", (group_id,)
        ).fetchall()
        if not remaining:
            new_cur = conn.execute(
                "INSERT INTO conversations (group_id, name) VALUES (?, 'Geral')", (group_id,)
            )
            conn.commit()
            remaining = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (new_cur.lastrowid,)
            ).fetchall()
        else:
            conn.commit()
    finally:
        conn.close()
    return [_row_to_conversation(r) for r in remaining]
```

- [ ] **Step 4: Registrar o router em `app/main.py`**

Em `app/main.py:13`, mudar:

```python
from app.routers import agents, groups, messages, models, settings
```

para:

```python
from app.routers import agents, conversations, groups, messages, models, settings
```

Em `app/main.py:49` (dentro de `create_app`), adicionar a linha logo após `app.include_router(groups.router)`:

```python
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(conversations.router)
    app.include_router(settings.router)
    app.include_router(messages.router)
    app.include_router(models.router)
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_conversations_api.py tests/test_groups_api.py -v`
Expected: PASS em todos, exceto `test_delete_group_cascades_members_messages_and_jobs` (ainda depende da Task 4, que muda a rota de mensagens) — confirme que ele falha apenas por causa da rota `/api/conversations/{id}/messages` não existir ainda (404), não por outro motivo.

- [ ] **Step 6: Commit**

```bash
git add app/routers/conversations.py app/main.py tests/test_conversations_api.py
git commit -m "feat: add conversations CRUD endpoints"
```

---

### Task 4: Router de mensagens migrado para `conversation_id`

**Files:**
- Modify: `app/routers/messages.py` (arquivo inteiro)
- Test: `tests/test_messages_api.py` (arquivo inteiro)

- [ ] **Step 1: Reescrever `tests/test_messages_api.py`**

Substituir o conteúdo inteiro do arquivo por:

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
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]
    return group, agent, conversation


def test_post_message_without_mention_creates_no_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "oi pessoal"})
    assert resp.status_code == 201

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_post_message_with_mention_creates_agent_turn_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@bob o que acha?"})
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
    assert jobs[0]["conversation_id"] == conversation["id"]
    payload = json.loads(jobs[0]["payload"])
    assert payload["trigger_message_id"] == message["id"]


def test_post_message_mentioning_non_member_creates_no_job(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "@alguem-que-nao-existe oi"})

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert jobs == []


def test_post_message_404_for_unknown_conversation(db):
    client = make_client(db)
    resp = client.post("/api/conversations/9999/messages", json={"content": "oi"})
    assert resp.status_code == 404


def test_list_messages_excludes_hidden(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "visível"})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
            (conversation["id"], "oculta"),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages")
    contents = [m["content"] for m in resp.json()]
    assert contents == ["visível"]


def test_list_messages_since_id_returns_only_newer(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    first = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "primeira"}).json()
    second = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "segunda"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages", params={"since_id": first["id"]})
    contents = [m["content"] for m in resp.json()]
    assert contents == ["segunda"]


def test_upload_image_creates_message_and_priority_job(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "olha essa foto"},
        files=files,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["image_path"] is not None
    assert body["conversation_id"] == conversation["id"]

    conn = get_connection()
    try:
        jobs = conn.execute("SELECT * FROM queue_jobs").fetchall()
    finally:
        conn.close()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "describe_image"
    assert jobs[0]["priority"] == 0
    assert jobs[0]["conversation_id"] == conversation["id"]


def test_upload_image_rejects_non_image_content_type(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("notes.txt", b"just text", "text/plain")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "isso não é imagem"},
        files=files,
    )
    assert resp.status_code == 415


def test_upload_image_rejects_oversized_file(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    big_payload = b"x" * (10 * 1024 * 1024 + 1)
    files = {"image": ("big.png", big_payload, "image/png")}
    resp = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "arquivo grande"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_image_404_for_unknown_conversation(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    resp = client.post(
        "/api/conversations/9999/messages/image",
        data={"content": "foto"},
        files=files,
    )
    assert resp.status_code == 404


def test_get_message_image_returns_file_bytes(db, tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDROOM_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)

    files = {"image": ("cat.png", b"fake-png-bytes", "image/png")}
    message = client.post(
        f"/api/conversations/{conversation['id']}/messages/image",
        data={"content": "foto"},
        files=files,
    ).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/image")
    assert resp.status_code == 200
    assert resp.content == b"fake-png-bytes"


def test_get_message_image_404_when_no_image(db):
    client = make_client(db)
    group, agent, conversation = _setup_group_with_agent(client)
    message = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "sem imagem"}).json()

    resp = client.get(f"/api/conversations/{conversation['id']}/messages/{message['id']}/image")
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `pytest tests/test_messages_api.py -v`
Expected: FAIL (rota `/api/conversations/{id}/messages` ainda não existe — 404 em todos os testes)

- [ ] **Step 3: Reescrever `app/routers/messages.py`**

Substituir o conteúdo inteiro do arquivo por:

```python
import json
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_connection
from app.mentions import extract_mentions

router = APIRouter(prefix="/api/conversations/{conversation_id}/messages", tags=["messages"])


class MessageIn(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_type: str
    sender_id: int | None
    content: str
    image_path: str | None
    hidden: bool
    created_at: str


def _row_to_message(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_type=row["sender_type"],
        sender_id=row["sender_id"],
        content=row["content"],
        image_path=row["image_path"],
        hidden=bool(row["hidden"]),
        created_at=row["created_at"],
    )


def enqueue_mentions(conn: sqlite3.Connection, conversation_id: int, trigger_message_id: int, content: str) -> None:
    """Create agent_turn jobs for every mentioned agent that is a member of the conversation's group."""
    names = extract_mentions(content)
    if not names:
        return

    conversation = conn.execute(
        "SELECT group_id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        return

    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT agents.id FROM agents
        JOIN group_members ON group_members.agent_id = agents.id
        WHERE group_members.group_id = ? AND lower(agents.name) IN ({placeholders})
        """,
        (conversation["group_id"], *names),
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, ?)",
            (conversation_id, row["id"], json.dumps({"trigger_message_id": trigger_message_id})),
        )


@router.get("", response_model=list[MessageOut])
def list_messages(conversation_id: int, since_id: int | None = None) -> list[MessageOut]:
    conn = get_connection()
    try:
        if since_id is None:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND hidden = 0 ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND hidden = 0 AND id > ? ORDER BY id",
                (conversation_id, since_id),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


@router.post("", response_model=MessageOut, status_code=201)
def post_message(conversation_id: int, message: MessageIn) -> MessageOut:
    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
            "VALUES (?, 'user', NULL, ?)",
            (conversation_id, message.content),
        )
        message_id = cur.lastrowid
        enqueue_mentions(conn, conversation_id, message_id, message.content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


def _upload_dir() -> Path:
    path = Path(os.environ.get("BOARDROOM_UPLOAD_DIR", "./data/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/image", response_model=MessageOut, status_code=201)
async def post_image_message(
    conversation_id: int, content: str = Form(""), image: UploadFile = File(...)
) -> MessageOut:
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="file must be an image")

    body = await image.read()
    if len(body) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="image too large (max 10MB)")

    conn = get_connection()
    try:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        suffix = Path(image.filename or "upload.png").suffix or ".png"
        dest = _upload_dir() / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(body)

        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, sender_id, content, image_path) "
            "VALUES (?, 'user', NULL, ?, ?)",
            (conversation_id, content, str(dest)),
        )
        message_id = cur.lastrowid

        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, NULL, 'describe_image', 0, ?)",
            (conversation_id, json.dumps({"image_path": str(dest), "message_id": message_id})),
        )
        enqueue_mentions(conn, conversation_id, message_id, content)
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row)


@router.get("/{message_id}/image")
def get_message_image(conversation_id: int, message_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT image_path FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["image_path"] is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(row["image_path"])
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_messages_api.py tests/test_groups_api.py tests/test_conversations_api.py -v`
Expected: PASS em todos (o `test_delete_group_cascades_members_messages_and_jobs` da Task 2 agora deve passar também)

- [ ] **Step 5: Commit**

```bash
git add app/routers/messages.py tests/test_messages_api.py
git commit -m "feat: scope messages to conversation_id instead of group_id"
```

---

### Task 5: Fila de jobs (`queue_worker.py`) migrada para `conversation_id`

**Files:**
- Modify: `app/queue_worker.py` (arquivo inteiro)
- Test: `tests/test_queue_worker.py` (arquivo inteiro)

- [ ] **Step 1: Reescrever `tests/test_queue_worker.py`**

Substituir o conteúdo inteiro do arquivo por (é uma renomeação mecânica de `group_id` para `conversation_id` nas colunas de `messages`/`queue_jobs`, com um novo helper `_create_conversation`):

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


def _create_conversation(conn, group_id, name="Geral"):
    cur = conn.execute("INSERT INTO conversations (group_id, name) VALUES (?, ?)", (group_id, name))
    return cur.lastrowid


def _add_member(conn, group_id, agent_id):
    conn.execute("INSERT INTO group_members (group_id, agent_id) VALUES (?, ?)", (group_id, agent_id))


def test_process_next_job_picks_highest_priority_first(db, monkeypatch, tmp_path):
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"fake-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, NULL, 'describe_image', 0, ?)",
        (conversation_id, json.dumps({"image_path": str(image_path), "message_id": 1})),
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


def test_process_next_job_agent_turn_posts_reply_and_marks_done(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
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
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, bob_id)
    _add_member(conn, group_id, alice_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, bob_id),
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


def test_process_next_job_rolls_back_partial_work_on_error(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: "olá, tudo bem?")

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.queue_worker.enqueue_mentions", _boom)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
        system_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'system'"
        ).fetchall()
        job = conn.execute("SELECT * FROM queue_jobs").fetchone()
    finally:
        conn.close()

    assert agent_messages == []
    assert job["status"] == "error"
    assert len(system_messages) == 1
    assert "boom" in system_messages[0]["content"]


def test_process_next_job_agent_turn_passes_image_to_vision_capable_agent(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
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
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None


def test_process_next_job_agent_turn_no_image_for_non_vision_agent(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=0)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
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
        lambda **kwargs: calls.append(kwargs) or "texto apenas",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0].get("image_base64") is None


def test_process_next_job_vision_agent_history_excludes_hidden_description(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (conversation_id, "descrição oculta gerada pelo describe_image"),
    )
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
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    assert calls[0]["image_base64"] is not None
    contents = [m["content"] for m in calls[0]["messages"]]
    assert not any("descrição oculta gerada pelo describe_image" in c for c in contents)


def test_process_next_job_vision_agent_history_includes_hidden_message_without_kind(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=1)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden) VALUES (?, 'system', ?, 1)",
        (conversation_id, "mensagem oculta sem hidden_kind"),
    )
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
        lambda **kwargs: calls.append(kwargs) or "vejo uma imagem",
    )

    process_next_job()

    assert len(calls) == 1
    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("mensagem oculta sem hidden_kind" in c for c in contents)


def test_process_next_job_non_vision_agent_history_includes_hidden_description(db, monkeypatch, tmp_path):
    image_path = tmp_path / "recent.png"
    image_path.write_bytes(b"fake-image-bytes")

    conn = get_connection()
    agent_id = _create_agent(conn, vision_capable=0)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, image_path) VALUES (?, 'user', '@bob olha isso', ?)",
        (conversation_id, str(image_path)),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) VALUES (?, 'system', ?, 1, 'image_description')",
        (conversation_id, "descrição oculta gerada pelo describe_image"),
    )
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
        lambda **kwargs: calls.append(kwargs) or "texto apenas",
    )

    process_next_job()

    assert len(calls) == 1
    contents = [m["content"] for m in calls[0]["messages"]]
    assert any("descrição oculta gerada pelo describe_image" in c for c in contents)


def test_process_next_job_no_pending_jobs_is_noop(db):
    assert process_next_job() is False


def test_process_next_job_respects_loop_limit(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', '@bob oi')",
        (conversation_id,),
    )
    for _ in range(20):
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation_id, agent_id),
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
    assert pending_count == 19
    assert warning is not None


def test_process_next_job_agent_searches_once_then_answers(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que dia é hoje?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
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
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'pesquise sem parar')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
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
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'oi')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
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


def test_process_next_job_forced_answer_still_searching_falls_back_to_generic_message(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'pesquise sem parar')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(
        [
            "BUSCAR: um",
            "BUSCAR: dois",
            "BUSCAR: tres",
            "BUSCAR: quatro",
            "BUSCAR: mais uma vez",
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
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == (
        "Não consegui concluir a busca a tempo, mas posso ajudar com o que já sei — pode perguntar de novo."
    )


def test_process_next_job_detects_buscar_even_with_prose_around_it(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que dia é hoje?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    replies = iter(["Vou pesquisar isso.\nBUSCAR: data de hoje", "Hoje é 18 de setembro de 2026."])
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: next(replies))

    captured_queries = []

    def _fake_web_search(query, **kwargs):
        captured_queries.append(query)
        return "Hoje é 18/09/2026."

    monkeypatch.setattr("app.queue_worker.web_search", _fake_web_search)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert captured_queries == ["data de hoje"]
    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == "Hoje é 18 de setembro de 2026."


def test_process_next_job_does_not_treat_buscar_mention_mid_sentence_as_search_command(db, monkeypatch):
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'o que é BUSCAR?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = "Aqui está minha resposta final sobre BUSCAR: como conceito de programação."
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    def _fail_if_called(query, **kwargs):
        raise AssertionError("web_search não deveria ser chamado para uma menção no meio da frase")

    monkeypatch.setattr("app.queue_worker.web_search", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == reply_text


def test_process_next_job_buscar_same_line_preamble_leaks_as_text_not_search(db, monkeypatch):
    """Documents the accepted trade-off of the line-anchored SEARCH_PATTERN: a preamble on the
    SAME line as `BUSCAR:` (e.g. "Vou pesquisar. BUSCAR: x") is deliberately NOT detected as a
    search command, so it leaks through as plain text instead of triggering a search. This is
    the accepted risk (see the comment above SEARCH_PATTERN in app/queue_worker.py) — a smaller
    cost than the false positive of matching "BUSCAR:" anywhere in the response, which fires
    unwanted network calls. Do not "fix" this by switching back to an unanchored `.search()`.
    """
    conn = get_connection()
    agent_id = _create_agent(conn)
    group_id = _create_group(conn)
    conversation_id = _create_conversation(conn, group_id)
    _add_member(conn, group_id, agent_id)
    conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'user', 'que tempo faz em SP?')",
        (conversation_id,),
    )
    conn.execute(
        "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
        "VALUES (?, ?, 'agent_turn', 1, '{}')",
        (conversation_id, agent_id),
    )
    conn.commit()
    conn.close()

    reply_text = "Vou pesquisar. BUSCAR: clima em SP"
    monkeypatch.setattr("app.queue_worker.chat_completion", lambda **kwargs: reply_text)

    def _fail_if_called(query, **kwargs):
        raise AssertionError("web_search não deveria ser chamado quando o preâmbulo está na mesma linha")

    monkeypatch.setattr("app.queue_worker.web_search", _fail_if_called)

    process_next_job()

    conn = get_connection()
    try:
        agent_messages = conn.execute(
            "SELECT * FROM messages WHERE sender_type = 'agent'"
        ).fetchall()
    finally:
        conn.close()

    assert len(agent_messages) == 1
    assert agent_messages[0]["content"] == reply_text
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `pytest tests/test_queue_worker.py -v`
Expected: FAIL (`sqlite3.OperationalError: no such column: conversation_id` — `queue_worker.py` ainda usa `group_id`)

- [ ] **Step 3: Reescrever `app/queue_worker.py`**

Substituir o conteúdo inteiro do arquivo por (renomeação de `group_id` para `conversation_id` em `_build_history`, `_find_recent_image`, `_process_describe_image`, `_process_agent_turn`, `process_next_job`; nota: o limite `max_pending_per_group` agora conta jobs ativos por *conversa*, não mais por todas as conversas de um grupo somadas — ver comentário no código):

```python
from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3

from app.db import get_connection
from app.llm_client import chat_completion
from app.mentions import extract_mentions
from app.routers.messages import enqueue_mentions
from app.web_search import web_search

logger = logging.getLogger(__name__)


def _get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _fetch_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM queue_jobs WHERE status = 'pending' "
        "ORDER BY priority ASC, id ASC LIMIT 1"
    ).fetchone()


WEB_SEARCH_INSTRUCTIONS = (
    "\n\nVocê pode pesquisar na internet quando precisar de informação atual ou que não sabe. "
    "Para isso, responda usando SOMENTE esta linha, nada mais: BUSCAR: sua consulta aqui. "
    "Você vai receber os resultados da busca e poderá responder normalmente em seguida, "
    "ou buscar de novo (no máximo 3 vezes) se ainda precisar de mais informação."
)

# Ancorado ao início de linha (não à string inteira) pra pegar o padrão mesmo quando o
# modelo escreve um preâmbulo numa linha separada antes de "BUSCAR: ...". Deliberadamente
# NÃO detecta "BUSCAR:" no meio de uma frase (ex.: "minha resposta sobre BUSCAR: conceito") —
# isso evitaria falso positivo (busca disparada por engano) às custas de eventualmente perder
# um preâmbulo que fica na MESMA linha do comando (ex.: "Vou pesquisar. BUSCAR: x"), que nesse
# caso vaza como texto normal — um risco menor que ativar uma busca indevida.
SEARCH_PATTERN = re.compile(r"^\s*BUSCAR:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
MAX_SEARCHES_PER_TURN = 3


def _build_history(
    conn: sqlite3.Connection,
    conversation_id: int,
    agent_persona: str,
    *,
    exclude_image_descriptions: bool = False,
) -> list[dict]:
    query = "SELECT sender_type, sender_id, content FROM messages WHERE conversation_id = ?"
    if exclude_image_descriptions:
        query += " AND (hidden = 0 OR IFNULL(hidden_kind, '') != 'image_description')"
    query += " ORDER BY id"
    rows = conn.execute(query, (conversation_id,)).fetchall()
    messages = [{"role": "system", "content": agent_persona + WEB_SEARCH_INSTRUCTIONS}]
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
        "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
        "VALUES (?, 'system', ?, 1, 'image_description')",
        (job["conversation_id"], description),
    )
    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))


def _find_recent_image(conn: sqlite3.Connection, conversation_id: int) -> str | None:
    row = conn.execute(
        "SELECT image_path FROM messages WHERE conversation_id = ? AND image_path IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row["image_path"] if row else None


def _process_agent_turn(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (job["agent_id"],)).fetchone()
    base_url = _get_setting(conn, "llama_swap_base_url")

    image_base64 = None
    if agent["vision_capable"]:
        image_path = _find_recent_image(conn, job["conversation_id"])
        if image_path:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("ascii")

    history = _build_history(
        conn,
        job["conversation_id"],
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
    match = SEARCH_PATTERN.search(reply)
    while match and searches_done < MAX_SEARCHES_PER_TURN:
        query = match.group(1).strip()
        try:
            results = web_search(query)
        except Exception as exc:
            results = f"Erro ao buscar: {exc}"

        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content, hidden, hidden_kind) "
            "VALUES (?, 'system', ?, 1, 'search_result')",
            (job["conversation_id"], f'Busca por "{query}":\n{results}'),
        )

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": f'Resultados da busca por "{query}":\n{results}'})
        reply = chat_completion(base_url=base_url, model=agent["model_name"], messages=history)
        searches_done += 1
        match = SEARCH_PATTERN.search(reply)

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
        if SEARCH_PATTERN.search(reply):
            reply = "Não consegui concluir a busca a tempo, mas posso ajudar com o que já sei — pode perguntar de novo."

    cur = conn.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_id, content) "
        "VALUES (?, 'agent', ?, ?)",
        (job["conversation_id"], agent["id"], reply),
    )
    agent_message_id = cur.lastrowid

    # Count active jobs (this job is still 'processing' at this point) to decide whether the
    # conversation's queue has room for a follow-up job from this reply. Antes da introdução de
    # múltiplas conversas por grupo, esse limite era por grupo; agora é por conversa individual.
    max_pending = int(_get_setting(conn, "max_pending_per_group") or "20")
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM queue_jobs WHERE conversation_id = ? AND status IN ('pending','processing')",
        (job["conversation_id"],),
    ).fetchone()["c"]

    conn.execute("UPDATE queue_jobs SET status = 'done' WHERE id = ?", (job["id"],))

    if active_count >= max_pending:
        if extract_mentions(reply):
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["conversation_id"], "Limite de fila atingido nesta conversa — novas menções foram ignoradas até a fila esvaziar."),
            )
        return

    enqueue_mentions(conn, job["conversation_id"], agent_message_id, reply)


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
            logger.exception(f"Erro ao processar job {job['id']}")
            conn.rollback()  # discard any partial, uncommitted work (e.g. the agent reply insert) before recording the error
            conn.execute("UPDATE queue_jobs SET status = 'error' WHERE id = ?", (job["id"],))
            conn.execute(
                "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
                (job["conversation_id"], f"Erro ao processar job {job['id']}: {exc}"),
            )
            conn.commit()
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: Rodar a suíte inteira e confirmar que passa**

Run: `pytest -v`
Expected: PASS em todos os testes (`test_db.py`, `test_groups_api.py`, `test_conversations_api.py`, `test_messages_api.py`, `test_queue_worker.py`, `test_agents_api.py`, `test_models_api.py`, `test_settings_api.py`, `test_mentions.py`, `test_llm_client.py`, `test_web_search.py`)

- [ ] **Step 5: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: scope queue jobs and agent history to conversation_id instead of group_id"
```

---

### Task 6: Frontend — abas de conversas

Não há testes automatizados de frontend neste projeto (é JS vanilla sem build/framework de testes). Este task termina com verificação manual no navegador (Task 7).

**Files:**
- Modify: `app/static/index.html:33-37`
- Modify: `app/static/app.js` (vários trechos)
- Modify: `app/static/style.css` (adicionar regras no final do arquivo)

- [ ] **Step 1: Adicionar a faixa de abas no HTML**

Em `app/static/index.html`, dentro de `#channel-content`, adicionar `<div id="conversation-tabs"></div>` logo após `#channel-header` e antes de `#channel-members`:

```html
          <div id="channel-header">
            <span id="channel-header-name"></span>
            <button type="button" id="rename-group-btn" class="btn-secondary">Renomear</button>
            <button type="button" id="delete-group-btn" class="btn-secondary">Apagar</button>
          </div>
          <div id="conversation-tabs"></div>
          <div id="channel-members">
```

- [ ] **Step 2: Adicionar estado e funções de conversa em `app.js`**

Em `app/static/app.js`, no objeto `state` (linhas 1-10), adicionar dois campos:

```javascript
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
  editingAgentId: null,
};
```

Substituir a função `selectGroup` (linhas 78-90 do arquivo atual) por:

```javascript
async function selectGroup(groupId) {
  state.activeGroupId = groupId;
  const group = state.groups.find((g) => g.id === groupId);
  document.getElementById("channel-header-name").textContent = group ? `# ${group.name}` : "";
  document.getElementById("channel-empty").classList.add("hidden");
  document.getElementById("channel-content").classList.remove("hidden");
  showView("channel");
  await loadGroups();
  await loadMembers(groupId);
  await loadConversations(groupId);
}

async function loadConversations(groupId) {
  state.conversations = await api(`/api/groups/${groupId}/conversations`);
  const stillActive = state.conversations.some((c) => c.id === state.activeConversationId);
  if (stillActive) {
    renderConversationTabs();
  } else {
    await selectConversation(state.conversations[0].id);
  }
}

function renderConversationTabs() {
  const bar = document.getElementById("conversation-tabs");
  bar.innerHTML = "";

  for (const conversation of state.conversations) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "conversation-tab" + (conversation.id === state.activeConversationId ? " active" : "");
    tab.onclick = () => selectConversation(conversation.id);

    const label = document.createElement("span");
    label.textContent = conversation.name;
    tab.appendChild(label);

    const closeBtn = document.createElement("span");
    closeBtn.className = "conversation-tab-delete";
    closeBtn.textContent = "×";
    closeBtn.setAttribute("aria-label", `Apagar conversa ${conversation.name}`);
    closeBtn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Apagar a conversa "${conversation.name}"?`)) return;
      const remaining = await api(
        `/api/groups/${state.activeGroupId}/conversations/${conversation.id}`,
        { method: "DELETE" }
      );
      state.conversations = remaining;
      if (state.activeConversationId === conversation.id) {
        await selectConversation(remaining[0].id);
      } else {
        renderConversationTabs();
      }
    };
    tab.appendChild(closeBtn);
    bar.appendChild(tab);
  }

  const newBtn = document.createElement("button");
  newBtn.type = "button";
  newBtn.id = "new-conversation-btn";
  newBtn.textContent = "+";
  newBtn.setAttribute("aria-label", "Nova conversa");
  newBtn.onclick = async () => {
    const name = prompt("Nome da nova conversa:");
    if (!name || !name.trim()) return;
    const conversation = await api(`/api/groups/${state.activeGroupId}/conversations`, {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    });
    state.conversations.push(conversation);
    await selectConversation(conversation.id);
  };
  bar.appendChild(newBtn);
}

async function selectConversation(conversationId) {
  state.activeConversationId = conversationId;
  state.lastMessageId = 0;
  document.getElementById("message-list").innerHTML = "";
  renderConversationTabs();
  await pollMessages();
}
```

Atualizar `renderMessage` (linhas 150-190) para usar `conversation_id` no lugar de `group_id` na URL da imagem:

```javascript
  if (message.image_path) {
    const img = document.createElement("img");
    img.src = `/api/conversations/${message.conversation_id}/messages/${message.id}/image`;
    img.alt = "Imagem enviada no chat";
    bubble.appendChild(img);
  }
```

Substituir `pollMessages` (linhas 209-222) por:

```javascript
async function pollMessages() {
  if (!state.activeConversationId) return;
  const messages = await api(
    `/api/conversations/${state.activeConversationId}/messages?since_id=${state.lastMessageId}`
  );
  for (const message of messages) {
    renderMessage(message);
    state.lastMessageId = message.id;
  }
  updateMessageListEmptyState();
  if (messages.length > 0) {
    document.getElementById("message-list").scrollTop = 1e9;
  }
}
```

Substituir o handler `delete-group-btn` (linhas 430-440) por:

```javascript
document.getElementById("delete-group-btn").onclick = async () => {
  if (!state.activeGroupId) return;
  const group = state.groups.find((g) => g.id === state.activeGroupId);
  const name = group ? group.name : "";
  if (!confirm(`Apagar o grupo "${name}"? Todo o histórico de mensagens será perdido permanentemente.`)) return;
  await api(`/api/groups/${state.activeGroupId}`, { method: "DELETE" });
  state.activeGroupId = null;
  state.activeConversationId = null;
  state.conversations = [];
  document.getElementById("channel-content").classList.add("hidden");
  document.getElementById("channel-empty").classList.remove("hidden");
  await loadGroups();
};
```

Substituir o handler `message-form` (linhas 492-513) por:

```javascript
document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!state.activeConversationId) return;
  const textInput = document.getElementById("message-input");
  const imageInput = document.getElementById("image-input");

  if (imageInput.files.length > 0) {
    const form = new FormData();
    form.append("content", textInput.value);
    form.append("image", imageInput.files[0]);
    await api(`/api/conversations/${state.activeConversationId}/messages/image`, { method: "POST", body: form });
    imageInput.value = "";
    document.getElementById("image-filename").textContent = "";
  } else {
    await api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: textInput.value }),
    });
  }
  textInput.value = "";
  await pollMessages();
};
```

- [ ] **Step 3: Adicionar estilos das abas em `app/static/style.css`**

Adicionar ao final do arquivo:

```css
#conversation-tabs {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  padding-bottom: 10px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.conversation-tab {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--ink-muted);
  padding: 6px 8px 6px 12px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  cursor: pointer;
  transition: background-color 150ms ease, color 150ms ease;
}
.conversation-tab:hover { background: var(--surface-hover); color: var(--ink); }
.conversation-tab.active { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }

.conversation-tab-delete {
  color: var(--ink-faint);
  font-size: 14px;
  line-height: 1;
  transition: color 150ms ease;
}
.conversation-tab.active .conversation-tab-delete { color: var(--accent-ink); opacity: 0.7; }
.conversation-tab-delete:hover { color: var(--danger); }

#new-conversation-btn {
  background: transparent;
  border: 1px dashed var(--border-strong);
  color: var(--ink-faint);
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
}
#new-conversation-btn:hover { background: var(--surface); color: var(--ink); }
```

- [ ] **Step 4: Rodar a suíte de backend novamente (garantia de que nada quebrou)**

Run: `pytest -v`
Expected: PASS em todos os testes (o frontend não tem testes automatizados, mas isso confirma que nenhum arquivo Python foi afetado por engano)

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/style.css
git commit -m "feat: add conversation tabs UI to the group chat view"
```

---

### Task 7: Verificação manual no navegador

**Files:** nenhum (apenas verificação)

- [ ] **Step 1: Subir o servidor**

Run: `start.bat` (ou, se preferir rodar em primeiro plano: `.venv\Scripts\python -m uvicorn app.main:app --reload`)

- [ ] **Step 2: Abrir `http://localhost:8000` no navegador e verificar**

1. Selecionar um grupo existente (criado antes desta feature) e confirmar que aparece uma aba "Geral" já selecionada, com o histórico de mensagens antigo intacto.
2. Clicar em "+", digitar um nome (ex.: "Due diligence") e confirmar que uma nova aba aparece e fica vazia/selecionada.
3. Enviar uma mensagem na nova conversa, trocar para a aba "Geral" e confirmar que a mensagem nova NÃO aparece lá (conversas isoladas).
4. Clicar no "×" da aba não-ativa e confirmar (no `confirm()` do navegador) — a aba some e a aba ativa não muda.
5. Apagar a única conversa restante do grupo (o "×" da última aba) e confirmar que uma nova aba "Geral" vazia é criada automaticamente e selecionada.
6. Criar um grupo novo do zero e confirmar que ele já nasce com uma aba "Geral" (sem precisar criar manualmente).
7. Apagar um grupo com múltiplas conversas (botão "Apagar" do grupo) e confirmar que tudo some sem erros no console do navegador.

- [ ] **Step 3: Reportar o resultado**

Se algum passo falhar, anotar o comportamento observado vs. esperado antes de seguir para qualquer ajuste — não corrigir "no escuro".

---

## Resumo de arquivos tocados

- `app/db.py` — schema + migração
- `app/routers/groups.py` — cria conversa "Geral" ao criar grupo
- `app/routers/conversations.py` — novo, CRUD de conversas
- `app/routers/messages.py` — migrado para `conversation_id`
- `app/queue_worker.py` — migrado para `conversation_id`
- `app/main.py` — registra o router de conversas
- `app/static/index.html`, `app/static/app.js`, `app/static/style.css` — UI de abas
- `tests/test_db.py`, `tests/test_groups_api.py`, `tests/test_conversations_api.py` (novo), `tests/test_messages_api.py`, `tests/test_queue_worker.py`
