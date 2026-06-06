"""Central pricing registry — single source of truth for all provider/model rates.

All rates are per-million tokens in USD. When updating rates, change only this file.
Callers that previously maintained their own rate tables (artemis/builders/_cost.py,
artemis/providers/openai/models.py, artemis/providers/gemini/models.py) now delegate
to get_rates().

Source: Anthropic pricing page, OpenAI pricing page, Google AI pricing page.
Sampled: 2026-06-06.

IMPORTANT: Do NOT edit rates retroactively — historical cost_events rows store a
snapshot of the rate at call time. Updating PRICING only affects future writes.
"""

from __future__ import annotations

from functools import lru_cache

# ---------------------------------------------------------------------------
# Master rate table
# All values: USD per 1,000,000 tokens (per-million).
# OpenAI and Gemini originally publish per-1k rates; multiply by 1000 here.
# ---------------------------------------------------------------------------
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-opus-4-7": {
            "input": 15.0,
            "output": 75.0,
            "cache_write": 18.75,
            "cache_read": 1.50,
        },
        "claude-sonnet-4-6": {
            "input": 3.0,
            "output": 15.0,
            "cache_write": 3.75,
            "cache_read": 0.30,
        },
        "claude-sonnet-4-5": {
            "input": 3.0,
            "output": 15.0,
            "cache_write": 3.75,
            "cache_read": 0.30,
        },
        "claude-opus-4-5": {
            "input": 15.0,
            "output": 75.0,
            "cache_write": 18.75,
            "cache_read": 1.50,
        },
        "claude-opus-4": {
            "input": 15.0,
            "output": 75.0,
            "cache_write": 18.75,
            "cache_read": 1.50,
        },
        "claude-haiku-4-5-20251001": {
            "input": 0.80,
            "output": 4.0,
            "cache_write": 1.0,
            "cache_read": 0.08,
        },
        "claude-haiku-4-5": {
            "input": 0.80,
            "output": 4.0,
            "cache_write": 1.0,
            "cache_read": 0.08,
        },
        "claude-haiku-3-5": {
            "input": 0.80,
            "output": 4.0,
            "cache_write": 1.0,
            "cache_read": 0.08,
        },
    },
    "openai": {
        "gpt-5": {"input": 1.25, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0},
        "gpt-5-mini": {"input": 0.25, "output": 2.0, "cache_write": 0.0, "cache_read": 0.0},
        "gpt-5-nano": {"input": 0.05, "output": 0.40, "cache_write": 0.0, "cache_read": 0.0},
        "gpt-4o": {"input": 2.5, "output": 10.0, "cache_write": 0.0, "cache_read": 0.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_write": 0.0, "cache_read": 0.0},
        "o3": {"input": 2.0, "output": 8.0, "cache_write": 0.0, "cache_read": 0.0},
        "o3-mini": {"input": 1.10, "output": 4.40, "cache_write": 0.0, "cache_read": 0.0},
        "o4-mini": {"input": 1.10, "output": 4.40, "cache_write": 0.0, "cache_read": 0.0},
    },
    "gemini": {
        "gemini-1.5-pro": {
            "input": 1.25,
            "output": 5.0,
            "cache_write": 0.0,
            "cache_read": 0.0,
        },
        "gemini-1.5-flash": {
            "input": 0.075,
            "output": 0.30,
            "cache_write": 0.0,
            "cache_read": 0.0,
        },
        "gemini-2.0-flash": {
            "input": 0.10,
            "output": 0.40,
            "cache_write": 0.0,
            "cache_read": 0.0,
        },
        "gemini-2.5-flash-preview-05-20": {
            "input": 0.15,
            "output": 0.60,
            "cache_write": 0.0,
            "cache_read": 0.0,
        },
        "gemini-2.5-pro-preview-05-06": {
            "input": 1.25,
            "output": 10.0,
            "cache_write": 0.0,
            "cache_read": 0.0,
        },
    },
    # claude-code = subscription CLI. Rates fall through to the anthropic table
    # for the actual model used. This entry exists so the registry doesn't KeyError
    # on provider="claude-code" lookups; callers use 'anthropic' model rates.
    "claude-code": {},
    # lm-studio = local inference, zero marginal cost.
    "lm-studio": {},
    # codex = OpenAI Codex (deprecated), treat as gpt-4o-mini equivalent.
    "codex": {},
}

# Prefix fallback order for anthropic unknown-version models.
# Checked in order; first match wins.
_ANTHROPIC_PREFIX_FALLBACKS: list[tuple[str, dict[str, float]]] = [
    ("claude-opus", PRICING["anthropic"]["claude-opus-4-7"]),
    ("claude-haiku", PRICING["anthropic"]["claude-haiku-4-5-20251001"]),
    ("claude-sonnet", PRICING["anthropic"]["claude-sonnet-4-6"]),
]

# Model-name aliases — collapse equivalent strings to one canonical form so
# the cost dashboard + routing-opportunities don't treat them as different
# models. Sources of aliases: agents.model is often stored as the short form
# while the API returns the date-suffixed form. Both refer to the same model
# with identical rates.
#
# Keys: any non-canonical string we've seen in the wild. Values: the
# canonical (date-suffixed) form.
MODEL_ALIASES: dict[str, str] = {
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}


def canonicalize_model(model: str) -> str:
    """Return the canonical form of a model name (or the input unchanged).

    Applied at cost_event write time so by_model rollups + routing-opportunity
    comparisons treat short/long forms of the same model identically.
    """
    return MODEL_ALIASES.get(model, model)


@lru_cache(maxsize=256)
def get_rates(provider: str, model: str) -> dict[str, float]:
    """Return rates dict with keys: input, output, cache_write, cache_read (all per-million USD).

    Resolution order:
    1. Exact match in PRICING[provider][model].
    2. For 'anthropic': prefix match against _ANTHROPIC_PREFIX_FALLBACKS.
    3. For 'claude-code': delegate to 'anthropic' provider with the same model.
    4. For 'lm-studio' / 'codex': return zero rates (local / deprecated).
    5. KeyError if provider is unknown and no fallback applies.

    Raises:
        KeyError: if the provider+model combo is unknown and no fallback applies.
    """
    provider_lower = provider.lower()
    model_lower = model.lower()

    # claude-code delegates to anthropic rates for the same model.
    if provider_lower == "claude-code":
        return get_rates("anthropic", model_lower)

    # Zero rates for local / deprecated providers.
    if provider_lower in ("lm-studio", "codex"):
        return {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}

    provider_table = PRICING.get(provider_lower)
    if provider_table is None:
        raise KeyError(f"Unknown provider: {provider!r}")

    # Exact match.
    if model_lower in provider_table:
        rates = provider_table[model_lower]
        return {
            "input": rates.get("input", 0.0),
            "output": rates.get("output", 0.0),
            "cache_write": rates.get("cache_write", 0.0),
            "cache_read": rates.get("cache_read", 0.0),
        }

    # Anthropic prefix fallback.
    if provider_lower == "anthropic":
        for prefix, fallback_rates in _ANTHROPIC_PREFIX_FALLBACKS:
            if model_lower.startswith(prefix):
                return {
                    "input": fallback_rates.get("input", 0.0),
                    "output": fallback_rates.get("output", 0.0),
                    "cache_write": fallback_rates.get("cache_write", 0.0),
                    "cache_read": fallback_rates.get("cache_read", 0.0),
                }

    raise KeyError(f"Unknown model {model!r} for provider {provider!r}")
