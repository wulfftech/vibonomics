import json

import httpx
import pytest
import respx

from vibonomics.evals.llm import ChatError, LLMClient, ToolSpec, conversation_id, to_anthropic_messages
from vibonomics.providers.base import ChatTarget

TOOL = ToolSpec(
    name="write_file",
    description="Write a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def openai_client() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "Let me write that.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": '{"path": "."}'},
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {"name": "broken", "arguments": "not json"},
                            },
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "cost": 0.0012},
        },
    )


@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_with_tool_calls():
    route = respx.post("https://example.test/v1/chat/completions").mock(return_value=openai_client())
    client = LLMClient(ChatTarget(wire="openai", base_url="https://example.test/v1", model="m", headers={"A": "b"}))
    reply = await client.chat([{"role": "user", "content": "hi"}], [TOOL])
    await client.aclose()
    assert reply.text == "Let me write that."
    assert reply.tool_calls[0].name == "list_files"
    assert reply.tool_calls[0].arguments == {"path": "."}
    assert reply.tool_calls[1].error is not None
    assert (reply.input_tokens, reply.output_tokens) == (12, 7)
    assert reply.cost_usd == 0.0012
    body = json.loads(route.calls[0].request.content)
    assert body["tools"][0]["function"]["name"] == "write_file"
    assert body["max_tokens"] == 4096


@pytest.mark.asyncio
@respx.mock
async def test_the_conversation_id_goes_in_the_providers_session_header_only():
    route = respx.post("https://example.test/v1/chat/completions").mock(return_value=openai_client())
    target = ChatTarget(
        wire="openai",
        base_url="https://example.test/v1",
        model="m",
        headers={"A": "b"},
        session_header="x-opencode-session",
    )
    plain = ChatTarget(wire="openai", base_url="https://example.test/v1", model="m", headers={"A": "b"})
    client, plain_client = LLMClient(target), LLMClient(plain)

    await client.chat([{"role": "user", "content": "no conversation yet"}])
    token = conversation_id.set("vibonomics-abc")
    try:
        await client.chat([{"role": "user", "content": "hi"}])
        await plain_client.chat([{"role": "user", "content": "hi"}])
    finally:
        conversation_id.reset(token)
    await client.aclose()
    await plain_client.aclose()

    without, with_id, other_provider = (call.request.headers for call in route.calls)
    assert "x-opencode-session" not in without
    assert with_id["x-opencode-session"] == "vibonomics-abc"
    assert with_id["a"] == "b"
    assert "x-opencode-session" not in other_provider


@pytest.mark.asyncio
@respx.mock
async def test_openai_retries_on_server_error():
    route = respx.post("https://example.test/v1/chat/completions")
    route.side_effect = [
        httpx.Response(500, text="oops"),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}),
    ]
    client = LLMClient(ChatTarget(wire="openai", base_url="https://example.test/v1", model="m", headers={}))
    reply = await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
    assert reply.text == "ok"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_openai_switches_to_max_completion_tokens():
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "max_tokens" in body:
            message = "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."
            return httpx.Response(400, json={"error": {"message": message}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    route = respx.post("https://example.test/v1/chat/completions").mock(side_effect=reply)
    client = LLMClient(ChatTarget(wire="openai", base_url="https://example.test/v1", model="m", headers={}))
    first = await client.chat([{"role": "user", "content": "hi"}])
    second = await client.chat([{"role": "user", "content": "again"}])
    await client.aclose()
    assert (first.text, second.text) == ("ok", "ok")
    assert route.call_count == 3
    assert json.loads(route.calls[1].request.content)["max_completion_tokens"] == 4096


@pytest.mark.asyncio
@respx.mock
async def test_openai_error_is_reported():
    respx.post("https://example.test/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    client = LLMClient(ChatTarget(wire="openai", base_url="https://example.test/v1", model="m", headers={}))
    with pytest.raises(ChatError, match="401"):
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_messages_roundtrip():
    route = respx.post("https://api.anthropic.test/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "toolu_1", "name": "list_files", "input": {"path": "."}},
                ],
                "usage": {"input_tokens": 20, "output_tokens": 9},
            },
        )
    )
    client = LLMClient(
        ChatTarget(wire="anthropic", base_url="https://api.anthropic.test", model="claude", headers={"x-api-key": "k"})
    )
    reply = await client.chat(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "look around"},
        ],
        [TOOL],
    )
    await client.aclose()
    assert reply.text == "Checking."
    assert reply.tool_calls[0].arguments == {"path": "."}
    assert (reply.input_tokens, reply.output_tokens) == (20, 9)
    body = json.loads(route.calls[0].request.content)
    assert body["system"] == "be brief"
    assert body["tools"][0]["input_schema"]["required"] == ["path"]
    assert route.calls[0].request.headers["x-api-key"] == "k"
    assert route.calls[0].request.headers["anthropic-version"] == "2023-06-01"


def test_anthropic_conversion_merges_tool_results():
    system, messages = to_anthropic_messages(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}},
                    {"id": "b", "type": "function", "function": {"name": "g", "arguments": "bad"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "result a"},
            {"role": "tool", "tool_call_id": "b", "content": "result b"},
        ]
    )
    assert system == "sys"
    assert messages[1]["role"] == "assistant"
    assert [b["type"] for b in messages[1]["content"]] == ["tool_use", "tool_use"]
    assert messages[2]["role"] == "user"
    assert [b["type"] for b in messages[2]["content"]] == ["tool_result", "tool_result"]
    assert messages[1]["content"][1]["input"]["_invalid_json"]


KEY = "sk-live-0123456789abcdef"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}}),
        httpx.Response(401, text=f"bad credentials {KEY}"),
        httpx.Response(200, json={"error": {"message": f"Invalid key {KEY}"}}),
    ],
    ids=["http-error-body", "plain-text-body", "error-in-a-200"],
)
async def test_a_key_the_provider_echoes_never_reaches_the_error(response):
    respx.post("https://example.test/v1/chat/completions").mock(return_value=response)
    target = ChatTarget(
        wire="openai", base_url="https://example.test/v1", model="m", headers={"Authorization": f"Bearer {KEY}"}
    )
    client = LLMClient(target)
    with pytest.raises(ChatError) as caught:
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
    assert KEY not in str(caught.value)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, text="x" * 280 + KEY),
        httpx.Response(401, json={"error": {"message": "y" * 280 + KEY}}),
    ],
    ids=["plain-text-body", "json-error-message"],
)
async def test_a_key_echoed_across_the_300_character_cut_is_still_scrubbed(response):
    respx.post("https://example.test/v1/chat/completions").mock(return_value=response)
    target = ChatTarget(
        wire="openai", base_url="https://example.test/v1", model="m", headers={"Authorization": f"Bearer {KEY}"}
    )
    client = LLMClient(target)
    with pytest.raises(ChatError) as caught:
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
    assert "sk-live" not in str(caught.value)
    assert KEY[8:16] not in str(caught.value)


@pytest.mark.asyncio
@respx.mock
async def test_a_key_echoed_by_anthropic_never_reaches_the_error():
    body = {"type": "error", "error": {"message": f"invalid x-api-key {KEY}"}}
    respx.post("https://example.test/v1/messages").mock(return_value=httpx.Response(200, json=body))
    target = ChatTarget(wire="anthropic", base_url="https://example.test", model="m", headers={"x-api-key": KEY})
    client = LLMClient(target)
    with pytest.raises(ChatError) as caught:
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
    assert KEY not in str(caught.value)
