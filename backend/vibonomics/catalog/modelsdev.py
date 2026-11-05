"""models.dev catalogue (MIT, https://github.com/anomalyco/models.dev).

Used as the base model list and price source for every provider, and as the source of
``canonical_model_id`` which lets Vibonomics match the same model across providers and benchmarks.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import get_config
from ..http import FetchError, cached_json, single_flight

log = logging.getLogger(__name__)

URL = "https://models.dev/api.json"
_MAX_AGE_SECONDS = 15 * 60
_RETRY_SECONDS = 60


@dataclass
class CanonicalInfo:
    canonical_id: str
    name: str
    family: str | None = None
    release_date: str | None = None
    open_weights: bool | None = None
    reasoning: bool | None = None
    provider_ids: set[str] = field(default_factory=set)


def creator_of(canonical_id: str | None) -> str | None:
    if canonical_id and "/" in canonical_id:
        return canonical_id.split("/", 1)[0]
    return None


class ModelsDevCatalog:
    def __init__(self, data: dict[str, Any], degraded: str | None = None):
        self.data = data
        self.degraded = degraded
        """Why this catalogue is empty when models.dev could not be reached and no earlier copy exists."""
        self._canonical: dict[str, CanonicalInfo] | None = None

    # -- providers ---------------------------------------------------------------------------

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        value = self.data.get(provider_id)
        return value if isinstance(value, dict) else None

    def models(self, provider_id: str) -> dict[str, dict[str, Any]]:
        if self.degraded:
            # An adapter builds its model list from this. Empty would read as "the provider has no models" and
            # the refresh would deactivate every row it has; an error leaves them as they were.
            raise FetchError(f"models.dev is unavailable and there is no earlier copy: {self.degraded}")
        p = self.provider(provider_id)
        return p.get("models", {}) if p else {}

    def model(self, provider_id: str, model_id: str) -> dict[str, Any] | None:
        # Not via models(): a lookup that finds nothing is enrichment that is missing, not a refusal.
        p = self.provider(provider_id)
        return p.get("models", {}).get(model_id) if p else None

    def canonical_for(self, provider_id: str, model_id: str) -> str | None:
        m = self.model(provider_id, model_id)
        if m and m.get("canonical_model_id"):
            return m["canonical_model_id"]
        return None

    # -- canonical index ---------------------------------------------------------------------

    def canonical_index(self) -> dict[str, CanonicalInfo]:
        if self._canonical is None:
            index: dict[str, CanonicalInfo] = {}
            for pid, provider in self.data.items():
                if not isinstance(provider, dict):
                    continue
                for m in provider.get("models", {}).values():
                    cid = m.get("canonical_model_id")
                    if not cid:
                        continue
                    info = index.get(cid)
                    if info is None:
                        info = CanonicalInfo(canonical_id=cid, name=m.get("name") or cid)
                        index[cid] = info
                    info.provider_ids.add(pid)
                    # Prefer facts from the model creator's own listing.
                    if pid == creator_of(cid) or info.family is None:
                        info.name = m.get("name") or info.name
                        info.family = m.get("family") or info.family
                        info.release_date = m.get("release_date") or info.release_date
                        info.open_weights = m.get("open_weights", info.open_weights)
                        info.reasoning = m.get("reasoning", info.reasoning)
            self._canonical = index
        return self._canonical

    def guess_canonical(self, model_id: str, name: str | None = None) -> str | None:
        """Best-effort canonical id for a model id the catalogue doesn't list directly.

        An exact id wins. Otherwise the candidates are the models that differ from it only by a date, a version
        number or ``-free``; of those the exact spelling is taken, then the undated model over a dated snapshot, and
        a tie is no answer, since a guess would hand one model another's scores.
        """
        index = self.canonical_index()
        if model_id in index:
            return model_id
        bare = model_id.split("/")[-1].split(":")[0].lower()
        key = normalize_model_key(bare)
        candidates = [cid for cid in index if normalize_model_key(cid.split("/")[-1]) == key]
        if len(candidates) > 1:
            candidates = [cid for cid in candidates if cid.split("/")[-1].lower() == bare] or candidates
        if len(candidates) > 1:
            candidates = [cid for cid in candidates if snapshot_suffix(cid) is None] or candidates
        return candidates[0] if len(candidates) == 1 else None


_DATE_SUFFIX = re.compile(r"[-_@](20\d{2}[-_]?\d{2}[-_]?\d{2}|\d{4}|\d{6}|\d{8})$")


def snapshot_suffix(value: str) -> str | None:
    """The date or version number a model id ends in (``-2411``, ``-2026-09-21``), digits only, else None."""
    match = _DATE_SUFFIX.search(re.sub(r"\s+", "-", value.lower().strip().split("/")[-1].split(":")[0]))
    return re.sub(r"\D", "", match.group(1)) if match else None


def normalize_model_key(value: str) -> str:
    """Normalise a model name for fuzzy comparison: lowercase, drop dates, treat . and - alike."""
    s = value.lower().strip()
    s = s.split("/")[-1]
    s = s.replace(":free", "").replace("-free", "")
    s = _DATE_SUFFIX.sub("", s)
    s = re.sub(r"[\s._]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _is_catalog(data: Any) -> bool:
    """models.dev answers ``{provider_id: {..., "models": {...}}}``; anything else is an error body or a stub."""
    return isinstance(data, dict) and any(
        isinstance(provider, dict) and isinstance(provider.get("models"), dict) for provider in data.values()
    )


_cache: tuple[float, ModelsDevCatalog] | None = None
"""When the catalogue held here stops being fresh (``time.monotonic``), and the catalogue."""


async def load_catalog(force: bool = False) -> ModelsDevCatalog:
    """The catalogue, from models.dev, else the last good copy on disk, else the one already in memory.

    With none of those it returns an empty catalogue whose ``degraded`` says why, rather than raising: most of what
    asks for it only enriches (descriptions, canonical ids), and a provider test or a refresh of providers that
    don't depend on it should not fail because models.dev is down. It is retried after a minute, not on every call.
    """
    if not force and _cache and time.monotonic() < _cache[0]:
        return _cache[1]
    # Callers that arrive while a load runs wait for it and share its catalogue, rather than each fetching and
    # building its own index (the first details request after the copy expires used to wait out the whole fetch).
    return await single_flight("models.dev", _load)


async def _load() -> ModelsDevCatalog:
    global _cache
    now = time.monotonic()
    try:
        data = await cached_json(URL, get_config().cache_dir / "models.dev.json", timeout=120, validate=_is_catalog)
    except FetchError as exc:
        previous = _cache[1] if _cache and not _cache[1].degraded else None
        log.warning(
            "models.dev is unavailable (%s); %s", exc, "using the copy in memory" if previous else "going on without it"
        )
        catalog = previous or ModelsDevCatalog({}, degraded=str(exc))
        _cache = (now + _RETRY_SECONDS, catalog)
        return catalog
    catalog = ModelsDevCatalog(data)
    _cache = (now + _MAX_AGE_SECONDS, catalog)
    return catalog
