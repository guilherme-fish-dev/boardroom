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
