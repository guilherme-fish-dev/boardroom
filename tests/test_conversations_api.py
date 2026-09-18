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


def test_stop_cancels_pending_jobs_and_leaves_processing(db):
    client = make_client(db)
    group = _create_group(client)
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('leo', 'p', 'm')"
        )
        agent_id = cur.lastrowid
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload) "
            "VALUES (?, ?, 'agent_turn', 1, '{}')",
            (conversation["id"], agent_id),
        )
        conn.execute(
            "INSERT INTO queue_jobs (conversation_id, agent_id, job_type, priority, payload, status) "
            "VALUES (?, ?, 'agent_turn', 1, '{}', 'processing')",
            (conversation["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.post(f"/api/groups/{group['id']}/conversations/{conversation['id']}/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"cancelled_pending": 1, "still_processing": 1}

    conn = get_connection()
    try:
        statuses = {
            row["status"]
            for row in conn.execute(
                "SELECT status FROM queue_jobs WHERE conversation_id = ?", (conversation["id"],)
            )
        }
        system_message = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND sender_type = 'system'",
            (conversation["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert statuses == {"error", "processing"}
    assert "1 resposta" in system_message["content"]


def test_stop_404_for_unknown_conversation(db):
    client = make_client(db)
    group = _create_group(client)

    resp = client.post(f"/api/groups/{group['id']}/conversations/9999/stop")
    assert resp.status_code == 404
