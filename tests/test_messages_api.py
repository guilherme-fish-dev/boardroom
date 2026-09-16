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
