from app.db import get_connection
from app.main import create_app
from fastapi.testclient import TestClient


def make_client(db):
    return TestClient(create_app())


def test_create_and_list_agent_category(db):
    client = make_client(db)
    resp = client.post("/api/agent-categories", json={"name": "financeiro"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "financeiro"
    assert body["agent_count"] == 0

    resp = client.get("/api/agent-categories")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["financeiro"]


def test_create_agent_category_duplicate_name_rejected(db):
    client = make_client(db)
    client.post("/api/agent-categories", json={"name": "financeiro"})
    resp = client.post("/api/agent-categories", json={"name": "financeiro"})
    assert resp.status_code == 409


def test_list_agent_categories_includes_agent_count(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('bob', 'x', 'm')"
        )
        agent_id = conn.execute("SELECT id FROM agents WHERE name = 'bob'").fetchone()["id"]
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/agent-categories")
    assert resp.json()[0]["agent_count"] == 1


def test_delete_agent_category_removes_it(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.delete(f"/api/agent-categories/{category['id']}")
    assert resp.status_code == 204

    resp = client.get("/api/agent-categories")
    assert resp.json() == []


def test_delete_agent_category_returns_404_for_unknown_id(db):
    client = make_client(db)
    resp = client.delete("/api/agent-categories/9999")
    assert resp.status_code == 404


def test_delete_agent_category_cascades_members(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO agents (name, persona_prompt, model_name) VALUES ('bob', 'x', 'm')"
        )
        agent_id = conn.execute("SELECT id FROM agents WHERE name = 'bob'").fetchone()["id"]
        conn.execute(
            "INSERT INTO agent_category_members (category_id, agent_id) VALUES (?, ?)",
            (category["id"], agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.delete(f"/api/agent-categories/{category['id']}")
    assert resp.status_code == 204

    conn = get_connection()
    try:
        members = conn.execute("SELECT * FROM agent_category_members").fetchall()
        agents = conn.execute("SELECT * FROM agents").fetchall()
    finally:
        conn.close()
    assert members == []
    assert len(agents) == 1


def _create_agent(client, name="bob"):
    return client.post(
        "/api/agents",
        json={
            "name": name,
            "persona_prompt": "x",
            "model_name": "m",
            "vision_capable": False,
        },
    ).json()


def test_list_agent_category_members_empty(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    resp = client.get("/api/agent-categories/9999/members")
    assert resp.status_code == 404


def test_add_agent_category_members_bulk(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")
    alice = _create_agent(client, "alice")

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add",
        json={"agent_ids": [bob["id"], alice["id"]]},
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert sorted(m["name"] for m in resp.json()) == ["alice", "bob"]


def test_add_agent_category_members_is_idempotent(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")

    client.post(f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [bob["id"]]})
    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert [m["name"] for m in resp.json()] == ["bob"]


def test_add_agent_category_members_invalid_agent_id_returns_400(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/add", json={"agent_ids": [9999]}
    )
    assert resp.status_code == 400

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert resp.json() == []


def test_add_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    bob = _create_agent(client, "bob")
    resp = client.post("/api/agent-categories/9999/members/add", json={"agent_ids": [bob["id"]]})
    assert resp.status_code == 404


def test_remove_agent_category_members_bulk(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")
    alice = _create_agent(client, "alice")
    client.post(
        f"/api/agent-categories/{category['id']}/members/add",
        json={"agent_ids": [bob["id"], alice["id"]]},
    )

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/remove", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204

    resp = client.get(f"/api/agent-categories/{category['id']}/members")
    assert [m["name"] for m in resp.json()] == ["alice"]


def test_remove_agent_category_members_is_idempotent_for_non_member(db):
    client = make_client(db)
    category = client.post("/api/agent-categories", json={"name": "financeiro"}).json()
    bob = _create_agent(client, "bob")

    resp = client.post(
        f"/api/agent-categories/{category['id']}/members/remove", json={"agent_ids": [bob["id"]]}
    )
    assert resp.status_code == 204


def test_remove_agent_category_members_returns_404_for_unknown_category(db):
    client = make_client(db)
    bob = _create_agent(client, "bob")
    resp = client.post("/api/agent-categories/9999/members/remove", json={"agent_ids": [bob["id"]]})
    assert resp.status_code == 404
