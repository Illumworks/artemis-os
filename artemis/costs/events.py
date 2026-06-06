"""Cost event writer — the single insertion point for cost_events rows.

Usage at every LLM call site:
    try:
        provider, model, path = adapter_identity(adapter)
        await record_cost_event(
            session, provider=provider, model=model, provider_path=path, ...
        )
    except Exception:
        logger.warning("cost_event recording failed", exc_info=True)

The try/except must live at the CALL SITE so that a DB or pricing failure never
propagates to the user. This module does NOT wrap internally — the caller owns
the guard so it's explicit and visible.

`adapter_identity(adapter)` is the canonical way to derive (provider, model,
provider_path) from a resolved adapter — replaces the legacy
isinstance(adapter, ClaudeCodeAdapter)-only check that misreported every
non-CC routing as "anthropic". Campaign-tied call sites use it now; the rest
of the call sites (FA chat, meetings, etc.) will migrate to it in a separate
cost-infra cleanup pass.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.models import CostEvent
from artemis.costs.pricing import canonicalize_model, get_rates

logger = logging.getLogger(__name__)


def adapter_identity(adapter: Any) -> tuple[str, str, str]:
    """Return (provider, model, provider_path) from a resolved adapter.

    The provider string matches what the pricing registry expects:
      claude-code | anthropic | openai | gemini | openrouter | lm-studio | codex

    provider_path is 'cli' only for CLI-class adapters (claude-code, codex);
    everything else is 'api'.

    The model is read from the adapter's own `_default_model` (most adapters)
    or `model` attribute, falling back to a literal "?" if neither is set.
    Unknown adapter types are reported as ('anthropic', '?', 'api') with a
    WARNING — same behavior as the legacy code, but at least it's audible.
    """
    cls_name = type(adapter).__name__
    model = getattr(adapter, "_default_model", None) or getattr(adapter, "model", None) or "?"

    if cls_name == "ClaudeCodeAdapter":
        return "claude-code", model, "cli"
    if cls_name == "CodexAdapter":
        return "codex", model, "cli"
    if cls_name == "LMStudioAdapter":
        return "lm-studio", model, "api"
    if cls_name == "GeminiAdapter":
        return "gemini", model, "api"
    if cls_name == "OpenAIAdapter":
        return "openai", model, "api"
    if cls_name == "OpenRouterAdapter":
        return "openrouter", model, "api"
    if cls_name == "AnthropicAdapter":
        return "anthropic", model, "api"

    logger.warning(
        "adapter_identity: unknown adapter class %r; defaulting to (anthropic/?/api)",
        cls_name,
    )
    return "anthropic", model, "api"


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
    campaign_candidate_id: int | None = None,
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
        campaign_candidate_id=campaign_candidate_id,
        duration_ms=duration_ms,
        is_error=is_error,
        error_kind=error_kind,
    )
    session.add(event)
    await session.flush()
    return event
