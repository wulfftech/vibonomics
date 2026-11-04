"""Copyable configuration snippets for common coding harnesses.

Snippets never contain real API keys: they reference an environment variable (``env_key``), an
editor secret prompt, or a clearly marked ``<...>`` placeholder the user replaces.

Formats were checked against each harness's official documentation in October 2026; every
snippet carries the ``docs_url`` it was checked against. A harness is only offered when the
provider can actually serve it (e.g. Claude Code needs an Anthropic-compatible endpoint, Codex
needs the OpenAI Responses API).
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse

from .providers.base import ProviderSpec


@dataclass(frozen=True)
class Routing:
    """How an OpenRouter request picks its provider. ``SnippetContext.routing`` None means OpenRouter's own routing."""

    variant: str | None = None
    """``floor`` (cheapest) or ``nitro`` (fastest): a suffix on the model id, so every harness can carry it."""
    provider: str | None = None
    """An endpoint slug such as ``deepinfra/fp8``, sent as a ``provider`` block that only some harnesses can carry."""
    allow_fallbacks: bool = False
    """Whether OpenRouter may try other providers when the pinned one fails."""

    def provider_block(self) -> dict[str, Any] | None:
        if not self.provider:
            return None
        block: dict[str, Any] = {"order": [self.provider]}
        if not self.allow_fallbacks:
            block["allow_fallbacks"] = False
        return block


@dataclass
class Snippet:
    id: str
    harness: str
    title: str
    language: str  # json | yaml | toml | bash | python | text
    content: str
    filename: str | None = None
    notes: str | None = None
    docs_url: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass
class SnippetContext:
    spec: ProviderSpec
    provider_name: str
    base_url: str
    """Effective OpenAI-compatible base URL (operator override or spec default)."""
    anthropic_base_url: str | None
    model_id: str
    model_name: str
    context_length: int | None
    max_output: int | None
    tool_call: bool | None
    reasoning: bool | None
    vision: bool
    modelsdev_provider: str | None
    """models.dev provider id, which is also OpenCode's built-in provider id."""
    env_key: str
    """Suggested environment variable holding the API key, e.g. OPENROUTER_API_KEY."""
    price_input: float | None = None
    """USD per 1M input tokens (used for harnesses that track cost of custom models)."""
    price_output: float | None = None
    """USD per 1M output tokens."""
    responses_api: bool | None = None
    """Whether ``base_url`` also serves the OpenAI Responses API (Codex needs it). None = infer from the host."""
    routing: Routing | None = None
    """An OpenRouter provider choice; ignored for any other endpoint."""
    client_restricted: str | None = None
    """The one harness id (``opencode``) that may call this model; every other client gets HTTP 403."""


# --- helpers -----------------------------------------------------------------------------------

_DEFAULT_CONTEXT = 128_000
_OUTPUT_CAP = 32_000
"""Upper bound for per-request max_tokens in harnesses that send it on every call."""

# Hosts whose OpenAI-compatible base URL is documented to also serve /responses.
_RESPONSES_HOSTS = {"api.openai.com", "openrouter.ai"}
# Provider ids Codex reserves for its built-in providers.
_CODEX_RESERVED = {"openai", "ollama", "lmstudio", "amazon-bedrock"}
# Zed's first-class provider ids (an openai_compatible entry with one of these names would clash).
_ZED_BUILTINS = {
    "anthropic",
    "openai",
    "google",
    "mistral",
    "deepseek",
    "xai",
    "x-ai",
    "opencode",
    "openrouter",
    "open-router",
    "vercel-ai-gateway",
    "bedrock",
    "ollama",
    "lmstudio",
}

_PLAIN_YAML = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
_YAML_KEYWORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}

Builder = Callable[[SnippetContext], Snippet | None]


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _yq(value: str) -> str:
    """A YAML scalar for ``value``: plain when unambiguous, otherwise double-quoted (JSON is valid YAML)."""
    if _PLAIN_YAML.fullmatch(value) and value.lower() not in _YAML_KEYWORDS:
        return value
    return json.dumps(value, ensure_ascii=False)


def _tq(value: str) -> str:
    """A TOML basic string (JSON string escapes are a subset of TOML's)."""
    return json.dumps(value, ensure_ascii=False)


def _py(value: Any) -> str:
    """A Python literal for JSON-shaped data (``json.dumps`` would write ``false`` where Python needs ``False``)."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py(k)}: {_py(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_py(v) for v in value) + "]"
    if isinstance(value, bool):
        return "True" if value else "False"
    return json.dumps(value, ensure_ascii=False)


def _num(value: float) -> str:
    return f"{value:g}"


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


def _slug(text: str, sep: str = "-") -> str:
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep) or "provider"


def _camel(text: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", text) if p]
    return (parts[0].lower() + "".join(p.capitalize() for p in parts[1:])) if parts else "provider"


def _placeholder(ctx: SnippetContext) -> str:
    return f"<paste your {ctx.env_key} value>"


def _is_openrouter(ctx: SnippetContext) -> bool:
    return ctx.spec.type == "openrouter" or _host(ctx.base_url) == "openrouter.ai"


def _pinned(ctx: SnippetContext) -> dict[str, Any] | None:
    """The OpenRouter ``provider`` request block to pin one provider, when the context asks for one."""
    return ctx.routing.provider_block() if ctx.routing and _is_openrouter(ctx) else None


def _anthropic_root(ctx: SnippetContext) -> str | None:
    """Anthropic-compatible base URL without a trailing ``/v1`` (what ANTHROPIC_BASE_URL expects)."""
    url = ctx.anthropic_base_url or (ctx.base_url if ctx.spec.wire == "anthropic" else None)
    if not url:
        return None
    url = url.rstrip("/")
    return url.removesuffix("/v1")


def _is_anthropic_first_party(ctx: SnippetContext) -> bool:
    return _host(_anthropic_root(ctx)) == "api.anthropic.com"


def _native_modelsdev(ctx: SnippetContext) -> str | None:
    """Provider id to use with harnesses built on the models.dev catalogue (OpenCode, Kilo Code)."""
    return ctx.modelsdev_provider if ctx.modelsdev_provider and not ctx.spec.custom else None


def _base_overridden(ctx: SnippetContext) -> bool:
    return ctx.base_url.rstrip("/") != ctx.spec.default_base_url.rstrip("/")


def _window(ctx: SnippetContext) -> tuple[int, int, bool]:
    """(context, max output, guessed): output kept well below the context window."""
    context = ctx.context_length if ctx.context_length and ctx.context_length > 0 else None
    output = ctx.max_output if ctx.max_output and ctx.max_output > 0 else None
    guessed = context is None or output is None
    context = context or _DEFAULT_CONTEXT
    output = min(output or min(16_384, context // 4), context // 2)
    return context, output, guessed


def _guess_note(ctx: SnippetContext) -> str:
    _, _, guessed = _window(ctx)
    return " Some token limits are estimates (the catalogue doesn't list them); adjust them." if guessed else ""


def _limit_note(ctx: SnippetContext) -> str:
    """Note for configs that only carry a ``limit`` block when the context size is known."""
    if not ctx.context_length:
        return " The catalogue has no context size for this model: add limit.context and limit.output yourself."
    return " limit.output is an estimate." if not ctx.max_output else ""


def _models_dev_entry(ctx: SnippetContext, *, custom: bool) -> dict[str, Any]:
    """Model entry in the OpenCode v1 / Kilo Code ``provider.<id>.models`` shape.

    For a built-in provider the entry is merged over the harness's own catalogue, so it only carries
    values we actually know; a custom provider gets everything the harness can't discover.
    """
    entry: dict[str, Any] = {"name": ctx.model_name}
    if not custom:
        if ctx.context_length and ctx.max_output:
            entry["limit"] = {"context": ctx.context_length, "output": ctx.max_output}
        return entry
    if ctx.tool_call is not None:
        entry["tool_call"] = ctx.tool_call
    if ctx.reasoning is not None:
        entry["reasoning"] = ctx.reasoning
    if ctx.vision:
        entry["attachment"] = True
        entry["modalities"] = {"input": ["text", "image"], "output": ["text"]}
    if ctx.context_length:
        context, output, _ = _window(ctx)
        entry["limit"] = {"context": context, "output": output}
    if ctx.price_input is not None and ctx.price_output is not None:
        entry["cost"] = {"input": ctx.price_input, "output": ctx.price_output}
    return entry


def _ui_lines(ctx: SnippetContext) -> tuple[str, list[str], str]:
    """(provider label, settings lines, docs key) for UI-configured VS Code extensions."""
    if _is_openrouter(ctx):
        return "OpenRouter", [f"OpenRouter API Key: {_placeholder(ctx)}", f"Model: {ctx.model_id}"], "openrouter"
    if _is_anthropic_first_party(ctx):
        return "Anthropic", [f"Anthropic API Key: {_placeholder(ctx)}", f"Model: {ctx.model_id}"], "anthropic"
    context, output, guessed = _window(ctx)
    lines = [
        f"Base URL: {ctx.base_url}",
        f"API Key: {_placeholder(ctx)}",
        f"Model ID: {ctx.model_id}",
        "Model Configuration:",
        f"  Max Output Tokens: {output}{' (estimate)' if guessed else ''}",
        f"  Context Window: {context}{' (estimate)' if guessed else ''}",
        f"  Image Support: {'on' if ctx.vision else 'off'}",
    ]
    if ctx.price_input is not None and ctx.price_output is not None:
        lines.append(f"  Input Price (per 1M tokens): {_num(ctx.price_input)}")
        lines.append(f"  Output Price (per 1M tokens): {_num(ctx.price_output)}")
    return "OpenAI Compatible", lines, "openai-compatible"


# --- VS Code -----------------------------------------------------------------------------------


def _vscode_copilot(ctx: SnippetContext) -> Snippet:
    anthropic_root = _anthropic_root(ctx)
    messages = ctx.spec.wire == "anthropic" and anthropic_root is not None
    context, output, _ = _window(ctx)
    model: dict[str, Any] = {
        "id": ctx.model_id,
        "name": ctx.model_name,
        "url": f"{anthropic_root}/v1/messages" if messages else f"{ctx.base_url}/chat/completions",
        "toolCalling": ctx.tool_call is not False,
        "vision": ctx.vision,
        "maxInputTokens": context - output,
        "maxOutputTokens": output,
    }
    if ctx.reasoning:
        model["thinking"] = True
    pin = _pinned(ctx)
    if pin:
        model["modelOptions"] = {"provider": pin}
    config = [
        {
            "name": ctx.provider_name,
            "vendor": "customendpoint",
            "apiKey": "${input:" + _camel(ctx.spec.type) + "ApiKey}",
            "apiType": "messages" if messages else "chat-completions",
            "models": [model],
        }
    ]
    notes = (
        "Run 'Chat: Manage Language Models' > Add Models > Custom Endpoint; VS Code opens chatLanguageModels.json. "
        "Add this entry to the array. VS Code asks for the key once (the ${input:...} variable) and keeps it in "
        f"secret storage: paste your {ctx.env_key} value. This replaces the deprecated "
        "github.copilot.chat.customOAIModels setting. If your provider is in the built-in list under Add Models, "
        "picking it there discovers models automatically. Copilot Business/Enterprise admins can disable BYOK."
    )
    if ctx.tool_call is None:
        notes += " Tool calling is assumed; set toolCalling to false if agent requests fail."
    elif ctx.tool_call is False:
        notes += " This model doesn't support tool calling, so it won't appear for agent sessions."
    if pin:
        notes += (
            " modelOptions holds request parameters sent with every request, but its docs only show temperature and "
            "top_p: check OpenRouter's Activity page to confirm the provider block arrives."
        )
    return Snippet(
        id="vscode-copilot",
        harness="VS Code Copilot",
        title="chatLanguageModels.json (Custom Endpoint)",
        language="json",
        filename="chatLanguageModels.json",
        content=_json(config),
        notes=notes + _guess_note(ctx),
        docs_url="https://code.visualstudio.com/docs/agent-customization/language-models",
    )


def _continue(ctx: SnippetContext) -> Snippet:
    if _is_openrouter(ctx):
        provider, api_base = "openrouter", ctx.base_url
    elif _is_anthropic_first_party(ctx):
        provider, api_base = "anthropic", None
    else:
        provider, api_base = "openai", ctx.base_url
    lines = [
        "name: Local Config",
        "version: 1.0.0",
        "schema: v1",
        "models:",
        f"  - name: {_yq(f'{ctx.model_name} via {ctx.provider_name}')}",
        f"    provider: {provider}",
        f"    model: {_yq(ctx.model_id)}",
    ]
    if api_base:
        lines.append(f"    apiBase: {_yq(api_base)}")
    lines.append(f"    apiKey: ${{{{ secrets.{ctx.env_key} }}}}")
    lines.append("    roles: [chat, edit, apply]")
    if ctx.tool_call is not None:
        caps = (["tool_use"] if ctx.tool_call else []) + (["image_input"] if ctx.vision else [])
        lines.append(f"    capabilities: [{', '.join(caps)}]")
    context, output, _ = _window(ctx)
    lines += [
        "    defaultCompletionOptions:",
        f"      contextLength: {context}",
        f"      maxTokens: {min(output, _OUTPUT_CAP)}",
    ]
    notes = (
        "Merge the models entry into ~/.continue/config.yaml (Windows: %USERPROFILE%\\.continue\\config.yaml); "
        "Continue reloads on save. The ${{ secrets.* }} reference is resolved from a .env file: add "
        f"{ctx.env_key}=... to ~/.continue/.env or <workspace>/.continue/.env."
    )
    if pin := _pinned(ctx):
        lines += [
            "    requestOptions:",
            "      extraBodyProperties:",
            "        provider:",
            f"          order: [{', '.join(_yq(p) for p in pin['order'])}]",
        ]
        if pin.get("allow_fallbacks") is False:
            lines.append("          allow_fallbacks: false")
        notes += (
            " extraBodyProperties is Continue's documented way to merge extra properties into the request body; "
            "check OpenRouter's Activity page to confirm the provider block arrives."
        )
    return Snippet(
        id="continue",
        harness="Continue",
        title="config.yaml model entry",
        language="yaml",
        filename="~/.continue/config.yaml",
        content="\n".join(lines) + "\n",
        notes=notes + _guess_note(ctx),
        docs_url="https://docs.continue.dev/reference",
    )


def _cline(ctx: SnippetContext) -> Snippet:
    label, lines, key = _ui_lines(ctx)
    return Snippet(
        id="cline",
        harness="Cline",
        title="Settings panel values",
        language="text",
        content="\n".join([f"API Provider: {label}", *lines]) + "\n",
        notes=(
            "Cline is configured in its settings panel (gear icon in the Cline view); enter these values there. "
            "It stores them in ~/.cline/data/settings/providers.json, but that file format isn't documented for "
            "hand editing."
        ),
        docs_url=f"https://docs.cline.bot/provider-config/{key}",
    )


def _roo(ctx: SnippetContext) -> Snippet | None:
    if ctx.tool_call is False:  # Roo Code only speaks native tool calling.
        return None
    label, lines, key = _ui_lines(ctx)
    return Snippet(
        id="roo-code",
        harness="Roo Code",
        title="Settings panel values",
        language="text",
        content="\n".join([f"API Provider: {label}", *lines]) + "\n",
        notes=(
            "Enter these in Roo Code's settings panel (gear icon). Roo Code requires native tool calling, so the "
            "model must support function calls."
        ),
        docs_url=f"https://roocodeinc.github.io/Roo-Code/providers/{key}/",
    )


def _kilo(ctx: SnippetContext) -> Snippet:
    native = _native_modelsdev(ctx)
    config: dict[str, Any] = {"$schema": "https://app.kilo.ai/config.json"}
    if native:
        provider: dict[str, Any] = {"models": {ctx.model_id: _models_dev_entry(ctx, custom=False)}}
        if _base_overridden(ctx):
            provider["options"] = {"baseURL": ctx.base_url}
        config["model"] = f"{native}/{ctx.model_id}"
        config["provider"] = {native: provider}
        how = (
            f"Kilo knows {native} natively: add your key under Settings > Providers. The models entry is merged "
            "over Kilo's catalogue, and adds the model if the catalogue lacks it."
        )
        docs = "https://kilo.ai/docs/ai-providers/openrouter" if native == "openrouter" else None
    else:
        config["model"] = f"openai-compatible/{ctx.model_id}"
        config["provider"] = {
            "openai-compatible": {
                "options": {"apiKey": f"{{env:{ctx.env_key}}}", "baseURL": ctx.base_url},
                "models": {ctx.model_id: _models_dev_entry(ctx, custom=True)},
            }
        }
        how = (
            f"The key is read from {ctx.env_key}; {{env:...}} only resolves in the global config, not in a repo's "
            "kilo.json. Settings > Providers > Custom provider is the UI equivalent."
        )
        docs = None
    return Snippet(
        id="kilo-code",
        harness="Kilo Code",
        title="kilo.jsonc",
        language="json",
        filename="~/.config/kilo/kilo.jsonc",
        content=_json(config),
        notes=f"Merge into the global Kilo Code config (used by the VS Code extension and the CLI). {how}"
        + ("" if native else _limit_note(ctx)),
        docs_url=docs or "https://kilo.ai/docs/code-with-ai/agents/custom-models",
    )


# --- CLI agents ---------------------------------------------------------------------------------


def _claude_code(ctx: SnippetContext) -> Snippet | None:
    root = _anthropic_root(ctx)
    if not root:
        return None
    if _is_anthropic_first_party(ctx):
        auth = (
            "Claude Code uses ANTHROPIC_API_KEY from your environment (approve it once when prompted) or your "
            "/login session."
        )
        if ctx.env_key != "ANTHROPIC_API_KEY":
            auth += f' To reuse {ctx.env_key}: export ANTHROPIC_API_KEY="${ctx.env_key}".'
        return Snippet(
            id="claude-code",
            harness="Claude Code",
            title="~/.claude/settings.json",
            language="json",
            filename="~/.claude/settings.json",
            content=_json({"model": ctx.model_id}),
            notes=f"Merge into ~/.claude/settings.json, or run `claude --model {ctx.model_id}`. {auth}",
            docs_url="https://code.claude.com/docs/en/model-config",
        )
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": root,
        "ANTHROPIC_AUTH_TOKEN": _placeholder(ctx),
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": ctx.model_id,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": ctx.model_id,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": ctx.model_id,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": ctx.model_id,
        "CLAUDE_CODE_SUBAGENT_MODEL": ctx.model_id,
    }
    claude_model = "claude" in ctx.model_id.lower()
    if not claude_model:
        # Claude Code assumes a 200K window and 32K output for model ids it doesn't recognise.
        if ctx.context_length:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(ctx.context_length)
        if ctx.max_output and ctx.max_output < 32_000:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(ctx.max_output)
    notes = (
        "Merge into ~/.claude/settings.json (or an untracked .claude/settings.local.json) and replace the "
        f"placeholder with your {ctx.env_key} value, or drop that line and export "
        f'ANTHROPIC_AUTH_TOKEN="${ctx.env_key}" in your shell. Never put the key in a committed '
        ".claude/settings.json. Run /logout first if you were signed in with a Claude account; /status should show "
        "the base URL and ANTHROPIC_AUTH_TOKEN. Every model alias, background task and subagent is pinned to this "
        "model so nothing falls back to a model the endpoint doesn't serve."
    )
    if not claude_model:
        notes += (
            " Anthropic doesn't support Claude Code with non-Claude models; results depend on the provider's "
            "Anthropic compatibility."
        )
    return Snippet(
        id="claude-code",
        harness="Claude Code",
        title="~/.claude/settings.json env block",
        language="json",
        filename="~/.claude/settings.json",
        content=_json({"env": env}),
        notes=notes,
        docs_url="https://code.claude.com/docs/en/llm-gateway-connect",
    )


def _opencode(ctx: SnippetContext) -> Snippet:
    native = _native_modelsdev(ctx)
    config: dict[str, Any] = {"$schema": "https://opencode.ai/config.json"}
    entry = _models_dev_entry(ctx, custom=not native)
    pin = _pinned(ctx)
    if pin:
        entry["options"] = {"provider": pin}
    if native:
        provider: dict[str, Any] = {"models": {ctx.model_id: entry}}
        if _base_overridden(ctx):
            provider["options"] = {"baseURL": ctx.base_url}
        config["model"] = f"{native}/{ctx.model_id}"
        config["provider"] = {native: provider}
        auth = f"OpenCode knows {native} natively: run /connect, or export its standard key variable."
    else:
        pid = _slug(ctx.spec.type)
        config["model"] = f"{pid}/{ctx.model_id}"
        config["provider"] = {
            pid: {
                "npm": "@ai-sdk/openai-compatible",
                "name": ctx.provider_name,
                "options": {"baseURL": ctx.base_url, "apiKey": f"{{env:{ctx.env_key}}}"},
                "models": {ctx.model_id: entry},
            }
        }
        auth = f"The key is read from {ctx.env_key}."
    if pin:
        auth += " The provider block under the model's options is OpenCode's documented OpenRouter routing."
    return Snippet(
        id="opencode",
        harness="OpenCode",
        title="opencode.json",
        language="json",
        filename="opencode.json",
        content=_json(config),
        notes=(
            "Use as <project>/opencode.json or merge into ~/.config/opencode/opencode.json. "
            f"{auth} Written in the V1 format, which OpenCode 2 also reads unchanged."
        )
        + ("" if native else _limit_note(ctx)),
        docs_url="https://opencode.ai/docs/providers/",
    )


def _codex(ctx: SnippetContext) -> Snippet | None:
    host = _host(ctx.base_url)
    responses = ctx.responses_api
    if responses is None:
        responses = host in _RESPONSES_HOSTS or ctx.spec.custom
    if not responses:
        return None  # Codex only speaks the Responses API (wire_api = "responses").
    docs = "https://learn.chatgpt.com/docs/config-file/config-advanced"
    if host == "api.openai.com":
        return Snippet(
            id="codex",
            harness="Codex CLI",
            title="~/.codex/config.toml",
            language="toml",
            filename="~/.codex/config.toml",
            content=f"model = {_tq(ctx.model_id)}\n",
            notes="Uses Codex's built-in openai provider; sign in with ChatGPT or an OpenAI API key.",
            docs_url=docs,
        )
    pid = _slug(ctx.spec.type)
    if pid in _CODEX_RESERVED:
        pid += "-custom"
    lines = [f"model = {_tq(ctx.model_id)}", f"model_provider = {_tq(pid)}"]
    if ctx.context_length:
        lines.append(f"model_context_window = {ctx.context_length}")
    lines += [
        "",
        f"[model_providers.{pid}]",
        f"name = {_tq(ctx.provider_name)}",
        f"base_url = {_tq(ctx.base_url)}",
        f"env_key = {_tq(ctx.env_key)}",
        'wire_api = "responses"',
    ]
    notes = (
        "Put this in ~/.codex/config.toml (Codex ignores provider settings in a project .codex/config.toml) and "
        f"export {ctx.env_key}. Codex only speaks the OpenAI Responses API."
    )
    if ctx.spec.custom and ctx.responses_api is None:
        notes += (
            " Check that your endpoint implements /responses: many OpenAI-compatible servers only offer "
            "/chat/completions."
        )
    if _is_openrouter(ctx):
        notes += (
            " OpenRouter's own guide uses a [model_providers.openrouter.auth] command instead of env_key so that "
            "Codex also loads OpenRouter's model catalogue."
        )
    return Snippet(
        id="codex",
        harness="Codex CLI",
        title="~/.codex/config.toml",
        language="toml",
        filename="~/.codex/config.toml",
        content="\n".join(lines) + "\n",
        notes=notes,
        docs_url=docs,
    )


def _aider(ctx: SnippetContext) -> Snippet:
    prefix = ctx.spec.aider_prefix
    if prefix:
        key_var = f"{prefix.upper().replace('-', '_')}_API_KEY"
        lines = [f"model: {_yq(f'{prefix}/{ctx.model_id}')}"]
        notes = f"Aider (via LiteLLM) reads the key from {key_var}."
        if key_var != ctx.env_key:
            notes += f' If you keep it in {ctx.env_key}: export {key_var}="${ctx.env_key}".'
        docs = {
            "openrouter": "https://aider.chat/docs/llms/openrouter.html",
            "anthropic": "https://aider.chat/docs/llms/anthropic.html",
        }.get(prefix, "https://aider.chat/docs/config/aider_conf.html")
    else:
        lines = [f"model: {_yq(f'openai/{ctx.model_id}')}", f"openai-api-base: {_yq(ctx.base_url)}"]
        notes = (
            f'The openai/ prefix reads OPENAI_API_KEY: export OPENAI_API_KEY="${ctx.env_key}" rather than putting '
            "the key in this file. Aider warns about context size for models LiteLLM doesn't know; describe them in "
            ".aider.model.metadata.json to silence it."
        )
        docs = "https://aider.chat/docs/llms/openai-compat.html"
    return Snippet(
        id="aider",
        harness="Aider",
        title=".aider.conf.yml",
        language="yaml",
        filename=".aider.conf.yml",
        content="\n".join(lines) + "\n",
        notes="Put this in ~/.aider.conf.yml or the root of your git repo, or pass the same values as flags. " + notes,
        docs_url=docs,
    )


def _aider_routing(ctx: SnippetContext) -> Snippet | None:
    """Aider keeps extra request-body fields in a second file, so pinning a provider is its own snippet."""
    pin = _pinned(ctx)
    if not pin or ctx.spec.aider_prefix != "openrouter":
        return None
    lines = [
        f"- name: {_yq(f'openrouter/{ctx.model_id}')}",
        "  extra_params:",
        "    extra_body:",
        "      provider:",
        f"        order: [{', '.join(_yq(p) for p in pin['order'])}]",
    ]
    if pin.get("allow_fallbacks") is False:
        lines.append("        allow_fallbacks: false")
    return Snippet(
        id="aider-routing",
        harness="Aider (provider)",
        title=".aider.model.settings.yml",
        language="yaml",
        filename=".aider.model.settings.yml",
        content="\n".join(lines) + "\n",
        notes=(
            "Save beside .aider.conf.yml, in your home directory or the root of your git repo. Aider adds extra_body "
            "to every request for this model; the name must match the model line in .aider.conf.yml."
        ),
        docs_url="https://aider.chat/docs/llms/openrouter.html",
    )


# --- Editors and other agents ------------------------------------------------------------------


def _zed(ctx: SnippetContext) -> Snippet:
    context, output, _ = _window(ctx)
    if _is_openrouter(ctx):
        provider_cfg: dict[str, Any] = {}
        if _base_overridden(ctx):
            provider_cfg["api_url"] = ctx.base_url
        model: dict[str, Any] = {
            "name": ctx.model_id,
            "display_name": ctx.model_name,
            "max_tokens": context,
            "max_output_tokens": output,
            "supports_tools": ctx.tool_call is not False,
            "supports_images": ctx.vision,
        }
        provider_cfg["available_models"] = [model]
        config: dict[str, Any] = {
            "language_models": {"open_router": provider_cfg},
            "agent": {"default_model": {"provider": "openrouter", "model": ctx.model_id}},
        }
        notes = "Zed reads OPENROUTER_API_KEY, or enter the key under Settings > AI > LLM Providers."
        docs = "https://zed.dev/docs/ai/use-a-gateway"
    elif _is_anthropic_first_party(ctx):
        model = {
            "name": ctx.model_id,
            "display_name": ctx.model_name,
            "max_tokens": context,
            "max_output_tokens": output,
        }
        config = {
            "language_models": {"anthropic": {"available_models": [model]}},
            "agent": {"default_model": {"provider": "anthropic", "model": ctx.model_id}},
        }
        notes = "Zed reads ANTHROPIC_API_KEY, or enter the key under Settings > AI > LLM Providers."
        docs = "https://zed.dev/docs/ai/use-api-access"
    else:
        pid = _slug(ctx.spec.type)
        if pid in _ZED_BUILTINS:
            pid += "-compat"  # an openai_compatible entry must not reuse a built-in provider id
        zed_env = pid.upper().replace("-", "_") + "_API_KEY"
        model = {
            "name": ctx.model_id,
            "display_name": ctx.model_name,
            "max_tokens": context,
            "max_output_tokens": output,
            "capabilities": {
                "tools": ctx.tool_call is not False,
                "images": ctx.vision,
                "parallel_tool_calls": False,
                "prompt_cache_key": False,
                "chat_completions": True,
                "interleaved_reasoning": False,
                "max_tokens_parameter": False,
            },
        }
        config = {
            "language_models": {"openai_compatible": {pid: {"api_url": ctx.base_url, "available_models": [model]}}}
        }
        notes = (
            f"Zed reads the key from {zed_env} (provider id in upper snake case) or the provider settings UI; never "
            "put keys in settings.json. Pick the model in the Agent Panel model selector."
        )
        if zed_env != ctx.env_key:
            notes += f' To reuse {ctx.env_key}: export {zed_env}="${ctx.env_key}".'
        docs = "https://zed.dev/docs/ai/use-api-access"
    return Snippet(
        id="zed",
        harness="Zed",
        title="settings.json",
        language="json",
        filename="~/.config/zed/settings.json",
        content=_json(config),
        notes="Merge into Zed's settings.json (zed: open settings file). " + notes + _guess_note(ctx),
        docs_url=docs,
    )


def _crush(ctx: SnippetContext) -> Snippet:
    pid = _slug(ctx.spec.type)
    if not pid.startswith("custom"):
        pid = f"custom-{pid}"  # don't replace Crush's own built-in provider of the same name
    if ctx.spec.wire == "anthropic" and (root := _anthropic_root(ctx)):
        provider = [
            f"provider add {pid} --type anthropic \\",
            f"  --name {shlex.quote(ctx.provider_name)} \\",
            f"  --base-url {shlex.quote(root + '/v1')} \\",
            f'  --api-key "${ctx.env_key}" \\',
            "  --extra-header anthropic-version 2023-06-01",
        ]
    else:
        provider = [
            f"provider add {pid} --type openai-compat \\",
            f"  --name {shlex.quote(ctx.provider_name)} \\",
            f"  --base-url {shlex.quote(ctx.base_url)} \\",
            f'  --api-key "${ctx.env_key}"',
        ]
    context, output, _ = _window(ctx)
    flags = [
        f"--name {shlex.quote(ctx.model_name)}",
        f"--context-window {context}",
        f"--default-max-tokens {min(output, _OUTPUT_CAP)}",
    ]
    if ctx.reasoning:
        flags.append("--can-reason true")
    if ctx.vision:
        flags.append("--supports-images true")
    if ctx.price_input is not None and ctx.price_output is not None:
        flags += [f"--price-input {_num(ctx.price_input)}", f"--price-output {_num(ctx.price_output)}"]
    model = [f"model add {shlex.quote(f'{pid}/{ctx.model_id}')} \\"]
    model += [f"  {flag} \\" for flag in flags[:-1]] + [f"  {flags[-1]}"]
    return Snippet(
        id="crush",
        harness="Crush",
        title="crushrc",
        language="bash",
        filename="~/.config/crush/crushrc",
        content="\n".join([*provider, "", *model]) + "\n",
        notes=(
            "Append to ~/.config/crush/crushrc (Windows: %USERPROFILE%\\.config\\crush\\crushrc), which replaces the "
            f"deprecated crush.json. Set {ctx.env_key} in your environment before starting Crush, then pick the "
            "model in the model switcher."
        )
        + _guess_note(ctx),
        docs_url="https://github.com/charmbracelet/crush#custom-providers",
    )


def _goose(ctx: SnippetContext) -> Snippet:
    docs = "https://goose-docs.ai/docs/getting-started/providers/"
    if _is_anthropic_first_party(ctx):
        # goose's own config example is exactly this provider; keys never go in config.yaml.
        content = "\n".join(
            [
                "active_provider: anthropic",
                "providers:",
                "  anthropic:",
                "    enabled: true",
                f"    model: {_yq(ctx.model_id)}",
                "    configured: true",
            ]
        )
        return Snippet(
            id="goose",
            harness="Goose",
            title="config.yaml",
            language="yaml",
            filename="~/.config/goose/config.yaml",
            content=content + "\n",
            notes=(
                "Merge into ~/.config/goose/config.yaml (Windows: %APPDATA%\\Block\\goose\\config\\config.yaml). "
                "goose reads ANTHROPIC_API_KEY or the key saved by `goose configure`; it ignores keys in config.yaml."
            ),
            docs_url="https://goose-docs.ai/docs/guides/config-files/",
        )
    name = _slug(ctx.spec.type, "_")
    if not ctx.spec.custom:
        name += "_compat"  # don't shadow goose's own built-in provider of the same name
    context, _, _ = _window(ctx)
    model: dict[str, Any] = {"name": ctx.model_id, "context_limit": context}
    if ctx.price_input is not None and ctx.price_output is not None:
        model["input_token_cost"] = round(ctx.price_input / 1_000_000, 15)
        model["output_token_cost"] = round(ctx.price_output / 1_000_000, 15)
    config = {
        "name": name,
        "engine": "openai",
        "display_name": ctx.provider_name,
        "description": f"{ctx.provider_name} models",
        "api_key_env": ctx.env_key,
        "base_url": f"{ctx.base_url}/chat/completions",
        "models": [model],
        "supports_streaming": True,
        "requires_auth": True,
    }
    notes = (
        f"Save as ~/.config/goose/custom_providers/{name}.json (Windows: "
        f"%APPDATA%\\Block\\goose\\config\\custom_providers\\{name}.json), export {ctx.env_key}, then run "
        f"`goose session start --provider {name}` or pick it in goose Desktop."
    )
    if _is_openrouter(ctx):
        notes += " goose also has a built-in OpenRouter provider (goose configure)."
    if not ctx.context_length:
        notes += " context_limit is an estimate."
    return Snippet(
        id="goose",
        harness="Goose",
        title="Custom provider JSON",
        language="json",
        filename=f"{name}.json",
        content=_json(config),
        notes=notes,
        docs_url=docs,
    )


# --- Raw API -----------------------------------------------------------------------------------


def _curl(ctx: SnippetContext) -> Snippet:
    request: dict[str, Any] = {"model": ctx.model_id, "messages": [{"role": "user", "content": "Hello"}]}
    if pin := _pinned(ctx):
        request["provider"] = pin
    body = json.dumps(request).replace("'", "'\\''")
    content = (
        f"curl {shlex.quote(ctx.base_url + '/chat/completions')} \\\n"
        f'  -H "Authorization: Bearer ${ctx.env_key}" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{body}'\n"
    )
    return Snippet(
        id="curl",
        harness="curl",
        title="Chat completion request",
        language="bash",
        content=content,
        notes=f"Smoke test against the OpenAI-compatible endpoint (bash/zsh). Set {ctx.env_key} first.",
        docs_url="https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create",
    )


def _python(ctx: SnippetContext) -> Snippet:
    extra = f"    extra_body={_py({'provider': pin})},\n" if (pin := _pinned(ctx)) else ""
    content = (
        "import os\n"
        "\n"
        "from openai import OpenAI\n"
        "\n"
        "client = OpenAI(\n"
        f"    base_url={json.dumps(ctx.base_url)},\n"
        f"    api_key=os.environ[{json.dumps(ctx.env_key)}],\n"
        ")\n"
        "response = client.chat.completions.create(\n"
        f"    model={json.dumps(ctx.model_id)},\n"
        '    messages=[{"role": "user", "content": "Hello"}],\n'
        f"{extra}"
        ")\n"
        "print(response.choices[0].message.content)\n"
    )
    return Snippet(
        id="python",
        harness="Python",
        title="openai SDK",
        language="python",
        filename="hello.py",
        content=content,
        notes=f"pip install openai, set {ctx.env_key}, then run it. Works with any OpenAI-compatible endpoint.",
        docs_url="https://github.com/openai/openai-python",
    )


_BUILDERS: tuple[Builder, ...] = (
    _vscode_copilot,
    _continue,
    _cline,
    _roo,
    _kilo,
    _claude_code,
    _opencode,
    _codex,
    _aider,
    _aider_routing,
    _zed,
    _crush,
    _goose,
    _curl,
    _python,
)


# Snippets with a place for the ``provider`` block; every other harness gets a note instead.
_CAN_PIN = {"vscode-copilot", "continue", "opencode", "aider-routing", "curl", "python"}

# Harness id → display name, for a model that only one client may call (``SnippetContext.client_restricted``).
_RESTRICTED_CLIENTS = {"opencode": "OpenCode"}


def generate(ctx: SnippetContext) -> list[Snippet]:
    """Snippets valid for this provider/model, most useful first (VS Code harnesses lead)."""
    routing = ctx.routing if _is_openrouter(ctx) else None
    if routing and routing.variant:
        # ":floor" and ":nitro" ride on the model id, so every builder carries them without knowing.
        ctx = replace(ctx, model_id=f"{ctx.model_id}:{routing.variant}")
    snippets = [s for build in _BUILDERS if (s := build(ctx)) is not None]
    if pinned := _pinned(ctx):
        slug = pinned["order"][0]
        two_files = any(s.id == "aider-routing" for s in snippets)
        for s in snippets:
            if s.id == "aider" and two_files:
                s.notes = (s.notes or "") + " Aider keeps the provider pin in a second file: see the next tab."
            elif s.id not in _CAN_PIN:
                s.notes = (s.notes or "") + (
                    f" This config has no place for a provider setting, so it does not pin {slug}: requests route as "
                    "OpenRouter decides."
                )
    if ctx.client_restricted:
        # Advice only: nothing here sends another client's identity or routes around the restriction.
        client = _RESTRICTED_CLIENTS.get(ctx.client_restricted, ctx.client_restricted)
        for s in snippets:
            if s.id != ctx.client_restricted:
                s.notes = (s.notes or "") + (
                    f" {ctx.model_name} is a free model that works only from the {client} client: "
                    "any other client gets HTTP 403, even with a valid key. Use a paid or Go model here."
                )
    return snippets
