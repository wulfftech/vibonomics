"""models.dev catalogue (MIT, https://github.com/anomalyco/models.dev).

Used as the base model list and price source for every provider, and as the source of
``canonical_model_id`` which lets Vibonomics match the same model across providers and benchmarks.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import get_config
from ..http import cached_json

URL = "https://models.dev/api.json"
_MAX_AGE_SECONDS = 15 * 60


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
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self._canonical: dict[str, CanonicalInfo] | None = None

    # -- providers ---------------------------------------------------------------------------

    def provider(self, provider_id: str) -> dict[str, Any] | None:
        value = self.data.get(provider_id)
        return value if isinstance(value, dict) else None

    def models(self, provider_id: str) -> dict[str, dict[str, Any]]:
        p = self.provider(provider_id)
        return p.get("models", {}) if p else {}

    def model(self, provider_id: str, model_id: str) -> dict[str, Any] | None:
        return self.models(provider_id).get(model_id)

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
        """Best-effort canonical id for a model id the catalogue doesn't list directly."""
        index = self.canonical_index()
        if model_id in index:
            return model_id
        key = normalize_model_key(model_id.split("/")[-1].split(":")[0])
        for cid in index:
            if normalize_model_key(cid.split("/")[-1]) == key:
                return cid
        return None


_DATE_SUFFIX = re.compile(r"[-_@](20\d{2}[-_]?\d{2}[-_]?\d{2}|\d{4}|\d{6}|\d{8})$")


def normalize_model_key(value: str) -> str:
    """Normalise a model name for fuzzy comparison: lowercase, drop dates, treat . and - alike."""
    s = value.lower().strip()
    s = s.split("/")[-1]
    s = s.replace(":free", "").replace("-free", "")
    s = _DATE_SUFFIX.sub("", s)
    s = re.sub(r"[\s._]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


_cache: tuple[float, ModelsDevCatalog] | None = None


async def load_catalog(force: bool = False) -> ModelsDevCatalog:
    global _cache
    now = time.monotonic()
    if not force and _cache and now - _cache[0] < _MAX_AGE_SECONDS:
        return _cache[1]
    data = await cached_json(URL, get_config().cache_dir / "models.dev.json", timeout=120)
    catalog = ModelsDevCatalog(data if isinstance(data, dict) else {})
    _cache = (now, catalog)
    return catalog
