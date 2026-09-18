from app.db import get_connection, get_setting


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


def test_init_db_seeds_default_settings(db):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'llama_swap_base_url'"
        ).fetchone()
    finally:
        conn.close()
    assert row["value"] == "http://localhost:8080"


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
