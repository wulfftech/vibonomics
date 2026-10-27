"""Requesty: public model list with live prices and endpoint discounts, key spend and org balance."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..catalog.modelsdev import creator_of
from ..http import FetchError, get_json
from .base import BalanceInfo, ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import _f, apply_price, openai_model_ids

log = logging.getLogger(__name__)

API = "https://router.requesty.ai/v1"
MANAGE = "https://api-v2.requesty.ai/v1/manage"
_MD = "requesty"


def _per_million(value: Any) -> float | None:
    v = _f(value)
    if v is None or v < 0:
        return None
    return round(v * 1_000_000, 6)


def _tier_price(t: dict[str, Any]) -> Price:
    return Price(
        input=_per_million(t.get("input_price")),
        output=_per_million(t.get("output_price")),
        cache_read=_per_million(t.get("cached_price")),
        cache_write=_per_million(t.get("caching_price") or t.get("caching_5m_price")),
    )


def _undiscount(value: float | None, fraction: float) -> float | None:
    return None if value is None else round(value / (1 - fraction), 6)


class RequestyAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="requesty",
        name="Requesty",
        description="LLM router across hundreds of provider endpoints, some discounted. The model list and prices "
        "are public; a key adds per-key monthly spend and the organisation balance. Pay-as-you-go adds a 5% "
        "platform fee on top of listed rates.",
        homepage="https://www.requesty.ai",
        default_base_url=API,
        anthropic_base_url="https://router.requesty.ai",
        modelsdev_ids=(_MD,),
        keys_url="https://app.requesty.ai/api-keys",
        billing_url="https://app.requesty.ai/settings/billing",
        balance="management",
        balance_note="A normal key shows its own monthly spend and limit; the organisation balance needs a key "
        "with manage permission.",
        mgmt_key_label="Management key",
        mgmt_key_help="Optional. An API key with manage permission, used for the organisation balance "
        "(GET /v1/manage/org).",
        aider_prefix=None,  # not a LiteLLM provider; use openai/<model> with this base URL
    )

    async def fetch_models(self) -> list[ModelOffer]:
        # Unauthenticated: every public model. With a key: only the models approved for the organisation.
        data = await get_json(f"{self.base_url}/models", headers=self.auth_headers(), timeout=60)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise FetchError(f"{self.base_url}/models: unexpected response shape")
        return [o for m in items if isinstance(m, dict) and (o := self._offer(m))]

    def _offer(self, m: dict[str, Any]) -> ModelOffer | None:
        mid = m.get("id")
        if not mid:
            return None
        catalog = self.ctx.catalog
        md = catalog.model(_MD, mid) or {}
        canonical = catalog.canonical_for(_MD, mid) or catalog.guess_canonical(
            m.get("model_canonical_name") or mid, m.get("model_canonical_name")
        )
        info = catalog.canonical_index().get(canonical) if canonical else None
        tiers = sorted(
            (t for t in (m.get("pricing") or []) if isinstance(t, dict)),
            key=lambda t: _f(t.get("prompt_tokens_threshold")) or 0,
        )
        base = tiers[0] if tiers else m
        price = _tier_price({**m, **base}) if tiers else _tier_price(m)
        modalities = ["text", "image"] if m.get("supports_vision") else ["text"]
        name = (
            md.get("name")
            or (info.name if info else None)
            or m.get("model_canonical_name")
            or mid.split("/", 1)[-1].split("@")[0]
        )
        if "@" in mid:  # regional endpoint, e.g. vertex/gemini-x@eu
            name = f"{name} ({mid.rsplit('@', 1)[1]})"
        offer = ModelOffer(
            model_id=mid,
            name=name,
            creator=creator_of(canonical) or m.get("model_lab") or mid.split("/", 1)[0],
            canonical_id=canonical,
            family=md.get("family") or (info.family if info else None),
            context_length=m.get("context_window"),
            max_output=m.get("max_output_tokens"),
            input_modalities=modalities,
            output_modalities=["text", "image"] if m.get("supports_image_generation") else ["text"],
            reasoning=m.get("supports_reasoning"),
            tool_call=m.get("supports_tool_calling"),
            open_weights=m.get("open_weights"),
            release_date=md.get("release_date") or (info.release_date if info else None),
            price=price,
            is_free=price.is_zero(),
            pricing_source="requesty api",
            extra={
                "description": m.get("description"),
                "hugging_face_id": m.get("hugging_face_id"),
                "quantization": m.get("quantization"),
                "geolocation": m.get("geolocation"),
                "data_retention": m.get("data_retention"),
                "data_used_for_training": m.get("data_used_for_training"),
                "live": True,
            },
        )
        if len(tiers) > 1:
            offer.extra["tiers"] = [
                {
                    **_price_dict(_tier_price(t)),
                    "tier": {"type": "context", "size": int(_f(t["prompt_tokens_threshold"]))},
                }
                for t in tiers[1:]
                if _f(t.get("prompt_tokens_threshold"))
            ]
        retires = m.get("retires_at") or m.get("retires")
        if retires:
            offer.extra["expires"] = _iso_date(retires)
        pct = _f(m.get("discount_percentage"))
        if pct and 0 < pct < 100:
            # Listed rates already include the discount (Requesty's model pages say "the rates below are what
            # you pay ... off a list rate of ..."), so the list price is rate / (1 - pct).
            frac = pct / 100
            apply_price(
                offer,
                price,
                "requesty api",
                list_price=Price(
                    input=_undiscount(price.input, frac),
                    output=_undiscount(price.output, frac),
                    cache_read=_undiscount(price.cache_read, frac),
                    cache_write=_undiscount(price.cache_write, frac),
                ),
                discount_note=f"{pct:g}% off on Requesty",
            )
        return offer

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key and not self.cfg.mgmt_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see spend and limits.")
        info = BalanceInfo(status="ok")
        errors: list[str] = []
        if self.cfg.api_key:
            try:
                key = await get_json(f"{MANAGE}/apikey/self", headers=self.auth_headers(), retries=1)
                info.key_label = key.get("name")
                info.key_usage = _f(key.get("monthly_spend"))
                limit = _f(key.get("monthly_limit"))
                info.key_limit = limit if limit else None  # 0 means unlimited
                if info.key_limit is not None and info.key_usage is not None:
                    info.key_remaining = round(info.key_limit - info.key_usage, 4)
                info.details["key"] = {"period": "month", "permissions": key.get("permissions")}
            except FetchError as exc:
                errors.append(f"key: {exc}")
        org_key = self.cfg.mgmt_key or self.cfg.api_key
        try:
            org = await get_json(f"{MANAGE}/org", headers={"Authorization": f"Bearer {org_key}"}, retries=1)
            info.account_balance = _f(org.get("balance"))
            info.details["organization"] = org.get("name")
        except FetchError as exc:
            if self.cfg.mgmt_key:
                errors.append(f"org: {exc}")
            else:
                info.details["org_hint"] = "Add a management key (manage permission) to see the org balance."
        if errors and info.key_usage is None and info.account_balance is None:
            return BalanceInfo(status="error", message="; ".join(e[:250] for e in errors))
        if errors:
            info.message = "; ".join(e[:250] for e in errors)
        return info

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; the public catalogue still works."
        try:
            key = await get_json(f"{MANAGE}/apikey/self", headers=self.auth_headers(), retries=0)
            return f"Key accepted ({key.get('name') or 'key'})."
        except FetchError as exc:
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                raise
        ids = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
        return f"Key accepted ({len(ids)} models approved)."


def _price_dict(p: Price) -> dict[str, float | None]:
    return {"input": p.input, "output": p.output, "cache_read": p.cache_read, "cache_write": p.cache_write}


def _iso_date(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)[:10] if value else None
