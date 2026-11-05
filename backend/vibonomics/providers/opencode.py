"""OpenCode Console (pay-as-you-go, formerly Zen) and OpenCode Go (subscription).

Both expose public model lists but no pricing API. models.dev (run by the same team) carries accurate
per-model prices; the console docs pages are parsed only to pick up discounts, which are published as
strikethrough prices and "50% off" notes.

A Go plan does report usage: ``/zen/go/v1/usage`` answers with rolling, weekly and monthly windows as a
percentage of the plan's included allowance. OpenCode documents no usage API, so this is the endpoint the
console itself calls; parsing is defensive and the UI labels it as unofficial. Console credit balance has
no equivalent: that lives behind the console's session cookie, not an API key.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from ..http import USER_AGENT, FetchError, get_json, get_text, public_url
from .base import BalanceInfo, ChatTarget, ModelOffer, ModelRoute, ProviderAdapter, ProviderSpec, Wire
from .common import modelsdev_offers, openai_model_ids, parse_markdown_tables
from .pricing_pages import PagePrice, apply_page_prices, find_column, parse_price_cell

log = logging.getLogger(__name__)

ZEN_API = "https://opencode.ai/inference/openai/v1"
GO_API = "https://opencode.ai/inference/go/openai/v1"
ZEN_MODELS = "https://opencode.ai/inference/v1/models"
GO_MODELS = "https://opencode.ai/inference/go/v1/models"
ZEN_DOCS = "https://opencode.ai/v2/docs/console/models/"
GO_DOCS = "https://opencode.ai/v2/docs/console/go/"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
MARKDOWN = {"Accept": "text/markdown"}

# Window keys the usage endpoint reports for a Go plan, in the order the console shows them.
GO_WINDOWS = (("rolling", "5-hour session"), ("weekly", "Weekly"), ("monthly", "Monthly"))

# What OpenCode says it does with your prompts on each free model. Transcribed on 2026-11-04 from the
# "Free models" and "Privacy" sections of https://opencode.ai/v2/docs/console/models/ and, for the Go-only
# Iceman, the "Privacy" section of https://opencode.ai/v2/docs/console/go/. Re-check the wording against those
# pages before adding or changing an entry; a free model missing here gets FREE_DATA_UNKNOWN rather than a guess.
# Keyed by model id and shared by Console and Go: Beam Free is on both, and both pages describe it the same way.
FREE_DATA_USE: dict[str, str] = {
    "glm-5.3-flash-free": "Zero retention, and your data is not used for model training.",
    "deepseek-v4.1-flash-free": "Zero retention, and your data is not used for model training.",
    "big-pickle": "During its free period, collected data may be used to improve the model.",
    "pulse-alpha-free": "During its free period, collected data may be retained and used to improve the model.",
    "flint-40b-free": "During its free period, collected data may be used to improve the model. "
    "Data is retained for 30 days.",
    "mimo-v2.6-flash-free": "During its free period, collected data may be used to improve the model.",
    "beam-free": "Your data is not used for training. It is retained for 30 days for safety reasons.",
    "iceman-contributor-free": "Free in exchange for permission to use your prompts and completions to improve "
    "the model.",
    **{
        model_id: "Trial use only: do not submit personal or confidential data. Use is logged for security and to "
        "improve NVIDIA products and services."
        for model_id in ("nemotron-3-ultra-free", "nemotron-3.5-lightning-free", "nemotron-3.5-super-vl-free")
    },
}
FREE_DATA_UNKNOWN = "OpenCode's docs don't say how this free model handles your data yet."

# OpenCode's Console docs: "Free-tier chat models require OpenCode; use paid models or Go from other clients."
# Every other harness gets HTTP 403, even with a valid key. Go's own free ids are not covered by that sentence.
CLIENT_RESTRICTED = "opencode"

# OpenCode Go asks clients for a stable id per conversation in this header, for routing and prompt caching.
SESSION_HEADER = "x-opencode-session"


def _on_opencode(url: str) -> bool:
    """Whether ``url`` is https on opencode.ai itself (the Console and Go APIs both live there)."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return (
        parsed.scheme == "https" and parsed.host == "opencode.ai" and parsed.port in (None, 443) and not parsed.userinfo
    )


def parse_pricing(markdown: str) -> list[PagePrice]:
    """Parse the "Model | Input | Output | Cached Read | Cached Write" tables on the console docs."""
    rows: dict[str, PagePrice] = {}
    for table in parse_markdown_tables(markdown):
        if not table:
            continue
        headers = list(table[0])
        i_model = find_column(headers, "model")
        i_in = find_column(headers, "input", exclude=("cached",))
        i_out = find_column(headers, "output")
        i_cache = find_column(headers, "cached read")
        i_write = find_column(headers, "cached write")
        i_limit = find_column(headers, "limit")
        if i_model is None or i_in is None or i_out is None:
            continue
        for row in table:
            name = row.get(headers[i_model], "").strip()
            if not name:
                continue
            input_now, input_list, until, note = parse_price_cell(row.get(headers[i_in]))
            output_now, output_list, _until2, _note2 = parse_price_cell(row.get(headers[i_out]))
            if input_now is None and output_now is None:
                continue
            extra = {"included_usage": row.get(headers[i_limit])} if i_limit is not None else {}
            rows.setdefault(
                name.lower(),
                PagePrice(
                    name=name,
                    input=input_now,
                    output=output_now,
                    cache_read=parse_price_cell(row.get(headers[i_cache]))[0] if i_cache is not None else None,
                    cache_write=parse_price_cell(row.get(headers[i_write]))[0] if i_write is not None else None,
                    list_input=input_list,
                    list_output=output_list,
                    discount_note=note,
                    discount_until=until,
                    extra=extra,
                ),
            )
    return list(rows.values())


def _reset_at(value: Any) -> str | None:
    """The endpoint sends ISO timestamps; anything else is dropped rather than shown to the operator."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def parse_usage(payload: Any) -> list[dict[str, Any]]:
    """The Go usage endpoint's reply → per-window percentages and reset times.

    The response is documented nowhere, so every field is optional: a window that is missing or
    carries a non-numeric percent is skipped rather than rendered as a wrong figure.
    """
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return []
    windows: list[dict[str, Any]] = []
    for key, label in GO_WINDOWS:
        node = usage.get(key)
        if not isinstance(node, dict):
            continue
        try:
            percent = float(node.get("percent"))
        except (TypeError, ValueError):
            continue
        windows.append(
            {
                "key": key,
                "label": label,
                "used_percentage": round(max(0.0, min(percent, 100.0)), 2),
                "resets_at": _reset_at(node.get("resetsAt")),
                "status": str(node.get("status") or "") or ("limit" if percent >= 100 else "ok"),
            }
        )
    return windows


def _junk(model_id: str) -> bool:
    lowered = model_id.lower()
    return lowered in {"test", "debug"} or lowered.endswith(("-backup", "-test"))


class _OpenCodeBase(ProviderAdapter):
    models_url: str
    docs_url: str
    openai_base: str
    subscription: bool = False

    async def fetch_models(self) -> list[ModelOffer]:
        offers = modelsdev_offers(self.ctx.catalog, self.spec.modelsdev_ids)
        known = {offer.model_id for offer in offers}
        try:
            live = [mid for mid in await openai_model_ids(self.models_url, {}) if not _junk(mid)]
            for offer in offers:
                if offer.model_id in live:
                    offer.extra["live"] = True
            for model_id in live:
                if model_id in known:
                    continue
                offers.append(
                    ModelOffer(
                        model_id=model_id,
                        name=model_id,
                        canonical_id=self.ctx.catalog.guess_canonical(model_id),
                        # models.dev can lag a new release; the "-free" suffix is how OpenCode names a free
                        # model, and it is what offer_from_modelsdev reads too.
                        is_free=model_id.endswith("-free"),
                        pricing_source="api (no price)",
                        extra={"live": True, "new_from_api": True},
                    )
                )
        except FetchError as exc:
            log.info("%s model list failed: %s", self.spec.name, exc)
        try:
            applied = apply_page_prices(
                offers, parse_pricing(await get_text(self.docs_url, headers=MARKDOWN, timeout=60)), self.spec.name
            )
            if applied == 0:
                for offer in offers:
                    offer.extra["pricing_page_error"] = "no pricing rows matched"
        except FetchError as exc:
            log.warning("%s pricing page failed: %s", self.spec.name, exc)
            for offer in offers:
                offer.extra["pricing_page_error"] = str(exc)[:300]
        if self.subscription:
            for offer in offers:
                offer.extra["subscription"] = True
                offer.extra["subscription_note"] = (
                    "Go is a monthly subscription; these per-token rates meter included usage, not extra billing."
                )
                offer.pricing_source = "opencode go metered rates"
        for offer in offers:
            if not offer.is_free:
                continue
            offer.extra["data_use"] = FREE_DATA_USE.get(offer.model_id, FREE_DATA_UNKNOWN)
            if not self.subscription:
                offer.extra["client_restricted"] = CLIENT_RESTRICTED
        return offers

    async def fetch_balance(self) -> BalanceInfo:
        if not self.subscription:
            return BalanceInfo(
                status="unsupported",
                message="Console credits are visible only in the OpenCode console. OpenCode Go usage is reported on "
                "the Go provider card.",
            )
        if not self.cfg.api_key:
            return BalanceInfo(
                status="needs_key",
                message="An API key from the Go workspace is what the usage endpoint reads.",
            )
        try:
            data = await get_json(
                USAGE_URL,
                headers={**self.auth_headers(), "Accept": "application/json"},
                timeout=30,
                retries=1,
            )
        except FetchError as exc:
            return BalanceInfo(status="error", message=f"Usage request failed: {str(exc)[:300]}")
        windows = parse_usage(data)
        if not windows:
            return BalanceInfo(
                status="error",
                message="The usage endpoint answered without any window; its response shape may have changed.",
            )
        return BalanceInfo(
            status="ok",
            details={
                "usage_windows": windows,
                "source": "zen-go-usage",
                "undocumented": "OpenCode documents no usage API; this is the endpoint its own console uses.",
            },
        )

    async def test_connection(self) -> str:
        # The inference base URL has no /models route (that 404 is what a bad key also looks like), so
        # the check uses the provider's public catalogue endpoint and says plainly what it proved.
        ids = await openai_model_ids(self.models_url, {})
        if not self.cfg.api_key:
            return f"Public catalogue reachable ({len(ids)} models); no API key set."
        if self.subscription:
            try:
                windows = parse_usage(
                    await get_json(
                        USAGE_URL,
                        headers={**self.auth_headers(), "Accept": "application/json"},
                        timeout=30,
                        retries=1,
                    )
                )
            except FetchError as exc:
                raise FetchError(f"The usage endpoint rejected this key: {str(exc)[:200]}") from exc
            return (
                f"Key accepted ({len(ids)} models in the catalogue; {len(windows)} Go usage window(s) read). "
                "That usage call is the key check OpenCode offers."
            )
        return (
            f"Key stored. The public catalogue lists {len(ids)} models; OpenCode has no key-check endpoint, so a "
            "test run or balance refresh is what would reject a bad key."
        )

    def _served_on(self, endpoint: dict[str, Any] | None) -> tuple[str, str]:
        """(``openai``, ``anthropic`` or ``google``, base URL) for a model's models.dev endpoint entry."""
        npm = str((endpoint or {}).get("npm") or "")
        api = str((endpoint or {}).get("api") or self.openai_base).rstrip("/")
        return ("google" if "google" in npm else "anthropic" if "anthropic" in npm else "openai"), api

    def model_route(self, extra: dict[str, Any]) -> ModelRoute:
        endpoint = extra.get("endpoint")
        route, api = self._served_on(endpoint if isinstance(endpoint, dict) else None)
        if route == "google" or not _on_opencode(api):
            return ModelRoute("other")
        return ModelRoute("anthropic" if route == "anthropic" else "openai", api)

    def chat_target(self, model_id: str) -> ChatTarget:
        route, api = self._served_on(self._endpoint_for(model_id))
        if not _on_opencode(api):
            # models.dev is community-edited and fetched at runtime; the key goes only where OpenCode serves.
            raise FetchError(
                f"models.dev lists {model_id} at {public_url(api)}, outside opencode.ai; "
                "Vibonomics will not send the OpenCode key there."
            )
        if route == "google":
            raise FetchError(
                "This model is served on OpenCode's Gemini route, which Vibonomics cannot call with its two "
                "supported APIs (OpenAI chat completions and Anthropic messages)."
            )
        wire: Wire = "anthropic" if route == "anthropic" else "openai"
        if self.subscription:
            # Go's docs ask any client other than OpenCode to name itself in its own user agent and to send a stable
            # id per conversation in x-opencode-session, and answer HTTP 400 without it. That is the documented way
            # in, so it is not sending another client's identity. The free Console tier is different: it admits
            # only OpenCode, and nothing here tries to get past that.
            return ChatTarget(
                wire=wire,
                base_url=api,
                model=model_id,
                headers={**self.auth_headers(), "User-Agent": USER_AGENT},
                session_header=SESSION_HEADER,
            )
        return ChatTarget(wire=wire, base_url=api, model=model_id, headers=self.auth_headers())

    def _endpoint_for(self, model_id: str) -> dict[str, Any] | None:
        for provider_id in self.spec.modelsdev_ids:
            model = self.ctx.catalog.model(provider_id, model_id)
            if model:
                endpoint = model.get("provider")
                return endpoint if isinstance(endpoint, dict) else None
        return None


class OpenCodeAdapter(_OpenCodeBase):
    models_url = ZEN_MODELS
    docs_url = ZEN_DOCS
    openai_base = ZEN_API

    spec = ProviderSpec(
        type="opencode",
        name="OpenCode Console",
        description="OpenCode's pay-as-you-go catalogue (formerly Zen). Public model list and models.dev prices; a "
        "key adds test runs and calls. Free models are only callable from the OpenCode client.",
        homepage="https://opencode.ai",
        default_base_url=ZEN_API,
        modelsdev_ids=("opencode",),
        keys_url="https://opencode.ai/console",
        billing_url="https://opencode.ai/console",
        balance="none",
        balance_note="No balance API; the balance is shown only in the OpenCode console.",
        default_enabled=True,
    )


class OpenCodeGoAdapter(_OpenCodeBase):
    models_url = GO_MODELS
    docs_url = GO_DOCS
    openai_base = GO_API
    subscription = True

    spec = ProviderSpec(
        type="opencode-go",
        name="OpenCode Go",
        description="OpenCode Go subscription catalogue ($10/month, or Go Plus at $40). Per-token rates meter "
        "included usage rather than bill per request. Public model list; a key adds test runs and the plan's "
        "rolling, weekly and monthly usage windows.",
        homepage="https://opencode.ai",
        default_base_url=GO_API,
        modelsdev_ids=("opencode-go",),
        keys_url="https://opencode.ai/console",
        billing_url="https://opencode.ai/console",
        balance="key",
        balance_note="Go plans report rolling, weekly and monthly usage windows to the workspace's API key. There "
        "is no dollar balance; extra usage is charged to Console credits.",
    )
