import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.db import init_db
from app.queue_worker import process_next_job
from app.routers import agents, conversations, groups, messages, models, settings, tts

logger = logging.getLogger(__name__)


class NoCacheStaticFiles(StaticFiles):
    """Force revalidation on every request instead of letting browsers cache
    HTML/CSS/JS indefinitely, which otherwise hides frontend edits behind a
    stale cache during local development."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


POLL_INTERVAL_SECONDS = 1.0


async def _worker_loop() -> None:
    while True:
        try:
            processed = await asyncio.to_thread(process_next_job)
        except Exception:
            logger.exception("worker loop iteration failed")
            processed = False
        await asyncio.sleep(0.05 if processed else POLL_INTERVAL_SECONDS)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(conversations.router)
    app.include_router(settings.router)
    app.include_router(messages.router)
    app.include_router(models.router)
    app.include_router(tts.router)

    @app.on_event("startup")
    async def _start_worker() -> None:
        app.state.worker_task = asyncio.create_task(_worker_loop())

    @app.on_event("shutdown")
    async def _stop_worker() -> None:
        app.state.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.worker_task

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", NoCacheStaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
