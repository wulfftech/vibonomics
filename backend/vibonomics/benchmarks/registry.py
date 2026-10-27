"""All benchmark sources, keyed by ``spec.id``. Add new sources here."""

from __future__ import annotations

from .arena import ArenaSource
from .artificial_analysis import ArtificialAnalysisSource
from .base import BenchmarkSource, SourceSpec
from .epoch import EpochSource
from .livebench import LiveBenchSource
from .openrouter_aa import OpenRouterAASource, OpenRouterDesignArenaSource
from .openrouter_datasets import OpenRouterDatasetsSource
from .terminal_bench import TerminalBenchSource

SOURCES: dict[str, type[BenchmarkSource]] = {
    cls.spec.id: cls
    for cls in (
        TerminalBenchSource,
        EpochSource,
        ArenaSource,
        OpenRouterDatasetsSource,
        LiveBenchSource,
        ArtificialAnalysisSource,
        OpenRouterAASource,
        OpenRouterDesignArenaSource,
    )
}


def all_source_specs() -> list[SourceSpec]:
    return [cls.spec for cls in SOURCES.values()]
