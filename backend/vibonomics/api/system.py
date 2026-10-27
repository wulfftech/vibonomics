"""Health, status and the live event stream."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import __version__, events
from ..config import get_config
from ..db import get_session
from ..jobs import KINDS, is_running
from ..settings_store import load_settings
from .settings import effective_sandbox_mode

router = APIRouter(tags=["system"])


@router.get("/ping", response_class=PlainTextResponse, include_in_schema=False)
def ping() -> str:
    return "pong"


class SystemStatus(BaseModel):
    version: str
    instance_name: str
    url_base: str
    sandbox_mode: str
    auth_enabled: bool
    running_jobs: list[str]


@router.get("/api/system/status", response_model=SystemStatus)
def system_status(session: Session = Depends(get_session)) -> SystemStatus:
    settings = load_settings(session)
    cfg = get_config()
    return SystemStatus(
        version=__version__,
        instance_name=settings.general.instance_name,
        url_base=cfg.url_base,
        sandbox_mode=effective_sandbox_mode(settings),
        auth_enabled=bool(cfg.password),
        running_jobs=[k for k in KINDS if is_running(k)],
    )


@router.get("/api/events", include_in_schema=False)
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
