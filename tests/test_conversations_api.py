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
