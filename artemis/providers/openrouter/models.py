"""OpenRouter model alias map.

Port of claudeck-artemis/server/providers/openrouter/model-map.js.

OpenRouter pricing is dynamic per-model and not available in a static table.
Cost defaults to 0.0 in V1. If the response body surfaces ``usage.total_cost``,
that value is used instead. This is documented in the adapter.
"""

from __future__ import annotations

# Short alias -> full OpenRouter model ID.
# Full IDs (e.g. "meta-llama/llama-3.1-70b-instruct") pass through unchanged.
OPENROUTER_MODEL_MAP: dict[str, str] = {
    # Free tier — large context, coding-first
    "llama-4-maverick-free": "meta-llama/llama-4-maverick:free",
    "llama-3.3-70b-free": "meta-llama/llama-3.3-70b-instruct:free",
    "gemma-4-31b-free": "google/gemma-4-31b-it:free",
    "gemma-4-26b-free": "google/gemma-4-26b-a4b-it:free",
    "nemotron-3-super-free": "nvidia/nemotron-3-super-120b-a12b:free",
    "laguna-m-free": "poolside/laguna-m.1:free",
    "laguna-xs-free": "poolside/laguna-xs.2:free",
    "nous-hermes-405b-free": "nousresearch/hermes-3-llama-3.1-405b:free",
    "mistral-7b-free": "mistralai/mistral-7b-instruct:free",
    # Paid — Claude via OpenRouter
    "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
    "claude-3.5-haiku": "anthropic/claude-3.5-haiku",
    "claude-3-opus": "anthropic/claude-3-opus",
    # Paid — OpenAI via OpenRouter
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "o1": "openai/o1",
    # Paid — Meta / Mistral
    "llama-3.1-70b": "meta-llama/llama-3.1-70b-instruct",
    "llama-3.1-405b": "meta-llama/llama-3.1-405b-instruct",
    "mistral-large": "mistralai/mistral-large",
}

# Default model when none is specified.
OPENROUTER_DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


def resolve_model(name: str | None) -> str:
    """Resolve a short alias to the full OpenRouter model ID.

    Unknown names (including full IDs) pass through unchanged.
    None returns the provider default.
    """
    if not name:
        return OPENROUTER_DEFAULT_MODEL
    return OPENROUTER_MODEL_MAP.get(name, name)
