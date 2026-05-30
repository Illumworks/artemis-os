"""MC1 — Memory-carryover helpers for Builder approval events.

Every time an operator approves a DefinitionProposal the approval should leave
a durable, evidence-linked memory observation so Floating Artemis can later
answer "why did this agent's definition change?" from memory alone.

MC1 handles definition_proposal approvals.  MC2-MC5 will add sibling helpers
(write_signal_gate1_approval_observation, write_skill_promotion_observation,
write_pipeline_gate_decision_observation, write_fa_marketing_approval_observation)
to this same module so all carryover logic lives in one place.

Multi-scope model (pre-MW1):
    Until MW1 lands (multi-scope join table), MC1 writes TWO observation rows,
    one per scope:

        scope 1 — primary   e.g. agent:marketing.qualifier.brief_composer
        scope 2 — audit     workspace:platform

    After MW1 lands the intent is to write ONE observation row with TWO scope-join
    rows.  The refactor is a single-function change: replace the double
    write_proposal_approval_observation call with a single write that passes both
    scopes to MW1's write_multi_scope_observation primitive.

Failure isolation:
    Every public helper wraps ALL db work in try/except.  Memory writes are
    additive; the approval commit is the durable source of truth.

source_kind="definition_proposal":
    This value is NOT in the EvidenceSourceKind Literal (CC23 banked).  We use the
    same raw pg_insert escape hatch that M5 used for "signal_queue": write directly
    to MemoryEvidence with pg_insert rather than through link_evidence (which would
    type-check source_kind against the Literal).  This is intentional; the Literal
    is a read-layer hint, not a DB constraint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_ACTOR_LABEL = "Operator"  # single-user dev mode; future: pull from auth context
_SUMMARY_MAX_CHARS = 200
_PLATFORM_SCOPE_KIND = "workspace"
_PLATFORM_SCOPE_ID = "platform"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _smart_truncate(text: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    """Truncate at a word boundary and append '…' if needed."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "…"


def _extract_summary(proposed_definition: dict[str, Any]) -> str:
    """Extract a short summary from the proposed definition."""
    goal = proposed_definition.get("goal")
    if goal and isinstance(goal, str):
        return _smart_truncate(goal.strip())
    system_prompt = proposed_definition.get("system_prompt")
    if system_prompt and isinstance(system_prompt, str):
        first_line = system_prompt.strip().splitlines()[0]
        return _smart_truncate(first_line)
    return "(no summary)"


def _compose_content(
    proposal_id: int,
    kind: str,
    target_slug: str,
    iso_date: str,
    citations: dict[str, Any] | None,
    proposed_by: str,
    proposed_definition: dict[str, Any],
) -> str:
    """Build the human-readable observation content string (Part C)."""
    run_ids: list[Any] = []
    if citations and isinstance(citations, dict):
        raw = citations.get("run_ids", [])
        if isinstance(raw, list):
            run_ids = raw

    runs_part = ", ".join(str(r) for r in run_ids) if run_ids else "(none)"
    summary = _extract_summary(proposed_definition)

    return (
        f"{_ACTOR_LABEL} approved definition proposal #{proposal_id} "
        f"for {kind} {target_slug} on {iso_date}. "
        f"Citations: runs {runs_part}. Proposed by: {proposed_by}. "
        f"Summary: {summary}"
    )


async def _write_one_observation(
    *,
    session: Any,
    scope_kind: str,
    scope_id: str,
    content: str,
    proposal_id: int,
    run_ids: list[Any],
) -> None:
    """Write a single approval observation + evidence links into an existing session.

    Uses pg_insert directly for the evidence rows so we can write
    source_kind="definition_proposal" without being blocked by the
    EvidenceSourceKind Literal (CC23 banked).
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.memory.models import MemoryEvidence, MemoryObservation
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import _content_hash, _embed_and_store, _ensure_scope  # noqa: PLC2701

    scope = Scope(scope_kind=scope_kind, scope_id=scope_id)
    await _ensure_scope(session, scope)

    ch = _content_hash(scope_kind, scope_id, content)
    stmt = (
        pg_insert(MemoryObservation)
        .values(
            scope_kind=scope_kind,
            scope_id=scope_id,
            category="definition_approval",
            content=content,
            content_hash=ch,
            source_quality=SourceQualityHint.operator,
            confidence=1.0,
        )
        .on_conflict_do_nothing(constraint="uq_obs_scope_hash")
    )
    await session.execute(stmt)

    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(MemoryObservation).where(
            MemoryObservation.scope_kind == scope_kind,
            MemoryObservation.scope_id == scope_id,
            MemoryObservation.content_hash == ch,
        )
    )
    obs = result.scalar_one()

    # Best-effort embedding (mirrors write_observation pattern)
    await _embed_and_store(session, "observation", obs.id, content)

    # Evidence: definition_proposal row
    for src_kind, src_id in [
        ("definition_proposal", proposal_id),
        *[("agent_run", r) for r in run_ids],
    ]:
        ev_stmt = (
            pg_insert(MemoryEvidence)
            .values(
                observation_id=obs.id,
                source_kind=src_kind,
                source_id=int(src_id),
                weight=1.0,
            )
            .on_conflict_do_nothing(constraint="uq_evidence_obs_source")
        )
        await session.execute(ev_stmt)


# ── Public API ────────────────────────────────────────────────────────────────


async def write_proposal_approval_observation(
    *,
    proposal_id: int,
    kind: str,
    target_id: int | None,
    target_slug: str,
    proposed_definition: dict[str, Any],
    proposed_by: str,
    citations: dict[str, Any] | None,
    builder_session_id: int | None = None,  # noqa: ARG001 — reserved for MC2-MC5 context
) -> None:
    """Write two approval observations (pre-MW1: one per scope) for a definition proposal.

    Called after engine.commit() succeeds in the approve route.  Failure is fully
    isolated: any exception is caught, logged as WARNING, and swallowed.  The
    definition_proposals status flip is the durable source of truth.

    Scopes written:
        1. Primary — agent:<dotted_agent_id> or skill:<slug>
        2. Audit   — workspace:platform

    Post-MW1 refactor: replace the two _write_one_observation calls with a single
    call to MW1's write_multi_scope_observation(scopes=[primary, audit], ...).
    """
    import artemis.db as _db

    iso_date = datetime.now(UTC).isoformat(timespec="seconds")
    run_ids: list[Any] = []
    if citations and isinstance(citations, dict):
        raw = citations.get("run_ids", [])
        if isinstance(raw, list):
            run_ids = [r for r in raw if isinstance(r, (int, str))]

    # Determine primary scope
    if kind == "agent":
        primary_scope_kind = "agent"
        primary_scope_id = target_slug
    else:
        primary_scope_kind = "skill"
        primary_scope_id = target_slug

    content = _compose_content(
        proposal_id=proposal_id,
        kind=kind,
        target_slug=target_slug,
        iso_date=iso_date,
        citations=citations,
        proposed_by=proposed_by,
        proposed_definition=proposed_definition,
    )

    try:
        async with _db.SessionLocal() as mem_session:
            # Scope 1: primary (agent or skill)
            await _write_one_observation(
                session=mem_session,
                scope_kind=primary_scope_kind,
                scope_id=primary_scope_id,
                content=content,
                proposal_id=proposal_id,
                run_ids=run_ids,
            )
            # Scope 2: audit trail (workspace:platform)
            await _write_one_observation(
                session=mem_session,
                scope_kind=_PLATFORM_SCOPE_KIND,
                scope_id=_PLATFORM_SCOPE_ID,
                content=content,
                proposal_id=proposal_id,
                run_ids=run_ids,
            )
            await mem_session.commit()
        logger.info(
            "MC1: approval observations written for proposal_id=%s kind=%s target=%s",
            proposal_id,
            kind,
            target_slug,
        )
    except Exception as exc:
        logger.warning(
            "MC1 memory observation write failed for proposal_id=%s: %s",
            proposal_id,
            exc,
            exc_info=True,
        )
