from fastapi import FastAPI

from app.db import init_db
from app.routers import agents, groups, messages, settings


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Boardroom")
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(settings.router)
    app.include_router(messages.router)
    return app


app = create_app()
