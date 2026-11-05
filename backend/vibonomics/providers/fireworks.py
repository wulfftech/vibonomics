"""Fireworks AI: docs pricing over models.dev, live model list, account state and month-to-date spend."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, OptionField, Price, ProviderAdapter, ProviderSpec
from .common import _f, apply_price, modelsdev_offers, openai_model_ids, overlay_live_ids, strip_markdown

log = logging.getLogger(__name__)

API = "https://api.fireworks.ai/inference/v1"
MGMT = "https://api.fireworks.ai/v1"
PRICING_PAGE = "https://docs.fireworks.ai/serverless/pricing.md"
_MODELS = "accounts/fireworks/models/"
_ROUTERS = "accounts/fireworks/routers/"

_ROW = re.compile(
    r"^\|\s*\[(?P<name>[^\]]+)\]\(https?://app\.fireworks\.ai/models/(?P<acct>[^/\s)]+)/(?P<slug>[^/\s)]+)\)"
)
_MONEY = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")
_STRIKE = re.compile(r"~~(.*?)~~")


@dataclass
class PriceRow:
    name: str
    model_id: str
    variant: str  # "" | "fast" | "us"
    standard: Price
    list_price: Price | None = None
    priority: Price | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _triple(cell: str) -> tuple[Price | None, Price | None]:
    """'$3.00 / $0.30 / $15.00' (input / cached / output) → (price, struck-through list price)."""
    t = cell.replace("\\", "")
    struck = _STRIKE.findall(t)
    values = [float(v) for v in _MONEY.findall(_STRIKE.sub(" ", t))]

    def mk(vals: list[float]) -> Price | None:
        if len(vals) >= 3:
            return Price(input=vals[0], cache_read=vals[1], output=vals[2])
        if len(vals) == 2:
            return Price(input=vals[0], output=vals[1])
        return None

    old = [float(v) for s in struck for v in _MONEY.findall(s)]
    return mk(values), (mk(old) if old else None)


def parse_pricing(markdown: str) -> list[PriceRow]:
    """Per-model rows of the serverless pricing table (model name links carry the model slug)."""
    rows: list[PriceRow] = []
    for line in markdown.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = strip_markdown(m.group("name"))
        slug, acct = m.group("slug"), m.group("acct")
        low = name.lower()
        # Fast mode is the base model's name plus "Fast", on the base model's own slug. A "Fast" elsewhere in a name, or
        # a slug that already says fast, is a model of its own, and a "-fast" router id for it would not exist.
        is_fast = re.sub(r"\s*\([^)]*\)", "", low).strip().endswith(" fast") and "fast" not in slug.split("-")
        variant = "us" if re.search(r"\(us(?:-only)?\)", low) else ("fast" if is_fast else "")
        model_id = f"accounts/{acct}/models/{slug}"
        if variant == "fast":
            model_id = f"accounts/{acct}/routers/{slug}-fast"
        standard, struck = _triple(cells[1])
        if standard is None:
            continue
        priority, _ = _triple(cells[2]) if len(cells) > 2 else (None, None)
        rows.append(
            PriceRow(
                name=re.sub(r"\s*\((?:preview|us|us-only)\)\s*", " ", name, flags=re.I).strip(),
                model_id=model_id,
                variant=variant,
                standard=standard,
                list_price=struck,
                priority=priority,
            )
        )
    return rows


def _money(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    return (_f(value.get("units")) or 0.0) + (_f(value.get("nanos")) or 0.0) / 1e9


class FireworksAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="fireworks",
        name="Fireworks AI",
        description="Fast serverless inference for open models. Prices come from Fireworks' pricing docs and "
        "models.dev; a key adds the live model list, account state and month-to-date spend.",
        homepage="https://fireworks.ai",
        default_base_url=API,
        anthropic_base_url="https://api.fireworks.ai/inference",
        modelsdev_ids=("fireworks-ai",),
        keys_url="https://app.fireworks.ai/settings/users/api-keys",
        billing_url="https://app.fireworks.ai/account/billing",
        balance="key",
        balance_note="No balance API: shows month-to-date spend and whether the account is suspended "
        "(for example when credits run out).",
        options=(
            OptionField(
                key="account_id",
                label="Account ID",
                description="Your Fireworks account id (the <id> in accounts/<id>). Optional: detected from the "
                "key when it can see exactly one account.",
                placeholder="my-account",
            ),
        ),
        aider_prefix="fireworks_ai",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        catalog = self.ctx.catalog
        # Deprecated catalogue entries are kept only while the pricing page or the live list still shows them.
        offers = modelsdev_offers(catalog, self.spec.modelsdev_ids, include_deprecated=True)
        rows: list[PriceRow] = []
        page_error: str | None = None
        try:
            rows = parse_pricing(await get_text(PRICING_PAGE, timeout=30))
            if not rows:
                raise ValueError("no model rows found in the pricing table")
        except (FetchError, ValueError) as exc:
            page_error = str(exc)[:300]
            log.warning("Fireworks pricing page unusable, keeping models.dev prices: %s", page_error)

        by_id = {o.model_id: o for o in offers}
        for row in rows:
            if row.variant == "us":
                continue
            offer = by_id.get(row.model_id)
            if offer is None:  # a headline model the catalogue hasn't picked up yet
                offer = ModelOffer(
                    model_id=row.model_id,
                    name=row.name,
                    canonical_id=catalog.guess_canonical(row.model_id.removesuffix("-fast"), row.name),
                    pricing_source="fireworks docs",
                )
                offer.creator = offer.canonical_id.split("/", 1)[0] if offer.canonical_id else None
                offers.append(offer)
                by_id[row.model_id] = offer
            offer.extra["listed_in_docs"] = True
            offer.deprecated = False  # on the current pricing page, so still served

        if self.cfg.api_key:
            live = await openai_model_ids(f"{self.base_url}/models", self.auth_headers())
            if live:
                live_set = set(live)
                offers = overlay_live_ids(offers, live, catalog, drop_missing=False)
                # Routers (fast/spot/latest aliases) may not be listed; only drop plain models that vanished.
                offers = [o for o in offers if not o.model_id.startswith(_MODELS) or o.model_id in live_set]
        offers = [o for o in offers if not o.deprecated or o.extra.get("live") or o.extra.get("listed_in_docs")]

        for o in offers:
            if page_error:
                o.extra["pricing_page_error"] = page_error
            if o.model_id.endswith("-spot"):
                o.extra["tier"] = "spot (best-effort capacity)"
        by_id = {o.model_id: o for o in offers}
        for row in rows:
            o = by_id.get(row.model_id)
            if o is None:
                continue
            if row.variant == "us":
                o.extra["us_only_price"] = _price_dict(row.standard)
                continue
            if row.priority:
                o.extra["priority_price"] = _price_dict(row.priority)
            p = row.standard
            apply_price(
                o,
                Price(
                    input=p.input,
                    output=p.output,
                    cache_read=p.cache_read if p.cache_read is not None else o.price.cache_read,
                    cache_write=o.price.cache_write,
                ),
                "fireworks docs",
                list_price=row.list_price,
                discount_note="Discounted on Fireworks' pricing page" if row.list_price else None,
            )
        return offers

    async def _account_id(self) -> str | None:
        configured = str((self.cfg.options or {}).get("account_id") or "").strip()
        if configured:
            return configured.removeprefix("accounts/").strip("/")
        data = await get_json(f"{MGMT}/accounts", headers=self.auth_headers(), params={"pageSize": 10}, retries=1)
        accounts = [a for a in (data.get("accounts") or []) if isinstance(a, dict) and a.get("name")]
        if len(accounts) == 1:
            return str(accounts[0]["name"]).removeprefix("accounts/")
        return None

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see spend and account state.")
        try:
            account = await self._account_id()
        except FetchError as exc:
            return BalanceInfo(status="error", message=f"Could not look up the account: {str(exc)[:250]}")
        if not account:
            return BalanceInfo(
                status="error", message="Set the Account ID option: the key can see zero or several accounts."
            )
        info = BalanceInfo(status="ok", details={"account_id": account})
        errors: list[str] = []
        try:
            acct = await get_json(f"{MGMT}/accounts/{account}", headers=self.auth_headers(), retries=1)
            suspend = acct.get("suspendState") or "UNSUSPENDED"
            info.details["suspend_state"] = suspend
            info.details["state"] = acct.get("state")
            info.key_label = acct.get("displayName") or account
        except FetchError as exc:
            errors.append(f"account: {exc}")
            suspend = None
        now = datetime.now(UTC)
        start = now.replace(day=1).strftime("%Y-%m-%dT00:00:00Z")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        try:
            summary = await get_json(
                f"{MGMT}/accounts/{account}/billing/summary",
                headers=self.auth_headers(),
                params={"startTime": start, "endTime": end},
                retries=1,
            )
            items = summary.get("lineItems") or []
            info.account_used = round(sum(_money(i.get("totalCost")) for i in items if isinstance(i, dict)), 4)
            codes = {(i.get("totalCost") or {}).get("currencyCode") for i in items if isinstance(i, dict)}
            codes.discard(None)
            if len(codes) == 1:
                info.currency = str(codes.pop())
        except FetchError as exc:
            errors.append(f"billing summary: {exc}")
        if errors and info.account_used is None and suspend is None:
            return BalanceInfo(status="error", message="; ".join(e[:200] for e in errors))
        notes = ["Month-to-date spend (Fireworks has no balance API)."]
        if suspend and suspend != "UNSUSPENDED":
            notes.insert(0, f"Account suspended: {suspend}.")
        notes.extend(e[:200] for e in errors)
        info.message = " ".join(notes)
        return info


def _price_dict(p: Price) -> dict[str, float | None]:
    return {"input": p.input, "cache_read": p.cache_read, "output": p.output}
