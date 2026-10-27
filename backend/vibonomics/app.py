"""FastAPI application: API routers, SPA hosting, startup/shutdown."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import __version__, events, sandbox_guard, scheduler
from .api import auth, benchmarks, evals, models, providers, refresh, settings, subscriptions, system
from .config import get_config
from .db import init_db, session_scope
from .evals import runner
from .http import close_client
from .jobs import KINDS, run_job, spawn
from .provider_service import seed_providers
from .settings_store import load_settings

log = logging.getLogger("vibonomics")


async def _startup_refresh() -> None:
    await asyncio.sleep(2)
    for kind in KINDS:
        await run_job(kind, "startup")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    events.bind_loop(asyncio.get_running_loop())
    await asyncio.to_thread(init_db)
    with session_scope() as s:
        seed_providers(s)
        app_settings = load_settings(s)
    runner.recover_interrupted()
    if not await sandbox_guard.refresh():
        spawn(sandbox_guard.keep_trying())
    scheduler.start(app_settings)
    if app_settings.refresh.refresh_on_startup:
        spawn(_startup_refresh())
    log.info("Vibonomics %s started", __version__)
    try:
        yield
    finally:
        scheduler.shutdown()
        await close_client()


def _route_path(request: Request) -> str:
    """The request path below this app's mount point.

    Under ``VIBONOMICS_URL_BASE`` this app is mounted, and Starlette keeps the prefix in the path (and
    in ``request.url``) while recording it in ``root_path``. Matching on the raw path would miss every
    ``/api`` route there.
    """
    path: str = request.scope["path"]
    root: str = request.scope.get("root_path", "")
    if root and path.startswith(f"{root}/"):
        return path[len(root) :]
    return path


def _api_app() -> FastAPI:
    app = FastAPI(
        title="Vibonomics",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if sandbox_guard.refuses(request.scope):
            return JSONResponse({"detail": "Requests from the sandbox network are refused"}, status_code=403)
        path = _route_path(request)
        open_path = path.startswith("/api/auth/") or path.startswith("/api/subscription/ingest/")
        if path.startswith("/api/") and not open_path and not auth.is_authenticated(request):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)

    for r in (
        system.router,
        auth.router,
        models.router,
        providers.router,
        settings.router,
        refresh.router,
        benchmarks.router,
        subscriptions.router,
        evals.router,
    ):
        app.include_router(r)

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    cfg = get_config()
    static_dir = cfg.static_dir
    index_cache: dict[str, str] = {}

    def index_html() -> str:
        if "html" not in index_cache:
            raw = (static_dir / "index.html").read_text(encoding="utf-8") if static_dir else ""
            index_cache["html"] = raw.replace("__URL_BASE__/", f"{cfg.url_base}/")
        return index_cache["html"]

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if static_dir is None:
            return HTMLResponse(
                "<h1>Vibonomics</h1><p>The web UI isn't built. Run <code>npm run build</code> in "
                "<code>frontend/</code> or use the Docker image. API docs: <a href='api/docs'>api/docs</a>.</p>"
            )
        if path:
            candidate = (static_dir / path).resolve()
            if candidate.is_file() and Path(static_dir.resolve()) in candidate.parents:
                headers = (
                    {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else None
                )
                return FileResponse(candidate, headers=headers)
        return HTMLResponse(index_html(), headers={"Cache-Control": "no-cache"})


def create_app() -> FastAPI:
    cfg = get_config()
    inner = _api_app()
    if not cfg.url_base:
        return inner
    outer = FastAPI(lifespan=inner.router.lifespan_context, docs_url=None, redoc_url=None, openapi_url=None)
    outer.mount(cfg.url_base, inner)
    return outer
