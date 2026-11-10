from pathlib import Path
import json
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import growth, media, memory, operations, people, prompts, runs, skills, system, teach
from .auth import install_auth


def create_app(
    application,
    static_dir: Path,
    providers,
    *,
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1:6186", "localhost:6186"}),
):
    origins = {"http://" + host for host in allowed_hosts}
    config = application.state.root / "dashboard.json"
    if config.exists():
        public_origin = json.loads(config.read_text(encoding="utf-8-sig")).get("public_origin", "")
        if public_origin:
            parsed = urlsplit(public_origin)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                raise ValueError("dashboard public_origin 必须为 HTTP 或 HTTPS 站点地址")
            allowed_hosts = allowed_hosts | {parsed.netloc}
            origins.add(parsed.scheme + "://" + parsed.netloc)
    app = FastAPI(title="石英", docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app, application.state, application.sessions, allowed_hosts, origins)
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
    app.include_router(prompts.router_for(application))
    app.include_router(media.router_for(application.memes))
    if getattr(application, "operations", None):
        app.include_router(operations.router_for(application))

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
