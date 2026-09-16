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
