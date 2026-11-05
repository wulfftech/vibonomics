"""Provider configuration (keys), connection tests and balances."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto, events
from ..catalog.modelsdev import load_catalog
from ..db import get_session, session_scope
from ..http import FetchError, public_url
from ..jobs import is_running, run_job, spawn
from ..orm import Balance, ProviderConfig
from ..provider_service import env_locked, resolve, resolve_draft
from ..providers.base import FetchContext, ResolvedProvider
from ..providers.registry import all_specs, get_adapter_class
from ..settings_store import load_settings
from .auth import is_authenticated

router = APIRouter(prefix="/api/providers", tags=["providers"])


class OptionFieldOut(BaseModel):
    key: str
    label: str
    description: str
    required: bool
    placeholder: str


class ProviderTypeOut(BaseModel):
    type: str
    name: str
    description: str
    homepage: str
    keys_url: str | None
    billing_url: str | None
    default_base_url: str
    balance: str
    balance_note: str
    mgmt_key_label: str | None
    mgmt_key_help: str | None
    models_require_key: bool
    custom: bool
    options: list[OptionFieldOut]
    env_key: str
    env_mgmt_key: str
    extra_key_label: str | None = None
    extra_key_help: str | None = None
    extra_key_env: str | None = None


class BalanceOut(BaseModel):
    status: str
    fetched_at: datetime | None = None
    account_balance: float | None = None
    currency: str = "USD"
    account_total: float | None = None
    account_used: float | None = None
    key_usage: float | None = None
    key_limit: float | None = None
    key_remaining: float | None = None
    key_label: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderOut(BaseModel):
    id: int
    type: str
    name: str
    enabled: bool
    base_url: str | None
    options: dict[str, Any]
    api_key_set: bool
    api_key_hint: str | None
    api_key_locked: bool
    mgmt_key_set: bool
    mgmt_key_hint: str | None
    mgmt_key_locked: bool
    extra_key_set: bool = False
    extra_key_hint: str | None = None
    extra_key_locked: bool = False
    last_refresh_at: datetime | None
    last_refresh_status: str | None
    last_refresh_message: str | None
    model_count: int
    balance: BalanceOut | None
    billing_url: str | None
    balance_support: str
    balance_note: str


class ProviderIn(BaseModel):
    type: str
    name: str | None = None
    enabled: bool = True
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(None, description="null keeps the stored key, empty string clears it")
    mgmt_key: str | None = None
    extra_key: str | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    base_url: str | None = None
    options: dict[str, Any] | None = None
    api_key: str | None = None
    mgmt_key: str | None = None
    extra_key: str | None = None


class TestResult(BaseModel):
    ok: bool
    message: str


class ProviderTestOverride(BaseModel):
    """Values from an unsaved form; only the fields present replace the stored ones (``options`` as a whole)."""

    api_key: str | None = None
    mgmt_key: str | None = None
    extra_key: str | None = None
    base_url: str | None = None
    options: dict[str, Any] | None = None


class ProviderRefreshIn(BaseModel):
    """Providers to refresh. A card that shows two providers sends both in one request."""

    provider_ids: list[int] = Field(min_length=1)


@router.get("/types", response_model=list[ProviderTypeOut])
def provider_types() -> list[ProviderTypeOut]:
    return [
        ProviderTypeOut(
            type=s.type,
            name=s.name,
            description=s.description,
            homepage=s.homepage,
            keys_url=s.keys_url,
            billing_url=s.billing_url,
            default_base_url=s.default_base_url,
            balance=s.balance,
            balance_note=s.balance_note,
            mgmt_key_label=s.mgmt_key_label,
            mgmt_key_help=s.mgmt_key_help,
            models_require_key=s.models_require_key,
            custom=s.custom,
            options=[OptionFieldOut(**o.__dict__) for o in s.options],
            env_key=s.env_key,
            env_mgmt_key=s.env_mgmt_key,
            extra_key_label=s.extra_key_label,
            extra_key_help=s.extra_key_help,
            extra_key_env=s.env_extra_key if s.extra_key_label else None,
        )
        for s in sorted(all_specs(), key=lambda s: (s.custom, s.name.lower()))
    ]


def _balance_out(b: Balance | None) -> BalanceOut | None:
    if b is None:
        return None
    return BalanceOut.model_validate(b, from_attributes=True)


def _provider_out(p: ProviderConfig, session: Session) -> ProviderOut:
    resolved = resolve(p)
    key_locked, mgmt_locked, extra_locked = env_locked(p.type)
    cls = get_adapter_class(p.type)
    return ProviderOut(
        id=p.id,
        type=p.type,
        name=p.name,
        enabled=p.enabled,
        base_url=p.base_url,
        options=dict(p.options or {}),
        api_key_set=bool(resolved.api_key),
        api_key_hint=crypto.mask(resolved.api_key),
        api_key_locked=key_locked,
        mgmt_key_set=bool(resolved.mgmt_key),
        mgmt_key_hint=crypto.mask(resolved.mgmt_key),
        mgmt_key_locked=mgmt_locked,
        extra_key_set=bool(resolved.extra_key),
        extra_key_hint=crypto.mask(resolved.extra_key),
        extra_key_locked=extra_locked,
        last_refresh_at=p.last_refresh_at,
        last_refresh_status=p.last_refresh_status,
        last_refresh_message=p.last_refresh_message,
        model_count=sum(1 for m in p.models if m.active),
        balance=_balance_out(session.get(Balance, p.id)),
        billing_url=cls.spec.billing_url if cls else None,
        balance_support=cls.spec.balance if cls else "none",
        balance_note=cls.spec.balance_note if cls else "",
    )


@router.get("", response_model=list[ProviderOut])
def list_providers(request: Request, session: Session = Depends(get_session)) -> list[ProviderOut]:
    providers = session.scalars(select(ProviderConfig).order_by(ProviderConfig.name)).all()
    out = [_provider_out(p, session) for p in providers]
    if not is_authenticated(request):
        # A visitor with no session sees which keys are set, never the end of one, and a base URL without the
        # user:password@ or ?token= a gateway may need. A key's label is withheld too: OpenRouter sends it as a
        # masked copy of the key, which is the same end the hints hide.
        out = [
            p.model_copy(
                update={
                    "api_key_hint": None,
                    "mgmt_key_hint": None,
                    "extra_key_hint": None,
                    "base_url": public_url(p.base_url) if p.base_url else p.base_url,
                    "balance": p.balance.model_copy(update={"key_label": None}) if p.balance else None,
                }
            )
            for p in out
        ]
    return out


def _apply_secret(current: str | None, new_value: str | None) -> str | None:
    if new_value is None:
        return current
    return crypto.encrypt(new_value.strip()) if new_value.strip() else None


@router.post("", response_model=ProviderOut, status_code=201)
def create_provider(body: ProviderIn, session: Session = Depends(get_session)) -> ProviderOut:
    cls = get_adapter_class(body.type)
    if cls is None:
        raise HTTPException(400, f"Unknown provider type '{body.type}'")
    if cls.spec.custom and not body.base_url:
        raise HTTPException(400, "A base URL is required for custom providers")
    p = ProviderConfig(
        type=body.type,
        name=(body.name or cls.spec.name).strip(),
        enabled=body.enabled,
        base_url=(body.base_url or "").strip() or None,
        options=body.options,
        api_key_enc=_apply_secret(None, body.api_key),
        mgmt_key_enc=_apply_secret(None, body.mgmt_key),
        extra_key_enc=_apply_secret(None, body.extra_key),
    )
    session.add(p)
    session.commit()  # before the event: a client that refetches on it must find the provider
    events.publish("providers.changed", {"id": p.id})
    return _provider_out(p, session)


@router.put("/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: int, body: ProviderUpdate, session: Session = Depends(get_session)) -> ProviderOut:
    p = session.get(ProviderConfig, provider_id)
    if p is None:
        raise HTTPException(404, "Provider not found")
    if body.name is not None:
        p.name = body.name.strip() or p.name
    if body.enabled is not None:
        p.enabled = body.enabled
    if body.base_url is not None:
        p.base_url = body.base_url.strip() or None
    if body.options is not None:
        p.options = body.options
    p.api_key_enc = _apply_secret(p.api_key_enc, body.api_key)
    p.mgmt_key_enc = _apply_secret(p.mgmt_key_enc, body.mgmt_key)
    p.extra_key_enc = _apply_secret(p.extra_key_enc, body.extra_key)
    session.commit()
    events.publish("providers.changed", {"id": p.id})
    return _provider_out(p, session)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(provider_id: int, session: Session = Depends(get_session)) -> None:
    p = session.get(ProviderConfig, provider_id)
    if p is None:
        raise HTTPException(404, "Provider not found")
    session.delete(p)
    session.commit()
    events.publish("providers.changed", {"id": provider_id})


@router.post("/test", response_model=TestResult)
async def test_provider_draft(body: ProviderIn) -> TestResult:
    """Test the values currently in the add-provider form, before anything is saved."""
    cls = get_adapter_class(body.type)
    if cls is None:
        raise HTTPException(400, f"Unknown provider type '{body.type}'")
    if cls.spec.custom and not (body.base_url or "").strip():
        raise HTTPException(400, "A base URL is required for custom providers")
    draft = resolve_draft(
        body.type,
        (body.name or cls.spec.name).strip(),
        api_key=body.api_key,
        mgmt_key=body.mgmt_key,
        extra_key=body.extra_key,
        base_url=body.base_url,
        options=body.options,
    )
    with session_scope() as session:
        settings = load_settings(session)
    return await _run_test(draft, settings)


@router.post("/{provider_id}/test", response_model=TestResult)
async def test_provider(provider_id: int, body: ProviderTestOverride | None = None) -> TestResult:
    """Test the saved configuration, optionally overriding fields from the open form."""
    with session_scope() as session:
        p = session.get(ProviderConfig, provider_id)
        if p is None:
            raise HTTPException(404, "Provider not found")
        settings = load_settings(session)
    resolved = resolve(p)
    withheld = _withhold_stored_keys(resolved, body)
    result = await _run_test(_override(resolved, body), settings)
    if withheld:
        result.message += " The saved key was not sent to a different host; enter the key in the form to test it there."
    return result


def _origin(url: str | None, default: str = "") -> tuple[str, str]:
    parts = urlsplit((url or "").strip() or default)
    return parts.scheme.lower(), parts.netloc.lower()


def _withhold_stored_keys(cfg: ResolvedProvider, body: ProviderTestOverride | None) -> bool:
    """Drop the saved keys when the test points at another host, unless the request brings its own.

    The test route is open to anything that can reach the API. Without this, one request naming a base URL of
    its choosing would be answered with the saved (or environment-locked) key in an Authorization header.
    """
    if body is None or body.base_url is None:
        return False
    adapter_cls = get_adapter_class(cfg.type)
    default = adapter_cls.spec.default_base_url if adapter_cls is not None else ""
    if _origin(body.base_url, default) == _origin(cfg.base_url, default):
        return False
    withheld = False
    for field in ("api_key", "mgmt_key", "extra_key"):
        if getattr(body, field) is None and getattr(cfg, field):
            setattr(cfg, field, None)
            withheld = True
    return withheld


def _override(cfg: ResolvedProvider, body: ProviderTestOverride | None) -> ResolvedProvider:
    if body is None:
        return cfg
    if body.api_key is not None:
        cfg.api_key = body.api_key.strip() or None
    if body.mgmt_key is not None:
        cfg.mgmt_key = body.mgmt_key.strip() or None
    if body.extra_key is not None:
        cfg.extra_key = body.extra_key.strip() or None
    if body.base_url is not None:
        cfg.base_url = body.base_url.strip() or None
    if body.options is not None:
        # Replace, as update_provider does: Save stores exactly this, so a cleared option must test as cleared.
        cfg.options = dict(body.options)
    return cfg


async def _run_test(resolved: ResolvedProvider, settings: Any) -> TestResult:
    adapter_cls = get_adapter_class(resolved.type)
    if adapter_cls is None:
        raise HTTPException(400, "Unknown provider type")
    catalog = await load_catalog()
    adapter = adapter_cls(resolved, FetchContext(catalog=catalog, settings=settings))
    try:
        message = await asyncio.wait_for(adapter.test_connection(), 45)
        return TestResult(ok=True, message=message)
    except TimeoutError:
        return TestResult(ok=False, message="Timed out after 45 seconds")
    except FetchError as exc:
        return TestResult(ok=False, message=str(exc) or "The provider rejected the request")
    except Exception as exc:  # a bad base URL or unexpected payload should still explain itself
        return TestResult(ok=False, message=f"{type(exc).__name__}: {exc}"[:500])


@router.post("/refresh", status_code=202)
async def refresh_providers(body: ProviderRefreshIn, session: Session = Depends(get_session)) -> dict[str, str]:
    """Refresh the given providers' models and balances, leaving the others alone.

    One request for the whole set keeps it one run. A run already in progress does not drop the call: the
    providers wait for it and are refreshed together afterwards, and the answer says ``queued``.
    """
    ids = set(body.provider_ids)
    known = set(session.scalars(select(ProviderConfig.id).where(ProviderConfig.id.in_(ids))).all())
    missing = sorted(ids - known)
    if missing:
        raise HTTPException(status_code=404, detail=f"Provider not found: {missing[0]}")
    return _spawn_refresh(ids)


@router.post("/{provider_id}/refresh", status_code=202)
async def refresh_provider(provider_id: int, session: Session = Depends(get_session)) -> dict[str, str]:
    """Refresh one provider's models and balance, leaving the other providers alone."""
    if session.get(ProviderConfig, provider_id) is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return _spawn_refresh({provider_id})


def _spawn_refresh(provider_ids: set[int]) -> dict[str, str]:
    """Start the refresh and say whether it is running now or waits for the run already in progress."""
    queued = is_running("models") or is_running("balances")
    spawn(_refresh_providers(provider_ids))
    return {"status": "queued" if queued else "started"}


async def _refresh_providers(provider_ids: set[int]) -> None:
    # Models first so the balance request sees the providers' current catalogues.
    await run_job("models", "provider", provider_ids)
    await run_job("balances", "provider", provider_ids)
