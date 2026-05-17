"""Gemini adapter — non-streaming completions via the Generative Language REST API.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
Applied here as: one clear ``complete()`` method, no nested option objects,
no magic parameter inference. The adapter does exactly one thing per call and
surfaces errors as typed exceptions so callers can branch cleanly.

Notes
-----
- ``cache_system`` / ``cache_tools`` from CompletionRequest are **ignored**.
  Gemini does not support Anthropic-style prompt caching; the fields are
  part of the shared protocol but have no effect on this adapter.
- Streaming is out of scope for V1. This adapter calls ``generateContent``
  (non-streaming). SSE streaming is a separate future slice.
- Tool-use is supported via Gemini function-calling (``functionDeclarations``).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.providers.errors import MissingApiKeyError, ProviderAPIError
from artemis.providers.gemini.models import GEMINI_DEFAULT_MODEL, estimate_cost, resolve_model

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Gemini finish_reason -> Artemis stop_reason
_FINISH_REASON_MAP: dict[str, str] = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "safety",
}


class GeminiAdapter:
    """Conforms to the ModelAdapter Protocol.

    Calls the Gemini ``generateContent`` REST endpoint (non-streaming).
    API key is read from ``GEMINI_API_KEY`` env var unless overridden.
    """

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
        """Send a single non-streaming completion request to Gemini.

        Resolves model, translates messages to Gemini ``contents[]`` format,
        POSTs to generateContent, and returns a typed CompletionResponse.

        ``cache_system`` and ``cache_tools`` in the request are ignored —
        Gemini does not support Anthropic-style prompt caching.
        """
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
            raise ProviderAPIError(response.status_code, response.text)

        data = response.json()
        return self._parse_response(data, model)

    # ── private helpers ────────────────────────────────────────────────────

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
                    parts.append(
                        {
                            "functionCall": {
                                "name": block.name,
                                "args": block.input,
                            }
                        }
                    )
                elif isinstance(block, ToolResultBlock):
                    # Tool results come back as user-role functionResponse parts
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

        # Parse token usage
        usage_meta = data.get("usageMetadata", {})
        input_tokens = int(usage_meta.get("promptTokenCount", 0))
        output_tokens = int(usage_meta.get("candidatesTokenCount", 0))

        # Compute cost from built-in pricing table
        cost_usd = estimate_cost(model, input_tokens, output_tokens)

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        # Map finish_reason to Artemis stop_reason
        finish_reason = candidate.get("finishReason", "STOP")
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, finish_reason.lower())

        # Surface cost as metadata on usage via a dynamic attr — Usage is a
        # slots=True dataclass so we store it separately in a wrapper dict.
        # Callers that need cost_usd should use the CompletionResponse metadata.
        return _GeminiCompletionResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=stop_reason,
            usage=usage,
            cost_usd=cost_usd,
        )


class _GeminiCompletionResponse(CompletionResponse):
    """CompletionResponse extended with Gemini-specific cost estimate.

    ``cost_usd`` is the estimated USD cost for this completion based on
    the pricing table in ``artemis.providers.gemini.models``.
    """

    # CompletionResponse uses slots=True dataclass; we cannot add a field
    # via inheritance through dataclass machinery, so we use __init__ + __slots__.
    __slots__ = ("cost_usd",)

    def __init__(
        self,
        *,
        message: Message,
        stop_reason: str,
        usage: Usage,
        cost_usd: float,
    ) -> None:
        # Call grandparent object.__init__ — CompletionResponse is a dataclass
        # so we set fields directly.
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "stop_reason", stop_reason)
        object.__setattr__(self, "usage", usage)
        object.__setattr__(self, "cost_usd", cost_usd)
