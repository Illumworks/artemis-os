"""OpenAI model alias map and pricing table.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Pricing is hard-coded here as a static table.  When prices change, update the
entries and refresh the source comment below.

Source: openai.com/api/pricing, sampled 2026-05-17.
"""

from __future__ import annotations

# Short alias -> canonical OpenAI model ID.
# Unknown names (including full model IDs) pass through unchanged.
OPENAI_MODEL_MAP: dict[str, str] = {
    "gpt-5": "gpt-5",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5-nano": "gpt-5-nano",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "o3": "o3",
    "o3-mini": "o3-mini",
    "o4-mini": "o4-mini",
}

# Pricing dict — input/output cost per 1k tokens in USD.
# TODO: reconcile exact figures at merge time; these were the best published
# prices available on the sample date above.
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-5": {"inputPer1k": 0.00125, "outputPer1k": 0.01},
    "gpt-5-mini": {"inputPer1k": 0.00025, "outputPer1k": 0.002},
    "gpt-5-nano": {"inputPer1k": 0.00005, "outputPer1k": 0.0004},
    "gpt-4o": {"inputPer1k": 0.0025, "outputPer1k": 0.01},
    "gpt-4o-mini": {"inputPer1k": 0.00015, "outputPer1k": 0.0006},
    # o-series (reasoning): higher output cost; reasoning tokens are charged at
    # the output rate.  No separate "reasoning token" line in this table — the
    # adapter adds them to output_tokens when present.
    "o3": {"inputPer1k": 0.002, "outputPer1k": 0.008},
    "o3-mini": {"inputPer1k": 0.0011, "outputPer1k": 0.0044},
    "o4-mini": {"inputPer1k": 0.0011, "outputPer1k": 0.0044},
}

# Default model — cheap and capable; override via default_model= or OPENAI_API_MODEL env var.
OPENAI_DEFAULT_MODEL = "gpt-5-mini"


def resolve_openai_model(name: str | None) -> str:
    """Resolve a short alias to the canonical OpenAI model ID.

    Unknown names pass through unchanged so that callers can supply full model
    IDs directly (e.g. ``"gpt-4o-2024-11-20"``).  None returns the default.
    """
    if not name:
        return OPENAI_DEFAULT_MODEL
    return OPENAI_MODEL_MAP.get(name, name)


def estimate_openai_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD for the given token counts.

    Falls back to gpt-4o-mini pricing for unknown model IDs — conservative and
    cheap, so estimates stay directionally correct without crashing.
    """
    pricing = OPENAI_PRICING.get(model_id, OPENAI_PRICING["gpt-4o-mini"])
    return (input_tokens / 1000) * pricing["inputPer1k"] + (output_tokens / 1000) * pricing[
        "outputPer1k"
    ]


def is_o_series(model_id: str) -> bool:
    """Return True for o-series reasoning models.

    o-series models use ``max_completion_tokens`` instead of ``max_tokens`` in
    the request body.  Detection is by model ID prefix ``"o"`` followed by a
    digit, matching ``o1``, ``o3``, ``o3-mini``, ``o4-mini``, etc.
    """
    import re

    return bool(re.match(r"^o\d", model_id))
