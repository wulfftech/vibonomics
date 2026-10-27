"""Match benchmark model names to models.dev canonical ids.

Order: operator alias (DB) → curated alias (aliases.yaml) → explicit id hints → normalised name
→ fuzzy name (match method "fuzzy": unambiguous suffix-stripped or word-order-insensitive keys), all
for the names as given, then again with effort words stripped. Exact ids win over normalised keys,
and a normalised key shared by several models matches none of them.
Unmatched records are still stored (canonical_id NULL) so the operator can map them in Settings.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..catalog.modelsdev import ModelsDevCatalog, normalize_model_key

_EFFORT_PATTERNS = [
    re.compile(
        r"\s*[\(\[]\s*(max|xhigh|x-high|high|medium|low|minimal|none|thinking|reasoning|non-reasoning)\s*[\)\]]\s*$",
        re.I,
    ),
    re.compile(r"[_:](max|xhigh|high|medium|low|minimal|thinking|reasoning)$", re.I),
    # After a hyphen, "max" and "thinking" are usually part of the model name (Qwen3.8-Max,
    # kimi-k2-thinking), so only the usual effort words count. Even those can be part of a name
    # (mistral-medium), so the matcher tries the full name first.
    re.compile(r"-(xhigh|high|medium|low|minimal)$", re.I),
]


def split_effort(name: str) -> tuple[str, str]:
    """'Claude Opus 5.5 (Max)' → ('Claude Opus 5.5', 'max'); 'gpt-6_xhigh' → ('gpt-6', 'xhigh').

    'Qwen3.8-Max' and 'kimi-k2-thinking' are model names and come back unchanged.
    """
    for pattern in _EFFORT_PATTERNS:
        m = pattern.search(name)
        if m:
            effort = m.group(1).lower().replace("x-high", "xhigh")
            return name[: m.start()].strip(), effort
    return name.strip(), ""


_TRAILING_GROUP = re.compile(r"\s*\(([^()]*)\)\s*$")
_EFFORT_WORDS = {
    "max": "max",
    "maximum": "max",
    "xhigh": "xhigh",
    "x-high": "xhigh",
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "minimal": "minimal",
    "none": "none",
}
_THINKING = re.compile(r"^(thinking|reasoning|extended thinking)(\s*[-\s]?\d+k)?$", re.I)
_NON_THINKING = re.compile(r"^(non[-\s]?thinking|non[-\s]?reasoning|no[-\s]thinking|instant)$", re.I)


def effort_word(value: str) -> str:
    """Normalise one effort label ('Extra High' → 'xhigh', 'Thinking 16K' → 'thinking'); '' if it isn't one."""
    v = value.strip().lower().replace("_", " ")
    if v in _EFFORT_WORDS:
        return _EFFORT_WORDS[v]
    if _THINKING.match(v):
        return "thinking"
    if _NON_THINKING.match(v):
        return "none"
    return ""


def split_qualifiers(name: str) -> tuple[str, str, list[str]]:
    """Strip trailing parenthesised qualifiers from a display name.

    'Claude Opus 4.5 (High, 32K, 20251101)' → ('Claude Opus 4.5', 'high', ['32K', '20251101']);
    'Kimi K2.5 (Thinking)' → ('Kimi K2.5', 'thinking', []). The first effort-like qualifier wins.
    """
    base = name.strip()
    effort = ""
    rest: list[str] = []
    while True:
        m = _TRAILING_GROUP.search(base)
        if not m or m.start() == 0:
            break
        base = base[: m.start()].strip()
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        for part in reversed(parts):  # groups are peeled right-to-left; keep the original order in ``rest``
            word = effort_word(part)
            if word and not effort:
                effort = word
            elif not word:
                rest.insert(0, part)
    return base, effort, rest


def token_key(value: str) -> str:
    """Order-insensitive key: 'claude-4.8-opus' and 'Claude Opus 4.8' → '4.8 claude opus'.

    Version numbers stay intact ('gemini-3-5-flash' ≠ 'gemini-5-3-flash') because a run of numeric
    tokens is glued back onto the token before it when that one ends in a digit.
    """
    merged: list[str] = []
    for tok in normalize_model_key(value).split("-"):
        if not tok:
            continue
        if tok.isdigit() and merged and merged[-1][-1].isdigit():
            merged[-1] = f"{merged[-1]}.{tok}"
        else:
            merged.append(tok)
    return " ".join(sorted(merged))


def exact_key(value: str) -> str:
    """Like ``normalize_model_key`` but keeps date/snapshot suffixes: 'qwen3.8-max-0902' → 'qwen3-8-max-0902'."""
    s = re.sub(r":[a-z0-9-]+$", "", value.lower().strip().split("/")[-1])  # OpenRouter ":free" etc.
    s = re.sub(r"[\s._]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


_TRAILING_DATE = re.compile(r"-(20\d{2})-?(\d{2})-?(\d{2})$")
_STRIPPABLE_SUFFIXES = {"it", "instruct", "preview"}
_SIZE_TOKEN = re.compile(r"^(a?\d+(\.\d+)?[bmt]|\d+x\d+b|e\d+b)$")


def _catalog_variants(name: str) -> list[str]:
    """The key itself plus shorter keys a benchmark might use for a catalogue model.

    'gemma-4-31b-it' → [..., 'gemma-4-31b', 'gemma-4']; 'nemotron-3-ultra-550b-a55b' → [...,
    'nemotron-3-ultra']; 'gpt-5.2-chat-latest' → [..., 'gpt-5-2-chat']. '-latest' is only dropped when
    three tokens remain, so 'gemini-pro-latest' never claims the 2023 'Gemini Pro'. Callers drop variants
    shared by several models.
    """
    tokens = [t for t in normalize_model_key(name).split("-") if t]
    out = ["-".join(tokens)]
    while len(tokens) > 2:
        last = tokens[-1]
        if not (last in _STRIPPABLE_SUFFIXES or _SIZE_TOKEN.match(last) or (last == "latest" and len(tokens) > 3)):
            break
        tokens = tokens[:-1]
        out.append("-".join(tokens))
    return out


@lru_cache(maxsize=1)
def curated_aliases() -> dict[str, dict[str, str | None]]:
    path = Path(__file__).with_name("aliases.yaml")
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(src): {str(k).lower(): v for k, v in (m or {}).items()} for src, m in data.items()}


def _unique(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """key → id for keys that point at exactly one id."""
    seen: dict[str, set[str]] = {}
    for k, v in pairs:
        if k:
            seen.setdefault(k, set()).add(v)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _clean(values: list[str]) -> list[str]:
    """Stripped, non-empty values in order, without repeats."""
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


class Matcher:
    def __init__(self, catalog: ModelsDevCatalog, operator_aliases: dict[tuple[str, str], str | None] | None = None):
        self.catalog = catalog
        self.operator_aliases = operator_aliases or {}
        self.index = catalog.canonical_index()
        self._by_provider_id: dict[str, str] = {}
        for pid, provider in catalog.data.items():
            if not isinstance(provider, dict):
                continue
            for mid, m in provider.get("models", {}).items():
                cid = m.get("canonical_model_id")
                if not cid:
                    continue
                self._by_provider_id.setdefault(mid.lower(), cid)
                self._by_provider_id.setdefault(f"{pid}/{mid}".lower(), cid)
        # Exact ids keep their snapshot suffix ("qwen3.8-max-0902"), so a dated benchmark name can
        # reach the dated model before date-stripped keys collapse it onto the undated one.
        self._by_exact = _unique([(exact_key(cid), cid) for cid in self.index])
        # Date-stripped keys of ids and names ("anthropic/claude-opus-5-5", "Claude Opus 5.5" →
        # "claude-opus-5-5"). A canonical id whose tail *is* the key owns it, so dated snapshots
        # ("deepseek-v4.1-flash-1017") only get it when no undated model exists. A key left with more
        # than one model (two dated snapshots, two owners) is ambiguous and resolves to nothing.
        claims: dict[str, set[str]] = {}
        owners: dict[str, set[str]] = {}
        for cid, info in self.index.items():
            k = normalize_model_key(cid)
            if k == exact_key(cid):
                owners.setdefault(k, set()).add(cid)
            for key in (k, normalize_model_key(info.name)):
                if key:
                    claims.setdefault(key, set()).add(cid)
        claims.update(owners)
        self._by_key = {k: next(iter(ids)) for k, ids in claims.items() if len(ids) == 1}
        # Fallback indexes, consulted only when every candidate failed the exact keys above. Keys
        # shared by more than one canonical model are ambiguous and dropped, and so are keys the exact
        # index already found ambiguous.
        variants: list[tuple[str, str]] = []
        for cid, info in self.index.items():
            names = [cid.split("/", 1)[-1], info.name]
            if ": " in info.name:  # "OpenAI: GPT-5.2 Chat"
                names.append(info.name.split(": ", 1)[1])
            variants += [(v, cid) for name in names for v in _catalog_variants(split_qualifiers(name)[0])]
        self._by_alt = {k: v for k, v in _unique(variants).items() if k not in claims}
        self._by_tokens = _unique(
            [(token_key(k), cid) for k, ids in claims.items() for cid in ids]
            + [(token_key(k), cid) for k, cid in self._by_alt.items()]
        )
        self._creators = {cid.split("/", 1)[0].lower() for cid in self.index if "/" in cid}

    def _name(self, value: str) -> str | None:
        """Exact id match, then the normalised (date-stripped) key.

        Snapshot suffixes name distinct releases ('qwen3.8-max-0902', 'claude-3-5-sonnet-20241022'), so a
        catalogue id with the same suffix wins. A full date that the catalogue doesn't list reaches a
        short-dated release for that day ('DeepSeek-V4.1-Flash-20261017' → deepseek-v4.1-flash-1017), or
        else collapses onto the undated model ('claude-opus-4-5-20251101' → claude-opus-4-5).
        """
        ek = exact_key(value)
        cid = self._by_exact.get(ek)
        if cid is None and (m := _TRAILING_DATE.search(ek)):
            cid = self._by_exact.get(f"{ek[: m.start()]}-{m.group(2)}{m.group(3)}")
        return cid or self._by_key.get(normalize_model_key(value))

    def _fuzzy(self, cand: str, strip_effort: bool) -> str | None:
        """Last-resort name match.

        Drops trailing '(...)' qualifiers (and an effort suffix with ``strip_effort``), matches catalogue
        ids without '-it' / '-instruct' / '-preview' / parameter-count suffixes ('Gemma 4 31B' →
        gemma-4-31b-it, 'Nemotron 3 Ultra' → nemotron-3-ultra-550b-a55b) when that is unambiguous, drops a
        leading creator word, and finally ignores word order ('claude-4.8-opus' → Claude Opus 4.8).
        """
        base, _effort, rest = split_qualifiers(cand)
        sizes = [q for q in rest if _SIZE_TOKEN.match(q.lower())]
        if sizes:  # "Qwen2.5-Coder (7B)" must not fall back to whichever Qwen2.5-Coder size exists
            base = " ".join([base, *sizes])
        if strip_effort:
            base = split_effort(base)[0]
        k = normalize_model_key(base)
        if not k:
            return None
        cid = self._by_key.get(k) or self._by_alt.get(k)
        if cid:
            return cid
        # "NVIDIA-Nemotron-3-Ultra" / "meta-llama-4-maverick" → drop the creator word.
        head, _, tail = k.partition("-")
        if tail and "-" in tail and head in self._creators:
            cid = self._by_key.get(tail) or self._by_alt.get(tail)
            if cid:
                return cid
        return self._by_tokens.get(token_key(base))

    def _match_forms(self, ids: list[str], names: list[str], strip_effort: bool) -> tuple[str | None, str]:
        """Explicit id hints, then exact names, then fuzzy names."""
        for hint in ids:
            if hint in self.index:
                return hint, "id"
            cid = self._by_provider_id.get(hint.lower()) or self._by_provider_id.get(hint.lower().split(":")[0])
            if cid:
                return cid, "id"
        for name in names:
            cid = self._name(name)
            # Drop a leading vendor word: "MoonshotAI: Kimi K3" / "Anthropic Claude Opus 5.5"
            if cid is None and ": " in name:
                cid = self._name(name.split(": ", 1)[1])
            if cid:
                return cid, "name"
        for name in names:
            cid = self._fuzzy(name, strip_effort)
            if cid:
                return cid, "fuzzy"
        return None, "unmatched"

    def match(
        self, source: str, source_model: str, model_ids: list[str] | None = None, fallback_ids: list[str] | None = None
    ) -> tuple[str | None, str]:
        """Canonical id and match method for one benchmark row.

        Names and hints are matched as given first. Only when that finds nothing are effort words
        stripped ('gpt-6_xhigh' → gpt-6) and ``fallback_ids`` tried, because an effort word can be part
        of a model's name ('Kimi K2 Thinking', 'mistral-medium').
        """
        key = (source, source_model)
        if key in self.operator_aliases:
            return self.operator_aliases[key], "operator"
        curated = curated_aliases().get(source, {})
        if source_model.lower() in curated:
            return curated[source_model.lower()], "curated"

        hints = _clean(model_ids or [])
        names = _clean([source_model, *hints])
        cid, method = self._match_forms(hints, names, strip_effort=False)
        if cid is None:
            stripped = _clean([*(split_effort(n)[0] for n in names), *(fallback_ids or [])])
            cid, method = self._match_forms(stripped, stripped, strip_effort=True)
        return cid, method

    def effort(self, name: str) -> str:
        """The effort suffix of a benchmark name, unless the whole name is a catalogue model ('mistral-medium')."""
        return "" if self._name(name) else split_effort(name)[1]
