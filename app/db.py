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
    hidden_kind TEXT,
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


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""
