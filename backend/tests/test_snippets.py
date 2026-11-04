import json
import re
import tomllib
from dataclasses import replace

import pytest
import yaml

from vibonomics.providers.base import ProviderSpec
from vibonomics.snippets import Routing, Snippet, SnippetContext, generate

OPENROUTER = ProviderSpec(
    type="openrouter",
    name="OpenRouter",
    description="",
    homepage="https://openrouter.ai",
    default_base_url="https://openrouter.ai/api/v1",
    anthropic_base_url="https://openrouter.ai/api",
    modelsdev_ids=("openrouter",),
    aider_prefix="openrouter",
)
ANTHROPIC = ProviderSpec(
    type="anthropic",
    name="Anthropic",
    description="",
    homepage="https://www.anthropic.com",
    default_base_url="https://api.anthropic.com/v1",
    wire="anthropic",
    modelsdev_ids=("anthropic",),
    aider_prefix="anthropic",
)
CUSTOM = ProviderSpec(
    type="custom-openai",
    name="Custom (OpenAI-compatible)",
    description="",
    homepage="https://example.com",
    default_base_url="http://localhost:4000/v1",
    custom=True,
)
DEEPSEEK = ProviderSpec(
    type="deepseek",
    name="DeepSeek",
    description="",
    homepage="https://www.deepseek.com",
    default_base_url="https://api.deepseek.com/v1",
    anthropic_base_url="https://api.deepseek.com/anthropic",
    modelsdev_ids=("deepseek",),
    aider_prefix="deepseek",
)


def make_ctx(spec: ProviderSpec, model_id: str, **overrides) -> SnippetContext:
    values = {
        "spec": spec,
        "provider_name": spec.name,
        "base_url": spec.default_base_url,
        "anthropic_base_url": spec.anthropic_base_url,
        "model_id": model_id,
        "model_name": model_id.split("/")[-1],
        "context_length": 262_144,
        "max_output": 65_536,
        "tool_call": True,
        "reasoning": True,
        "vision": False,
        "modelsdev_provider": spec.modelsdev_ids[0] if spec.modelsdev_ids else None,
        "env_key": spec.type.upper().replace("-", "_") + "_API_KEY",
    }
    values.update(overrides)
    return SnippetContext(**values)


CONTEXTS = {
    "openrouter": make_ctx(OPENROUTER, "qwen/qwen3-coder:free", price_input=0.2, price_output=0.8),
    "anthropic": make_ctx(ANTHROPIC, "claude-sonnet-5-5", context_length=1_000_000, vision=True),
    "custom": make_ctx(CUSTOM, "my-model", context_length=None, max_output=None, tool_call=False, reasoning=None),
    "deepseek": make_ctx(DEEPSEEK, "deepseek-chat", context_length=128_000, max_output=8_000, reasoning=False),
}
ROUTED = make_ctx(OPENROUTER, "deepseek/deepseek-v4.1-flash", price_input=0.045, price_output=0.5)
CONTEXTS["openrouter-pinned"] = replace(ROUTED, routing=Routing(provider="deepinfra/fp8"))
CONTEXTS["openrouter-floor"] = replace(ROUTED, routing=Routing(variant="floor"))


def by_id(snippets: list[Snippet]) -> dict[str, Snippet]:
    return {s.id: s for s in snippets}


def parse(snippet: Snippet):
    if snippet.language == "json":
        return json.loads(snippet.content)
    if snippet.language == "yaml":
        return yaml.safe_load(snippet.content)
    if snippet.language == "toml":
        return tomllib.loads(snippet.content)
    if snippet.language == "python":
        return compile(snippet.content, snippet.filename or "<snippet>", "exec")
    return snippet.content


# Shapes of real credentials: vendor prefixes, or long random-looking alphanumeric runs.
KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"sk-(or|ant)-"),
    re.compile(r"\b(gsk|xai|nvapi|glpat)[-_][A-Za-z0-9]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{32,}\b"),
]


@pytest.mark.parametrize("name", CONTEXTS)
def test_snippets_parse_and_are_complete(name):
    snippets = generate(CONTEXTS[name])
    ids = [s.id for s in snippets]
    assert len(ids) == len(set(ids))
    assert ids[0] == "vscode-copilot"  # VS Code harnesses lead
    assert ids[-2:] == ["curl", "python"]
    for s in snippets:
        assert s.notes and s.docs_url and s.docs_url.startswith("https://"), s.id
        assert s.content.strip(), s.id
        parse(s)
        for pattern in KEY_PATTERNS:
            assert not pattern.search(s.content), (s.id, pattern.pattern)
        assert set(s.to_dict()) == {"id", "harness", "title", "language", "content", "filename", "notes", "docs_url"}


def test_openrouter():
    ctx = CONTEXTS["openrouter"]
    s = by_id(generate(ctx))
    assert list(s) == [
        "vscode-copilot",
        "continue",
        "cline",
        "roo-code",
        "kilo-code",
        "claude-code",
        "opencode",
        "codex",
        "aider",
        "zed",
        "crush",
        "goose",
        "curl",
        "python",
    ]

    vscode = parse(s["vscode-copilot"])[0]
    assert vscode["vendor"] == "customendpoint"
    assert vscode["apiType"] == "chat-completions"
    assert vscode["apiKey"] == "${input:openrouterApiKey}"
    model = vscode["models"][0]
    assert model["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert model["toolCalling"] is True and model["thinking"] is True
    assert model["maxInputTokens"] + model["maxOutputTokens"] <= ctx.context_length

    cont = parse(s["continue"])["models"][0]
    assert cont["provider"] == "openrouter"
    assert cont["model"] == "qwen/qwen3-coder:free"
    assert cont["apiKey"] == "${{ secrets.OPENROUTER_API_KEY }}"
    assert cont["capabilities"] == ["tool_use"]

    assert "API Provider: OpenRouter" in s["cline"].content

    kilo = parse(s["kilo-code"])
    assert kilo["model"] == "openrouter/qwen/qwen3-coder:free"

    claude = parse(s["claude-code"])["env"]
    assert claude["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert claude["ANTHROPIC_API_KEY"] == ""
    assert claude["ANTHROPIC_AUTH_TOKEN"].startswith("<")
    assert claude["ANTHROPIC_MODEL"] == claude["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == ctx.model_id
    assert claude["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "262144"  # non-Claude id: Claude Code would assume 200K

    opencode = parse(s["opencode"])
    assert opencode["model"] == "openrouter/qwen/qwen3-coder:free"
    assert "npm" not in opencode["provider"]["openrouter"]

    codex = parse(s["codex"])
    assert codex["model_provider"] == "openrouter"
    assert codex["model_providers"]["openrouter"] == {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "wire_api": "responses",
    }

    assert parse(s["aider"]) == {"model": "openrouter/qwen/qwen3-coder:free"}

    zed = parse(s["zed"])
    assert zed["agent"]["default_model"] == {"provider": "openrouter", "model": ctx.model_id}
    assert zed["language_models"]["open_router"]["available_models"][0]["name"] == ctx.model_id

    assert "provider add custom-openrouter --type openai-compat" in s["crush"].content
    assert '--api-key "$OPENROUTER_API_KEY"' in s["crush"].content
    assert "--price-input 0.2" in s["crush"].content

    goose = parse(s["goose"])
    assert goose["name"] == "openrouter_compat"
    assert goose["api_key_env"] == "OPENROUTER_API_KEY"
    assert goose["base_url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert goose["models"][0]["input_token_cost"] == pytest.approx(0.2e-6)

    assert "$OPENROUTER_API_KEY" in s["curl"].content
    assert 'os.environ["OPENROUTER_API_KEY"]' in s["python"].content


def test_anthropic_native():
    ctx = CONTEXTS["anthropic"]
    s = by_id(generate(ctx))
    assert "codex" not in s  # api.anthropic.com doesn't serve the Responses API

    vscode = parse(s["vscode-copilot"])[0]
    assert vscode["apiType"] == "messages"
    assert vscode["models"][0]["url"] == "https://api.anthropic.com/v1/messages"
    assert vscode["models"][0]["vision"] is True

    # First-party: no gateway variables, just the model.
    assert parse(s["claude-code"]) == {"model": "claude-sonnet-5-5"}
    assert "ANTHROPIC_API_KEY" in s["claude-code"].notes

    cont = parse(s["continue"])["models"][0]
    assert cont["provider"] == "anthropic"
    assert "apiBase" not in cont
    assert cont["capabilities"] == ["tool_use", "image_input"]

    assert "API Provider: Anthropic" in s["roo-code"].content
    assert parse(s["opencode"])["model"] == "anthropic/claude-sonnet-5-5"
    assert parse(s["aider"]) == {"model": "anthropic/claude-sonnet-5-5"}
    assert parse(s["zed"])["agent"]["default_model"]["provider"] == "anthropic"
    assert "provider add custom-anthropic --type anthropic" in s["crush"].content
    assert "https://api.anthropic.com/v1" in s["crush"].content
    goose = parse(s["goose"])
    assert goose["active_provider"] == "anthropic"
    assert goose["providers"]["anthropic"]["model"] == "claude-sonnet-5-5"


def test_custom_openai_compatible():
    ctx = CONTEXTS["custom"]
    s = by_id(generate(ctx))
    assert "claude-code" not in s  # no Anthropic-compatible endpoint
    assert "roo-code" not in s  # Roo Code needs native tool calling
    assert "codex" in s and "/responses" in s["codex"].notes  # unknown endpoint: offered with a caveat

    vscode_model = parse(s["vscode-copilot"])[0]["models"][0]
    assert vscode_model["toolCalling"] is False
    assert "thinking" not in vscode_model

    opencode = parse(s["opencode"])
    provider = opencode["provider"]["custom-openai"]
    assert opencode["model"] == "custom-openai/my-model"
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"] == {"baseURL": "http://localhost:4000/v1", "apiKey": "{env:CUSTOM_OPENAI_API_KEY}"}
    assert provider["models"]["my-model"]["tool_call"] is False
    assert "limit" not in provider["models"]["my-model"]  # unknown context: don't invent one

    kilo = parse(s["kilo-code"])
    assert kilo["provider"]["openai-compatible"]["options"]["apiKey"] == "{env:CUSTOM_OPENAI_API_KEY}"

    assert parse(s["aider"]) == {"model": "openai/my-model", "openai-api-base": "http://localhost:4000/v1"}
    assert "OPENAI_API_KEY" in s["aider"].notes

    zed = parse(s["zed"])["language_models"]["openai_compatible"]["custom-openai"]
    assert zed["api_url"] == "http://localhost:4000/v1"
    assert zed["available_models"][0]["capabilities"]["tools"] is False

    cont = parse(s["continue"])["models"][0]
    assert cont["provider"] == "openai"
    assert cont["apiBase"] == "http://localhost:4000/v1"
    assert cont["capabilities"] == []

    assert "API Provider: OpenAI Compatible" in s["cline"].content
    assert "Base URL: http://localhost:4000/v1" in s["cline"].content
    assert parse(s["goose"])["name"] == "custom_openai"
    assert "estimate" in s["vscode-copilot"].notes


def test_provider_with_anthropic_base_url():
    ctx = CONTEXTS["deepseek"]
    s = by_id(generate(ctx))
    assert "codex" not in s  # Responses API support not known for this host

    claude = parse(s["claude-code"])["env"]
    assert claude["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert claude["ANTHROPIC_AUTH_TOKEN"] == "<paste your DEEPSEEK_API_KEY value>"
    assert claude["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "128000"
    assert claude["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    assert "non-Claude" in s["claude-code"].notes

    # OpenAI-compatible wire: Copilot and Crush stay on chat completions.
    assert parse(s["vscode-copilot"])[0]["apiType"] == "chat-completions"
    assert "--type openai-compat" in s["crush"].content
    assert parse(s["opencode"])["model"] == "deepseek/deepseek-chat"
    assert parse(s["aider"]) == {"model": "deepseek/deepseek-chat"}
    # Zed has its own "deepseek" provider, so the OpenAI-compatible entry gets a distinct id and key variable.
    assert "deepseek-compat" in parse(s["zed"])["language_models"]["openai_compatible"]
    assert 'export DEEPSEEK_COMPAT_API_KEY="$DEEPSEEK_API_KEY"' in s["zed"].notes

    # An explicit flag overrides the host heuristic.
    with_responses = by_id(generate(make_ctx(DEEPSEEK, "deepseek-chat", responses_api=True)))
    assert parse(with_responses["codex"])["model_provider"] == "deepseek"


def test_claude_models_are_not_resized():
    ctx = make_ctx(OPENROUTER, "anthropic/claude-sonnet-5.5", context_length=1_000_000, max_output=16_000)
    env = parse(by_id(generate(ctx))["claude-code"])["env"]
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env


def test_overridden_base_url_reaches_native_configs():
    ctx = make_ctx(OPENROUTER, "qwen/qwen3-coder", base_url="https://gateway.example.com/openrouter/v1")
    s = by_id(generate(ctx))
    assert "codex" not in s  # unknown host: no Responses API guarantee
    assert parse(s["opencode"])["provider"]["openrouter"]["options"] == {
        "baseURL": "https://gateway.example.com/openrouter/v1"
    }
    assert parse(s["zed"])["language_models"]["open_router"]["api_url"] == "https://gateway.example.com/openrouter/v1"


def test_codex_avoids_reserved_provider_ids():
    spec = ProviderSpec(
        type="ollama",
        name="Ollama",
        description="",
        homepage="https://ollama.com",
        default_base_url="http://localhost:11434/v1",
        custom=True,
    )
    codex = parse(by_id(generate(make_ctx(spec, "qwen3:8b")))["codex"])
    assert codex["model_provider"] == "ollama-custom"
    assert "ollama-custom" in codex["model_providers"]


def test_codex_on_openai_uses_builtin_provider():
    spec = ProviderSpec(
        type="openai",
        name="OpenAI",
        description="",
        homepage="https://openai.com",
        default_base_url="https://api.openai.com/v1",
        modelsdev_ids=("openai",),
    )
    assert parse(by_id(generate(make_ctx(spec, "gpt-6.1-sol")))["codex"]) == {"model": "gpt-6.1-sol"}


# --- OpenRouter routing ------------------------------------------------------------------------

PIN = {"order": ["deepinfra/fp8"], "allow_fallbacks": False}
MODEL = "deepseek/deepseek-v4.1-flash"


def routed(**routing) -> dict[str, Snippet]:
    return by_id(generate(replace(ROUTED, routing=Routing(**routing))))


def test_a_variant_rides_on_the_model_id_of_every_snippet():
    snippets = routed(variant="floor")
    for s in snippets.values():
        assert f"{MODEL}:floor" in s.content, s.id
        assert "does not pin" not in (s.notes or ""), s.id


def test_pinning_puts_a_provider_block_where_the_harness_has_room():
    snippets = routed(provider="deepinfra/fp8")
    body = json.loads(re.search(r"-d '(.*)'", snippets["curl"].content).group(1))
    assert body["provider"] == PIN
    assert 'extra_body={"provider": {"order": ["deepinfra/fp8"], "allow_fallbacks": False}}' in (
        snippets["python"].content
    )
    assert parse(snippets["opencode"])["provider"]["openrouter"]["models"][MODEL]["options"] == {"provider": PIN}
    assert parse(snippets["vscode-copilot"])[0]["models"][0]["modelOptions"] == {"provider": PIN}
    assert parse(snippets["continue"])["models"][0]["requestOptions"] == {"extraBodyProperties": {"provider": PIN}}
    assert parse(snippets["aider-routing"]) == [
        {"name": f"openrouter/{MODEL}", "extra_params": {"extra_body": {"provider": PIN}}}
    ]


def test_a_harness_without_room_says_it_cannot_pin():
    snippets = routed(provider="deepinfra/fp8")
    for harness in ("cline", "roo-code", "kilo-code", "claude-code", "codex", "zed", "crush", "goose"):
        assert "does not pin deepinfra/fp8" in snippets[harness].notes, harness
    assert "second file" in snippets["aider"].notes
    for harness in ("vscode-copilot", "continue", "opencode", "aider-routing", "curl", "python"):
        assert "does not pin" not in snippets[harness].notes, harness


def test_allowing_fallbacks_drops_the_flag_that_forbids_them():
    snippets = routed(provider="deepinfra/fp8", allow_fallbacks=True)
    assert parse(snippets["opencode"])["provider"]["openrouter"]["models"][MODEL]["options"] == {
        "provider": {"order": ["deepinfra/fp8"]}
    }
    assert "allow_fallbacks" not in snippets["aider-routing"].content


def test_default_routing_changes_nothing():
    plain = generate(ROUTED)
    assert "aider-routing" not in [s.id for s in plain]
    assert [s.to_dict() for s in generate(replace(ROUTED, routing=Routing()))] == [s.to_dict() for s in plain]


def test_routing_means_nothing_to_another_endpoint():
    ctx = CONTEXTS["deepseek"]
    pinned = replace(ctx, routing=Routing(variant="nitro", provider="deepinfra/fp8"))
    assert [s.to_dict() for s in generate(pinned)] == [s.to_dict() for s in generate(ctx)]
