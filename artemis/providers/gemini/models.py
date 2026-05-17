"""Gemini model alias map and pricing table.

Port of claudeck-artemis/server/providers/gemini/model-map.js.
Pricing is per-token in USD, sourced from Google AI pricing page.
Actual billing is on Google's side; these figures are for cost estimation only.
"""

from __future__ import annotations

# Short alias -> full Gemini model ID.
# Full IDs pass through unchanged via resolve_model().
GEMINI_MODEL_MAP: dict[str, str] = {
    "gemini-pro": "gemini-1.5-pro",
    "gemini-flash": "gemini-1.5-flash",
    "gemini-flash-2": "gemini-2.0-flash",
    "gemini-2.5-flash": "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-pro": "gemini-2.5-pro-preview-05-06",
}

# Per-1k-token pricing (input / output) in USD.
GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-1.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.005},
    "gemini-1.5-flash": {"input_per_1k": 0.000075, "output_per_1k": 0.0003},
    "gemini-2.0-flash": {"input_per_1k": 0.0001, "output_per_1k": 0.0004},
    "gemini-2.5-flash-preview-05-20": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
    "gemini-2.5-pro-preview-05-06": {"input_per_1k": 0.00125, "output_per_1k": 0.01},
}

# Default model when no model is specified at construction or request time.
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


def resolve_model(name: str | None) -> str:
    """Resolve a short alias to the full Gemini model ID.

    Unknown names (including full IDs) pass through unchanged.
    None returns the provider default.
    """
    if not name:
        return GEMINI_MODEL_MAP.get(GEMINI_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL)
    return GEMINI_MODEL_MAP.get(name, name)


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a completion.

    Falls back to gemini-2.0-flash pricing for unknown model IDs.
    """
    pricing = GEMINI_PRICING.get(model_id, GEMINI_PRICING["gemini-2.0-flash"])
    return (input_tokens / 1000) * pricing["input_per_1k"] + (output_tokens / 1000) * pricing[
        "output_per_1k"
    ]
