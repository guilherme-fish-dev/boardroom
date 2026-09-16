import asyncio

from fastapi import FastAPI

from app.db import init_db
from app.queue_worker import process_next_job
from app.routers import agents, groups, messages, settings

POLL_INTERVAL_SECONDS = 1.0


async def _worker_loop() -> None:
    while True:
        processed = await asyncio.to_thread(process_next_job)
        await asyncio.sleep(0.05 if processed else POLL_INTERVAL_SECONDS)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(settings.router)
    app.include_router(messages.router)

    @app.on_event("startup")
    async def _start_worker() -> None:
        app.state.worker_task = asyncio.create_task(_worker_loop())

    @app.on_event("shutdown")
    async def _stop_worker() -> None:
        app.state.worker_task.cancel()

    return app


app = create_app()
