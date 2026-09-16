from fastapi.testclient import TestClient

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


def test_remove_member(db):
    client = make_client(db)
    agent = _create_agent(client)
    group = client.post("/api/groups", json={"name": "investidores"}).json()
    client.post(f"/api/groups/{group['id']}/members", json={"agent_id": agent["id"]})

    resp = client.delete(f"/api/groups/{group['id']}/members/{agent['id']}")
    assert resp.status_code == 204

    resp = client.get(f"/api/groups/{group['id']}/members")
    assert resp.json() == []
