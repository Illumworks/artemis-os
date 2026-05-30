"""Memory-carryover helpers for approval events across the platform.

Every time an operator (or FA) approves something durable — a definition
proposal, a Gate-1 signal, a skill promotion, a pipeline human-gate decision,
or an FA-driven signal update — the approval leaves a durable, evidence-linked
memory observation so Floating Artemis can later answer "why did this happen?"
from memory alone.

Helpers in this module (all route through _multi_scope_observation_write):
  MC1  write_proposal_approval_observation   — definition-proposal approval
  MC2  write_signal_gate1_approval_observation — Gate-1 approve/reject
  MC3  write_skill_promotion_observation       — skill promoted to approved
  MC4  write_pipeline_gate_decision_observation — human-gate node decision
  MC5  write_fa_marketing_approval_observation  — FA approve on user's behalf

Multi-scope model (MW1):
  One observation row + N scope-join rows in memory_observation_scopes.
  Primary scope lives in both the legacy columns and the join table
  (is_primary=True). Secondary scopes only exist in the join table.

Failure isolation:
  Every public helper wraps ALL db work in try/except.  Memory writes are
  additive; the approval commit is the durable source of truth.

source_kind values used here that are NOT in EvidenceSourceKind Literal:
  definition_proposal, agent_run, signal_queue, skill,
  pipeline_run, floating_artemis_messages
  _link_evidence_raw bypasses the Literal type-check via raw pg_insert.
  (CC23 resolved the Literal extension; these are now in EvidenceSourceKind.)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

_ACTOR_LABEL = "Operator"  # single-user dev mode; future: pull from auth context
_SUMMARY_MAX_CHARS = 200
_PLATFORM_SCOPE_KIND = "workspace"
_PLATFORM_SCOPE_ID = "platform"


# ── Shared utilities ──────────────────────────────────────────────────────────


def _compose_iso_date() -> str:
    """Standard ISO timestamp for observation content."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _smart_truncate(text: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    """Truncate at a word boundary and append '…' if needed."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "…"


# CC28: _source_id_to_int removed — memory_evidence.source_id is now TEXT.
# Skill slugs, pipeline_run UUIDs, FA session IDs can be stored as-is.
# Legacy note: observations written before CC28 (obs ids 29–31, MC3/MC4/MC5 smokes)
# have SHA-256-hashed BigInt values in source_id (now stored as numeric strings).
# Per lossless invariant those rows are NOT modified; they're listed here for audit.
_LEGACY_HASHED_OBSERVATION_IDS: tuple[int, ...] = (29, 30, 31)  # MC3/MC4/MC5 smokes


async def _link_evidence_raw(
    session: Any,
    observation_id: int,
    source_kind: str,
    source_id: str,
) -> None:
    """Raw pg_insert for evidence linking — bypasses the EvidenceSourceKind Literal.

    CC23 banked: extend Literal to include source kinds used here
    (definition_proposal, agent_run, signal_queue, pipeline_run, skill,
    floating_artemis_messages). Until then, this helper writes directly to
    MemoryEvidence with pg_insert rather than through link_evidence (which
    type-checks source_kind against the Literal).  The Literal is a read-layer
    hint, not a DB constraint.

    CC28: source_id is now TEXT — numeric IDs pass as str(id), slugs/UUIDs pass
    as-is. No SHA-256 hashing. _source_id_to_int was removed.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.memory.models import MemoryEvidence

    stmt = (
        pg_insert(MemoryEvidence)
        .values(
            observation_id=observation_id,
            source_kind=source_kind,
            source_id=source_id,
            weight=1.0,
        )
        .on_conflict_do_nothing(constraint="uq_evidence_obs_source")
    )
    await session.execute(stmt)


async def _multi_scope_observation_write(
    *,
    primary_scope_kind: str,
    primary_scope_id: str,
    additional_scope_kinds: list[str],
    additional_scope_ids: list[str],
    content: str,
    category: str,
    confidence_origin: str,
    source_quality: float,
    wing: Literal["working", "durable"] = "durable",
) -> int:
    """Shared multi-scope write pattern. Returns the new observation_id.

    Opens a fresh SessionLocal session (per M1's pattern), ensures all scopes
    exist, calls write_observation with additional_scopes, commits.
    Failure is caller's responsibility to catch.
    """
    import artemis.db as _db
    from artemis.memory.schemas import Scope
    from artemis.memory.store import get_or_create_scope, write_observation

    primary_scope = Scope(scope_kind=primary_scope_kind, scope_id=primary_scope_id)
    additional_scopes = [
        Scope(scope_kind=k, scope_id=i)
        for k, i in zip(additional_scope_kinds, additional_scope_ids, strict=True)
    ]

    async with _db.SessionLocal() as session:
        await get_or_create_scope(session, primary_scope.scope_kind, primary_scope.scope_id)
        for s in additional_scopes:
            await get_or_create_scope(session, s.scope_kind, s.scope_id)

        obs = await write_observation(
            session,
            scope=primary_scope,
            additional_scopes=additional_scopes,
            content=content,
            category=category,
            source_quality=source_quality,
            confidence_origin=confidence_origin,
            wing=wing,
        )
        await session.commit()
        return obs.id


# ── MC1: definition-proposal approval ────────────────────────────────────────


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


def _compose_proposal_content(
    proposal_id: int,
    kind: str,
    target_slug: str,
    iso_date: str,
    citations: dict[str, Any] | None,
    proposed_by: str,
    proposed_definition: dict[str, Any],
) -> str:
    """Build the human-readable observation content string for MC1."""
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


async def write_proposal_approval_observation(
    *,
    proposal_id: int,
    kind: str,
    target_id: int | None,  # noqa: ARG001 — reserved
    target_slug: str,
    proposed_definition: dict[str, Any],
    proposed_by: str,
    citations: dict[str, Any] | None,
    builder_session_id: int | None = None,  # noqa: ARG001 — reserved for context
) -> None:
    """MC1: Write one approval observation with two scope-join rows for a definition proposal.

    MW1 pattern: ONE observation row + TWO scope-join rows (via additional_scopes).
    Previously wrote 2 separate observation rows (pre-MW1 workaround).

    Scopes:
        Primary   — agent:<target_slug> or skill:<target_slug>
        Secondary — workspace:platform  (audit trail)

    Called after engine.commit() succeeds in the approve route. Failure is fully
    isolated: any exception is caught, logged as WARNING, and swallowed.
    """
    import artemis.db as _db

    iso_date = _compose_iso_date()
    run_ids: list[Any] = []
    if citations and isinstance(citations, dict):
        raw = citations.get("run_ids", [])
        if isinstance(raw, list):
            run_ids = [r for r in raw if isinstance(r, (int, str))]

    primary_scope_kind = "agent" if kind == "agent" else "skill"
    content = _compose_proposal_content(
        proposal_id=proposal_id,
        kind=kind,
        target_slug=target_slug,
        iso_date=iso_date,
        citations=citations,
        proposed_by=proposed_by,
        proposed_definition=proposed_definition,
    )

    try:
        from artemis.memory.schemas import SourceQualityHint

        obs_id = await _multi_scope_observation_write(
            primary_scope_kind=primary_scope_kind,
            primary_scope_id=target_slug,
            additional_scope_kinds=[_PLATFORM_SCOPE_KIND],
            additional_scope_ids=[_PLATFORM_SCOPE_ID],
            content=content,
            category="definition_approval",
            confidence_origin="mc_definition_proposal",
            source_quality=SourceQualityHint.operator,
        )
        # Attach evidence in the same session opened by the commit in SessionLocal
        async with _db.SessionLocal() as session:
            await _link_evidence_raw(session, obs_id, "definition_proposal", str(proposal_id))
            for run_id in run_ids:
                await _link_evidence_raw(session, obs_id, "agent_run", str(run_id))
            await session.commit()
        logger.info(
            "MC1: approval observation written (id=%s) for proposal_id=%s kind=%s target=%s",
            obs_id,
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


# ── MC2: Signal Gate-1 approval ───────────────────────────────────────────────


async def write_signal_gate1_approval_observation(
    *,
    signal_id: int,
    new_status: str,
    decided_by: str,
    decision_payload: dict[str, Any] | None,
) -> None:
    """MC2: Write memory observation when Gate 1 approves/rejects a signal-brief.

    Multi-scope: workspace:marketing (primary) + workspace:platform (audit).
    Evidence: signal_queue source.
    Called after successful Gate-1 approve or reject transition.
    Failure is fully isolated.
    """
    import artemis.db as _db

    iso_date = _compose_iso_date()

    # Extract headline and reason codes from payload if present
    headline: str = ""
    reason_codes: list[str] = []
    if decision_payload and isinstance(decision_payload, dict):
        headline = str(decision_payload.get("headline", ""))
        raw_codes = decision_payload.get("reason_codes", [])
        if isinstance(raw_codes, list):
            reason_codes = [str(c) for c in raw_codes if c]

    decision_word = "approved" if "approved" in new_status else "rejected"
    content_parts = [f"{decided_by} {decision_word} signal #{signal_id} at Gate 1 on {iso_date}."]
    if headline:
        content_parts.append(f" Headline: {_smart_truncate(headline, 120)}.")
    if reason_codes:
        content_parts.append(f" Reason codes: {', '.join(reason_codes[:5])}.")
    content = "".join(content_parts)

    try:
        from artemis.memory.schemas import SourceQualityHint

        obs_id = await _multi_scope_observation_write(
            primary_scope_kind="workspace",
            primary_scope_id="marketing",
            additional_scope_kinds=[_PLATFORM_SCOPE_KIND],
            additional_scope_ids=[_PLATFORM_SCOPE_ID],
            content=content,
            category="signal_gate1_decision",
            confidence_origin="mc_signal_gate1",
            source_quality=SourceQualityHint.operator,
        )
        async with _db.SessionLocal() as session:
            await _link_evidence_raw(session, obs_id, "signal_queue", str(signal_id))
            await session.commit()
        logger.info(
            "MC2: Gate-1 observation written (id=%s) for signal_id=%s status=%s",
            obs_id,
            signal_id,
            new_status,
        )
    except Exception as exc:
        logger.warning(
            "MC2 memory observation write failed for signal_id=%s: %s",
            signal_id,
            exc,
            exc_info=True,
        )


# ── MC3: Skill promotion ──────────────────────────────────────────────────────


async def write_skill_promotion_observation(
    *,
    skill_slug: str,
    skill_name: str,
    description: str | None,
    promoted_by: str,
) -> None:
    """MC3: Skill promoted to approved status.

    Multi-scope: skill:<slug> (primary) + workspace:platform (audit).
    Evidence: skill source.
    Failure is fully isolated.
    """
    import artemis.db as _db

    iso_date = _compose_iso_date()
    desc_part = _smart_truncate(description, 200) if description else "no description"
    content = (
        f"{promoted_by} promoted skill '{skill_slug}' to approved status on {iso_date}. "
        f"Name: {skill_name}. "
        f"Description: {desc_part}."
    )

    try:
        from artemis.memory.schemas import SourceQualityHint

        obs_id = await _multi_scope_observation_write(
            primary_scope_kind="skill",
            primary_scope_id=skill_slug,
            additional_scope_kinds=[_PLATFORM_SCOPE_KIND],
            additional_scope_ids=[_PLATFORM_SCOPE_ID],
            content=content,
            category="skill_promotion",
            confidence_origin="mc_skill_promotion",
            source_quality=SourceQualityHint.operator,
        )
        async with _db.SessionLocal() as session:
            await _link_evidence_raw(session, obs_id, "skill", skill_slug)
            await session.commit()
        logger.info(
            "MC3: skill promotion observation written (id=%s) for skill=%s",
            obs_id,
            skill_slug,
        )
    except Exception as exc:
        logger.warning(
            "MC3 memory observation write failed for skill_slug=%s: %s",
            skill_slug,
            exc,
            exc_info=True,
        )


# ── MC4: Pipeline human-gate decision ────────────────────────────────────────


async def write_pipeline_gate_decision_observation(
    *,
    pipeline_run_id: str,
    pipeline_id: str,
    node_id: str,
    decision: str,
    decided_by: str,
    decision_payload: dict[str, Any] | None,
) -> None:
    """MC4: Pipeline human-gate decision (approved/rejected).

    Multi-scope: pipeline:<pipeline_id> (primary) + workspace:platform (audit).
    CC27: ScopeKind Literal now includes 'pipeline'; using it directly.
    Previously used workspace:pipeline-<id> as a workaround (pre-CC27 rows are
    preserved verbatim per lossless invariant).
    Evidence: pipeline_run source.
    Failure is fully isolated.
    """
    import artemis.db as _db

    iso_date = _compose_iso_date()
    payload_summary: str = "(no payload)"
    if decision_payload and isinstance(decision_payload, dict):
        pipeline_name = decision_payload.get("pipeline_name", "")
        if pipeline_name:
            payload_summary = _smart_truncate(str(pipeline_name), 100)
    content = (
        f"{decided_by} {decision} pipeline {pipeline_id} gate at node {node_id} on {iso_date}. "
        f"Context: {payload_summary}."
    )

    try:
        from artemis.memory.schemas import SourceQualityHint

        obs_id = await _multi_scope_observation_write(
            primary_scope_kind="pipeline",  # CC27: was workspace:pipeline-<id>
            primary_scope_id=pipeline_id,
            additional_scope_kinds=[_PLATFORM_SCOPE_KIND],
            additional_scope_ids=[_PLATFORM_SCOPE_ID],
            content=content,
            category="pipeline_gate_decision",
            confidence_origin="mc_pipeline_gate",
            source_quality=SourceQualityHint.operator,
        )
        async with _db.SessionLocal() as session:
            await _link_evidence_raw(session, obs_id, "pipeline_run", pipeline_run_id)
            await session.commit()
        logger.info(
            "MC4: pipeline gate observation written (id=%s) run=%s node=%s decision=%s",
            obs_id,
            pipeline_run_id,
            node_id,
            decision,
        )
    except Exception as exc:
        logger.warning(
            "MC4 memory observation write failed for pipeline_run_id=%s: %s",
            pipeline_run_id,
            exc,
            exc_info=True,
        )


# ── MC5: FA tool-driven signal approval ──────────────────────────────────────


async def write_fa_marketing_approval_observation(
    *,
    signal_id: int,
    new_status: str,
    fa_session_id: str,
    user_directive: str | None,
) -> None:
    """MC5: FA approved signal on user's behalf during chat.

    Multi-scope (3 scopes):
      agent:floating-artemis (primary — FA is the author)
      workspace:marketing    (target domain)
      workspace:platform     (audit)
    Evidence: signal_queue + floating_artemis_messages.
    Failure is fully isolated.
    """
    import artemis.db as _db

    iso_date = _compose_iso_date()
    directive_part = (
        _smart_truncate(user_directive, 200) if user_directive else "inferred from context"
    )
    decision_word = "approved" if "approved" in new_status else new_status
    content = (
        f"FA {decision_word} signal #{signal_id} on behalf of user "
        f"during chat session {fa_session_id} on {iso_date}. "
        f"User directive: {directive_part}."
    )

    try:
        from artemis.memory.schemas import SourceQualityHint

        obs_id = await _multi_scope_observation_write(
            primary_scope_kind="agent",
            primary_scope_id="floating-artemis",
            additional_scope_kinds=["workspace", _PLATFORM_SCOPE_KIND],
            additional_scope_ids=["marketing", _PLATFORM_SCOPE_ID],
            content=content,
            category="fa_marketing_approval",
            confidence_origin="mc_fa_marketing",
            source_quality=SourceQualityHint.operator,
        )
        async with _db.SessionLocal() as session:
            await _link_evidence_raw(session, obs_id, "signal_queue", str(signal_id))
            await _link_evidence_raw(session, obs_id, "floating_artemis_messages", fa_session_id)
            await session.commit()
        logger.info(
            "MC5: FA approval observation written (id=%s) for signal_id=%s session=%s",
            obs_id,
            signal_id,
            fa_session_id,
        )
    except Exception as exc:
        logger.warning(
            "MC5 memory observation write failed for signal_id=%s: %s",
            signal_id,
            exc,
            exc_info=True,
        )
