import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    persona_prompt TEXT NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL,
    vision_capable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    icon TEXT NOT NULL DEFAULT '💬',
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
    pdf_path TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    hidden_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image','extract_pdf')),
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
    "max_history_messages": "40",
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


def _ensure_group_icon_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)")}
    if "icon" not in columns:
        conn.execute("ALTER TABLE groups ADD COLUMN icon TEXT NOT NULL DEFAULT '💬'")


def _ensure_agent_subtitle_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(agents)")}
    if "subtitle" not in columns:
        conn.execute("ALTER TABLE agents ADD COLUMN subtitle TEXT NOT NULL DEFAULT ''")


def _ensure_pdf_path_column(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "pdf_path" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN pdf_path TEXT")


def _ensure_conversations_stopped_at_column(conn: sqlite3.Connection) -> None:
    """stopped_at is the durable record that the user clicked "stop": unlike the queue_jobs
    status flip (which only touches rows that already exist at click time), this survives the
    gap between clicking stop and an in-flight LLM call finishing. That in-flight reply's own
    @mentions would otherwise re-enqueue new jobs — a chain of agents mentioning each other
    that a one-time bulk UPDATE can never catch, because those jobs don't exist yet when stop
    runs. enqueue_mentions checks this column before inserting anything; a new user message
    clears it again, since that's the signal that the user wants the conversation to continue."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "stopped_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN stopped_at TEXT")


def _ensure_queue_jobs_allows_extract_pdf(conn: sqlite3.Connection) -> None:
    """Adds 'extract_pdf' to the queue_jobs.job_type CHECK constraint by rebuilding the
    table (SQLite has no ALTER TABLE support for changing a CHECK constraint in place).

    This migration is resumable rather than atomic: `ALTER TABLE ... RENAME TO` commits
    immediately in SQLite even inside an explicit transaction (verified empirically — it is
    not something an app-level BEGIN/COMMIT can prevent), so a process crash between any two
    statements here cannot be rolled back. It uses its own exclusive temp table name
    (`queue_jobs_pdf_migration_old`) rather than the generic `queue_jobs_old` name used by
    `_migrate_queue_jobs_to_conversation_id` elsewhere in this file — reusing that name would
    let a crash here be picked up by the other migration's leftover-table check (or vice
    versa) and processed against the wrong assumptions (e.g. joining on a `group_id` column
    that no longer exists), corrupting or orphaning data. Every step below re-checks its own
    progress: the RENAME only runs if the temp table isn't already there from an earlier
    interrupted attempt, `queue_jobs` is only recreated if missing, and the INSERT uses
    OR IGNORE so rows already copied before a prior crash aren't duplicated. Re-running
    `init_db()` after a crash always finishes the migration without losing or duplicating
    any row, regardless of which statement it died on."""
    temp_table = "queue_jobs_pdf_migration_old"

    if temp_table not in _existing_tables(conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='queue_jobs'"
        ).fetchone()
        if row is None or "extract_pdf" in row["sql"]:
            return
        conn.execute(f"ALTER TABLE queue_jobs RENAME TO {temp_table}")

    if "queue_jobs" not in _existing_tables(conn):
        conn.execute(
            "CREATE TABLE queue_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
            "agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,"
            "job_type TEXT NOT NULL CHECK (job_type IN ('agent_turn','describe_image','extract_pdf')),"
            "priority INTEGER NOT NULL,"
            "payload TEXT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),"
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )

    conn.execute(
        f"INSERT OR IGNORE INTO queue_jobs SELECT id, conversation_id, agent_id, job_type, priority, "
        f"payload, status, created_at FROM {temp_table}"
    )
    conn.execute(f"DROP TABLE {temp_table}")


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _migrate_messages_to_conversation_id(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "group_id" in columns:
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

    if "messages_old" in _existing_tables(conn):
        conn.execute(
            "INSERT OR IGNORE INTO messages (id, conversation_id, sender_type, sender_id, "
            "content, image_path, hidden, hidden_kind, created_at) "
            "SELECT m.id, c.id, m.sender_type, m.sender_id, m.content, m.image_path, "
            "m.hidden, m.hidden_kind, m.created_at "
            "FROM messages_old m JOIN conversations c ON c.group_id = m.group_id AND c.name = 'Geral'"
        )
        conn.execute("DROP TABLE messages_old")


def _migrate_queue_jobs_to_conversation_id(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(queue_jobs)")}
    if "group_id" in columns:
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

    if "queue_jobs_old" in _existing_tables(conn):
        conn.execute(
            "INSERT OR IGNORE INTO queue_jobs (id, conversation_id, agent_id, job_type, "
            "priority, payload, status, created_at) "
            "SELECT j.id, c.id, j.agent_id, j.job_type, j.priority, j.payload, j.status, j.created_at "
            "FROM queue_jobs_old j JOIN conversations c ON c.group_id = j.group_id AND c.name = 'Geral'"
        )
        conn.execute("DROP TABLE queue_jobs_old")


def _ensure_conversations_table(conn: sqlite3.Connection) -> None:
    """Migrate a pre-conversations database: create one 'Geral' conversation per existing
    group and move messages/queue_jobs from group_id to conversation_id. No-op on a fresh
    install (SCHEMA already creates messages/queue_jobs with conversation_id, so the
    `group_id` column never exists) and no-op on an already-migrated database.

    This migration is resumable rather than atomic: `ALTER TABLE ... RENAME TO` commits
    immediately in SQLite even inside an explicit transaction (verified empirically — it is
    not something an app-level BEGIN/COMMIT can prevent), so a process crash between any two
    statements here cannot be rolled back. There is deliberately no top-level "is anything
    pending?" short-circuit: `messages` and `queue_jobs` migrate independently and can be left
    in different states by a crash (e.g. `messages` fully migrated while `queue_jobs` hasn't
    even started), so every step below re-checks its own progress and runs unconditionally,
    relying on each one being individually idempotent. Re-running `init_db()` after a crash
    always finishes the migration without losing or duplicating any row, regardless of which
    statement it died on."""
    # Idempotent: only backfills a 'Geral' conversation for a group that doesn't have one yet,
    # so re-running this after a crash never creates a duplicate.
    conn.execute(
        "INSERT INTO conversations (group_id, name) "
        "SELECT id, 'Geral' FROM groups WHERE id NOT IN (SELECT group_id FROM conversations)"
    )
    _migrate_messages_to_conversation_id(conn)
    _migrate_queue_jobs_to_conversation_id(conn)


def _recover_orphaned_processing_jobs(conn: sqlite3.Connection) -> None:
    """A job only sits in 'processing' while some process's worker loop is actively
    running it (process_next_job marks it 'processing' right before calling the LLM,
    and always moves it to 'done' or 'error' in a finally-equivalent try/except once
    that call returns). If one is still 'processing' at startup, the process that was
    running it is gone — killed or crashed — without a chance to record the outcome,
    so it would otherwise sit "processing" forever, permanently stuck behind the
    conversation's pending-status indicator (GET .../messages/pending) with no way to
    resolve on its own. Reset it to 'pending' so the new process's worker loop retries
    it, and note the interruption in the conversation so it isn't a silent retry."""
    stuck = conn.execute("SELECT * FROM queue_jobs WHERE status = 'processing'").fetchall()
    for job in stuck:
        conn.execute("UPDATE queue_jobs SET status = 'pending' WHERE id = ?", (job["id"],))
        conn.execute(
            "INSERT INTO messages (conversation_id, sender_type, content) VALUES (?, 'system', ?)",
            (
                job["conversation_id"],
                "O servidor foi reiniciado enquanto uma resposta estava sendo gerada — tentando de novo.",
            ),
        )


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _ensure_hidden_kind_column(conn)
        _ensure_group_icon_column(conn)
        _ensure_agent_subtitle_column(conn)
        _ensure_conversations_table(conn)
        _ensure_pdf_path_column(conn)
        _ensure_conversations_stopped_at_column(conn)
        _ensure_queue_jobs_allows_extract_pdf(conn)
        _recover_orphaned_processing_jobs(conn)
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
