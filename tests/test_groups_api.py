from fastapi.testclient import TestClient

from app.db import get_connection
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


def test_add_member_with_nonexistent_agent_returns_404(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.post(f"/api/groups/{group['id']}/members", json={"agent_id": 9999})
    assert resp.status_code == 404


def test_remove_member(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})

    resp = client.delete(f"/api/groups/{group['id']}/members/{agent['id']}")
    assert resp.status_code == 204

    resp = client.get(f"/api/groups/{group['id']}/members")
    assert resp.json() == []


def test_update_group_renames_it(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.put(f"/api/groups/{group['id']}", json={"name": "financas"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "financas"

    resp = client.get("/api/groups")
    assert [g["name"] for g in resp.json()] == ["financas"]


def test_update_group_duplicate_name_rejected(db):
    client = make_client(db)
    client.post("/api/groups", json={"name": "investidores"})
    produto = client.post("/api/groups", json={"name": "produto"}).json()

    resp = client.put(f"/api/groups/{produto['id']}", json={"name": "investidores"})
    assert resp.status_code == 409


def test_update_group_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.put("/api/groups/9999", json={"name": "novo-nome"})
    assert resp.status_code == 404


def test_create_group_creates_default_conversation(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.get(f"/api/groups/{group['id']}/conversations")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["Geral"]


def test_delete_group_removes_it(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.delete(f"/api/groups/{group['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/groups")
    assert resp.json() == []


def test_delete_group_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/groups/9999")
    assert resp.status_code == 404


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


def test_create_group_with_custom_icon(db):
    client = make_client(db)
    resp = client.post("/api/groups", json={"name": "investidores", "icon": "📈"})
    assert resp.status_code == 201
    assert resp.json()["icon"] == "📈"


def test_create_group_without_icon_defaults_to_speech_bubble(db):
    client = make_client(db)
    resp = client.post("/api/groups", json={"name": "investidores"})
    assert resp.status_code == 201
    assert resp.json()["icon"] == "💬"


def test_update_group_changes_icon(db):
    client = make_client(db)
    group = client.post("/api/groups", json={"name": "investidores"}).json()

    resp = client.put(f"/api/groups/{group['id']}", json={"name": "investidores", "icon": "💰"})
    assert resp.status_code == 200
    assert resp.json()["icon"] == "💰"
