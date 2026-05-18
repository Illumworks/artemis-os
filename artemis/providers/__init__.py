"""Artemis provider registry.

Exports the two primary entry points for the rest of the codebase:

    from artemis.providers import get_adapter, list_providers

    adapter = get_adapter("gemini", api_key="...")
    response = await adapter.complete(request)

Streaming types are re-exported here for convenience:

    from artemis.providers import StreamEvent, StreamTextDelta, ...
"""

from artemis.providers.claude_code.adapter import ClaudeCodeAdapter
from artemis.providers.codex.adapter import CodexAdapter
from artemis.providers.lm_studio.adapter import LMStudioAdapter
from artemis.providers.openai.adapter import OpenAIAdapter
from artemis.providers.registry import get_adapter, list_providers
from artemis.providers.streaming import (
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseDelta,
    StreamToolUseStart,
    StreamUsage,
)

__all__ = [
    "get_adapter",
    "list_providers",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "LMStudioAdapter",
    "OpenAIAdapter",
    "StreamEvent",
    "StreamTextDelta",
    "StreamToolUseStart",
    "StreamToolUseDelta",
    "StreamMessageStop",
    "StreamUsage",
]
