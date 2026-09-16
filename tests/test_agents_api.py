from fastapi.testclient import TestClient

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
