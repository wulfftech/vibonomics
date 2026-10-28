"""The price maths of ``frontend/src/lib/pricing.ts``, for API clients that cannot run the UI's copy.

The UI computes blended prices and value in the browser, from the profile the operator picks. A client such as
a script choosing which model to send work to needs the same figures from the server, so these functions are
a line-for-line copy. Keep the two in step: a formula changed in one must change in the other.

- Cache-aware input price: ``in_eff = input·(1-c) + (cache_read or input)·c``
- Estimated cost per task: ``(tin·in_eff + tout·output) / 1e6``
- Value: ``score / task_cost`` (infinite for a free model with a score)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    """The size of one unit of work, as a usage profile or an API client describes it."""

    input_tokens: int
    output_tokens: int
    cache_hit_rate: float = 0.0


def _known(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def effective_input_price(price_input: float | None, cache_read: float | None, cache_hit_rate: float) -> float | None:
    """The input price per 1M tokens once the cache hit rate is applied."""
    if price_input is None or not _known(price_input):
        return None
    c = min(1.0, max(0.0, cache_hit_rate))
    read = cache_read if cache_read is not None and _known(cache_read) else price_input
    return price_input * (1 - c) + read * c


def task_cost(
    price_input: float | None,
    price_output: float | None,
    cache_read: float | None,
    task: Task,
    is_free: bool = False,
) -> float | None:
    """Estimated USD for one task; 0 for a free model, None when a price is unknown."""
    if is_free:
        return 0.0
    in_eff = effective_input_price(price_input, cache_read, task.cache_hit_rate)
    if in_eff is None or price_output is None or not _known(price_output):
        return None
    return (task.input_tokens * in_eff + task.output_tokens * price_output) / 1e6


def value_of(score: float | None, cost: float | None) -> float | None:
    """Score points per dollar per task: infinite for a free model with a score, None without a score or price."""
    if score is None or not _known(score) or cost is None:
        return None
    if cost <= 0:
        return math.inf
    return score / cost
