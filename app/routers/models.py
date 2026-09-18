import sqlite3

from fastapi import APIRouter, HTTPException

from app.db import get_connection, get_setting
from app.llm_client import list_models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def get_models() -> dict:
    conn = get_connection()
    try:
        base_url = get_setting(conn, "llama_swap_base_url")
    finally:
        conn.close()

    try:
        models = list_models(base_url=base_url)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"não foi possível buscar modelos do llama-swap: {exc}",
        )

    return {"models": models}
