"""Moonshot AI (Kimi): models.dev prices, live model list and account balance with a key."""

from __future__ import annotations

import logging
from typing import Any

from ..http import FetchError, get_json
from .base import BalanceInfo, ModelOffer, OptionField, ProviderAdapter, ProviderSpec
from .common import _f, modelsdev_offers, overlay_live_ids

log = logging.getLogger(__name__)

API = "https://api.moonshot.ai/v1"
API_CN = "https://api.moonshot.cn/v1"
ANTHROPIC_CN = "https://api.moonshot.cn/anthropic"


class MoonshotAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="moonshot",
        name="Moonshot AI (Kimi)",
        description="Kimi models direct from Moonshot. Prices come from models.dev (Kimi's price table is rendered "
        "client-side); a key adds the live model list and the account balance.",
        homepage="https://platform.kimi.ai",
        default_base_url=API,
        anthropic_base_url="https://api.moonshot.ai/anthropic",
        modelsdev_ids=("moonshotai",),
        keys_url="https://platform.kimi.ai/console/api-keys",
        billing_url="https://platform.kimi.ai/console",
        balance="key",
        balance_note="Available, voucher and cash balance from GET /v1/users/me/balance (USD; CNY on the China "
        "platform).",
        options=(
            OptionField(
                key="region",
                label="Platform",
                description="'global' for keys from platform.kimi.ai (api.moonshot.ai), 'cn' for keys from "
                "platform.kimi.com (api.moonshot.cn). Keys only work on the platform that issued them.",
                placeholder="global",
            ),
        ),
        aider_prefix="moonshot",
    )

    @property
    def is_cn(self) -> bool:
        region = str((self.cfg.options or {}).get("region") or "").strip().lower()
        return region in {"cn", "china"} or "moonshot.cn" in (self.cfg.base_url or "")

    @property
    def base_url(self) -> str:
        if self.cfg.base_url:
            return self.cfg.base_url.rstrip("/")
        return API_CN if self.is_cn else API

    @property
    def anthropic_base_url(self) -> str | None:
        # A key only works on the platform that issued it.
        return ANTHROPIC_CN if self.is_cn else self.spec.anthropic_base_url

    async def fetch_models(self) -> list[ModelOffer]:
        catalog = self.ctx.catalog
        # models.dev's China listing converts CNY prices to USD.
        pids = ("moonshotai-cn",) if self.is_cn else self.spec.modelsdev_ids
        if not self.cfg.api_key:
            return modelsdev_offers(catalog, pids)
        data = await get_json(f"{self.base_url}/models", headers=self.auth_headers())
        live: list[dict[str, Any]] = [m for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        if not live:
            return modelsdev_offers(catalog, pids)
        # Retired-but-still-served models keep their catalogue metadata (flagged deprecated).
        offers = overlay_live_ids(
            modelsdev_offers(catalog, pids, include_deprecated=True), [m["id"] for m in live], catalog
        )
        by_id = {m["id"]: m for m in live}
        for o in offers:
            m = by_id.get(o.model_id)
            if not m:
                continue
            o.context_length = m.get("context_length") or o.context_length
            if m.get("supports_reasoning") is not None:
                o.reasoning = bool(m["supports_reasoning"])
            mods = list(o.input_modalities or ["text"])
            for flag, mod in (("supports_image_in", "image"), ("supports_video_in", "video")):
                if m.get(flag) and mod not in mods:
                    mods.append(mod)
            o.input_modalities = mods
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see the account balance.")
        try:
            body = await get_json(f"{self.base_url}/users/me/balance", headers=self.auth_headers(), retries=1)
        except FetchError as exc:
            return BalanceInfo(status="error", message=str(exc)[:300])
        return parse_balance(body, currency="CNY" if self.is_cn else "USD")


def parse_balance(body: Any, currency: str = "USD") -> BalanceInfo:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return BalanceInfo(status="error", message="Unexpected balance response from Moonshot.")
    available = _f(data.get("available_balance"))
    voucher = _f(data.get("voucher_balance"))
    cash = _f(data.get("cash_balance"))
    info = BalanceInfo(
        status="ok",
        account_balance=available,
        currency=currency,
        details={"voucher_balance": voucher, "cash_balance": cash},
    )
    if cash is not None and cash < 0:
        info.message = f"Cash balance is negative ({cash:.2f} {currency} owed); only vouchers remain usable."
    elif available is not None and available <= 0:
        info.message = "Available balance is used up; inference calls will be rejected until you top up."
    return info
