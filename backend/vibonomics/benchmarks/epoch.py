"""Epoch AI Benchmarking Hub (https://epoch.ai/benchmarks).

Epoch publishes everything as one zip of CSVs (~2-3 MB). We only use data Epoch produces itself,
which is CC BY 4.0: the Epoch Capabilities Index and Epoch's own SWE-bench Verified and MirrorCode
runs. The ``*_external.csv`` files are other leaderboards' results that keep their original
licences, so they are left alone.

``model_metadata.csv`` maps Epoch's API-style model versions (``claude-opus-5-5_max``) to model
groups and display names, which we pass along as matching hints.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx

from ..http import FetchError, get_client
from .base import BenchmarkSource, MetricSpec, ScoreRecord, SourceSpec
from .matching import effort_word, split_qualifiers

ZIP_URL = "https://epoch.ai/data/benchmark_data.zip"
MAX_BYTES = 50 * 1024 * 1024
MAX_HINTS = 6
_ECI = "epoch_capabilities_index/eci_scores.csv"
_RUNS = {
    # file → (metric, url)
    "swe_bench_verified.csv": ("swe_bench_verified", "https://epoch.ai/benchmarks/swe-bench-verified"),
    "mirrorcode.csv": ("mirrorcode", "https://epoch.ai/benchmarks/mirrorcode"),
}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


async def download_zip(url: str = ZIP_URL, max_bytes: int = MAX_BYTES) -> bytes:
    """Stream the archive into memory, refusing anything larger than ``max_bytes``."""
    try:
        async with get_client().stream("GET", url, timeout=120) as resp:
            if resp.status_code >= 400:
                raise FetchError(f"GET {url}: HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FetchError(f"GET {url}: archive is {int(declared) // 1_048_576} MB, over the cap")
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise FetchError(f"GET {url}: archive exceeds {max_bytes // 1_048_576} MB, giving up")
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise FetchError(f"GET {url}: {type(exc).__name__}: {exc}") from exc


def read_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Rows of one CSV in the archive (matched by path suffix, so a top-level folder doesn't matter)."""
    member = next((n for n in zf.namelist() if n == name or n.endswith("/" + name)), None)
    if member is None:
        return []
    text = zf.read(member).decode("utf-8-sig", errors="replace")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def split_version(model_version: str) -> tuple[str, str]:
    """'claude-opus-5-5_max' → ('claude-opus-5-5', 'max'); 'glm-5.3_unknown' → ('glm-5.3', '')."""
    base, sep, suffix = model_version.rpartition("_")
    if sep and base:
        word = effort_word(suffix)
        if word or suffix.lower() in ("unknown", "default"):
            return base, word
    return model_version, ""


_FULL_DATE = re.compile(r"-20\d{2}-?\d{2}-?\d{2}$")


def _dedupe(values: list[str]) -> list[str]:
    """Unique hints, with 'claude-opus-4-5' ahead of 'claude-opus-4-5-20251101'.

    models.dev often lists a dated snapshot and its alias as two canonical models; the alias has more
    offerings, and a full-date snapshot id would otherwise win the id lookup.
    """
    expanded: list[str] = []
    for v in values:
        if v and _FULL_DATE.search(v):
            expanded.append(_FULL_DATE.sub("", v))
        expanded.append(v)
    seen: set[str] = set()
    out = []
    for v in expanded:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out[:MAX_HINTS]


class EpochSource(BenchmarkSource):
    spec = SourceSpec(
        id="epoch",
        name="Epoch AI Benchmarking Hub",
        homepage="https://epoch.ai/benchmarks",
        description="Epoch Capabilities Index (a single general-capability scale built from dozens of "
        "benchmarks) plus Epoch's own SWE-bench Verified and MirrorCode runs.",
        license="CC BY 4.0",
        attribution="Data: Epoch AI, 'Capabilities & benchmarking', epoch.ai/benchmarks, CC BY 4.0",
        default_enabled=True,
        metrics=(
            MetricSpec(
                "eci",
                "Epoch Capabilities Index",
                "intelligence",
                weight=1.5,
                description="Epoch's general capability score, fitted across many benchmarks.",
            ),
            MetricSpec(
                "swe_bench_verified",
                "SWE-bench Verified (Epoch runs)",
                "coding",
                weight=0.75,
                unit="%",
                description="Epoch's own runs; near saturation and not run on the newest models.",
            ),
            MetricSpec(
                "mirrorcode",
                "MirrorCode (Epoch)",
                "coding",
                weight=0.5,
                unit="%",
                description="Epoch's long-horizon software reimplementation benchmark; few models so far.",
            ),
        ),
    )

    async def fetch(self) -> list[ScoreRecord]:
        blob = await download_zip()
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise FetchError(f"{ZIP_URL}: not a zip archive") from exc
        with zf:
            return parse_archive(zf)


def parse_archive(zf: zipfile.ZipFile) -> list[ScoreRecord]:
    today = datetime.now(UTC).date().isoformat()
    meta = {r["model_version"]: r for r in read_csv(zf, "model_metadata.csv") if r.get("model_version")}
    by_group: dict[str, list[str]] = {}
    for version, row in meta.items():
        if row.get("model_group"):
            by_group.setdefault(row["model_group"].lower(), []).append(split_version(version)[0])

    records: list[ScoreRecord] = []
    for row in read_csv(zf, _ECI):
        name = row.get("Model") or row.get("Display name")
        value = _num(row.get("eci"))
        if not name or value is None:
            continue
        versions = [split_version(v.strip())[0] for v in (row.get("model_versions") or "").replace(";", ",").split(",")]
        group_ids = [v for v in by_group.get(name.lower(), []) if "/" not in v]
        records.append(
            ScoreRecord(
                metric="eci",
                source_model=name,
                value=round(value, 2),
                model_ids=_dedupe([row.get("Display name") or "", *versions, *group_ids]),
                as_of=today,
                url="https://epoch.ai/eci",
                extra={
                    "ci": [_num(row.get("eci_ci_low")), _num(row.get("eci_ci_high"))],
                    "organization": row.get("Organization") or None,
                    "release_date": row.get("date") or None,
                },
            )
        )

    for file, (metric, url) in _RUNS.items():
        best: dict[str, ScoreRecord] = {}
        for row in read_csv(zf, file):
            version = row.get("Model version")
            value = _num(row.get("Best score (across scorers)"))
            if value is None:
                value = _num(row.get("mean_score"))
            if not version or value is None:
                continue
            base, effort = split_version(version)
            info = meta.get(version) or {}
            display = split_qualifiers(info.get("display_name") or "")[0]
            pct = round(value * 100 if value <= 1 else value, 2)
            cur = best.get(version)
            if cur is not None and cur.value >= pct:
                continue  # Epoch sometimes lists repeated runs; keep the best
            best[version] = ScoreRecord(
                metric=metric,
                source_model=version,
                value=pct,
                effort=effort,
                model_ids=_dedupe([base, info.get("model_group") or "", display]),
                as_of=(row.get("Started at") or "")[:10] or today,
                url=url,
                extra={
                    "stderr": _num(row.get("stderr")),
                    "organization": row.get("Organization") or None,
                    "release_date": row.get("Release date") or None,
                },
            )
        records.extend(best.values())

    if not any(r.metric == "eci" for r in records):
        raise FetchError("Epoch: eci_scores.csv missing or empty in benchmark_data.zip")
    return records
