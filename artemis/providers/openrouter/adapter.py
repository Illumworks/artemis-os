"""OpenRouter adapter -- completions and SSE streaming via the OpenAI-compatible API.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- ``complete()`` calls ``/chat/completions`` (non-streaming) and is untouched.
- ``stream()`` adds ``stream: true`` and yields ``StreamEvent`` objects.
- ``cache_system`` / ``cache_tools`` are ignored.
- Pricing is dynamic; ``cost_usd`` from ``usage.total_cost`` or 0.0.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.providers.errors import MissingApiKeyError, ProviderAPIError
from artemis.providers.openrouter.models import OPENROUTER_DEFAULT_MODEL, resolve_model
from artemis.providers.streaming import (
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseDelta,
    StreamToolUseStart,
    StreamUsage,
)

_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_APP_SITE_URL = "https://artemis.local"
_APP_TITLE = "Artemis"

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


class OpenRouterAdapter:
    """Conforms to the ModelAdapter and SupportsStreaming protocols."""

    def __init__(
        self,
        *,
        default_model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not resolved_key:
            raise MissingApiKeyError(
                "OPENROUTER_API_KEY is not set. "
                "Pass api_key= explicitly or set OPENROUTER_API_KEY in ~/.artemis/.env"
            )
        self._api_key = resolved_key
        self._default_model = resolve_model(default_model or OPENROUTER_DEFAULT_MODEL)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a single non-streaming completion request to OpenRouter."""
        model = resolve_model(request.model) if request.model else self._default_model

        body = self._build_body(request, model)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": _APP_SITE_URL,
            "X-Title": _APP_TITLE,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_OPENROUTER_API_BASE}/chat/completions",
                json=body,
                headers=headers,
                timeout=120.0,
            )

        if not response.is_success:
            raise ProviderAPIError(response.status_code, response.text)

        data = response.json()
        return self._parse_response(data)

    async def stream(
        self,
        request: CompletionRequest,
        *,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream via OpenAI-format SSE (``stream: true``).

        Yields StreamTextDelta, StreamToolUseStart, StreamToolUseDelta,
        StreamMessageStop, StreamUsage.  Pass cancel= to stop early.
        """
        return self._stream_impl(request, cancel=cancel)

    async def _stream_impl(
        self,
        request: CompletionRequest,
        *,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        model = resolve_model(request.model) if request.model else self._default_model
        body = self._build_body(request, model)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": _APP_SITE_URL,
            "X-Title": _APP_TITLE,
        }

        async with (
            httpx.AsyncClient() as client,
            client.stream(
                "POST",
                f"{_OPENROUTER_API_BASE}/chat/completions",
                json=body,
                headers=headers,
                timeout=120.0,
            ) as response,
        ):
            if not response.is_success:
                error_text = await response.aread()
                raise ProviderAPIError(response.status_code, error_text.decode())

            finish_reason_seen: str | None = None
            input_tokens = 0
            output_tokens = 0
            cost_usd = 0.0
            seen_tool_ids: set[str] = set()
            buffer = ""

            async for raw_chunk in response.aiter_text():
                if cancel is not None and cancel.is_set():
                    break

                buffer += raw_chunk
                while "\n\n" in buffer:
                    event_block, buffer = buffer.split("\n\n", 1)
                    for line in event_block.splitlines():
                        if not line.startswith("data:"):
                            continue
                        json_str = line[5:].strip()
                        if not json_str or json_str == "[DONE]":
                            continue

                        try:
                            parsed: dict[str, Any] = json.loads(json_str)
                        except json.JSONDecodeError:
                            continue

                        usage_data = parsed.get("usage")
                        if usage_data:
                            input_tokens = int(usage_data.get("prompt_tokens", 0))
                            output_tokens = int(usage_data.get("completion_tokens", 0))
                            cost_usd = float(usage_data.get("total_cost", 0.0))

                        choice = (parsed.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}

                        if cancel is not None and cancel.is_set():
                            break

                        content = delta.get("content")
                        if content:
                            yield StreamTextDelta(text=content)

                        tool_call_chunks: list[dict[str, Any]] = delta.get("tool_calls") or []
                        for tc in tool_call_chunks:
                            if cancel is not None and cancel.is_set():
                                break
                            tc_id = tc.get("id", "")
                            fn = tc.get("function") or {}
                            name = fn.get("name", "")
                            arguments_chunk = fn.get("arguments", "")

                            if tc_id and tc_id not in seen_tool_ids and name:
                                seen_tool_ids.add(tc_id)
                                yield StreamToolUseStart(id=tc_id, name=name)

                            if arguments_chunk:
                                emit_id = tc_id or next(iter(seen_tool_ids), "")
                                yield StreamToolUseDelta(id=emit_id, partial_json=arguments_chunk)

                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason_seen = fr

            stop_reason = _FINISH_REASON_MAP.get(
                finish_reason_seen or "stop",
                (finish_reason_seen or "stop").lower(),
            )
            yield StreamMessageStop(stop_reason=stop_reason)
            yield StreamUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd if cost_usd else 0.0,
            )

    def _build_body(self, request: CompletionRequest, model: str) -> dict[str, Any]:
        messages = self._translate_messages(request)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        return body

    @staticmethod
    def _translate_messages(request: CompletionRequest) -> list[dict[str, Any]]:
        """Translate Artemis Message list to OpenAI chat messages format."""
        result: list[dict[str, Any]] = []

        if request.system:
            result.append({"role": "system", "content": request.system})

        for msg in request.messages:
            if msg.role == "user":
                text_parts: list[str] = []
                tool_results: list[dict[str, Any]] = []

                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolResultBlock):
                        content_str: str
                        try:
                            parsed = json.loads(block.content)
                            content_str = json.dumps(parsed)
                        except (json.JSONDecodeError, TypeError):
                            content_str = str(block.content)
                        tool_results.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.tool_use_id,
                                "content": content_str,
                            }
                        )

                if tool_results:
                    result.extend(tool_results)
                elif text_parts:
                    result.append({"role": "user", "content": " ".join(text_parts)})

            elif msg.role == "assistant":
                text_parts_a: list[str] = []
                tool_calls: list[dict[str, Any]] = []

                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts_a.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(
                            {
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": json.dumps(block.input),
                                },
                            }
                        )

                api_msg: dict[str, Any] = {"role": "assistant"}
                api_msg["content"] = " ".join(text_parts_a) if text_parts_a else None
                if tool_calls:
                    api_msg["tool_calls"] = tool_calls
                result.append(api_msg)

        return result

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> CompletionResponse:
        choice = (data.get("choices") or [{}])[0]
        msg_data = choice.get("message", {})
        blocks: list[Any] = []

        content = msg_data.get("content")
        if content:
            blocks.append(TextBlock(text=content))

        tool_calls = msg_data.get("tool_calls") or []
        for tc in tool_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_input: dict[str, Any] = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed_input = {}
            blocks.append(
                ToolUseBlock(id=tc.get("id", ""), name=fn.get("name", ""), input=parsed_input)
            )

        usage_data = data.get("usage", {})
        cost_usd: float = float(usage_data.get("total_cost", 0.0))
        usage = Usage(
            input_tokens=int(usage_data.get("prompt_tokens", 0)),
            output_tokens=int(usage_data.get("completion_tokens", 0)),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        finish_reason = choice.get("finish_reason", "stop") or "stop"
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, finish_reason.lower())

        return _OpenRouterCompletionResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=stop_reason,
            usage=usage,
            cost_usd=cost_usd,
        )


class _OpenRouterCompletionResponse(CompletionResponse):
    """CompletionResponse extended with OpenRouter cost."""

    __slots__ = ("cost_usd",)

    def __init__(
        self, *, message: Message, stop_reason: str, usage: Usage, cost_usd: float
    ) -> None:
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "stop_reason", stop_reason)
        object.__setattr__(self, "usage", usage)
        object.__setattr__(self, "cost_usd", cost_usd)
