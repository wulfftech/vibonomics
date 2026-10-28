"""Async chat client for the two APIs Vibonomics supports: OpenAI chat completions and Anthropic messages.

Messages are kept internally in OpenAI's chat format (``role``, ``content``, ``tool_calls``,
``tool_call_id``) because it is the common denominator for every harness snippet; the Anthropic
wire format is converted on the way in and out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..http import scrub_secrets
from ..providers.base import ChatTarget

log = logging.getLogger(__name__)

RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ChatError(Exception):
    """A chat request failed in a way the operator should see."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def anthropic(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None
    raw_arguments: str = ""
    error: str | None = None


@dataclass
class ChatReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_arguments(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw, dict):
        return raw, None
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return {}, None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"arguments were not valid JSON ({exc.msg})"
    if not isinstance(value, dict):
        return None, "arguments were JSON but not an object"
    return value, None


class LLMClient:
    """One model target. Reused across the tasks of a run so retry state is shared."""

    def __init__(
        self,
        target: ChatTarget,
        *,
        timeout: float = 180.0,
        max_tokens: int = 4096,
    ):
        self.target = target
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._token_param = "max_tokens"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20.0), follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- public -----------------------------------------------------------------------------

    async def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None) -> ChatReply:
        if self.target.wire == "anthropic":
            return await self._chat_anthropic(messages, tools)
        return await self._chat_openai(messages, tools)

    def _scrub(self, text: str) -> str:
        """Some providers echo the key they rejected; it must not reach a stored result, a summary or a log."""
        return scrub_secrets(text, self.target.headers)

    # -- OpenAI wire ------------------------------------------------------------------------

    def _openai_url(self) -> str:
        base = self.target.base_url.rstrip("/")
        return f"{base}/chat/completions"

    async def _chat_openai(self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None) -> ChatReply:
        payload: dict[str, Any] = {"model": self.target.model, "messages": messages, self._token_param: self.max_tokens}
        if tools:
            payload["tools"] = [t.openai() for t in tools]
            payload["tool_choice"] = "auto"
        if "openrouter.ai" in self.target.base_url:
            payload["usage"] = {"include": True}
        data, latency = await self._post(self._openai_url(), payload, self.target.headers)
        if isinstance(data.get("error"), dict) and "choices" not in data:
            raise ChatError(self._scrub(str(data["error"].get("message") or data["error"])))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = _flatten_content(message.get("content"))
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            arguments, error = _parse_arguments(fn.get("arguments"))
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{index}"),
                    name=str(fn.get("name") or ""),
                    arguments=arguments,
                    raw_arguments=fn.get("arguments") if isinstance(fn.get("arguments"), str) else "",
                    error=error,
                )
            )
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        return ChatReply(
            text=text,
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            finish_reason=choice.get("finish_reason"),
            latency_ms=latency,
            raw={"id": data.get("id"), "model": data.get("model")},
        )

    # -- Anthropic wire ---------------------------------------------------------------------

    def _anthropic_url(self) -> str:
        base = self.target.base_url.rstrip("/")
        return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"

    def _anthropic_headers(self) -> dict[str, str]:
        headers = dict(self.target.headers)
        headers.setdefault("anthropic-version", "2023-06-01")
        return headers

    async def _chat_anthropic(self, messages: list[dict[str, Any]], tools: list[ToolSpec] | None) -> ChatReply:
        system, converted = to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.target.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [t.anthropic() for t in tools]
        data, latency = await self._post(self._anthropic_url(), payload, self._anthropic_headers())
        if data.get("type") == "error":
            raise ChatError(self._scrub(str((data.get("error") or {}).get("message") or "Anthropic error")))
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for index, block in enumerate(data.get("content") or []):
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                arguments, error = _parse_arguments(block.get("input"))
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"toolu_{index}"),
                        name=str(block.get("name") or ""),
                        arguments=arguments,
                        raw_arguments=json.dumps(block.get("input")) if block.get("input") is not None else "",
                        error=error,
                    )
                )
        usage = data.get("usage") or {}
        return ChatReply(
            text="".join(text_parts),
            tool_calls=calls,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=data.get("stop_reason"),
            latency_ms=latency,
            raw={"id": data.get("id"), "model": data.get("model")},
        )

    # -- transport --------------------------------------------------------------------------

    async def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[dict[str, Any], int]:
        started = time.monotonic()
        last_error = ""
        for attempt in range(4):
            try:
                response = await self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = scrub_secrets(f"{type(exc).__name__}: {exc}", headers)
                if attempt == 3:
                    break
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if response.status_code in (400, 422):
                body = response.text
                if self._token_param == "max_tokens" and "max_completion_tokens" in body:
                    # Newer OpenAI reasoning models renamed the parameter.
                    self._token_param = "max_completion_tokens"
                    tokens = payload.get("max_tokens", self.max_tokens)
                    payload = {k: v for k, v in payload.items() if k != "max_tokens"}
                    payload["max_completion_tokens"] = tokens
                    continue
                if "temperature" in body and "temperature" in payload:
                    payload = {k: v for k, v in payload.items() if k != "temperature"}
                    continue
            if response.status_code in RETRIABLE_STATUS and attempt < 3:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            if response.status_code >= 400:
                raise ChatError(self._scrub(f"HTTP {response.status_code}: {_error_text(response)}"))
            latency = int((time.monotonic() - started) * 1000)
            try:
                data = response.json()
            except ValueError as exc:
                raise ChatError("The endpoint returned a non-JSON response") from exc
            if not isinstance(data, dict):
                raise ChatError("The endpoint returned an unexpected response")
            return data, latency
        raise ChatError(f"Request failed after retries: {last_error}")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(2.0 * (2**attempt), 20.0)


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(data)[:300]


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in {"text", "output_text"}:
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    return str(content)


# -- Anthropic conversion --------------------------------------------------------------------


def to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI-style messages to an Anthropic system prompt plus messages array."""
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "developer"}:
            text = _flatten_content(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": _flatten_content(message.get("content"))[:20000],
            }
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = _flatten_content(message.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                arguments, error = _parse_arguments(fn.get("arguments"))
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": arguments if arguments is not None else {"_invalid_json": error or "bad arguments"},
                    }
                )
            if not blocks:
                blocks.append({"type": "text", "text": "(no content)"})
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": "user", "content": _flatten_content(message.get("content"))})
    return "\n\n".join(system_parts), _merge_roles(converted)


def _merge_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic requires alternating roles; fold consecutive same-role messages together."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            prev = merged[-1]
            prev_content = (
                prev["content"] if isinstance(prev["content"], list) else [{"type": "text", "text": prev["content"]}]
            )
            extra = (
                message["content"]
                if isinstance(message["content"], list)
                else [{"type": "text", "text": message["content"]}]
            )
            prev["content"] = [*prev_content, *extra]
        else:
            merged.append({"role": message["role"], "content": message["content"]})
    return merged


def assistant_message(reply: ChatReply) -> dict[str, Any]:
    """The assistant turn in OpenAI chat format, for appending back to the conversation."""
    message: dict[str, Any] = {"role": "assistant", "content": reply.text or ""}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments if call.arguments is not None else {}),
                },
            }
            for call in reply.tool_calls
        ]
    return message


def tool_call_payload(calls: list[ToolCall]) -> list[dict[str, Any]]:
    """Compact form used in stored transcripts."""
    return [{"id": call.id, "name": call.name, "arguments": call.arguments, "error": call.error} for call in calls]
