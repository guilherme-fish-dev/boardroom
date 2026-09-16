from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_get_settings_returns_defaults(db):
    client = make_client(db)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llama_swap_base_url"] == "http://localhost:8080"
    assert body["default_vision_model"] == ""
    assert body["max_pending_per_group"] == "20"


def test_update_settings(db):
    client = make_client(db)
    resp = client.put(
        "/api/settings",
        json={
            "llama_swap_base_url": "http://localhost:9090",
            "default_vision_model": "qwen2-vl-7b",
            "max_pending_per_group": "10",
        },
    )
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["default_vision_model"] == "qwen2-vl-7b"
