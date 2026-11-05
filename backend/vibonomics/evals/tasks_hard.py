"""The Hard suite: tasks meant to separate models that all clear the Standard suite.

Run 3 of the Standard suite gave 119 of its 135 graded rows a perfect score, so it could not tell the free models
apart. These tasks keep the visible tests thin and put the difficulty in the rules the prompt states and the
hidden tests check: boundary cases, what must not be mutated, an exact format, a value that depends on an earlier
tool result. Every coding task still ships a reference solution that ``tests/evals/test_suites.py`` runs against
its visible and hidden tests, and proves the starting workspace fails them.
"""

from __future__ import annotations

from .llm import ToolSpec
from .suites import TaskDef
from .tasks_coding import _task

# -- agentic coding ---------------------------------------------------------------------------

INTERVALS_WORKSPACE = {
    "intervals.py": '''def merge_intervals(intervals):
    """Merge overlapping or touching half-open intervals."""
    raise NotImplementedError
''',
    "tests/test_intervals.py": """import unittest

from intervals import merge_intervals


class MergeTests(unittest.TestCase):
    def test_overlapping(self):
        self.assertEqual(merge_intervals([(1, 4), (3, 6)]), [(1, 6)])

    def test_separate_and_unsorted(self):
        self.assertEqual(merge_intervals([(5, 7), (1, 2)]), [(1, 2), (5, 7)])

    def test_empty_input(self):
        self.assertEqual(merge_intervals([]), [])
""",
}

INTERVALS_HIDDEN = {
    "tests/test_intervals_rules.py": """import unittest

from intervals import merge_intervals


class MergeRuleTests(unittest.TestCase):
    def test_touching_intervals_merge(self):
        self.assertEqual(merge_intervals([(1, 3), (3, 5)]), [(1, 5)])

    def test_contained_intervals_are_absorbed(self):
        self.assertEqual(merge_intervals([(1, 10), (2, 3), (4, 5)]), [(1, 10)])

    def test_empty_intervals_are_dropped(self):
        self.assertEqual(merge_intervals([(2, 2), (5, 4), (1, 3)]), [(1, 3)])

    def test_the_input_is_not_modified(self):
        data = [[5, 7], [1, 6]]
        merge_intervals(data)
        self.assertEqual(data, [[5, 7], [1, 6]])

    def test_lists_come_back_as_tuples(self):
        self.assertEqual(merge_intervals([[1, 2], [4, 5]]), [(1, 2), (4, 5)])

    def test_floats_and_negatives(self):
        self.assertEqual(merge_intervals([(-3.5, -1), (-2, 0.5)]), [(-3.5, 0.5)])

    def test_an_item_that_is_not_a_pair_raises_value_error(self):
        for bad in ([(1, 2, 3)], [5], [(1,)], ["x"]):
            with self.assertRaises(ValueError):
                merge_intervals(bad)
""",
}

INTERVALS_REFERENCE = {
    "intervals.py": '''def merge_intervals(intervals):
    """Merge overlapping or touching half-open intervals."""
    pairs = []
    for item in intervals:
        try:
            start, end = item
        except (TypeError, ValueError):
            raise ValueError(f"not a (start, end) pair: {item!r}") from None
        if start < end:
            pairs.append((start, end))
    pairs.sort()
    merged = []
    for start, end in pairs:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
''',
}

CACHE_WORKSPACE = {
    "cache.py": '''import time


class TTLCache:
    """A cache holding at most `capacity` entries, each valid for `ttl` seconds."""

    def __init__(self, capacity, ttl, clock=time.monotonic):
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def get(self, key, default=None):
        raise NotImplementedError

    def __len__(self):
        raise NotImplementedError

    def __contains__(self, key):
        raise NotImplementedError
''',
    "tests/test_cache.py": """import unittest

from cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BasicTests(unittest.TestCase):
    def test_set_and_get(self):
        cache = TTLCache(2, 10, clock=Clock())
        cache.set("a", 1)
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("missing"))
        self.assertEqual(cache.get("missing", "fallback"), "fallback")

    def test_an_entry_expires(self):
        clock = Clock()
        cache = TTLCache(2, 10, clock=clock)
        cache.set("a", 1)
        clock.advance(11)
        self.assertIsNone(cache.get("a"))

    def test_the_least_recently_used_entry_is_evicted(self):
        cache = TTLCache(2, 100, clock=Clock())
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
""",
}

CACHE_HIDDEN = {
    "tests/test_cache_rules.py": """import unittest

from cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def cache(self, capacity=2, ttl=10):
        return TTLCache(capacity, ttl, clock=self.clock)

    def test_an_entry_is_expired_exactly_at_its_ttl(self):
        cache = self.cache()
        cache.set("a", 1)
        self.clock.advance(9)
        self.assertEqual(cache.get("a"), 1)
        self.clock.advance(1)
        self.assertIsNone(cache.get("a"))

    def test_get_does_not_extend_the_expiry(self):
        cache = self.cache()
        cache.set("a", 1)
        self.clock.advance(6)
        self.assertEqual(cache.get("a"), 1)
        self.clock.advance(5)
        self.assertIsNone(cache.get("a"))

    def test_set_restarts_the_ttl(self):
        cache = self.cache()
        cache.set("a", 1)
        self.clock.advance(6)
        cache.set("a", 2)
        self.clock.advance(6)
        self.assertEqual(cache.get("a"), 2)

    def test_get_makes_an_entry_the_most_recently_used(self):
        cache = self.cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")
        cache.set("c", 3)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("c"), 3)

    def test_updating_a_key_at_capacity_evicts_nothing(self):
        cache = self.cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)
        self.assertEqual(cache.get("a"), 10)
        self.assertEqual(cache.get("b"), 2)

    def test_expired_entries_do_not_cost_a_live_one_its_place(self):
        cache = self.cache()
        cache.set("a", 1)
        self.clock.advance(5)
        cache.set("b", 2)
        self.clock.advance(6)
        cache.set("c", 3)
        self.assertEqual(cache.get("b"), 2)
        self.assertEqual(cache.get("c"), 3)

    def test_len_counts_live_entries_only(self):
        cache = self.cache()
        cache.set("a", 1)
        self.clock.advance(5)
        cache.set("b", 2)
        self.assertEqual(len(cache), 2)
        self.clock.advance(6)
        self.assertEqual(len(cache), 1)

    def test_membership_does_not_refresh_recency(self):
        cache = self.cache()
        cache.set("a", 1)
        cache.set("b", 2)
        self.assertIn("a", cache)
        cache.set("c", 3)
        self.assertNotIn("a", cache)
        self.assertIn("b", cache)

    def test_bad_arguments_raise_value_error(self):
        for capacity, ttl in ((0, 1), (-1, 1), (1, 0), (1, -5)):
            with self.assertRaises(ValueError):
                TTLCache(capacity, ttl)
""",
}

CACHE_REFERENCE = {
    "cache.py": '''import time
from collections import OrderedDict


class TTLCache:
    """A cache holding at most `capacity` entries, each valid for `ttl` seconds."""

    def __init__(self, capacity, ttl, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.capacity = capacity
        self.ttl = ttl
        self._clock = clock
        self._items = OrderedDict()  # key -> (value, stored_at), least recently used first

    def _expired(self, stored_at):
        return self._clock() - stored_at >= self.ttl

    def _purge(self):
        for key in [k for k, (_value, stored_at) in self._items.items() if self._expired(stored_at)]:
            del self._items[key]

    def set(self, key, value):
        if key in self._items:
            del self._items[key]
        elif len(self._items) >= self.capacity:
            self._purge()
            if len(self._items) >= self.capacity:
                self._items.popitem(last=False)
        self._items[key] = (value, self._clock())

    def get(self, key, default=None):
        entry = self._items.get(key)
        if entry is None:
            return default
        value, stored_at = entry
        if self._expired(stored_at):
            del self._items[key]
            return default
        self._items.move_to_end(key)
        return value

    def __len__(self):
        self._purge()
        return len(self._items)

    def __contains__(self, key):
        entry = self._items.get(key)
        return entry is not None and not self._expired(entry[1])
''',
}

SEMVER_WORKSPACE = {
    "semver.py": '''def compare(a, b):
    """Return -1, 0 or 1 as version a has lower, equal or higher precedence than b."""
    raise NotImplementedError
''',
    "tests/test_semver.py": """import unittest

from semver import compare


class CompareTests(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(compare("1.2.3", "1.2.3"), 0)

    def test_ordering(self):
        self.assertEqual(compare("1.2.3", "1.2.4"), -1)
        self.assertEqual(compare("2.0.0", "1.9.9"), 1)

    def test_a_prerelease_is_lower_than_its_release(self):
        self.assertEqual(compare("1.0.0-alpha", "1.0.0"), -1)
""",
}

SEMVER_HIDDEN = {
    "tests/test_semver_rules.py": """import unittest

from semver import compare


class PrecedenceTests(unittest.TestCase):
    def test_numeric_parts_compare_as_numbers(self):
        self.assertEqual(compare("1.10.0", "1.9.0"), 1)

    def test_the_precedence_chain_from_the_spec(self):
        chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for lower, higher in zip(chain, chain[1:]):
            self.assertEqual(compare(lower, higher), -1, (lower, higher))
            self.assertEqual(compare(higher, lower), 1, (lower, higher))

    def test_build_metadata_is_ignored(self):
        self.assertEqual(compare("1.0.0+build1", "1.0.0+build2"), 0)
        self.assertEqual(compare("1.0.0-rc.1+x", "1.0.0-rc.1"), 0)

    def test_numeric_identifiers_rank_below_alphanumeric_ones(self):
        self.assertEqual(compare("1.0.0-1", "1.0.0-a"), -1)
        self.assertEqual(compare("1.0.0-2", "1.0.0-10"), -1)

    def test_alphanumeric_identifiers_compare_in_ascii_order(self):
        self.assertEqual(compare("1.0.0-Beta", "1.0.0-alpha"), -1)


class InvalidVersionTests(unittest.TestCase):
    def test_malformed_versions_raise_value_error(self):
        for bad in ("1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.3-", "1.2.3-01", "a.b.c", "", "1.2.3+", "v1.2.3"):
            with self.assertRaises(ValueError, msg=bad):
                compare(bad, "1.0.0")
            with self.assertRaises(ValueError, msg=bad):
                compare("1.0.0", bad)
""",
}

SEMVER_REFERENCE = {
    "semver.py": r'''import re

_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    rf"(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _parse(text):
    match = _VERSION.match(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"not a semantic version: {text!r}")
    major, minor, patch, prerelease, _build = match.groups()
    return (int(major), int(minor), int(patch)), (prerelease.split(".") if prerelease else None)


def _cmp(a, b):
    return (a > b) - (a < b)


def compare(a, b):
    """Return -1, 0 or 1 as version a has lower, equal or higher precedence than b."""
    core_a, pre_a = _parse(a)
    core_b, pre_b = _parse(b)
    if core_a != core_b:
        return _cmp(core_a, core_b)
    if pre_a is None or pre_b is None:
        return _cmp(pre_a is None, pre_b is None)
    for x, y in zip(pre_a, pre_b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return _cmp(int(x), int(y))
        if x.isdigit():
            return -1
        if y.isdigit():
            return 1
        return _cmp(x, y)
    return _cmp(len(pre_a), len(pre_b))
''',
}

ORDER_WORKSPACE = {
    "buildorder.py": '''class CycleError(ValueError):
    """Raised when the dependencies contain a cycle."""


def build_order(deps):
    """Return every package, each after the packages it depends on."""
    raise NotImplementedError
''',
    "tests/test_buildorder.py": """import unittest

from buildorder import CycleError, build_order


class BuildOrderTests(unittest.TestCase):
    def test_dependencies_come_first(self):
        self.assertEqual(build_order({"app": ["lib", "log"], "lib": ["log"]}), ["log", "lib", "app"])

    def test_independent_packages_are_alphabetical(self):
        self.assertEqual(build_order({"b": [], "a": []}), ["a", "b"])

    def test_a_cycle_raises(self):
        with self.assertRaises(CycleError):
            build_order({"a": ["b"], "b": ["a"]})
""",
}

ORDER_HIDDEN = {
    "tests/test_buildorder_rules.py": """import unittest

from buildorder import CycleError, build_order


class RuleTests(unittest.TestCase):
    def test_packages_named_only_as_dependencies_are_included(self):
        self.assertEqual(build_order({"app": ["libc"]}), ["libc", "app"])

    def test_a_duplicate_dependency_counts_once(self):
        self.assertEqual(build_order({"app": ["lib", "lib"], "lib": []}), ["lib", "app"])

    def test_the_smallest_name_goes_first_among_those_ready(self):
        self.assertEqual(build_order({"z": [], "m": ["z"], "a": ["z"], "b": []}), ["b", "z", "a", "m"])

    def test_empty_input(self):
        self.assertEqual(build_order({}), [])

    def test_a_package_that_needs_itself_is_a_cycle(self):
        with self.assertRaises(CycleError):
            build_order({"a": ["a"]})

    def test_the_message_names_the_cycle_and_nothing_else(self):
        deps = {"alpha": ["beta"], "beta": ["gamma"], "gamma": ["alpha"], "omega": ["alpha"], "free": []}
        with self.assertRaises(CycleError) as caught:
            build_order(deps)
        message = str(caught.exception)
        for name in ("alpha", "beta", "gamma"):
            self.assertIn(name, message)
        self.assertNotIn("omega", message)
        self.assertNotIn("free", message)

    def test_a_cycle_error_is_a_value_error(self):
        self.assertTrue(issubclass(CycleError, ValueError))
""",
}

ORDER_REFERENCE = {
    "buildorder.py": '''import heapq


class CycleError(ValueError):
    """Raised when the dependencies contain a cycle."""


def _find_cycle(graph, stuck):
    """One cycle among the packages the sort could not place, smallest name first."""
    node = min(stuck)
    path = []
    seen = {}
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = min(dep for dep in graph[node] if dep in stuck)
    return path[seen[node]:] + [node]


def build_order(deps):
    """Return every package, each after the packages it depends on."""
    graph = {name: set(requires) for name, requires in deps.items()}
    for requires in list(graph.values()):
        for name in requires:
            graph.setdefault(name, set())
    dependents = {name: [] for name in graph}
    for name, requires in graph.items():
        for dep in requires:
            dependents[dep].append(name)
    blocked = {name: len(requires) for name, requires in graph.items()}
    ready = [name for name, count in blocked.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for dependent in dependents[name]:
            blocked[dependent] -= 1
            if blocked[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(order) < len(graph):
        stuck = set(graph) - set(order)
        raise CycleError("cycle: " + " -> ".join(_find_cycle(graph, stuck)))
    return order
''',
}

CART_WORKSPACE = {
    "pricing.py": """def line_total(price, quantity):
    return price * quantity


def apply_discount(amount, percent):
    return round(amount - amount * percent / 100, 2)
""",
    "cart.py": """from pricing import apply_discount, line_total


class Cart:
    def __init__(self, items=[]):
        self.items = items

    def add(self, name, price, quantity=1):
        self.items.append((name, price, quantity))

    def subtotal(self):
        return sum(line_total(price, quantity) for _name, price, quantity in self.items)

    def total(self, discount_percent=0):
        return apply_discount(self.subtotal(), discount_percent)
""",
    "tests/test_cart.py": """import unittest

from cart import Cart


class CartTests(unittest.TestCase):
    def test_total_without_a_discount(self):
        cart = Cart()
        cart.add("pen", 1.5, 2)
        cart.add("ink", 4.0)
        self.assertEqual(cart.total(), 7.0)

    def test_total_with_a_discount(self):
        cart = Cart()
        cart.add("pen", 10.0, 3)
        self.assertEqual(cart.total(10), 27.0)

    def test_separate_carts_do_not_share_items(self):
        first = Cart()
        first.add("pen", 1.0)
        self.assertEqual(Cart().items, [])
""",
}

CART_HIDDEN = {
    "tests/test_cart_rules.py": """import unittest

from cart import Cart


class RuleTests(unittest.TestCase):
    def test_a_half_cent_rounds_up(self):
        cart = Cart()
        cart.add("sticker", 0.25)
        self.assertEqual(cart.total(50), 0.13)

    def test_another_half_cent_rounds_up(self):
        cart = Cart()
        cart.add("sticker", 0.625)
        self.assertEqual(cart.total(), 0.63)

    def test_a_discount_outside_0_to_100_raises_value_error(self):
        cart = Cart()
        cart.add("pen", 2.0)
        for bad in (-1, 100.5, 101):
            with self.assertRaises(ValueError):
                cart.total(bad)

    def test_the_limits_of_the_range_are_allowed(self):
        cart = Cart()
        cart.add("pen", 2.0)
        self.assertEqual(cart.total(0), 2.0)
        self.assertEqual(cart.total(100), 0.0)

    def test_the_list_passed_in_is_not_changed(self):
        items = [("pen", 2.0, 1)]
        cart = Cart(items)
        cart.add("ink", 1.0)
        self.assertEqual(items, [("pen", 2.0, 1)])
        self.assertEqual(cart.total(), 3.0)
""",
}

CART_REFERENCE = {
    "pricing.py": """from decimal import ROUND_HALF_UP, Decimal


def line_total(price, quantity):
    return price * quantity


def apply_discount(amount, percent):
    if not 0 <= percent <= 100:
        raise ValueError("a discount must be between 0 and 100 percent")
    discounted = Decimal(str(amount)) * (Decimal(100) - Decimal(str(percent))) / Decimal(100)
    return float(discounted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
""",
    "cart.py": """from pricing import apply_discount, line_total


class Cart:
    def __init__(self, items=None):
        self.items = [] if items is None else list(items)

    def add(self, name, price, quantity=1):
        self.items.append((name, price, quantity))

    def subtotal(self):
        return sum(line_total(price, quantity) for _name, price, quantity in self.items)

    def total(self, discount_percent=0):
        return apply_discount(self.subtotal(), discount_percent)
""",
}

HARD_INTERVALS = _task(
    id="agentic-intervals",
    name="Merge intervals to the letter",
    description="Merge half-open intervals: touching ones merge, empty ones go, the input stays untouched.",
    prompt=(
        "Implement `merge_intervals(intervals)` in `intervals.py`.\n\n"
        "`intervals` is a list of `(start, end)` pairs of numbers, each half-open: `[start, end)`. Return a new "
        "sorted list of `(start, end)` tuples in which overlapping or touching intervals are merged, so `(1, 3)` "
        "and `(3, 5)` become `(1, 5)`. An interval with `start >= end` is empty and is dropped. Do not modify the "
        "list you are given or the pairs in it, and return tuples even if the pairs came in as lists. Empty input "
        "gives `[]`. An item that is not a pair of two values raises `ValueError`. Run the tests and make them pass."
    ),
    workspace=INTERVALS_WORKSPACE,
    hidden_tests=INTERVALS_HIDDEN,
    reference=INTERVALS_REFERENCE,
    max_steps=14,
    judge_rubric=(
        "Does it follow every stated rule (touching merge, empty dropped, no mutation, tuples, ValueError for a "
        "non-pair) rather than only the visible examples? Penalise sorting the caller's list in place."
    ),
)

HARD_CACHE = _task(
    id="agentic-ttl-cache",
    name="A cache with expiry and eviction",
    description="Implement an LRU cache whose entries also expire, with an injectable clock.",
    prompt=(
        "Implement `TTLCache` in `cache.py`.\n\n"
        "`TTLCache(capacity, ttl, clock=time.monotonic)` holds at most `capacity` entries, each valid for `ttl` "
        "seconds. An entry is expired once `clock() - stored_at >= ttl`. `set(key, value)` stores the value and "
        "restarts that key's ttl. `get(key, default=None)` returns the value if the key is present and not expired, "
        "else `default`; a successful `get` makes the key the most recently used but does NOT extend its expiry. "
        "When `set` adds a new key to a full cache it first drops expired entries, and only if the cache is still "
        "full evicts the least recently used entry. Updating a key that is already present never evicts another. "
        "`len(cache)` counts live entries only, and `key in cache` agrees with `get` but does not change recency. "
        "A `capacity` below 1 or a `ttl` of 0 or less raises `ValueError`. Run the tests and make them pass."
    ),
    workspace=CACHE_WORKSPACE,
    hidden_tests=CACHE_HIDDEN,
    reference=CACHE_REFERENCE,
    max_steps=14,
    judge_rubric=(
        "Are expiry and recency kept separate (get refreshes recency, not the ttl)? Does eviction purge expired "
        "entries before evicting a live one? Penalise O(n) scans where an ordered mapping would do only if the "
        "behaviour is also wrong."
    ),
)

HARD_SEMVER = _task(
    id="agentic-semver",
    name="Compare semantic versions",
    description="Implement Semantic Versioning 2.0.0 precedence, including pre-releases and rejecting bad input.",
    prompt=(
        "Implement `compare(a, b)` in `semver.py` for version strings like `1.2.3`, `1.0.0-alpha.1` and "
        "`2.0.0+build5`. Return -1, 0 or 1 as `a` has lower, equal or higher precedence than `b`, following "
        "Semantic Versioning 2.0.0: compare major, minor and patch as numbers; ignore build metadata (after `+`); a "
        "version with a pre-release (after `-`) is lower than the same version without one; compare pre-release "
        "identifiers (split on `.`) left to right, numeric identifiers as numbers, other identifiers in ASCII order, "
        "a numeric identifier lower than an alphanumeric one, and the shorter list lower when every earlier "
        "identifier is equal. Anything that is not a valid version raises `ValueError`: that includes a missing or "
        "extra part, a leading zero in a numeric part (`01.2.3`, or `1.0.0-01`), an empty pre-release or build, and "
        "a leading `v`. Run the tests and make them pass."
    ),
    workspace=SEMVER_WORKSPACE,
    hidden_tests=SEMVER_HIDDEN,
    reference=SEMVER_REFERENCE,
    max_steps=14,
    judge_rubric=(
        "Does it implement the precedence rules rather than string comparison? Does it validate strictly and "
        "ignore build metadata? Penalise comparing pre-release text as a whole string."
    ),
)

HARD_ORDER = _task(
    id="agentic-build-order",
    name="Dependency order with cycle detection",
    description="Order packages by dependency with a deterministic tie-break, and report a cycle precisely.",
    prompt=(
        "Implement `build_order(deps)` in `buildorder.py`.\n\n"
        "`deps` maps a package name to the list of packages it depends on, e.g. "
        "`{'app': ['lib', 'log'], 'lib': ['log']}`. Return a list of every package, each after all the packages it "
        "depends on. A package that is named only as a dependency (not as a key) must appear too. Whenever several "
        "packages could go next, take the alphabetically smallest first. A dependency listed twice counts once. If "
        "the dependencies contain a cycle, including a package that depends on itself, raise `CycleError` with a "
        "message of the form `cycle: a -> b -> a` that names only the packages on one cycle: not packages that "
        "merely depend on it. Run the tests and make them pass."
    ),
    workspace=ORDER_WORKSPACE,
    hidden_tests=ORDER_HIDDEN,
    reference=ORDER_REFERENCE,
    max_steps=14,
    judge_rubric=(
        "Does it pick the alphabetically smallest ready package each time (not depth-first order)? Does the cycle "
        "message list only the cycle's members? Penalise reporting every package the sort could not place."
    ),
)

HARD_CART = _task(
    id="agentic-cart-bugs",
    name="Two bugs behind one bug report",
    description="Carts leak items into each other and some discounted totals are a cent off; fix both causes.",
    prompt=(
        "Bug report: 'Carts created separately sometimes contain each other's items, and some totals with a "
        "discount are a cent off.' Find both causes in `cart.py` and `pricing.py` and fix them.\n\n"
        "Amounts round half up to the cent (0.125 becomes 0.13) and `total` returns a float. A discount outside "
        "0 to 100 percent must raise `ValueError`. `Cart(items)` starts from a copy of `items` and never changes "
        "the list it was given. Do not modify the tests. Run the tests and make them pass."
    ),
    workspace=CART_WORKSPACE,
    hidden_tests=CART_HIDDEN,
    reference=CART_REFERENCE,
    max_steps=14,
    judge_rubric=(
        "Did it find both causes (a shared mutable default, and banker's rounding from round())? Did it fix "
        "rounding with a decimal rule rather than nudging floats? Penalise fixing only the bug the visible test "
        "shows."
    ),
)

# -- tool use ---------------------------------------------------------------------------------

ORDER_TOOLS = (
    ToolSpec(
        name="find_user",
        description="Find a customer by email address. Returns the customer's id.",
        parameters={"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
    ),
    ToolSpec(
        name="list_orders",
        description="List a customer's orders as id and date, in no particular order.",
        parameters={"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
    ),
    ToolSpec(
        name="get_order",
        description="Get one order's details, including its total.",
        parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    ),
    ToolSpec(
        name="refund_order",
        description="Refund an order. Only for when the user asks for a refund.",
        parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    ),
)

HARD_TOOL_CHAIN = TaskDef(
    id="tool-chain-ids",
    name="Chain three calls through returned ids",
    description="Each call needs an id from the last result, and the right order must be picked from a list.",
    kind="tools",
    category="tool_use",
    prompt="How much was the most recent order placed by dana@example.com?",
    system="Use the tools to answer. Make one call at a time, because each needs what the last returned.",
    tools=ORDER_TOOLS,
    tool_results={
        "find_user": '{"id": "u_482", "name": "Dana"}',
        "list_orders": (
            '{"orders": [{"id": "o_91", "date": "2026-10-02"}, {"id": "o_93", "date": "2026-10-29"}, '
            '{"id": "o_92", "date": "2026-10-15"}]}'
        ),
        "get_order": '{"id": "o_93", "total": 184.20, "currency": "AUD"}',
        "refund_order": '{"ok": true}',
    },
    check={
        "tool_calls": {
            "mode": "sequence",
            "expect": [
                {"name": "find_user", "args": {"email": "dana@example.com"}},
                {"name": "list_orders", "args": {"user_id": "u_482"}},
                {"name": "get_order", "args": {"order_id": "o_93"}},
            ],
            "args_exact": True,
        },
        "answer": {"contains_all": ["184.2"]},
    },
    max_steps=6,
)

CALENDAR_TOOLS = (
    ToolSpec(
        name="create_event",
        description="Create a calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {
                    "type": "string",
                    "description": "Local start time as YYYY-MM-DDTHH:MM, with no seconds and no timezone.",
                },
                "duration_minutes": {"type": "integer"},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Email addresses."},
            },
            "required": ["title", "start", "duration_minutes", "attendees"],
        },
    ),
    ToolSpec(
        name="send_email",
        description="Send an email.",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}},
            "required": ["to", "subject"],
        },
    ),
)

HARD_TOOL_SCHEDULE = TaskDef(
    id="tool-schedule",
    name="Turn a relative date into exact arguments",
    description="Work out 'next Tuesday' from today's date and pass every argument in the exact form asked for.",
    kind="tools",
    category="tool_use",
    prompt=(
        "Book a 45 minute planning session called 'Q1 planning' next Tuesday at 2:30pm with sam@example.com and "
        "lee@example.com. Do not send any separate email."
    ),
    system="Today is Wednesday 2026-11-04. Use the tools to do what was asked, then confirm in one sentence.",
    tools=CALENDAR_TOOLS,
    tool_results={"create_event": '{"ok": true, "id": "evt_7"}', "send_email": '{"ok": true}'},
    check={
        "tool_calls": {
            "mode": "sequence",
            "expect": [
                {
                    "name": "create_event",
                    "args": {
                        "start": "2026-11-10T14:30",
                        "duration_minutes": 45,
                        "attendees": ["sam@example.com", "lee@example.com"],
                    },
                }
            ],
            "args_exact": True,
        }
    },
    max_steps=4,
)

# -- instruction following and accuracy -------------------------------------------------------

HARD_BULLETS = TaskDef(
    id="instr-bullets",
    name="Several format rules at once",
    description="Three bullets, each within a word range, avoiding a letter and a word, ending in an exact word.",
    kind="chat",
    category="instruction_following",
    prompt=(
        "Write exactly three bullet points about why backups matter. Rules: each bullet is a single line that "
        "starts with '- '; each has between 6 and 9 words, not counting the dash; no bullet contains the letter "
        "'z' or the word 'data'; the last bullet ends with the word 'restore' followed by a full stop. Output only "
        "the three bullets."
    ),
    system="Follow the output format exactly.",
    check={
        "answer": {
            "lines": {
                "count": 3,
                "prefix": "- ",
                "min_words": 6,
                "max_words": 9,
                "forbidden_letters": "z",
                "forbidden_words": ["data"],
                "last_line_ends_with": "restore.",
            }
        }
    },
)

MEMO = (
    "Facilities memo, 3 October. The annual fire drill takes place on 14 October at 10:00 and every floor takes "
    "part. Floor 3 (Finance) assembles in the north car park. All other floors assemble on the south lawn. The "
    "Annexe building runs its own drill on 21 October and is not covered by this memo.\n\n"
    "Update, 9 October. Because of the electrical works the drill moves to 16 October at 10:00. Nothing else about "
    "it changes.\n\n"
    "Update, 14 October. The north car park is closed for resurfacing, so Floor 3 now assembles on the south lawn "
    "with everyone else. Accessibility wardens are the one exception: they still assemble at the entrance to the "
    "north car park, which stays open for pedestrians."
)

HARD_REVISIONS = TaskDef(
    id="acc-revisions",
    name="Follow a memo through its updates",
    description="The answer depends on two later updates and an exception; the first version is out of date.",
    kind="chat",
    category="accuracy",
    prompt=(
        f"Use only this memo.\n\n{MEMO}\n\n"
        "Question: A Finance warden on Floor 3 is also an accessibility warden. On what date, and where, do they "
        "assemble for the drill? Answer in one sentence."
    ),
    check={
        "answer": {
            "regex": r"(?s)(?=.*(?:\b16(?:th)?(?:\s+of)?\s+october|october\s+16))(?=.*north car park)",
        }
    },
)

HARD_TRAIN = TaskDef(
    id="reason-train",
    name="A journey with a delay",
    description="Distance over speed, plus a hold, added to a departure time.",
    kind="chat",
    category="accuracy",
    prompt=(
        "A train leaves at 09:40 and covers 255 km at a constant 85 km/h, but is held for 12 minutes at a signal on "
        "the way. At what time does it arrive? Answer in 24-hour HH:MM format only."
    ),
    check={"answer": {"regex": r"\b12:52\b"}},
)

HARD_MODULAR = TaskDef(
    id="reason-modular",
    name="A remainder with a large exponent",
    description="Find the remainder of 7^2026 divided by 5 without computing the power.",
    kind="chat",
    category="accuracy",
    prompt="What is the remainder when 7^2026 is divided by 5? Answer with the number only.",
    check={"answer": {"regex": r"^\W*4\W*$"}},
)

HARD_AGENTIC = (HARD_INTERVALS, HARD_CACHE, HARD_SEMVER, HARD_ORDER, HARD_CART)
HARD_TOOLS = (HARD_TOOL_CHAIN, HARD_TOOL_SCHEDULE)
HARD_CHAT = (HARD_BULLETS, HARD_REVISIONS, HARD_TRAIN, HARD_MODULAR)
