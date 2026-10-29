"""Application settings and benchmark source configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crypto, events
from ..benchmarks.registry import SOURCES
from ..config import get_config
from ..db import get_session
from ..orm import BenchmarkScore
from ..settings_store import AppSettings, BenchmarkSourceSettings, load_settings, save_settings
from .auth import is_authenticated

router = APIRouter(prefix="/api/settings", tags=["settings"])


class MetricOut(BaseModel):
    id: str
    label: str
    dimension: str
    unit: str
    weight: float
    description: str


class BenchmarkSourceOut(BaseModel):
    id: str
    name: str
    homepage: str
    description: str
    license: str
    attribution: str
    default_enabled: bool
    requires_key: bool
    key_label: str | None
    key_url: str | None
    terms_warning: str | None
    metrics: list[MetricOut]
    enabled: bool
    key_set: bool
    key_hint: str | None
    records: int
    matched: int
    fetched_at: str | None


class SandboxInfo(BaseModel):
    effective_mode: str
    sidecar_url_set: bool


class SettingsOut(BaseModel):
    settings: AppSettings
    sources: list[BenchmarkSourceOut]
    sandbox: SandboxInfo
    auth_enabled: bool
    secret_key_from_env: bool


class SettingsIn(BaseModel):
    settings: AppSettings
    benchmark_keys: dict[str, str | None] = Field(
        default_factory=dict, description="source id → new key; empty string clears, omitted keeps"
    )


def effective_sandbox_mode(settings: AppSettings) -> str:
    mode = settings.evals.sandbox_mode
    if mode == "auto":
        return "sidecar" if get_config().sandbox_url else "disabled"
    if mode == "sidecar" and not get_config().sandbox_url:
        return "disabled"
    return mode


def _out(session: Session, settings: AppSettings) -> SettingsOut:
    stats = {
        src: (total, matched, fetched)
        for src, total, matched, fetched in session.execute(
            select(
                BenchmarkScore.source,
                func.count(BenchmarkScore.id),
                func.count(BenchmarkScore.canonical_id),
                func.max(BenchmarkScore.fetched_at),
            ).group_by(BenchmarkScore.source)
        )
    }
    sources = []
    for sid, cls in SOURCES.items():
        spec = cls.spec
        src = settings.benchmark_source(sid, spec.default_enabled)
        key = crypto.decrypt(src.api_key_enc)
        total, matched, fetched = stats.get(sid, (0, 0, None))
        sources.append(
            BenchmarkSourceOut(
                id=sid,
                name=spec.name,
                homepage=spec.homepage,
                description=spec.description,
                license=spec.license,
                attribution=spec.attribution,
                default_enabled=spec.default_enabled,
                requires_key=spec.requires_key,
                key_label=spec.key_label,
                key_url=spec.key_url,
                terms_warning=spec.terms_warning,
                metrics=[MetricOut(**m.__dict__) for m in spec.metrics],
                enabled=src.enabled,
                key_set=bool(key),
                key_hint=crypto.mask(key),
                records=total,
                matched=matched,
                fetched_at=fetched.isoformat() if fetched else None,
            )
        )
    public = settings.model_copy(deep=True)
    for s in public.benchmarks.sources.values():
        s.api_key_enc = None
    public.subscriptions.claude_ingest_token_enc = None
    return SettingsOut(
        settings=public,
        sources=sources,
        sandbox=SandboxInfo(
            effective_mode=effective_sandbox_mode(settings), sidecar_url_set=bool(get_config().sandbox_url)
        ),
        auth_enabled=bool(get_config().password),
        secret_key_from_env=bool(get_config().secret_key),
    )


@router.get("", response_model=SettingsOut)
def get_settings(request: Request, session: Session = Depends(get_session)) -> SettingsOut:
    out = _out(session, load_settings(session))
    if not is_authenticated(request):
        # A visitor with no session sees which keys are set, never the end of one.
        for source in out.sources:
            source.key_hint = None
    return out


@router.put("", response_model=SettingsOut)
def put_settings(body: SettingsIn, session: Session = Depends(get_session)) -> SettingsOut:
    current = load_settings(session)
    new = body.settings.model_copy(deep=True)
    # Keys never round-trip through the client; carry stored ones over unless replaced.
    for sid in SOURCES:
        old = current.benchmarks.sources.get(sid)
        incoming = new.benchmarks.sources.get(sid) or BenchmarkSourceSettings(
            enabled=old.enabled if old else SOURCES[sid].spec.default_enabled
        )
        incoming.api_key_enc = old.api_key_enc if old else None
        if sid in body.benchmark_keys:
            value = body.benchmark_keys[sid]
            incoming.api_key_enc = crypto.encrypt(value.strip()) if value and value.strip() else None
        new.benchmarks.sources[sid] = incoming
    # The ingest token belongs to the subscription endpoints. The UI never sends it back, so taking it
    # from the body would wipe it and cut off the status line already configured with it.
    new.subscriptions = current.subscriptions.model_copy(deep=True)
    if not new.scoring.profiles:
        new.scoring.profiles = AppSettings().scoring.profiles
    if new.scoring.default_profile not in {p.id for p in new.scoring.profiles}:
        new.scoring.default_profile = new.scoring.profiles[0].id
    save_settings(session, new)
    session.flush()
    events.publish("settings.changed", {})
    from ..scheduler import reschedule

    reschedule(new)
    return _out(session, new)
