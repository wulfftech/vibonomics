"""Glue between stored provider configs and adapters (key resolution, env overrides, seeding)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crypto
from .config import env_value
from .orm import ProviderConfig
from .providers.base import FetchContext, ProviderAdapter, ResolvedProvider
from .providers.registry import ADAPTERS, get_adapter_class


def resolve(cfg: ProviderConfig) -> ResolvedProvider:
    adapter_cls = get_adapter_class(cfg.type)
    api_key = crypto.decrypt(cfg.api_key_enc)
    mgmt_key = crypto.decrypt(cfg.mgmt_key_enc)
    extra_key = crypto.decrypt(cfg.extra_key_enc)
    if adapter_cls is not None:
        api_key = env_value(adapter_cls.spec.env_key) or api_key
        mgmt_key = env_value(adapter_cls.spec.env_mgmt_key) or mgmt_key
        extra_key = env_value(adapter_cls.spec.env_extra_key) or extra_key
    return ResolvedProvider(
        id=cfg.id,
        type=cfg.type,
        name=cfg.name,
        api_key=_trimmed(api_key),
        mgmt_key=_trimmed(mgmt_key),
        base_url=cfg.base_url,
        options=dict(cfg.options or {}),
        extra_key=_trimmed(extra_key),
    )


def resolve_draft(
    provider_type: str,
    name: str,
    *,
    api_key: str | None,
    mgmt_key: str | None,
    extra_key: str | None,
    base_url: str | None,
    options: dict[str, Any] | None,
) -> ResolvedProvider:
    """A provider as typed into an unsaved form.

    No environment lookup, unlike ``resolve``: the base URL is the caller's, and an environment key sent to it
    would be a key the operator never agreed to send there. The test must also check the key that was typed.
    """
    return ResolvedProvider(
        id=0,
        type=provider_type,
        name=name,
        api_key=_trimmed(api_key),
        mgmt_key=_trimmed(mgmt_key),
        base_url=(base_url or "").strip() or None,
        options=dict(options or {}),
        extra_key=_trimmed(extra_key),
    )


def _trimmed(secret: str | None) -> str | None:
    """A pasted key often carries a trailing newline, which no API accepts in a header."""
    return (secret or "").strip() or None


def build_adapter(cfg: ProviderConfig, ctx: FetchContext) -> ProviderAdapter | None:
    adapter_cls = get_adapter_class(cfg.type)
    if adapter_cls is None:
        return None
    return adapter_cls(resolve(cfg), ctx)


def env_locked(provider_type: str) -> tuple[bool, bool, bool]:
    adapter_cls = get_adapter_class(provider_type)
    if adapter_cls is None:
        return False, False, False
    return (
        bool(env_value(adapter_cls.spec.env_key)),
        bool(env_value(adapter_cls.spec.env_mgmt_key)),
        bool(env_value(adapter_cls.spec.env_extra_key)),
    )


def seed_providers(session: Session) -> None:
    """Create default providers on first run, plus any provider whose key is set via environment."""
    existing = {p.type for p in session.scalars(select(ProviderConfig))}
    first_run = not existing
    for ptype, cls in ADAPTERS.items():
        if ptype in existing or cls.spec.custom:
            continue
        if (first_run and cls.spec.default_enabled) or env_value(cls.spec.env_key):
            session.add(ProviderConfig(type=ptype, name=cls.spec.name, enabled=True, options={}))
