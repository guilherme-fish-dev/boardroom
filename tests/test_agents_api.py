from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_create_and_list_agent(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "Você é um investidor conservador.",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "bob"
    assert created["vision_capable"] is False

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert names == ["bob"]


def test_create_agent_duplicate_name_rejected(db):
    client = make_client(db)
    payload = {
        "name": "bob",
        "persona_prompt": "x",
        "model_name": "qwen2.5-7b",
        "vision_capable": False,
    }
    assert client.post("/api/agents", json=payload).status_code == 201
    resp = client.post("/api/agents", json=payload)
    assert resp.status_code == 409


def test_update_agent(db):
    client = make_client(db)
    created = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.put(
        f"/api/agents/{created['id']}",
        json={
            "name": "bob",
            "persona_prompt": "novo prompt",
            "model_name": "llava-7b",
            "vision_capable": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["persona_prompt"] == "novo prompt"
    assert body["vision_capable"] is True


def test_update_agent_duplicate_name_rejected(db):
    client = make_client(db)
    client.post(
        "/api/agents",
        json={
            "name": "alice",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    bob = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.put(
        f"/api/agents/{bob['id']}",
        json={
            "name": "alice",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 409


def test_generate_persona_requires_assistant_model_configured(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": ""},
    )
    assert resp.status_code == 400


def test_generate_persona_returns_generated_text(db, monkeypatch):
    client = make_client(db)
    client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:8080",
            "default_vision_model": "",
            "max_pending_per_group": "20",
            "assistant_model": "qwen2.5-7b",
        },
    )

    captured = {}

    def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return "Você é um investidor cauteloso, avesso a risco, que pondera cada decisão."

    monkeypatch.setattr("app.routers.agents.chat_completion", fake_chat_completion)

    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": "bob"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "persona_prompt": "Você é um investidor cauteloso, avesso a risco, que pondera cada decisão."
    }
    assert captured["model"] == "qwen2.5-7b"
    assert captured["base_url"] == "http://localhost:8080"
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert "bob" in captured["messages"][1]["content"]
    assert "investidor cauteloso" in captured["messages"][1]["content"]


def test_generate_persona_returns_502_on_failure(db, monkeypatch):
    client = make_client(db)
    client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:8080",
            "default_vision_model": "",
            "max_pending_per_group": "20",
            "assistant_model": "qwen2.5-7b",
        },
    )

    def _raise(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.agents.chat_completion", _raise)

    resp = client.post(
        "/api/agents/generate-persona",
        json={"draft": "investidor cauteloso", "agent_name": ""},
    )

    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]


def test_delete_agent_removes_it(db):
    client = make_client(db)
    agent = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.delete(f"/api/agents/{agent['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/agents")
    assert resp.json() == []


def test_delete_agent_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/agents/9999")
    assert resp.status_code == 404


def test_delete_agent_cascades_group_membership_and_jobs(db):
    client = make_client(db)
    agent = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})
    conversation = client.get(f"/api/groups/{group['id']}/conversations").json()[0]

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

    resp = client.delete(f"/api/agents/{agent['id']}")
    assert resp.status_code == 204

    conn = get_connection()
    try:
        members = conn.execute(
            "SELECT * FROM group_members WHERE agent_id = ?", (agent["id"],)
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM queue_jobs WHERE agent_id = ?", (agent["id"],)
        ).fetchall()
    finally:
        conn.close()
    assert members == []
    assert jobs == []


def test_create_agent_named_all_rejected(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "all",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 422


def test_create_agent_named_all_rejected_case_insensitive(db):
    client = make_client(db)
    resp = client.post(
        "/api/agents",
        json={
            "name": "ALL",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 422


def test_update_agent_renamed_to_all_rejected(db):
    client = make_client(db)
    bob = client.post(
        "/api/agents",
        json={
            "name": "bob",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    ).json()

    resp = client.put(
        f"/api/agents/{bob['id']}",
        json={
            "name": "all",
            "persona_prompt": "x",
            "model_name": "qwen2.5-7b",
            "vision_capable": False,
        },
    )
    assert resp.status_code == 422
