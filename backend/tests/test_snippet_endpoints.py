"""Config snippets point where the provider's adapter really calls: its effective base URL and, for OpenCode, the
route each model is served on."""

from __future__ import annotations

import json

import pytest

from vibonomics.api.models import build_snippets
from vibonomics.orm import Model, ProviderConfig
from vibonomics.providers import opencode

ZEN = "https://opencode.ai/inference"
GO = "https://opencode.ai/inference/go"
OPENAI_WIRE_ONLY = {"continue", "cline", "roo-code", "aider", "zed", "goose", "curl", "python"}


def snippets(provider: ProviderConfig, model_id: str, **fields) -> dict[str, dict]:
    m = Model(
        model_id=model_id,
        name=fields.pop("name", model_id),
        price_input=1.0,
        price_output=2.0,
        context_length=128_000,
        max_output=8_000,
        tool_call=True,
        extra=fields.pop("extra", {}),
        **fields,
    )
    return {s.id: s.model_dump() for s in build_snippets(m, provider)}


def env_block(snippet: dict) -> dict:
    return json.loads(snippet["content"])["env"]


# -- the adapter's own base URL (V-2) ------------------------------------------------------------


def test_moonshot_global_snippets_use_the_global_platform():
    s = snippets(ProviderConfig(type="moonshot", name="Moonshot", options={}), "kimi-k3")
    assert "https://api.moonshot.ai/v1/chat/completions" in s["curl"]["content"]
    assert env_block(s["claude-code"])["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"


@pytest.mark.parametrize("options", [{"region": "cn"}, {"region": "China"}])
def test_moonshot_cn_snippets_use_the_china_platform(options):
    s = snippets(ProviderConfig(type="moonshot", name="Moonshot", options=options), "kimi-k3")
    assert "https://api.moonshot.cn/v1/chat/completions" in s["curl"]["content"]
    assert env_block(s["claude-code"])["ANTHROPIC_BASE_URL"] == "https://api.moonshot.cn/anthropic"
    for harness in ("python", "continue", "zed", "goose", "crush", "cline"):
        assert "moonshot.ai" not in s[harness]["content"], harness
        assert "moonshot.cn" in s[harness]["content"], harness


def test_zai_standard_and_coding_endpoints():
    standard = snippets(ProviderConfig(type="zai", name="Z.ai", options={}), "glm-5.3")
    assert "https://api.z.ai/api/paas/v4/chat/completions" in standard["curl"]["content"]
    coding = snippets(ProviderConfig(type="zai", name="Z.ai", options={"endpoint": "coding"}), "glm-5.3")
    assert "https://api.z.ai/api/coding/paas/v4/chat/completions" in coding["curl"]["content"]
    assert "/api/coding/paas/v4" in coding["python"]["content"]
    assert "/api/coding/paas/v4" in coding["goose"]["content"]


def test_a_configured_base_url_wins():
    provider = ProviderConfig(type="moonshot", name="Moonshot", base_url="https://gateway.example/moonshot/v1/")
    s = snippets(provider, "kimi-k3")
    assert "https://gateway.example/moonshot/v1/chat/completions" in s["curl"]["content"]


@pytest.mark.parametrize(
    "configured",
    [
        "https://admin:hunter2@gw.example/v1",
        "https://gw.example/v1?token=sk-test-123456789012",
        "https://admin:hunter2@gw.example/v1?token=sk-test-123456789012#frag",
    ],
)
def test_credentials_in_a_base_url_never_reach_a_snippet(configured):
    # Snippets are returned to visitors of an open-read instance.
    provider = ProviderConfig(type="custom-openai", name="Gateway", base_url=configured)
    s = snippets(provider, "my-model")
    assert s
    for snippet in s.values():
        text = snippet["content"] + (snippet["notes"] or "")
        assert "hunter2" not in text and "sk-test-123456789012" not in text and "token=" not in text, snippet["id"]
    assert "https://gw.example/v1/chat/completions" in s["curl"]["content"]


# -- OpenCode serves each model on its own route (V-3) -------------------------------------------


def opencode_model(provider_type: str, model_id: str, endpoint: dict | None) -> dict[str, dict]:
    pid = "opencode" if provider_type == "opencode" else "opencode-go"
    extra = {"modelsdev_provider": pid}
    if endpoint is not None:
        extra["endpoint"] = endpoint
    provider = ProviderConfig(type=provider_type, name="OpenCode", options={})
    return snippets(provider, model_id, extra=extra)


def test_an_openai_route_model_gets_the_openai_wire_snippets():
    endpoint = {"npm": "@ai-sdk/openai-compatible", "api": f"{ZEN}/openai/v1"}
    s = opencode_model("opencode", "glm-5.3", endpoint)
    assert set(s) >= OPENAI_WIRE_ONLY
    assert "claude-code" not in s
    assert f"{ZEN}/openai/v1/chat/completions" in s["curl"]["content"]


def test_a_model_with_no_endpoint_on_file_is_served_on_the_default_openai_route():
    s = opencode_model("opencode", "big-pickle", None)
    assert set(s) >= OPENAI_WIRE_ONLY
    assert f"{ZEN}/openai/v1/chat/completions" in s["curl"]["content"]
    go = opencode_model("opencode-go", "glm-5.3", None)
    assert f"{GO}/openai/v1/chat/completions" in go["curl"]["content"]


@pytest.mark.parametrize(
    ("provider_type", "api", "root"),
    [
        ("opencode", f"{ZEN}/anthropic/v1", f"{ZEN}/anthropic"),
        ("opencode-go", f"{GO}/anthropic/v1", f"{GO}/anthropic"),
    ],
)
def test_an_anthropic_route_model_gets_claude_code_and_no_chat_completions(provider_type, api, root):
    s = opencode_model(provider_type, "claude-opus-5-5", {"npm": "@ai-sdk/anthropic", "api": api})
    assert env_block(s["claude-code"])["ANTHROPIC_BASE_URL"] == root
    assert not OPENAI_WIRE_ONLY & set(s), "an OpenAI-wire snippet for a model served on the Anthropic route"
    assert "chat/completions" not in "".join(snippet["content"] for snippet in s.values())
    assert {"vscode-copilot", "crush", "opencode", "kilo-code"} <= set(s)
    vscode = json.loads(s["vscode-copilot"]["content"])[0]
    assert vscode["apiType"] == "messages"
    assert vscode["models"][0]["url"] == f"{root}/v1/messages"
    assert "--type anthropic" in s["crush"]["content"] and f"{root}/v1" in s["crush"]["content"]


def test_a_gemini_route_model_gets_no_chat_completions_snippet():
    # The adapter refuses this route itself ("Gemini route ... two supported APIs").
    endpoint = {"npm": "@ai-sdk/google", "api": f"{ZEN}/google/v1beta"}
    s = opencode_model("opencode", "gemini-4-argon", endpoint)
    assert set(s) == {"opencode", "kilo-code"}  # harnesses that find the route in models.dev themselves


def test_an_endpoint_outside_opencode_ai_gets_no_url_snippet():
    # models.dev is community-edited; the adapter will not send a key there, so no snippet should invite one to.
    s = opencode_model("opencode", "odd", {"npm": "@ai-sdk/openai-compatible", "api": "https://attacker.example/v1"})
    assert set(s) == {"opencode", "kilo-code"}
    assert "attacker.example" not in "".join(snippet["content"] for snippet in s.values())


def test_the_native_opencode_snippet_names_no_url_for_any_route():
    for endpoint in (
        {"npm": "@ai-sdk/anthropic", "api": f"{ZEN}/anthropic/v1"},
        {"npm": "@ai-sdk/google", "api": f"{ZEN}/google/v1beta"},
        {"api": f"{ZEN}/openai/v1"},
    ):
        config = json.loads(opencode_model("opencode", "m", endpoint)["opencode"]["content"])
        assert config["model"] == "opencode/m"
        assert "options" not in config["provider"]["opencode"], endpoint


def test_the_route_the_adapter_calls_is_the_one_the_snippets_use():
    # chat_target and the snippets read the same catalogue entry, so they cannot drift apart.
    from vibonomics.catalog.modelsdev import ModelsDevCatalog
    from vibonomics.providers.base import FetchContext, ResolvedProvider
    from vibonomics.settings_store import AppSettings

    endpoint = {"npm": "@ai-sdk/anthropic", "api": f"{ZEN}/anthropic/v1"}
    catalog = ModelsDevCatalog({"opencode": {"id": "opencode", "models": {"m": {"id": "m", "provider": endpoint}}}})
    cfg = ResolvedProvider(
        id=1, type="opencode", name="OpenCode", api_key="sk-test-x", mgmt_key=None, base_url=None, options={}
    )
    adapter = opencode.OpenCodeAdapter(cfg, FetchContext(catalog=catalog, settings=AppSettings()))
    target = adapter.chat_target("m")
    route = adapter.model_route({"endpoint": endpoint})
    assert route is not None
    assert (target.wire, target.base_url) == (route.wire, route.base_url)
