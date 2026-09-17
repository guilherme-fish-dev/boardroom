from fastapi.testclient import TestClient

from app.main import create_app


def make_client(db):
    return TestClient(create_app())


def test_get_models_returns_list(db, monkeypatch):
    monkeypatch.setattr(
        "app.routers.models.list_models",
        lambda **kwargs: ["qwen2.5-7b", "llava-7b"],
    )
    client = make_client(db)

    resp = client.get("/api/models")

    assert resp.status_code == 200
    assert resp.json() == {"models": ["qwen2.5-7b", "llava-7b"]}


def test_get_models_returns_502_on_failure(db, monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.routers.models.list_models", _raise)
    client = make_client(db)

    resp = client.get("/api/models")

    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]
