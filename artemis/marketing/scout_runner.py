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
from artemis.marketing.models import ScoutRun, SignalQueue, TerritoryConfig
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.scout_sources import SCOUT_SOURCE_ADAPTERS
from artemis.marketing.scout_sources.base import ScoutSourceAdapter
from artemis.providers import get_adapter
from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

logger = logging.getLogger(__name__)
DEFAULT_CADENCE_SECONDS = 14400
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
    llm_adapter = None
    for candidate in filter(None, [agent.provider, agent.fallback_provider, "anthropic"]):
        try:
            llm_adapter = get_adapter(candidate)
            break
        except (MissingApiKeyError, UnknownProviderError, Exception):
            continue
    items_processed = signals_emitted = signals_rejected = 0
    cost_usd = 0.0
    errors: list[dict[str, Any]] = []
    status = "complete"
    for raw_item in raw_items:
        if cost_usd >= cost_cap_usd:
            status = "partial_complete"
            break
        items_processed += 1
        if llm_adapter is None:
            signals_rejected += 1
            continue
        prompt_parts = [f"Item:\n{raw_item.content}"]
        if raw_item.source_url:
            prompt_parts.append(f"URL: {raw_item.source_url}")
        if territory_config:
            prompt_parts.append(f"Hot states: {territory_config.get('hot_states', [])}")
        prompt_parts.append(
            "Return JSON: headline, sourceType, sourceUrl, campaignFamily, urgencyTier, "
            "reasonCodes, whyFlagged, evidence. sourceType in: "
            "manual|starbridge|news_article|board_minutes|state_doe|linkedin_post."
        )
        try:
            resp = await llm_adapter.complete(
                CompletionRequest(
                    messages=[
                        Message(role="user", content=[TextBlock(text="\n".join(prompt_parts))])
                    ],
                    system="\n\n".join(
                        part
                        for part in [
                            agent.system_prompt or "",
                            reason_code_system_suffix(agent.reason_codes_emitted),
                        ]
                        if part
                    ),
                    model=agent.model,
                    max_tokens=1024,
                )
            )
            payload = json.loads(
                "".join(b.text for b in resp.message.content if hasattr(b, "text"))
            )
            if resp.usage:
                cost_usd += resp.usage.input_tokens * 2.5e-7 + resp.usage.output_tokens * 1.25e-6
        except json.JSONDecodeError as exc:
            errors.append({"i": items_processed - 1, "error": f"json: {exc}"})
            signals_rejected += 1
            continue
        except Exception as exc:
            errors.append({"i": items_processed - 1, "error": str(exc)})
            signals_rejected += 1
            continue
        try:
            normalized = normalize_intake_payload(payload, scout_type=slug)
        except ValueError as exc:
            errors.append({"i": items_processed - 1, "error": f"normalize: {exc}"})
            signals_rejected += 1
            continue  # TODO: write to unresolved_signals when table exists
        session.add(
            SignalQueue(
                source_type=normalized.source_type,
                source_url=normalized.source_url,
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
        )
        await session.flush()
        signals_emitted += 1
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
