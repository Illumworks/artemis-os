"""Scout runner — M5b. Single path for all 9 scouts.
TODO(memory_layer): upsert_last_seen after emit (function not yet implemented).
TODO(M3): transition(session, "signal", id, SignalState.qualified) post-qualify.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.builders.models import Agent
from artemis.connectors.resolver import ConnectorNotConfigured, get_credentials_for_tool
from artemis.costs.events import record_cost_event
from artemis.marketing.models import ScoutRun, SignalQueue, TerritoryConfig
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.scout_sources import SCOUT_SOURCE_ADAPTERS
from artemis.marketing.scout_sources.base import ScoutSourceAdapter
from artemis.providers.fallback import complete_with_fallback
from artemis.providers.gemini.adapter import _strip_wrapping_code_fence

logger = logging.getLogger(__name__)
# Daily. These sources (legislation, board minutes, RFPs, funding notices) update on the order of
# days–weeks, and runs were observed deduping to ~0 new signals at the old 4h cadence — 6×/day was
# pure throughput waste. Daily also matches the agents' own declared cadence_seconds (86400).
# TODO: respect each agent's blueprint cadence_seconds instead of one global default.
DEFAULT_CADENCE_SECONDS = 86400
DEFAULT_COST_CAP_USD = 1.00


async def get_source_credentials(
    session: AsyncSession,
    agent_db_id: int,
    tool_namespace: str,
) -> dict[str, str] | None:
    """Return connector credentials for a source adapter, or None if not linked.

    Source adapters should call this instead of reading env vars directly so
    credentials can be managed via the Connectors UI.
    """
    try:
        return await get_credentials_for_tool(session, agent_db_id, tool_namespace)
    except ConnectorNotConfigured:
        logger.debug(
            "No connector linked for agent %s / namespace %s — falling back to env",
            agent_db_id,
            tool_namespace,
        )
        return None


class ScoutMode(enum.StrEnum):
    scheduled = "scheduled"
    manual = "manual"
    backfill = "backfill"


@dataclass
class ScoutRunResult:
    agent_id: str
    run_id: str
    mode: ScoutMode
    status: str  # noqa: E702
    items_processed: int
    signals_emitted: int
    signals_rejected: int
    cost_usd: float  # noqa: E702
    errors: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None


def reason_code_system_suffix(reason_codes: Any) -> str:
    codes = [str(code).strip() for code in (reason_codes or []) if str(code).strip()]
    if codes:
        return (
            f"You may emit ONLY these reason codes: [{', '.join(codes)}].\n"
            "Any other code will be rejected by intake validation."
        )
    return "Any registered reason code is valid."


async def _call_llm(
    primary_provider: str,
    fallback_provider: str,
    user_parts: list[str],
    system_prompt: str,
    model: str,
) -> tuple[dict[str, Any] | None, float, str | None]:
    """Call the LLM with Gemini → claude-code fallback, parse JSON response.

    Returns (payload_dict, cost_delta, error_str).
    On success error_str is None; on failure payload_dict is None.
    Extracted as a module-level helper so it can be tested and so the
    per-item retry in run_scout doesn't redefine a closure each iteration.
    """
    try:
        serving: list[str] = []
        resp = await complete_with_fallback(
            CompletionRequest(
                messages=[Message(role="user", content=[TextBlock(text="\n".join(user_parts))])],
                system=system_prompt,
                model=model,
                max_tokens=1024,
            ),
            primary=primary_provider,
            fallback=fallback_provider,
            serving_provider_out=serving,
        )
        raw_text = "".join(b.text for b in resp.message.content if hasattr(b, "text"))
        payload: dict[str, Any] = json.loads(_strip_wrapping_code_fence(raw_text))
        delta = (
            (resp.usage.input_tokens * 2.5e-7 + resp.usage.output_tokens * 1.25e-6)
            if resp.usage
            else 0.0
        )
        # Record cost — never propagate failures.
        try:
            from artemis.db import SessionLocal

            _actual_provider = serving[0] if serving else primary_provider
            _path = "cli" if _actual_provider == "claude-code" else "api"
            async with SessionLocal() as _cost_session:
                await record_cost_event(
                    _cost_session,
                    provider=_actual_provider,
                    model=model,
                    provider_path=_path,
                    feature_tag="marketing_scout",
                    input_tokens=getattr(resp.usage, "input_tokens", 0) if resp.usage else 0,
                    output_tokens=getattr(resp.usage, "output_tokens", 0) if resp.usage else 0,
                    cache_creation_input_tokens=getattr(
                        resp.usage, "cache_creation_input_tokens", 0
                    )
                    if resp.usage
                    else 0,
                    cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0)
                    if resp.usage
                    else 0,
                )
                await _cost_session.commit()
        except Exception:
            logger.warning("cost_event recording failed in _call_llm (scout)", exc_info=True)
        return payload, delta, None
    except json.JSONDecodeError as exc:
        return None, 0.0, f"json: {exc}"
    except Exception as exc:
        return None, 0.0, str(exc)


async def run_scout(
    session: AsyncSession,
    agent_id: str,
    mode: ScoutMode = ScoutMode.scheduled,
    *,
    adapter_override: ScoutSourceAdapter | None = None,
    cost_cap_usd: float = DEFAULT_COST_CAP_USD,
) -> ScoutRunResult:
    """Run one scout end-to-end. Caller owns commit/rollback. Raises ValueError if not found."""
    started_at = datetime.now(UTC)
    slug = agent_id.split(".")[-1]
    run_id = f"agent_run_{started_at.strftime('%Y%m%d')}_{slug}_{str(uuid4())[:8]}"
    agent = (
        await session.execute(select(Agent).where(Agent.agent_id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise ValueError(f"Agent not found: {agent_id!r}. Run M5 seed.")
    locked = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": f"scout:{agent_id}"}
        )
    ).scalar_one()
    if not locked:
        return ScoutRunResult(
            agent_id=agent_id,
            run_id=run_id,
            mode=mode,
            status="skipped_locked",
            items_processed=0,
            signals_emitted=0,
            signals_rejected=0,
            cost_usd=0.0,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )
    source_adapter = adapter_override or SCOUT_SOURCE_ADAPTERS.get(slug)
    if source_adapter is None:
        raise ValueError(f"No source adapter for slug {slug!r}")
    tc = (await session.execute(select(TerritoryConfig).limit(1))).scalar_one_or_none()
    territory_config = (
        {"family": tc.family, "hot_states": tc.hot_states, "standard_states": tc.standard_states}
        if tc
        else None
    )
    last_row = (
        await session.execute(
            select(ScoutRun)
            .where(
                ScoutRun.scout_type == slug, ScoutRun.status.in_(["complete", "partial_complete"])
            )
            .order_by(ScoutRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    raw_items = source_adapter.fetch(territory_config, last_row.started_at if last_row else None)

    # Provider routing: use the agent's declared provider as primary and
    # fallback_provider as fallback.  complete_with_fallback handles Gemini 429s
    # by cascading to claude-code transparently at call time.
    _primary_provider = agent.provider or "claude-code"
    _fallback_provider = agent.fallback_provider or "claude-code"

    # H2: extract allowlist once per run (list[str] | None).
    # None means no restriction (e.g. empty JSONB list treated as unrestricted).
    _rc_emitted = agent.reason_codes_emitted or []
    allowlist: list[str] | None = [str(c) for c in _rc_emitted if str(c).strip()] or None

    items_processed = signals_emitted = signals_rejected = 0
    cost_usd = 0.0
    errors: list[dict[str, Any]] = []
    status = "complete"
    # H2 per-batch learning: carry last validation error into system prompt suffix
    # so the LLM self-corrects on the next item without prompt-level changes.
    _last_validation_error: str | None = None
    for raw_item in raw_items:
        if cost_usd >= cost_cap_usd:
            status = "partial_complete"
            break
        items_processed += 1
        prompt_parts = [f"Item:\n{raw_item.content}"]
        if raw_item.source_url:
            prompt_parts.append(f"URL: {raw_item.source_url}")
        if territory_config:
            prompt_parts.append(f"Hot states: {territory_config.get('hot_states', [])}")
        prompt_parts.append(
            "Return JSON: headline, sourceType, sourceUrl, campaignFamily, urgencyTier, "
            "reasonCodes, whyFlagged, evidence. sourceType in: "
            "manual|starbridge|news_article|board_minutes|state_doe|linkedin_post|legiscan."
        )
        # H2: append last validation error to teach the LLM on this item.
        if _last_validation_error:
            prompt_parts.append(
                f"VALIDATION ERROR from previous item (self-correct): {_last_validation_error}"
            )
        system_prompt = "\n\n".join(
            p
            for p in [
                agent.system_prompt or "",
                reason_code_system_suffix(agent.reason_codes_emitted),
            ]
            if p
        )

        payload, delta, call_err = await _call_llm(
            _primary_provider, _fallback_provider, prompt_parts, system_prompt, agent.model
        )
        cost_usd += delta
        if call_err or payload is None:
            err_str = call_err or "empty payload"
            errors.append({"i": items_processed - 1, "error": err_str})
            signals_rejected += 1
            continue

        # H2: validate + allowlist check.
        try:
            normalized = normalize_intake_payload(
                payload,
                scout_type=slug,
                reason_codes_allowlist=allowlist,
            )
            _last_validation_error = None  # clear on success
        except ValueError as exc:
            logger.warning(
                "Scout %r item %d validation failed — retrying once: %s",
                slug,
                items_processed - 1,
                exc,
            )
            # Per-item single retry: append the error and re-call (capped at 1).
            retry_parts = prompt_parts + [
                f"VALIDATION ERROR — your previous JSON was rejected: {exc}. Fix and re-emit."
            ]
            payload2, delta2, call_err2 = await _call_llm(
                _primary_provider, _fallback_provider, retry_parts, system_prompt, agent.model
            )
            cost_usd += delta2
            if call_err2 or payload2 is None:
                errors.append({"i": items_processed - 1, "error": f"retry_call: {call_err2}"})
                signals_rejected += 1
                _last_validation_error = str(exc)
                continue
            try:
                normalized = normalize_intake_payload(
                    payload2,
                    scout_type=slug,
                    reason_codes_allowlist=allowlist,
                )
                _last_validation_error = None
            except ValueError as exc2:
                errors.append({"i": items_processed - 1, "error": f"normalize_retry: {exc2}"})
                signals_rejected += 1
                _last_validation_error = str(exc2)
                continue  # TODO: write to unresolved_signals when table exists
        signal_row = SignalQueue(
            source_type=normalized.source_type,
            source_url=normalized.source_url,
            pipeline_run_id=payload.get("pipelineRunId") or payload.get("pipeline_run_id"),
            headline=normalized.headline,
            summary=normalized.verbatim_snippet or normalized.headline,
            campaign_family=normalized.campaign_family,
            urgency_tier=normalized.urgency_tier,
            discovered_by=normalized.discovered_by,
            district_id=normalized.district,
            state=normalized.state_code,
            reason_codes=normalized.reason_codes,
            signal_status="pending_qualification",
            provenance={
                "run_id": run_id,
                "agent_id": agent_id,
                "why_flagged": normalized.why_flagged,
            },
        )
        session.add(signal_row)
        await session.flush()
        signals_emitted += 1

        # Best-effort, non-fatal auto-qualification (mirrors intake route semantics).
        # Signal write always wins; qualification failure is logged and swallowed.
        try:
            from artemis.marketing.qualification import run_and_store_qualification

            await run_and_store_qualification(session, signal_row)
        except Exception:  # noqa: BLE001
            logger.warning(
                "scout_runner: qualification failed for signal id=%s (non-fatal)",
                signal_row.id,
            )
    ended_at = datetime.now(UTC)
    session.add(
        ScoutRun(
            id=run_id,
            scout_type=slug,
            status=status,
            created_signal_ids=[],
            errors=errors,
            started_at=started_at,
            completed_at=ended_at,
            dry_run_summary={
                "mode": str(mode),
                "items_processed": items_processed,
                "signals_emitted": signals_emitted,
                "signals_rejected": signals_rejected,
                "cost_usd": round(cost_usd, 6),
                "errors": errors,
            },
        )
    )
    await session.flush()
    return ScoutRunResult(
        agent_id=agent_id,
        run_id=run_id,
        mode=mode,
        status=status,
        items_processed=items_processed,
        signals_emitted=signals_emitted,
        signals_rejected=signals_rejected,
        cost_usd=round(cost_usd, 6),
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
    )
