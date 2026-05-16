"""Cost estimation helper for agent runs.

IMPORTANT: These are approximate rates only. Always verify current pricing at:
  https://www.anthropic.com/pricing

Sonnet 4.6 baseline: $3/M input, $15/M output
Opus multiplier: ~5x input, ~5x output
Haiku multiplier: ~0.27x input, ~0.27x output

This module is intentionally not wired to a live pricing API — running cost
estimates must be treated as ball-park figures for budgeting and alerting only.
"""

from __future__ import annotations

# Per-million-token rates (USD)
_RATES: dict[str, tuple[float, float]] = {
    # model_key: (input_rate_per_million, output_rate_per_million)
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-haiku-3-5": (0.80, 4.00),
    # Fallback patterns checked by prefix below
}

# Prefix → rates for unknown exact versions
_PREFIX_RATES: list[tuple[str, tuple[float, float]]] = [
    ("claude-opus", (15.00, 75.00)),
    ("claude-haiku", (0.80, 4.00)),
    ("claude-sonnet", (3.00, 15.00)),
]


def _get_rates(model: str) -> tuple[float, float]:
    """Return (input_rate, output_rate) per million tokens for *model*."""
    lower = model.lower()
    if lower in _RATES:
        return _RATES[lower]
    for prefix, rates in _PREFIX_RATES:
        if lower.startswith(prefix):
            return rates
    # Unknown model: fall back to Sonnet rates
    return _RATES["claude-sonnet-4-6"]


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    model: str = "claude-sonnet-4-6",
) -> float:
    """Return a rough USD cost estimate for one run.

    Rates are hard-coded approximations. Verify with Anthropic's pricing page
    before using for billing. Cache tokens are NOT accounted for here.

    Args:
        input_tokens:  Total input token count (uncached).
        output_tokens: Total output token count.
        model:         Model identifier string (partial matches accepted).

    Returns:
        Estimated cost in USD as a float.
    """
    in_rate, out_rate = _get_rates(model)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
