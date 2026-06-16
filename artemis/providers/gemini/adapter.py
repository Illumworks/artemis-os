"""Gemini adapter -- completions and SSE streaming via the Generative Language REST API.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- ``complete()`` calls ``generateContent`` (non-streaming) and is untouched.
- ``stream()`` calls ``streamGenerateContent?alt=sse`` and yields ``StreamEvent`` objects.
- Tool-use is supported via Gemini function-calling (``functionDeclarations``).
- ``cache_system`` / ``cache_tools`` are ignored; Gemini has no Anthropic-style caching.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.providers.errors import GeminiRateLimitError, MissingApiKeyError, ProviderAPIError
from artemis.providers.gemini.models import GEMINI_DEFAULT_MODEL, estimate_cost, resolve_model
from artemis.providers.streaming import (
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseDelta,
    StreamToolUseStart,
    StreamUsage,
)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_FINISH_REASON_MAP: dict[str, str] = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "safety",
}


class GeminiAdapter:
    """Conforms to the ModelAdapter and SupportsStreaming protocols."""

    def __init__(
        self,
        *,
        default_model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not resolved_key:
            raise MissingApiKeyError(
                "GEMINI_API_KEY is not set. "
                "Pass api_key= explicitly or set GEMINI_API_KEY in ~/.artemis/.env"
            )
        self._api_key = resolved_key
        self._default_model = resolve_model(default_model or GEMINI_DEFAULT_MODEL)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a single non-streaming completion request to Gemini."""
        model = resolve_model(request.model) if request.model else self._default_model

        url = f"{_GEMINI_API_BASE}/models/{model}:generateContent?key={self._api_key}"
        body = self._build_body(request, model)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=120.0,
            )

        if not response.is_success:
            if response.status_code in (429, 503):
                raise GeminiRateLimitError(response.status_code, response.text)
            raise ProviderAPIError(response.status_code, response.text)

        data = response.json()
        return self._parse_response(data, model)

    async def stream(
        self,
        request: CompletionRequest,
        *,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream via ``streamGenerateContent?alt=sse``."""
        return self._stream_impl(request, cancel=cancel)

    async def _stream_impl(
        self,
        request: CompletionRequest,
        *,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        model = resolve_model(request.model) if request.model else self._default_model
        url = f"{_GEMINI_API_BASE}/models/{model}:streamGenerateContent?alt=sse&key={self._api_key}"
        body = self._build_body(request, model)

        async with (
            httpx.AsyncClient() as client,
            client.stream(
                "POST",
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=120.0,
            ) as response,
        ):
            if not response.is_success:
                error_text = await response.aread()
                if response.status_code in (429, 503):
                    raise GeminiRateLimitError(response.status_code, error_text.decode())
                raise ProviderAPIError(response.status_code, error_text.decode())

            input_tokens = 0
            output_tokens = 0
            finish_reason_seen: str | None = None
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

                        candidate = (parsed.get("candidates") or [{}])[0]
                        parts: list[dict[str, Any]] = (
                            candidate.get("content", {}).get("parts") or []
                        )

                        for part in parts:
                            if cancel is not None and cancel.is_set():
                                break
                            if "text" in part:
                                yield StreamTextDelta(text=part["text"])
                            elif "functionCall" in part:
                                fc = part["functionCall"]
                                tool_id = str(uuid.uuid4())
                                yield StreamToolUseStart(id=tool_id, name=fc.get("name", ""))
                                yield StreamToolUseDelta(
                                    id=tool_id,
                                    partial_json=json.dumps(fc.get("args", {})),
                                )

                        fr = candidate.get("finishReason")
                        if fr and fr != "FINISH_REASON_UNSPECIFIED":
                            finish_reason_seen = fr

                        usage_meta = parsed.get("usageMetadata")
                        if usage_meta:
                            input_tokens = int(usage_meta.get("promptTokenCount", 0))
                            output_tokens = int(usage_meta.get("candidatesTokenCount", 0))

            stop_reason = _FINISH_REASON_MAP.get(
                finish_reason_seen or "STOP",
                (finish_reason_seen or "STOP").lower(),
            )
            yield StreamMessageStop(stop_reason=stop_reason)
            cost = estimate_cost(model, input_tokens, output_tokens)
            yield StreamUsage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    def _build_body(self, request: CompletionRequest, model: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contents": self._translate_messages(request.messages),
            "generationConfig": {"maxOutputTokens": request.max_tokens},
        }

        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}

        if request.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        }
                        for t in request.tools
                    ]
                }
            ]

        return body

    @staticmethod
    def _translate_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Translate Artemis Message list to Gemini ``contents[]`` format."""
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = "model" if msg.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                elif isinstance(block, ToolUseBlock):
                    parts.append({"functionCall": {"name": block.name, "args": block.input}})
                elif isinstance(block, ToolResultBlock):
                    content_value: Any
                    try:
                        content_value = json.loads(block.content)
                    except (json.JSONDecodeError, TypeError):
                        content_value = block.content
                    parts.append(
                        {
                            "functionResponse": {
                                "name": block.tool_use_id,
                                "response": {"content": content_value},
                            }
                        }
                    )

            if parts:
                contents.append({"role": role, "parts": parts})

        return contents

    @staticmethod
    def _parse_response(data: dict[str, Any], model: str) -> CompletionResponse:
        candidate = (data.get("candidates") or [{}])[0]
        content_data = candidate.get("content", {})
        raw_parts: list[dict[str, Any]] = content_data.get("parts", [])

        blocks: list[Any] = []
        for part in raw_parts:
            if "text" in part:
                blocks.append(TextBlock(text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                blocks.append(
                    ToolUseBlock(
                        id=str(uuid.uuid4()),
                        name=fc.get("name", ""),
                        input=dict(fc.get("args", {})),
                    )
                )

        usage_meta = data.get("usageMetadata", {})
        input_tokens = int(usage_meta.get("promptTokenCount", 0))
        output_tokens = int(usage_meta.get("candidatesTokenCount", 0))

        cost_usd = estimate_cost(model, input_tokens, output_tokens)

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        finish_reason = candidate.get("finishReason", "STOP")
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, finish_reason.lower())

        return _GeminiCompletionResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=stop_reason,
            usage=usage,
            cost_usd=cost_usd,
        )


class _GeminiCompletionResponse(CompletionResponse):
    """CompletionResponse extended with Gemini-specific cost estimate."""

    __slots__ = ("cost_usd",)

    def __init__(
        self,
        *,
        message: Message,
        stop_reason: str,
        usage: Usage,
        cost_usd: float,
    ) -> None:
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "stop_reason", stop_reason)
        object.__setattr__(self, "usage", usage)
        object.__setattr__(self, "cost_usd", cost_usd)
