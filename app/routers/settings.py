from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_connection

router = APIRouter(prefix="/api/settings", tags=["settings"])


class Settings(BaseModel):
    llama_swap_base_url: str
    default_vision_model: str
    max_pending_per_group: str


@router.get("", response_model=Settings)
def get_settings() -> Settings:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    values = {r["key"]: r["value"] for r in rows}
    return Settings(**values)


@router.put("", response_model=Settings)
def update_settings(settings: Settings) -> Settings:
    conn = get_connection()
    try:
        for key, value in settings.model_dump().items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
    return get_settings()
