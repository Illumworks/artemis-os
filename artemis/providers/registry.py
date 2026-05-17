"""Provider registry — dispatch by string ID.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
Applied here as: ``get_adapter()`` is the single entry point; callers pass a
string and keyword args, the registry does the rest.
"""

from __future__ import annotations

from collections.abc import Callable

from artemis.agent.client import AnthropicAdapter, ModelAdapter
from artemis.providers.errors import UnknownProviderError
from artemis.providers.gemini.adapter import GeminiAdapter
from artemis.providers.openrouter.adapter import OpenRouterAdapter

_BUILDERS: dict[str, Callable[..., ModelAdapter]] = {
    "anthropic": lambda **kw: AnthropicAdapter(**kw),
    "gemini": lambda **kw: GeminiAdapter(**kw),
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
