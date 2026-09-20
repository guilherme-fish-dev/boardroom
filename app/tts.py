import io
import os
import re
import threading
import time
import wave
from pathlib import Path

from piper import PiperVoice

_VOICE: PiperVoice | None = None
_VOICE_LOCK = threading.Lock()

# Piper sintetiza fonema por fonema sem noção de markdown — sem isso, ele lê os
# símbolos em voz alta ("asterisco, asterisco, negrito, asterisco, asterisco").
_MARKDOWN_PATTERNS = [
    (re.compile(r"^#{1,6}\s*", re.MULTILINE), ""),        # headings
    (re.compile(r"^\s*-{3,}\s*$", re.MULTILINE), ""),     # linhas horizontais (---)
    (re.compile(r"^\s*[-*]\s+", re.MULTILINE), ""),       # marcadores de lista
    (re.compile(r"`{1,3}([^`]*)`{1,3}"), r"\1"),          # código inline/bloco
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),              # **negrito**
    (re.compile(r"__([^_]+)__"), r"\1"),                  # __negrito__
    (re.compile(r"\*([^*]+)\*"), r"\1"),                  # *itálico*
    (re.compile(r"(?<!\w)_([^_]+)_(?!\w)"), r"\1"),       # _itálico_
]


def _strip_markdown(text: str) -> str:
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _voice_model_path() -> Path:
    return Path(
        os.environ.get("BOARDROOM_PIPER_VOICE_MODEL", "./data/piper-voices/pt_BR-faber-medium.onnx")
    )


def is_available() -> bool:
    return _voice_model_path().exists()


def _get_voice() -> PiperVoice:
    global _VOICE
    if _VOICE is None:
        with _VOICE_LOCK:
            if _VOICE is None:
                _VOICE = PiperVoice.load(str(_voice_model_path()))
    return _VOICE


CACHE_MAX_AGE_SECONDS = 60 * 60  # 1 hora


def purge_stale_cache(cache_dir: Path, max_age_seconds: int = CACHE_MAX_AGE_SECONDS) -> None:
    """Apaga áudios cacheados com mais de max_age_seconds. Sem isso, o cache cresce sem
    limite pra sempre — nada mais o esvazia (a exclusão por mensagem apagada em
    messages.py cobre só esse caso, não o de mensagens nunca apagadas)."""
    cutoff = time.time() - max_age_seconds
    for wav_path in cache_dir.glob("*.wav"):
        try:
            if wav_path.stat().st_mtime < cutoff:
                wav_path.unlink()
        except OSError:
            pass


def synthesize_wav(text: str) -> bytes:
    """Sintetiza texto em áudio WAV (bytes), usando a mesma voz carregada uma única vez.

    Chamável de threads concorrentes: onnxruntime.InferenceSession.run é thread-safe,
    então não há necessidade de lock além do carregamento inicial da voz acima."""
    voice = _get_voice()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(_strip_markdown(text), wav_file)
    return buffer.getvalue()
