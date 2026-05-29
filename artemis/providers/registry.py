"""Provider registry — dispatch by string ID.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
Applied here as: ``get_adapter()`` is the single entry point; callers pass a
string and keyword args, the registry does the rest.

Tool-execution capability matrix (CC19)
----------------------------------------
+-------------+------------------------------------+
| Provider    | Tool support in .complete()        |
+-------------+------------------------------------+
| anthropic   | Native tool_use loop               |
| gemini      | Function-calling translated        |
| openai      | tool_calls ↔ tool_use translation  |
| openrouter  | Same as openai                     |
| claude-code | Via MCP path ONLY                  |
|             | (.complete() with tools → MCP      |
|             | server; or .run_with_tools())       |
| codex       | Text-only — tools silently ignored |
|             | (emits logger.warning)             |
| lm-studio   | Text-only fallback — tool support  |
|             | model-dependent, not reliable      |
|             | (emits logger.warning)             |
+-------------+------------------------------------+

When routing surfaces that require tool execution (Builder, Floating Artemis,
Pipeline AI Panel), prefer anthropic/gemini/openai/openrouter for in-process
tool_use, or claude-code for subscription-based MCP execution.  Do NOT route
tool-using surfaces to codex or lm-studio without explicit warning suppression.
"""

from __future__ import annotations

from collections.abc import Callable

from artemis.agent.client import AnthropicAdapter, ModelAdapter
from artemis.providers.claude_code.adapter import ClaudeCodeAdapter
from artemis.providers.codex.adapter import CodexAdapter
from artemis.providers.errors import UnknownProviderError
from artemis.providers.gemini.adapter import GeminiAdapter
from artemis.providers.lm_studio.adapter import LMStudioAdapter
from artemis.providers.openai.adapter import OpenAIAdapter
from artemis.providers.openrouter.adapter import OpenRouterAdapter

_BUILDERS: dict[str, Callable[..., ModelAdapter]] = {
    "anthropic": lambda **kw: AnthropicAdapter(**kw),
    "claude-code": lambda **kw: ClaudeCodeAdapter(**kw),
    "codex": lambda **kw: CodexAdapter(**kw),
    "gemini": lambda **kw: GeminiAdapter(**kw),
    "lm-studio": lambda **kw: LMStudioAdapter(**kw),
    "openai": lambda **kw: OpenAIAdapter(**kw),
    "openrouter": lambda **kw: OpenRouterAdapter(**kw),
}


def get_adapter(provider_id: str, **kwargs: object) -> ModelAdapter:
    """Return a ModelAdapter for the given provider_id.

    Parameters
    ----------
    provider_id:
        One of ``"anthropic"``, ``"gemini"``, ``"openrouter"``.
    **kwargs:
        Passed verbatim to the adapter constructor (e.g. ``api_key``,
        ``default_model``).

    Raises
    ------
    UnknownProviderError
        If ``provider_id`` is not registered.
    MissingApiKeyError
        Propagated from the adapter constructor when the required env var
        is absent and no ``api_key`` kwarg is provided.
    """
    builder = _BUILDERS.get(provider_id)
    if builder is None:
        raise UnknownProviderError(f"Unknown provider: {provider_id!r}")
    return builder(**kwargs)


def list_providers() -> list[str]:
    """Return alphabetically sorted list of registered provider IDs."""
    return sorted(_BUILDERS.keys())
