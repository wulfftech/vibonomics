"""DeepSeek: HTML pricing table, live model list, account balance API."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import FetchError, get_json, get_text
from .base import BalanceInfo, ModelOffer, ProviderAdapter, ProviderSpec
from .common import modelsdev_offers
from .pricing_pages import PagePrice, apply_page_prices, parse_price_cell

log = logging.getLogger(__name__)

API = "https://api.deepseek.com/v1"
PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"
_OFFICIAL_BASE = "https://api.deepseek.com"

_PRICE_ROWS = {
    "1M INPUT TOKENS (CACHE HIT)": "cache_read",
    "1M INPUT TOKENS (CACHE MISS)": "input",
    "1M OUTPUT TOKENS": "output",
}
_MODES = {"OFF-PEAK", "PEAK"}


def _size(text: str) -> int | None:
    # "DEFAULT: 4K MAXIMUM: 8K": the model can write the maximum, which is the figure that is wanted.
    m = re.search(r"MAXIMUM:?\s*([0-9]+(?:\.[0-9]+)?)\s*([KM])", text or "", re.I) or re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*([KM])", text or "", re.I
    )
    if not m:
        return None
    return int(float(m.group(1)) * (1_000_000 if m.group(2).upper() == "M" else 1_000))


def _colspan(cell: Any) -> int:
    try:
        return min(max(int(cell.get("colspan") or 1), 1), 50)  # the page is third-party: no unbounded spans
    except (TypeError, ValueError):
        return 1


def _spread(values: list[str], spans: list[int]) -> list[str]:
    """Value cells one per model column: a cell that spans several columns (``colspan``) is every model's."""
    return [value for value, span in zip(values, spans, strict=True) for _ in range(span)]


def _record(
    entries: dict[str, dict[str, Any]], model_ids: list[str], label: str, mode: str | None, values: list[str]
) -> None:
    if mode not in _MODES:
        return
    bucket = "peak" if mode == "PEAK" else "off_peak"
    field = _PRICE_ROWS[label]
    for idx, mid in enumerate(model_ids):
        price, *_ = parse_price_cell(values[idx] if idx < len(values) else None)
        if price is not None:
            entries[mid][bucket][field] = price


def parse_pricing(html: str) -> list[PagePrice]:
    """Parse the two-column (flash / pro) pricing table on DeepSeek's docs page.

    The table relies on row/colspans, so it's read as raw cell lists: a price-group row looks like
    ``["PRICING (2)", "1M INPUT TOKENS (CACHE MISS)", "OFF-PEAK", "$0.15", "$0.66"]`` and the
    matching peak row like ``["PEAK", "$0.3", "$1.32"]``.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[PagePrice] = []
    for table in soup.find_all("table"):
        cells = [tr.find_all(["th", "td"]) for tr in table.find_all("tr")]
        grid = [[c.get_text(" ", strip=True) for c in row] for row in cells]
        spans = [[_colspan(c) for c in row] for row in cells]
        if not grid or not grid[0] or grid[0][0].upper() != "MODEL":
            continue
        model_ids = [re.sub(r"\s*\(\d+\)$", "", c).strip() for c in grid[0][1:]]
        entries: dict[str, dict[str, Any]] = {mid: {"off_peak": {}, "peak": {}} for mid in model_ids}
        context: dict[str, int | None] = dict.fromkeys(model_ids)
        max_out: dict[str, int | None] = dict.fromkeys(model_ids)
        current_label: str | None = None

        for row, row_spans in zip(grid[1:], spans[1:], strict=True):
            if not row:
                continue
            upper = [c.upper() for c in row]
            label_idx = next((i for i, c in enumerate(upper) if c in _PRICE_ROWS), None)
            if label_idx is not None:
                current_label = upper[label_idx]
                mode_idx = label_idx + 1
                mode = upper[mode_idx] if mode_idx < len(upper) and upper[mode_idx] in _MODES else None
                _record(
                    entries,
                    model_ids,
                    current_label,
                    mode,
                    _spread(row[mode_idx + 1 :], row_spans[mode_idx + 1 :]),
                )
                continue
            if upper[0] in _MODES and current_label is not None:
                _record(entries, model_ids, current_label, upper[0], _spread(row[1:], row_spans[1:]))
                continue
            if upper[0] == "CONTEXT LENGTH" and len(row) >= 2:
                values = _spread(row[1:], row_spans[1:])
                for idx, mid in enumerate(model_ids):
                    context[mid] = _size(values[idx] if idx < len(values) else values[0])
            elif upper[0] == "MAX OUTPUT" and len(row) >= 2:
                values = _spread(row[1:], row_spans[1:])
                for idx, mid in enumerate(model_ids):
                    max_out[mid] = _size(values[idx] if idx < len(values) else values[0])
        for mid in model_ids:
            peak, off_peak = entries[mid]["peak"], entries[mid]["off_peak"]
            if not peak:
                continue
            rows.append(
                PagePrice(
                    name=mid,
                    model_id=mid,
                    input=peak.get("input"),
                    output=peak.get("output"),
                    cache_read=peak.get("cache_read"),
                    extra={
                        "off_peak_prices": off_peak or None,
                        "off_peak_note": "Off-peak rates are about 50% lower; see DeepSeek's peak-hour schedule."
                        if off_peak
                        else None,
                        "context_length": context.get(mid),
                        "max_output": max_out.get(mid),
                    },
                )
            )
    return rows


class DeepSeekAdapter(ProviderAdapter):
    spec = ProviderSpec(
        type="deepseek",
        name="DeepSeek",
        description="DeepSeek models direct. Prices come from DeepSeek's pricing page (peak rates, with off-peak "
        "rates kept alongside); a key adds the live model list and the account balance.",
        homepage="https://www.deepseek.com",
        default_base_url=API,
        anthropic_base_url=f"{_OFFICIAL_BASE}/anthropic",
        modelsdev_ids=("deepseek",),
        keys_url="https://platform.deepseek.com/api_keys",
        billing_url="https://platform.deepseek.com/top_up",
        balance="key",
        balance_note="Available, granted and topped-up balance from GET /user/balance.",
        aider_prefix="deepseek",
    )

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        if self.cfg.api_key:
            try:
                data = await get_json(f"{API}/models", headers=self.auth_headers(), timeout=30)
                live = {m.get("id") for m in data.get("data", []) if m.get("id")}
                for offer in offers:
                    if offer.model_id in live:
                        offer.extra["live"] = True
            except FetchError as exc:
                log.info("DeepSeek model list failed: %s", exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(PRICING_URL, timeout=60)), "deepseek pricing page"
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("DeepSeek pricing page failed: %s", exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        if not self.cfg.api_key:
            return BalanceInfo(status="needs_key", message="Add an API key to see the account balance.")
        data = await get_json(f"{API}/user/balance", headers=self.auth_headers(), timeout=20, retries=1)
        infos = data.get("balance_infos") or []
        preferred = next((i for i in infos if str(i.get("currency")).upper() == "USD"), infos[0] if infos else None)
        if preferred is None:
            return BalanceInfo(status="error", message="DeepSeek returned no balance information.")
        details = {
            str(i.get("currency")): {
                "total": i.get("total_balance"),
                "granted": i.get("granted_balance"),
                "topped_up": i.get("topped_up_balance"),
            }
            for i in infos
        }
        return BalanceInfo(
            status="ok",
            account_balance=_num(preferred.get("total_balance")),
            currency=str(preferred.get("currency") or "USD"),
            details={"available": bool(data.get("is_available")), "balances": details},
        )

    async def test_connection(self) -> str:
        if not self.cfg.api_key:
            return "No API key set; prices still load from DeepSeek's pricing page."
        await get_json(f"{API}/user/balance", headers=self.auth_headers(), timeout=20, retries=0)
        return "Key accepted (balance endpoint reachable)."


def _num(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None
