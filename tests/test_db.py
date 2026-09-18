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


def test_init_db_resumes_migration_interrupted_after_rename(tmp_path, monkeypatch):
    """Simulates a crash between the rename+create step and the copy+drop step of
    _ensure_conversations_table (ALTER TABLE ... RENAME TO commits immediately in SQLite and
    can't be rolled back, so this is a real state the migration must be able to resume from
    without losing data)."""
    from app.db import get_connection, init_db

    db_file = tmp_path / "interrupted.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages_old (
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
        CREATE TABLE messages (
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
        CREATE TABLE queue_jobs_old (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            agent_id INTEGER,
            job_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE queue_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
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
    conn.execute("INSERT INTO conversations (id, group_id, name) VALUES (1, 1, 'Geral')")
    conn.execute(
        "INSERT INTO messages_old (id, group_id, sender_type, content) VALUES (1, 1, 'user', 'oi')"
    )
    conn.execute(
        "INSERT INTO queue_jobs_old (id, group_id, agent_id, job_type, priority, payload) "
        "VALUES (1, 1, NULL, 'agent_turn', 1, '{}')"
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_connection()
    try:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conversations = conn.execute("SELECT * FROM conversations").fetchall()
        message = conn.execute("SELECT * FROM messages WHERE id = 1").fetchone()
        job = conn.execute("SELECT * FROM queue_jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert "messages_old" not in tables
    assert "queue_jobs_old" not in tables
    assert len(conversations) == 1  # no duplicate "Geral" created for the already-migrated group
    assert message["conversation_id"] == conversations[0]["id"]
    assert message["content"] == "oi"
    assert job["conversation_id"] == conversations[0]["id"]


def test_init_db_resumes_migration_when_messages_done_but_queue_jobs_not_started(tmp_path, monkeypatch):
    """Simulates a crash right after `messages` finished migrating (messages_old already
    dropped) but before `queue_jobs` was even touched (still on the old group_id schema, no
    queue_jobs_old yet). A top-level guard that only inspects `messages`'s own state (or the
    presence of *either* _old table) would wrongly conclude nothing is pending and permanently
    abandon the queue_jobs migration — this is exactly what a prior version of this migration
    got wrong."""
    from app.db import get_connection, init_db

    db_file = tmp_path / "asymmetric.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages (
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
    conn.execute("INSERT INTO conversations (id, group_id, name) VALUES (1, 1, 'Geral')")
    conn.execute(
        "INSERT INTO messages (id, conversation_id, sender_type, content) VALUES (1, 1, 'user', 'oi')"
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
        queue_jobs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(queue_jobs)")}
        conversations = conn.execute("SELECT * FROM conversations").fetchall()
        job = conn.execute("SELECT * FROM queue_jobs WHERE id = 1").fetchone()
    finally:
        conn.close()

    assert "group_id" not in queue_jobs_columns
    assert "conversation_id" in queue_jobs_columns
    assert len(conversations) == 1  # no duplicate "Geral" backfilled for the already-covered group
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
