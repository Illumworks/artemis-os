"""Artemis provider registry.

Exports the two primary entry points for the rest of the codebase:

    from artemis.providers import get_adapter, list_providers

    adapter = get_adapter("gemini", api_key="...")
    response = await adapter.complete(request)
"""

from artemis.providers.registry import get_adapter, list_providers

__all__ = ["get_adapter", "list_providers"]
