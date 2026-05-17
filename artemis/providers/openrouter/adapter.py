"""OpenRouter adapter — non-streaming completions via the OpenAI-compatible API.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
Applied here as: one clear ``complete()`` method, no nested option objects,
no magic parameter inference. Errors surface as typed exceptions so callers
can branch without inspecting exception messages.

Notes
-----
- ``cache_system`` / ``cache_tools`` from CompletionRequest are **ignored**.
  OpenRouter does not support Anthropic-style prompt caching; the fields are
  part of the shared protocol but have no effect on this adapter.
- Streaming is out of scope for V1. This adapter calls ``/chat/completions``
  without the ``stream`` flag. SSE streaming is a separate future slice.
- Pricing is dynamic per-model on OpenRouter. ``cost_usd`` defaults to 0.0.
  If the response body surfaces ``usage.total_cost``, that value is surfaced
  on the returned ``_OpenRouterCompletionResponse.cost_usd`` field.
- Tool-use is supported via the OpenAI ``tool_calls`` format.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.providers.errors import MissingApiKeyError, ProviderAPIError
from artemis.providers.openrouter.models import OPENROUTER_DEFAULT_MODEL, resolve_model

_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_APP_SITE_URL = "https://artemis.local"
_APP_TITLE = "Artemis"

# OpenRouter finish_reason -> Artemis stop_reason
_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


class OpenRouterAdapter:
    """Conforms to the ModelAdapter Protocol.

    Calls the OpenRouter ``/chat/completions`` endpoint (non-streaming,
    OpenAI-compatible format). API key is read from ``OPENROUTER_API_KEY``
    env var unless overridden.
    """

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
        """Send a single non-streaming completion request to OpenRouter.

        Resolves model, translates messages to OpenAI chat format, POSTs to
        /chat/completions, and returns a typed CompletionResponse.

        ``cache_system`` and ``cache_tools`` in the request are ignored —
        OpenRouter does not support Anthropic-style prompt caching.

        ``cost_usd`` on the returned response is sourced from ``usage.total_cost``
        in the API response when present; otherwise defaults to 0.0.
        """
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

    # ── private helpers ────────────────────────────────────────────────────

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

        # System prompt goes first as a system role message
        if request.system:
            result.append({"role": "system", "content": request.system})

        for msg in request.messages:
            if msg.role == "user":
                # Collect text blocks + tool result blocks from user turn
                text_parts: list[str] = []
                tool_results: list[dict[str, Any]] = []

                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolResultBlock):
                        # Tool results are separate messages with role "tool"
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
                if text_parts_a:
                    api_msg["content"] = " ".join(text_parts_a)
                else:
                    api_msg["content"] = None
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
                ToolUseBlock(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    input=parsed_input,
                )
            )

        usage_data = data.get("usage", {})
        input_tokens = int(usage_data.get("prompt_tokens", 0))
        output_tokens = int(usage_data.get("completion_tokens", 0))

        # OpenRouter surfaces total_cost in some responses; default to 0.0
        cost_usd: float = float(usage_data.get("total_cost", 0.0))

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
    """CompletionResponse extended with OpenRouter cost.

    ``cost_usd`` is sourced from ``usage.total_cost`` in the API response
    when present; otherwise 0.0. OpenRouter does not have a static pricing
    table — cost is billed dynamically per model.
    """

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
