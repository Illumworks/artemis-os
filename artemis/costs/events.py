"""Cost event writer — the single insertion point for cost_events rows.

Usage at every LLM call site:
    try:
        await record_cost_event(session, provider=..., model=..., ...)
    except Exception:
        logger.warning("cost_event recording failed", exc_info=True)

The try/except must live at the CALL SITE so that a DB or pricing failure never
propagates to the user. This module does NOT wrap internally — the caller owns
the guard so it's explicit and visible.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.models import CostEvent
from artemis.costs.pricing import canonicalize_model, get_rates

logger = logging.getLogger(__name__)


async def record_cost_event(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    provider_path: str,  # 'cli' | 'api'
    feature_tag: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    source_kind: str | None = None,
    source_id: str | None = None,
    agent_id: int | None = None,
    session_id: str | None = None,
    workflow_run_id: int | None = None,
    duration_ms: int | None = None,
    is_error: bool = False,
    error_kind: str | None = None,
) -> CostEvent:
    """Compute cost from the pricing registry, write a cost_events row, return it.

    Cost formula (USD):
        cost = (
            input_tokens          * input_rate
          + output_tokens         * output_rate
          + cache_creation_tokens * cache_write_rate
          + cache_read_tokens     * cache_read_rate
        ) / 1_000_000

    Rates are looked up from artemis.costs.pricing.get_rates and snapshotted
    onto the row — they will not change retroactively even if pricing.py is
    updated later.

    Raises:
        Any exception from the DB write or pricing lookup — callers MUST wrap
        this function in try/except and log a WARNING. The LLM call result is
        what the user sees; a recording failure must be invisible to them.
    """
    # Canonicalize model name so by_model rollups don't split aliases
    # (e.g. "claude-haiku-4-5" and "claude-haiku-4-5-20251001" → same model).
    model = canonicalize_model(model)

    try:
        rates = get_rates(provider, model)
    except KeyError:
        logger.warning(
            "cost_event: unknown provider/model combo provider=%r model=%r; using zero rates",
            provider,
            model,
        )
        rates = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}

    cost_usd = (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_creation_input_tokens * rates["cache_write"]
        + cache_read_input_tokens * rates["cache_read"]
    ) / 1_000_000

    event = CostEvent(
        provider=provider,
        model=model,
        provider_path=provider_path,
        feature_tag=feature_tag,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        input_rate_per_million=rates["input"],
        output_rate_per_million=rates["output"],
        cache_write_rate_per_million=rates["cache_write"],
        cache_read_rate_per_million=rates["cache_read"],
        cost_usd=cost_usd,
        source_kind=source_kind,
        source_id=source_id,
        agent_id=agent_id,
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        duration_ms=duration_ms,
        is_error=is_error,
        error_kind=error_kind,
    )
    session.add(event)
    await session.flush()
    return event
