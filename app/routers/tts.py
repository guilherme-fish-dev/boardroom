from fastapi import APIRouter
from pydantic import BaseModel

from app.tts import is_available

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TtsStatusOut(BaseModel):
    available: bool


@router.get("/status", response_model=TtsStatusOut)
def get_tts_status() -> TtsStatusOut:
    return TtsStatusOut(available=is_available())
