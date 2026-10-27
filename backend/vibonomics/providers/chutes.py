"""Chutes: public model list with live prices, account discounts/price overrides and balance with a key."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..catalog.modelsdev import creator_of
from ..http import FetchError, get_json
from .base import BalanceInfo, ModelOffer, Price, ProviderAdapter, ProviderSpec
from .common import _f, apply_price, modelsdev_offers

log = logging.getLogger(__name__)

API = "https://llm.chutes.ai/v1"
MGMT = "https://api.chutes.ai"
_MD = "chutes"
_TEE = re.compile(r"-TEE$", re.I)


class ChutesAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="chutes",
        name="Chutes",
        description="Decentralised serverless inference (Bittensor SN64), with models in trusted execution "
        "environments. The model list and prices are public; a key adds the balance, subscription usage and any "
        "account-specific discounts.",
        homepage="https://chutes.ai",
        default_base_url=API,
        modelsdev_ids=(_MD,),
        keys_url="https://chutes.ai/app/settings/api-keys",
        billing_url="https://chutes.ai/app/settings/billing",
        balance="key",
        balance_note="Account balance (USD) from GET /users/me, plus plan usage from /users/me/subscription_usage.",
        aider_prefix="chutes",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        # The catalogue is public; fetch it without the key so a bad key can't hide the model list.
        try:
            data = await get_json(f"{self.base_url}/models", timeout=60)
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                raise FetchError(f"{self.base_url}/models: unexpected response shape")
        except FetchError as exc:
            log.warning("Chutes model list failed, using models.dev: %s", exc)
            offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
            for o in offers:
                o.extra["pricing_page_error"] = str(exc)[:300]
            return offers
        offers = [o for m in items if isinstance(m, dict) and (o := self._offer(m))]
        if self.cfg.api_key:
            await self._apply_account_pricing(offers)
        return offers

    def _offer(self, m: dict[str, Any]) -> ModelOffer | None:
        mid = m.get("id")
        if not mid:
            return None
        catalog = self.ctx.catalog
        md = catalog.model(_MD, mid) or {}
        canonical = catalog.canonical_for(_MD, mid) or catalog.guess_canonical(_TEE.sub("", mid))
        pricing = m.get("pricing") or {}
        usd = m.get("price") or {}

        def rate(key: str, alt: str) -> float | None:
            v = _f(pricing.get(key))
            if v is None:
                v = _f((usd.get(alt) or {}).get("usd"))
            return None if v is None or v < 0 else round(v, 6)

        price = Price(
            input=rate("prompt", "input"),
            output=rate("completion", "output"),
            cache_read=rate("input_cache_read", "input_cache_read"),
        )
        features = m.get("supported_features") or []
        info = catalog.canonical_index().get(canonical) if canonical else None
        return ModelOffer(
            model_id=mid,
            name=md.get("name") or mid.split("/", 1)[-1],
            creator=creator_of(canonical) or (mid.split("/", 1)[0].lower() if "/" in mid else None),
            canonical_id=canonical,
            family=md.get("family") or (info.family if info else None),
            context_length=m.get("context_length") or m.get("max_model_len"),
            max_output=m.get("max_output_length"),
            input_modalities=list(m.get("input_modalities") or ["text"]),
            output_modalities=list(m.get("output_modalities") or ["text"]),
            reasoning=("reasoning" in features) if features else md.get("reasoning"),
            tool_call=("tools" in features) if features else md.get("tool_call"),
            open_weights=md.get("open_weights", info.open_weights if info else None),
            release_date=md.get("release_date") or (info.release_date if info else None),
            price=price,
            is_free=price.is_zero(),
            pricing_source="chutes api" if price.known() else "chutes api (no price)",
            extra={
                "chute_id": m.get("chute_id"),
                "root": m.get("root"),
                "quantization": m.get("quantization"),
                "confidential_compute": m.get("confidential_compute"),
                "live": True,
            },
        )

    async def _apply_account_pricing(self, offers: list[ModelOffer]) -> None:
        """Apply account price overrides and discounts the way Chutes bills them (override, then discount)."""
        try:
            overrides = await get_json(f"{MGMT}/users/me/price_overrides", headers=self.auth_headers(), retries=1)
            discounts = await get_json(f"{MGMT}/users/me/discounts", headers=self.auth_headers(), retries=1)
        except FetchError as exc:
            log.info("Chutes account pricing unavailable: %s", exc)
            for o in offers:
                o.extra["account_pricing_error"] = str(exc)[:200]
            return
        apply_account_pricing(offers, overrides, discounts)

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key:
            return BalanceInfo(status="needs_key", message="Add an API key (cpk_...) to see the account balance.")
        try:
            me = await get_json(f"{MGMT}/users/me", headers=self.auth_headers(), retries=1)
        except FetchError as exc:
            return BalanceInfo(status="error", message=str(exc)[:300])
        info = BalanceInfo(
            status="ok",
            account_balance=_f(me.get("balance")),
            key_label=me.get("username"),
            details={"permissions": me.get("permissions")},
        )
        try:
            sub = await get_json(f"{MGMT}/users/me/subscription_usage", headers=self.auth_headers(), retries=1)
        except FetchError as exc:
            log.info("Chutes subscription usage unavailable: %s", exc)
            sub = None
        if isinstance(sub, dict) and sub.get("subscription"):
            info.details["subscription"] = sub
            monthly = sub.get("monthly") or {}
            if monthly.get("cap") is not None and monthly.get("usage") is not None:
                info.message = (
                    f"Plan ${_f(sub.get('monthly_price')) or 0:g}/month: ${_f(monthly['usage']) or 0:.2f} of "
                    f"${_f(monthly['cap']) or 0:.2f} pay-as-you-go-equivalent usage this cycle."
                )
        return info

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; the public catalogue still works."
        me = await get_json(f"{MGMT}/users/me", headers=self.auth_headers(), retries=0)
        return f"Key accepted ({me.get('username') or 'account'})."


def apply_account_pricing(offers: list[ModelOffer], overrides: Any, discounts: Any) -> None:
    by_chute: dict[str, dict[str, Any]] = {}
    for row in overrides if isinstance(overrides, list) else []:
        if isinstance(row, dict) and row.get("chute_id"):
            by_chute[str(row["chute_id"])] = row
    disc: dict[str, float] = {}
    for row in discounts if isinstance(discounts, list) else []:
        if isinstance(row, dict) and row.get("chute_id") is not None:
            d = _f(row.get("discount"))
            if d is not None and 0 < d <= 1:
                disc[str(row["chute_id"])] = d
    if not by_chute and not disc:
        return
    for o in offers:
        chute = o.extra.get("chute_id")
        if not chute or not o.price.known():
            continue
        public = o.price
        p_in, p_out = public.input, public.output
        cache_ratio = (public.cache_read / public.input) if public.cache_read is not None and public.input else None
        notes: list[str] = []
        override = by_chute.get(str(chute))
        if override:
            if _f(override.get("per_million_in")) is not None:
                p_in = _f(override["per_million_in"])
            if _f(override.get("per_million_out")) is not None:
                p_out = _f(override["per_million_out"])
            cd = _f(override.get("cache_discount"))
            if cd is not None and 0 <= cd <= 1:
                cache_ratio = 1 - cd
            notes.append("account price override")
        d = disc.get(str(chute), disc.get("*"))
        if d:
            p_in = p_in * (1 - d) if p_in is not None else None
            p_out = p_out * (1 - d) if p_out is not None else None
            notes.append(f"{d * 100:g}% account discount")
        if p_in is None or p_out is None:
            continue
        new = Price(
            input=round(p_in, 6),
            output=round(p_out, 6),
            cache_read=round(p_in * cache_ratio, 6) if cache_ratio is not None else None,
        )
        apply_price(
            o,
            new,
            "chutes api (account pricing)",
            list_price=Price(input=public.input, output=public.output, cache_read=public.cache_read),
            discount_note="Chutes " + " + ".join(notes),
        )
