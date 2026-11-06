"""Checks what a preset is about to write against what OpenRouter lists at this moment.

A plan is ranked on the copy of the model data from the last refresh, which can be hours old. In that time a provider
can leave a model, go down, change its price or drop a parameter, and a preset written from the copy would name a
provider that cannot serve it. So a plan, and the apply that writes it, read the public endpoint listing of every
model in the config (``GET /models/<id>/endpoints``, no key needed) and say what is wrong. An error refuses the write;
a warning is shown and lets it through. A listing that cannot be read is a warning: OpenRouter being slow is no proof
that the preset is wrong, and it must not stop a write that is otherwise fine.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from .http import FetchError, get_json
from .providers.openrouter import _per_million

if TYPE_CHECKING:
    from .presets import Candidate

log = logging.getLogger(__name__)

CONCURRENCY = 4
LISTING_TIMEOUT = 15
MIN_UPTIME = 50.0  # percent over the last 30 minutes, below which a pinned endpoint is called out
PRICE_DRIFT = 0.20  # how far a live price may be from the planned one before it is called out, as a fraction
EXPIRY_WINDOW = timedelta(days=7)

Level = Literal["ok", "warning", "error"]


@dataclass
class Check:
    level: Level
    check: str  # listing | provider | pins | health | price | expiry | params | max_tokens
    model: str | None
    provider: str | None  # the provider tag the finding is about, when it is about one
    message: str


@dataclass
class Validation:
    items: list[Check]
    checked_at: datetime
    ok: bool  # False exactly when an item is an error


# ---- rules shared with the ranking, which applies them to the stored copy -----------------------------------------


def unsupported(params: Any, required: Sequence[str]) -> list[str]:
    """The ``required`` request parameters an endpoint is known not to take.

    An endpoint that does not say which it takes (stored before they were kept, or listed without them) is not known to
    lack any: unknown is not a failure.
    """
    if not required or not isinstance(params, list):
        return []
    have = {p for p in params if isinstance(p, str)}
    return [name for name in required if name not in have]


def cap_below(cap: Any, max_tokens: int | None) -> bool:
    """Whether an endpoint's output cap is a known number smaller than ``max_tokens``."""
    return max_tokens is not None and isinstance(cap, int | float) and not isinstance(cap, bool) and cap < max_tokens


# ---- reading the listing -------------------------------------------------------------------------------------------


@dataclass
class _Listing:
    state: Literal["ok", "missing", "unreadable"]
    data: dict[str, Any] = field(default_factory=dict)  # the model's object in the answer, which may carry its expiry
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


async def _fetch(base_url: str, model_id: str, gate: asyncio.Semaphore) -> _Listing:
    url = f"{base_url.rstrip('/')}/models/{quote(model_id, safe='/:@~')}/endpoints"
    async with gate:
        try:
            payload = await get_json(url, timeout=LISTING_TIMEOUT, retries=1)
        except FetchError as exc:
            if exc.status == 404:
                return _Listing("missing")
            return _Listing(
                "unreadable", reason=f"OpenRouter answered HTTP {exc.status}" if exc.status else "no answer in time"
            )
        except Exception as exc:  # a check that cannot run is a warning, whatever stopped it
            log.warning("The live check of %s could not run: %s: %s", model_id, type(exc).__name__, exc)
            return _Listing("unreadable", reason="the check itself failed")
    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(endpoints, list):
        return _Listing("unreadable", reason="the answer held no list of endpoints")
    return _Listing("ok", data=data, endpoints=[e for e in endpoints if isinstance(e, dict)])


def _model_ids(config: dict[str, Any]) -> list[str]:
    listed = config.get("models")
    found = [config.get("model"), *(listed if isinstance(listed, list) else [])]
    return list(dict.fromkeys(m for m in found if isinstance(m, str) and m))


def _pinned(config: dict[str, Any], providers: list[dict[str, Any]], models: list[str]) -> dict[str, list[str]]:
    """The provider tags the preset names for each model: those the plan assigned to it that ``only`` lists.

    A tag in ``only`` that the plan gives to no model is whatever the live preset already had, and is not checked.
    """
    block = config.get("provider")
    named = {t.lower() for t in (block.get("only") if isinstance(block, dict) else None) or [] if isinstance(t, str)}
    pins: dict[str, list[str]] = {model: [] for model in models}
    for row in providers:
        tag = row.get("tag")
        if not isinstance(tag, str) or tag.lower() not in named:
            continue
        for model in row.get("models") or []:
            if model in pins and tag not in pins[model]:
                pins[model].append(tag)
    return pins


def _matches(tag: str, endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The live endpoints a pinned tag names: by its full tag, the provider part of it, or the provider's name."""
    want = tag.lower()
    found = []
    for ep in endpoints:
        live_tag = ep.get("tag") if isinstance(ep.get("tag"), str) else ""
        if want in {live_tag.lower(), live_tag.split("/")[0].lower(), str(ep.get("provider_name") or "").lower()}:
            found.append(ep)
    return found


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _planned_price(tag: str, pick: Candidate | None, row: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """What the plan stored for this tag: the model's own endpoint, since one tag can price two models differently."""
    for ep in pick.endpoints if pick else []:
        if isinstance(ep, dict) and ep.get("tag") == tag:
            return _number(ep.get("input")), _number(ep.get("output"))
    return (_number(row.get("input")), _number(row.get("output"))) if row else (None, None)


def _drifted(planned: float, live: float) -> bool:
    if planned == 0:  # free in the plan, and not now
        return live > 0
    return abs(live - planned) / planned > PRICE_DRIFT


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _money(value: float) -> str:
    return f"${value:g}"


# ---- one model -----------------------------------------------------------------------------------------------------


def _check_model(
    model: str,
    listing: _Listing,
    pick: Candidate | None,
    pins: list[str],
    rows: dict[str, dict[str, Any]],
    now: datetime,
    require: Sequence[str],
    max_tokens: int | None,
) -> list[Check]:
    if listing.state == "missing":
        return [
            Check("error", "listing", model, None, f"{model} has no live endpoint: OpenRouter has no listing for it.")
        ]
    if listing.state == "unreadable":
        return [Check("warning", "listing", model, None, f"Could not check live data for {model}: {listing.reason}.")]
    endpoints = listing.endpoints
    if not endpoints:
        return [Check("error", "listing", model, None, f"{model} has no live endpoint on OpenRouter right now.")]

    found: list[Check] = []
    expires = _when(listing.data.get("expiration_date")) or _when(pick.expires if pick else None)
    if expires is not None and expires - now <= EXPIRY_WINDOW:
        verb = "expired" if expires <= now else "expires"
        found.append(Check("warning", "expiry", model, None, f"{model} {verb} on {expires.date().isoformat()}."))

    live_pins = 0
    for tag in pins:
        matched = _matches(tag, endpoints)
        if not matched:
            found.append(
                Check("error", "provider", model, tag, f"{tag} is no longer listed for {model} on OpenRouter.")
            )
            continue
        live_pins += 1
        for ep in matched:
            found.extend(_check_endpoint(model, tag, ep, matched, pick, rows.get(tag), require, max_tokens))
    if pins and not live_pins:
        found.append(
            Check(
                "error",
                "pins",
                model,
                None,
                f"None of the providers the preset names for {model} is live, so it could not be served.",
            )
        )

    if not pins:
        # Nothing is pinned, so OpenRouter chooses an endpoint. A requirement that none of them meets is not an error
        # here, since nothing the preset writes is wrong, but it will not do what the operator asked.
        if require and all(unsupported(ep.get("supported_parameters"), require) for ep in endpoints):
            found.append(
                Check(
                    "warning",
                    "params",
                    model,
                    None,
                    f"No live endpoint of {model} supports {', '.join(require)}, so it will be ignored for this model.",
                )
            )
        if max_tokens is not None and all(cap_below(ep.get("max_completion_tokens"), max_tokens) for ep in endpoints):
            found.append(
                Check(
                    "warning",
                    "max_tokens",
                    model,
                    None,
                    f"No live endpoint of {model} allows {max_tokens:,} output tokens.",
                )
            )

    if any(item.level == "error" for item in found):
        return found
    count = len(endpoints)
    summary = f"{count} live endpoint{'s' if count != 1 else ''}, " + (
        "pins verified" if pins else "no providers pinned"
    )
    return [Check("ok", "listing", model, None, summary), *found]


def _check_endpoint(
    model: str,
    tag: str,
    ep: dict[str, Any],
    matched: list[dict[str, Any]],
    pick: Candidate | None,
    row: dict[str, Any] | None,
    require: Sequence[str],
    max_tokens: int | None,
) -> list[Check]:
    """What a live endpoint that the preset pins says about itself."""
    out: list[Check] = []
    status, uptime = _number(ep.get("status")), _number(ep.get("uptime_last_30m"))
    if status is not None and status != 0:
        out.append(
            Check("warning", "health", model, tag, f"{tag} on {model} reports status {status:g} (0 is healthy).")
        )
    if uptime is not None and uptime < MIN_UPTIME:
        out.append(
            Check("warning", "health", model, tag, f"{tag} on {model} was up {uptime:g}% of the last 30 minutes.")
        )

    if len(matched) == 1 and str(ep.get("tag") or "").lower() == tag.lower():
        pricing = ep.get("pricing") if isinstance(ep.get("pricing"), dict) else {}
        planned_in, planned_out = _planned_price(tag, pick, row)
        moved = []
        for label, planned, live in (
            ("input", planned_in, _per_million(pricing.get("prompt"))),
            ("output", planned_out, _per_million(pricing.get("completion"))),
        ):
            if planned is not None and live is not None and _drifted(planned, live):
                change = f", {(live / planned - 1) * 100:+.0f}%" if planned > 0 else ""
                moved.append(f"{label} {_money(live)} per million (planned at {_money(planned)}{change})")
        if moved:
            out.append(Check("warning", "price", model, tag, f"{tag} on {model} now charges {'; '.join(moved)}."))

    missing = unsupported(ep.get("supported_parameters"), require)
    if missing:
        out.append(
            Check(
                "error",
                "params",
                model,
                tag,
                f"{tag} on {model} does not support {', '.join(missing)}, which the preset requires.",
            )
        )
    if cap_below(ep.get("max_completion_tokens"), max_tokens):
        cap = ep.get("max_completion_tokens")
        out.append(
            Check(
                "error",
                "max_tokens",
                model,
                tag,
                f"{tag} on {model} caps its output at {cap:,.0f} tokens, below the {max_tokens:,} the preset requires.",
            )
        )
    return out


async def validate_live(
    base_url: str,
    config: dict[str, Any],
    picks: list[Candidate],
    providers: list[dict[str, Any]],
    *,
    require_params: Sequence[str] = (),
    max_tokens: int | None = None,
) -> Validation:
    """What is wrong with ``config`` if it were written now, from the live listing of each of its models.

    ``picks`` and ``providers`` are the plan's: the models as ranked (their stored endpoints give the planned prices)
    and the providers it chose, each with the models it serves. Reads only, and never raises for a listing that fails.
    """
    models = _model_ids(config)
    gate = asyncio.Semaphore(CONCURRENCY)
    listings = await asyncio.gather(*(_fetch(base_url, model, gate) for model in models))
    now = datetime.now(UTC)
    picked = {c.model_id: c for c in picks}
    rows = {p["tag"]: p for p in providers if isinstance(p.get("tag"), str)}
    pins = _pinned(config, providers, models)
    items: list[Check] = []
    for model, listing in zip(models, listings, strict=True):
        items.extend(
            _check_model(model, listing, picked.get(model), pins[model], rows, now, tuple(require_params), max_tokens)
        )
    return Validation(items=items, checked_at=now, ok=not any(item.level == "error" for item in items))
