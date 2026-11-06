"""A local copy of every preset on the owner's OpenRouter account.

The settings page lists the account's presets, which is a call per preset to OpenRouter. This reads them all on a
timer and on demand and keeps the result in the ``settings`` table (``preset_catalog``), so the page reads local
data. Nothing here writes to OpenRouter: it is two GETs, the list and one read per preset.

Whether Vibonomics manages a preset is not stored here. ``view`` works it out from ``preset_state`` each time it is
read, so adopting or creating a preset shows at once without waiting for the next sync.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from . import events
from .db import session_scope
from .http import get_json, scrub_secrets, single_flight
from .jobs import failure_text
from .orm import Setting, utcnow
from .presets import _auth, load_access, read_state

log = logging.getLogger(__name__)

CATALOG_KEY = "preset_catalog"
MAX_PRESETS = 100
READ_CONCURRENCY = 6
TIMEOUT = 20
NO_KEY = "Add an OpenRouter API key under Settings, Providers first."

# The keys the app decides for a preset. Everything else is the owner's, and goes back as it was. Kept here because
# presets.py defines its own set; change both together.
_APP_KEYS = frozenset({"model", "models", "route", "provider"})
_APP_PROVIDER_KEYS = frozenset({"only", "order", "sort"})

_running = False


def is_syncing() -> bool:
    return _running


# ---- reading the account -------------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _preset_id(*sources: dict[str, Any]) -> str | None:
    """The id OpenRouter gives a preset, as a string; None when the answer names none."""
    for source in sources:
        raw = source.get("id")
        if isinstance(raw, str | int) and not isinstance(raw, bool) and str(raw):
            return str(raw)
    return None


def model_ids(config: dict[str, Any]) -> list[str]:
    """The models a preset config names, in order: its ``model``, then any ``models`` fallbacks, once each."""
    listed = config.get("models")
    found = [config.get("model"), *(listed if isinstance(listed, list) else [])]
    out: list[str] = []
    for value in found:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def passthrough_keys(config: dict[str, Any]) -> list[str]:
    """The keys of a config that the app has no setting for: it sends these back as it found them."""
    keys = [key for key in config if key not in _APP_KEYS]
    provider = config.get("provider")
    if isinstance(provider, dict):
        keys += [f"provider.{key}" for key in provider if key not in _APP_PROVIDER_KEYS]
    return sorted(keys)


def _entry(row: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """One catalogue entry, from the list row and the preset's own read (which wins where both say something)."""
    slug = str(row["slug"])
    designated = data.get("designated_version")
    live = designated if isinstance(designated, dict) else {}
    config = live.get("config") if isinstance(live.get("config"), dict) else {}
    provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
    only = provider.get("only")
    version = live.get("version")
    return {
        "slug": slug,
        "preset_id": _preset_id(data, row),
        "name": _text(data.get("name")) or _text(row.get("name")) or slug,
        "status": _text(data.get("status")) or _text(row.get("status")) or "active",
        "version": version if isinstance(version, int) and not isinstance(version, bool) else None,
        "updated_at": _text(data.get("updated_at")) or _text(row.get("updated_at")),
        "models": model_ids(config),
        "providers": [p for p in only if isinstance(p, str)] if isinstance(only, list) else [],
        "config": config,
        "has_system_prompt": bool(live.get("system_prompt")),
        "passthrough_keys": passthrough_keys(config),
    }


async def _read_one(
    base: str, headers: dict[str, str], row: dict[str, Any], gate: asyncio.Semaphore
) -> tuple[dict[str, Any] | None, str | None]:
    """The entry for one listed preset, or why it could not be read."""
    async with gate:
        try:
            body = await get_json(
                f"{base}/presets/{quote(str(row['slug']), safe='')}", headers=headers, timeout=TIMEOUT, retries=1
            )
        except Exception as exc:  # one preset must not sink the rest of the sync
            return None, scrub_secrets(failure_text(exc), headers)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None, "the answer held no preset"
    return _entry(row, data), None


# ---- the stored catalogue ------------------------------------------------------------------------------------


def read_catalog() -> dict[str, Any]:
    """What the last sync stored: ``synced_at``, ``error`` and ``presets`` (no ``managed``). Reads no network."""
    with session_scope() as s:
        row = s.get(Setting, CATALOG_KEY)
        value = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    presets = value.get("presets")
    return {
        "synced_at": value.get("synced_at"),
        "error": value.get("error"),
        "presets": [p for p in presets if isinstance(p, dict)] if isinstance(presets, list) else [],
    }


def _save(document: dict[str, Any]) -> bool:
    """Store the catalogue; False when it is what was already there."""
    with session_scope() as s:
        row = s.get(Setting, CATALOG_KEY)
        if row is not None and row.value == document:
            return False
        if row is None:
            s.add(Setting(key=CATALOG_KEY, value=document))
        else:
            row.value = document
    return True


async def _store(document: dict[str, Any]) -> dict[str, Any]:
    if await asyncio.to_thread(_save, document):
        # after the commit: a client that refetches on the event must read what was just stored
        events.publish("presets.changed", {"status": "catalog"})
    return document


def view() -> dict[str, Any]:
    """The catalogue as the API shows it: each entry says whether Vibonomics manages that preset, as of now."""
    catalog = read_catalog()
    owned = {s for s in read_state().get("owned") or [] if isinstance(s, str)}
    return {
        "synced_at": catalog["synced_at"],
        "syncing": _running,
        "error": catalog["error"],
        "presets": [{**p, "managed": p.get("slug") in owned} for p in catalog["presets"]],
    }


# ---- syncing -------------------------------------------------------------------------------------------------


async def _sync_once() -> dict[str, Any]:
    base, key, _state = await asyncio.to_thread(load_access)
    previous = await asyncio.to_thread(read_catalog)
    if not key:
        return await _store({**previous, "error": NO_KEY})
    headers = _auth(key)
    try:
        body = await get_json(f"{base}/presets", headers=headers, timeout=TIMEOUT, retries=1)
        listing = body.get("data") if isinstance(body, dict) else None
        if not isinstance(listing, list):
            raise ValueError("the answer held no list of presets")
    except Exception as exc:
        # a transient failure must not empty the catalogue the page is showing
        why = scrub_secrets(failure_text(exc), headers)
        return await _store({**previous, "error": f"Could not read your presets from OpenRouter: {why}"})

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in listing:
        slug = r.get("slug") if isinstance(r, dict) else None
        if isinstance(slug, str) and slug and slug not in seen:
            seen.add(slug)
            rows.append(r)
    rows = rows[:MAX_PRESETS]
    notes: list[str] = []
    reported = body.get("total_count")
    # the list may be paged, or longer than the cap: either way the catalogue is short of the account
    if isinstance(reported, int) and reported > len(rows):
        notes.append(f"OpenRouter lists {reported} presets and only {len(rows)} are in the catalogue.")

    gate = asyncio.Semaphore(READ_CONCURRENCY)
    reads = await asyncio.gather(*(_read_one(base, headers, row, gate) for row in rows))
    earlier = {p.get("slug"): p for p in previous["presets"]}
    entries: list[dict[str, Any]] = []
    failed: list[tuple[str, str]] = []
    for row, (entry, why) in zip(rows, reads, strict=True):
        if entry is None:
            slug = row["slug"]
            failed.append((slug, why or ""))
            kept = earlier.get(slug)
            if kept is None:
                entry = _entry(row, {})
            else:
                # the list is newer than the old copy about these two; the old copy is all there is of the rest
                entry = {
                    **kept,
                    "name": _text(row.get("name")) or kept.get("name"),
                    "status": _text(row.get("status")) or kept.get("status"),
                }
        entries.append(entry)
    if failed:
        names = ", ".join(slug for slug, _why in failed[:5]) + (", ..." if len(failed) > 5 else "")
        notes.insert(
            0,
            f"The sync was partial: could not read {len(failed)} of {len(rows)} presets ({names}); their last copy "
            f"was kept. First error: {failed[0][1][:200]}",
        )

    entries.sort(key=lambda e: str(e.get("slug")))
    entries.sort(key=lambda e: e.get("updated_at") or "", reverse=True)  # newest first; the sort is stable
    return await _store({"synced_at": utcnow().isoformat(), "error": " ".join(notes) or None, "presets": entries})


async def _run() -> dict[str, Any]:
    global _running
    _running = True
    try:
        return await _sync_once()
    finally:
        _running = False


async def sync() -> dict[str, Any]:
    """Read the account's presets into the catalogue and return what is stored.

    Callers that arrive while a run is in flight share it, so a Refresh button and the timer do not each make the
    hundred requests. A problem with the key or with OpenRouter is stored in ``error``, not raised.
    """
    return await single_flight(CATALOG_KEY, _run)
