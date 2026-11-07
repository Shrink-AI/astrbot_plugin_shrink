from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import growth, media, memory, people, runs, skills, system, teach
from .auth import install_auth


def create_app(
    application,
    static_dir: Path,
    providers,
    *,
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1:6186", "localhost:6186"}),
):
    app = FastAPI(title="石英", docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app, application.state, application.sessions, allowed_hosts)
    app.include_router(
        system.router_for(
            application.state, application.status, application.learning, providers,
            application.digest, application.consolidation, application.teach,
        )
    )
    app.include_router(
        growth.router_for(
            application.persona, application.learning, application.tasks, application.state,
            application.expressions,
        )
    )
    app.include_router(memory.router_for(application))
    app.include_router(people.router_for(application))
    app.include_router(runs.router_for(application))
    app.include_router(teach.router_for(application))
    app.include_router(skills.router_for(application))
    app.include_router(media.router_for(application.memes))

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    return app
