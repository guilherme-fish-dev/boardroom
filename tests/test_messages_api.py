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


def test_messages_are_isolated_between_conversations_in_the_same_group(db):
    client = make_client(db)
    group, agent, geral = _setup_group_with_agent(client)
    outra = client.post(f"/api/groups/{group['id']}/conversations", json={"name": "outra"}).json()

    client.post(f"/api/conversations/{geral['id']}/messages", json={"content": "mensagem na geral"})
    client.post(f"/api/conversations/{outra['id']}/messages", json={"content": "mensagem na outra"})

    geral_contents = [m["content"] for m in client.get(f"/api/conversations/{geral['id']}/messages").json()]
    outra_contents = [m["content"] for m in client.get(f"/api/conversations/{outra['id']}/messages").json()]

    assert geral_contents == ["mensagem na geral"]
    assert outra_contents == ["mensagem na outra"]


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
