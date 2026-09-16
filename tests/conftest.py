import pytest

from app.db import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("BOARDROOM_DB_PATH", str(db_file))
    init_db()
    yield db_file
