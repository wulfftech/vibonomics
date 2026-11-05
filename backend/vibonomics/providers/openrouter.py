"""OpenRouter: public model list with live prices, per-endpoint discounts, key and credit APIs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..catalog.modelsdev import creator_of
from ..http import FetchError, get_json
from ..pricing import Task, task_cost
from ..settings_store import DEFAULT_PROFILES
from .base import BalanceInfo, ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import _f

log = logging.getLogger(__name__)

API = "https://openrouter.ai/api/v1"


def _per_million(value: Any) -> float | None:
    v = _f(value)
    if v is None or v < 0:  # OpenRouter uses -1 for "variable" router pricing
        return None
    return round(v * 1_000_000, 6)


def pick_discounted_endpoint(listed: Price, endpoints: list[dict[str, Any]], task: Task) -> dict[str, Any] | None:
    """The discounted endpoint worth showing in place of OpenRouter's listed price, or None.

    Providers of one model discount by different amounts, and a big percentage off a high price is still a high
    price. So the discounted endpoints are ranked by what one ``task`` costs on each, not by percentage, and the
    winner is used only when it is no dearer than the listed price. The listed endpoint may be the discounted
    one itself, hence the small tolerance.
    """
    listed_cost = task_cost(listed.input, listed.output, listed.cache_read, task)
    best: dict[str, Any] | None = None
    best_cost = 0.0
    for entry in endpoints:
        if not 0 < entry["discount"] < 1 or entry["input"] is None or entry["output"] is None:
            continue
        cost = task_cost(entry["input"], entry["output"], listed.cache_read, task)
        if cost is None:
            continue
        if best is None or (cost, -entry["discount"]) < (best_cost, -best["discount"]):
            best, best_cost = entry, cost
    if best is None or (listed_cost is not None and best_cost > listed_cost * 1.005):
        return None
    return best


class OpenRouterAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="openrouter",
        name="OpenRouter",
        description="Router across hundreds of models. Public catalogue; key adds per-key spend and limits.",
        homepage="https://openrouter.ai",
        default_base_url=API,
        anthropic_base_url="https://openrouter.ai/api",
        modelsdev_ids=("openrouter",),
        keys_url="https://openrouter.ai/settings/keys",
        billing_url="https://openrouter.ai/settings/credits",
        balance="management",
        balance_note="Key spend/limit works with a normal key; account credits need a management key.",
        mgmt_key_label="Management key",
        mgmt_key_help="Optional. Needed for account-wide credits (GET /api/v1/credits).",
        default_enabled=True,
        aider_prefix="openrouter",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=60)
        offers: list[ModelOffer] = []
        catalog = self.ctx.catalog
        for m in data.get("data", []):
            mid = m.get("id")
            if not mid:
                continue
            pricing = m.get("pricing") or {}
            price = Price(
                input=_per_million(pricing.get("prompt")),
                output=_per_million(pricing.get("completion")),
                cache_read=_per_million(pricing.get("input_cache_read")),
                cache_write=_per_million(pricing.get("input_cache_write")),
            )
            arch = m.get("architecture") or {}
            top = m.get("top_provider") or {}
            params = m.get("supported_parameters") or []
            canonical = catalog.canonical_for("openrouter", mid) or catalog.guess_canonical(mid, m.get("name"))
            md = catalog.model("openrouter", mid) or {}
            name = str(m.get("name") or mid)
            if ": " in name:  # "Anthropic: Claude Opus 5.5" → "Claude Opus 5.5"
                name = name.split(": ", 1)[1]
            offer = ModelOffer(
                model_id=mid,
                name=name,
                creator=creator_of(canonical) or mid.split("/", 1)[0],
                canonical_id=canonical,
                family=md.get("family"),
                context_length=m.get("context_length") or top.get("context_length"),
                max_output=top.get("max_completion_tokens"),
                input_modalities=list(arch.get("input_modalities") or []),
                output_modalities=list(arch.get("output_modalities") or []),
                reasoning=("reasoning" in params) or bool(m.get("reasoning")),
                tool_call="tools" in params,
                open_weights=md.get("open_weights"),
                release_date=md.get("release_date") or _created_date(m.get("created")),
                price=price,
                is_free=mid.endswith(":free") or price.is_zero(),
                pricing_source="openrouter api",
                extra={
                    "canonical_slug": m.get("canonical_slug"),
                    "hugging_face_id": m.get("hugging_face_id"),
                    "expiration_date": m.get("expiration_date"),
                    "description": m.get("description"),
                },
            )
            if m.get("expiration_date"):
                offer.extra["expires"] = m["expiration_date"]
            offers.append(offer)

        if self.ctx.settings.refresh.openrouter_endpoint_discounts:
            await self._apply_endpoint_discounts(offers)
        return offers

    async def _apply_endpoint_discounts(self, offers: list[ModelOffer]) -> None:
        sem = asyncio.Semaphore(8)
        failures = 0
        profile = (self.ctx.settings.scoring.profiles or DEFAULT_PROFILES)[0]
        task = Task(profile.task_input_tokens, profile.task_output_tokens, profile.cache_hit_rate)

        async def one(offer: ModelOffer) -> None:
            nonlocal failures
            if offer.is_free:
                return
            async with sem:
                try:
                    data = await get_json(f"{API}/models/{offer.model_id}/endpoints", timeout=20, retries=1)
                except FetchError:
                    failures += 1
                    return
            endpoints = (data.get("data") or {}).get("endpoints") or []
            summary = []
            for ep in endpoints:
                p = ep.get("pricing") or {}
                summary.append(
                    {
                        "provider": ep.get("provider_name"),
                        "tag": ep.get("tag"),
                        "quantization": ep.get("quantization"),
                        "input": _per_million(p.get("prompt")),
                        "output": _per_million(p.get("completion")),
                        # Each provider prices its own cache, or has none: the model's listed cache price is only one
                        # endpoint's, so a ranking that borrows it picks the wrong provider for a cache-heavy task.
                        "cache_read": _per_million(p.get("input_cache_read")),
                        "discount": _f(p.get("discount")) or 0.0,
                        "uptime_1d": ep.get("uptime_last_1d"),
                        "service_tier": ep.get("service_tier"),
                    }
                )
            offer.extra["endpoints"] = summary
            entry = pick_discounted_endpoint(offer.price, summary, task)
            if entry:
                disc, cur_in, cur_out = entry["discount"], entry["input"], entry["output"]
                offer.price = Price(
                    input=cur_in,
                    output=cur_out,
                    cache_read=offer.price.cache_read,
                    cache_write=offer.price.cache_write,
                )
                offer.list_price = Price(input=round(cur_in / (1 - disc), 6), output=round(cur_out / (1 - disc), 6))
                offer.discount = round(disc, 4)
                others = {e["provider"] for e in summary if e["discount"] > 0 and e["provider"] != entry["provider"]}
                offer.discount_note = f"{round(disc * 100)}% off via {entry['provider']}"
                if others:
                    plural = "s" if len(others) > 1 else ""
                    offer.discount_note += f"; {len(others)} other provider{plural} discounted too"

        await asyncio.gather(*(one(o) for o in offers))
        if failures:
            log.info("OpenRouter: %d endpoint lookups failed", failures)

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key and not self.cfg.mgmt_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see spend and limits.")
        info = BalanceInfo(status="ok")
        errors: list[str] = []
        if self.cfg.api_key:
            try:
                key = (await get_json(f"{API}/key", headers=self.auth_headers(), retries=1)).get("data") or {}
                info.key_label = key.get("label")
                info.key_usage = _f(key.get("usage"))
                info.key_limit = _f(key.get("limit"))
                info.key_remaining = _f(key.get("limit_remaining"))
                info.details["key"] = {
                    k: key.get(k)
                    for k in (
                        "usage_daily",
                        "usage_weekly",
                        "usage_monthly",
                        "limit_reset",
                        "is_free_tier",
                        "expires_at",
                        "free_model_daily_requests",
                    )
                    if k in key
                }
            except FetchError as exc:
                errors.append(f"key: {exc}")
        credit_key = self.cfg.mgmt_key or self.cfg.api_key
        try:
            credits = (
                await get_json(f"{API}/credits", headers={"Authorization": f"Bearer {credit_key}"}, retries=1)
            ).get("data") or {}
            total, used = _f(credits.get("total_credits")), _f(credits.get("total_usage"))
            info.account_total, info.account_used = total, used
            if total is not None and used is not None:
                info.account_balance = round(total - used, 4)
        except FetchError as exc:
            if self.cfg.mgmt_key:
                errors.append(f"credits: {exc}")
            else:
                info.details["credits_hint"] = "Add a management key to see account credits."
        if errors and info.key_usage is None and info.account_balance is None:
            return BalanceInfo(status="error", message="; ".join(errors))
        if errors:
            info.message = "; ".join(errors)
        return info

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; the public catalogue still works."
        key = (await get_json(f"{API}/key", headers=self.auth_headers(), retries=0)).get("data") or {}
        label = key.get("label") or "key"
        return f"Key accepted ({label})."


def _created_date(created: Any) -> str | None:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(int(created), UTC).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None
