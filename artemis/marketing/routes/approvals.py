"""Approvals router — /api/approvals.

Endpoints:
  GET  /          — list approvals (filtered)
  GET  /{id}      — get single approval
  POST /{id}/decision — record approve/reject decision

The Python approvals schema is simpler than the Node's unified_approvals.
Fields the Python schema doesn't have (target_type, approval_kind, payload)
are not stored — the route returns null for those.

OP1 approval-resume side effect: when an approval with kind='automation_run'
is approved, and approval.subject_id matches an automation_run in
awaiting_approval status, the run is dispatched in-process (no HTTP self-call).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.marketing.models import Approval, CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import decide_approval
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.marketing.state_machine import (
    DeliverableState,
    WorkspaceState,
    transition,
)
from artemis.marketing.workspace import recompute_workspace_state_from_deliverables
from artemis.pipelines import repository as pipeline_repo

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/approvals",
    tags=["approvals"],
    dependencies=[Depends(require_token)],
)

_VALID_DECISIONS = {"approved", "rejected", "revision_requested"}
_PIPE4_GATE_KINDS = frozenset({"signal_brief", "content_draft"})
_MC2_SIGNAL_KINDS = frozenset({"signal_brief", "signal_gate1", "gate1"})
_CONTENT_DRAFT_STATUS_MAP = {
    "approved": DeliverableState.approved,
    "rejected": DeliverableState.rejected,
    "revision_requested": DeliverableState.revised,
}


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str | None = None
    status: str | None = None
    reason: str | None = None
    reviewer: str | None = None
    decided_by_camel: str | None = Field(default=None, alias="decidedBy")
    decided_by: str | None = None
    decision_payload_camel: dict[str, Any] | None = Field(default=None, alias="decisionPayload")
    decision_payload: dict[str, Any] | None = None


@router.get("")
@router.get("/")
async def list_approvals_route(
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List approvals with optional status / kind filter."""
    q = select(Approval)
    if status:
        q = q.where(Approval.status == status)
    if kind:
        q = q.where(Approval.kind == kind)
    q = q.order_by(Approval.created_at.desc()).limit(limit)
    result = await session.execute(q)
    rows = result.scalars().all()
    return [await _serialize_with_session(session, a) for a in rows]


@router.get("/{approval_id}")
async def get_approval_route(
    approval_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single approval."""
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise not_found("Approval not found", "approval_not_found")  # noqa: B904
    return await _serialize_with_session(session, approval)


@router.post("/{approval_id}/decision")
async def decide(
    approval_id: int,
    body: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Record an approve or reject decision on a pending approval.

    Body: { status: "approved"|"rejected", decidedBy, decisionPayload? }

    The Node app's /decision endpoint accepted "approve"/"reject" strings;
    the Python schema stores the resolved state "approved"/"rejected" directly.
    We accept both forms for compatibility.
    """
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise not_found("Approval not found", "approval_not_found")  # noqa: B904

    if approval.status != "pending":
        raise bad_request(
            f"Approval is already {approval.status}",
            "approval_not_pending",
        )

    # Accept both "approve" → "approved" and the direct form "approved"
    raw_decision = body.status or body.decision or ""
    decision_map = {
        "approve": "approved",
        "reject": "rejected",
        "request_revision": "revision_requested",
        "request_changes": "revision_requested",
    }
    decision = decision_map.get(raw_decision, raw_decision)

    if decision not in _VALID_DECISIONS:
        raise bad_request(
            "decision must be 'approved', 'rejected', or 'revision_requested'",
            "approval_invalid_decision",
        )
    if decision == "revision_requested" and approval.kind != "content_draft":
        raise bad_request(
            "revision_requested is only supported for content_draft approvals",
            "approval_invalid_decision",
        )

    decided_by = body.reviewer or body.decided_by_camel or body.decided_by or "operator"
    decision_payload = _merge_decision_payload(approval, body, decision, decided_by)

    approval, resume_result = await apply_approval_decision(
        session,
        approval=approval,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
    )

    # OP1 approval-resume side effect: if this approval is for an automation_run
    # and it was just approved, dispatch the run in-process.
    if decision == "approved" and approval.kind == "automation_run":
        asyncio.create_task(_resume_automation_run(approval.subject_id))

    # MC2: memory carryover for signal-brief gate approvals via generic route.
    # signal_queue.py already fires carryover for /{signal_id}/approve; this
    # covers the same Gate-1 surface when reached via the generic approvals API.
    if approval.kind in _MC2_SIGNAL_KINDS and not _is_pipe4_gate_approval(approval):
        from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

        # subject_id may be int or str depending on context
        try:
            signal_id_for_mc2 = int(approval.subject_id.split(":")[0])
        except Exception:
            signal_id_for_mc2 = None
        if signal_id_for_mc2 is not None:
            # Capture reason from decision_payload defensively (may be any type)
            _mc2_reason: str | None = None
            if isinstance(decision_payload, dict):
                raw_reason = decision_payload.get("reason")
                if isinstance(raw_reason, str) and raw_reason:
                    _mc2_reason = raw_reason
            if settings.env != "test":
                asyncio.create_task(
                    write_signal_gate1_approval_observation(
                        signal_id=signal_id_for_mc2,
                        new_status=decision,
                        decided_by=decided_by,
                        decision_payload=decision_payload,
                        rejection_reason=_mc2_reason if decision == "rejected" else None,
                        agent_slug="marketing.qualifier.cross_reference",
                    )
                )

    payload = await _serialize_with_session(session, approval)
    if resume_result is not None:
        payload["resume"] = resume_result
    return payload


async def apply_approval_decision(
    session: AsyncSession,
    *,
    approval: Approval,
    decision: str,
    decided_by: str,
    decision_payload: dict[str, Any],
) -> tuple[Approval, dict[str, Any] | None]:
    """Apply one approval decision, including PIPE4 gate side effects when relevant."""
    if approval.status != "pending":
        raise bad_request(
            f"Approval is already {approval.status}",
            "approval_not_pending",
        )

    if _is_pipe4_gate_approval(approval):
        return await _decide_pipe4_gate_approval(
            session,
            approval=approval,
            decision=decision,
            decided_by=decided_by,
            decision_payload=decision_payload,
            dispatch_mode="background",
        )

    updated = await decide_approval(
        session,
        approval_id=approval.id,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
    )
    await session.commit()
    return updated, None


async def find_pending_pipe4_approval(
    session: AsyncSession,
    *,
    subject_id: str,
) -> Approval | None:
    """Return the newest pending PIPE4 approval row for ``run_id:node_id`` if present."""
    result = await session.execute(
        select(Approval)
        .where(
            Approval.subject_id == subject_id,
            Approval.status == "pending",
            Approval.kind.in_(tuple(_PIPE4_GATE_KINDS)),
        )
        .order_by(Approval.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _resume_automation_run(run_id: str) -> None:
    """In-process callback: dispatch an automation_run that was awaiting approval."""
    import artemis.db as _db
    from artemis.automations import repository as auto_repo
    from artemis.automations.dispatch import dispatch_automation_run

    async with _db.SessionLocal() as session:
        try:
            run = await auto_repo.get_automation_run(session, run_id)
            if run.status != "awaiting_approval":
                return
            auto = await auto_repo.get_automation(session, run.automation_id)
            await auto_repo.update_automation_run(session, run_id, status="queued")
            await session.flush()
            await dispatch_automation_run(session, auto, run_id)
            await session.commit()
        except Exception:
            logger.exception("approval-resume: dispatch failed for automation_run=%s", run_id)
            await session.rollback()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "kind": a.kind,
        "subjectId": a.subject_id,
        "status": a.status,
        "decidedBy": a.decided_by,
        "decidedAt": a.decided_at.isoformat() if a.decided_at else None,
        "decisionPayload": a.decision_payload,
        # PIPE4 gate rendering context (null for non-PIPE4 approvals)
        "pipe4Context": a.pipe4_context,
        # Fields the Python schema doesn't have (Node compat — return null)
        "targetType": None,
        "approvalKind": None,
        "payload": None,
        "createdAt": a.created_at.isoformat(),
    }


async def _serialize_with_session(session: AsyncSession, a: Approval) -> dict[str, Any]:
    payload = _serialize(a)
    payload["pipe4Context"] = await _hydrate_pipe4_context(session, a)
    return payload


def _merge_decision_payload(
    approval: Approval,
    body: ApprovalDecisionRequest,
    decision: str,
    decided_by: str,
) -> dict[str, Any]:
    payload = (
        dict(approval.decision_payload or {}) if isinstance(approval.decision_payload, dict) else {}
    )
    extra_payload = body.decision_payload_camel or body.decision_payload or {}
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    if body.reason:
        payload["reason"] = body.reason
    payload["decision"] = decision
    payload["decided_by"] = decided_by
    payload["decided_at"] = datetime.now(UTC).isoformat()
    return payload


async def _hydrate_pipe4_context(
    session: AsyncSession, approval: Approval
) -> dict[str, Any] | None:
    if not isinstance(approval.pipe4_context, dict):
        return approval.pipe4_context

    pipe4_context = dict(approval.pipe4_context)
    run_id = pipe4_context.get("pipeline_run_id")
    if not run_id and isinstance(approval.subject_id, str) and ":" in approval.subject_id:
        run_id = approval.subject_id.split(":", 1)[0]
        pipe4_context["pipeline_run_id"] = run_id
    if not run_id:
        return pipe4_context

    from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

    try:
        run = await pipeline_repo.get_pipeline_run(session, str(run_id))
    except ValueError:
        return pipe4_context

    pipe4_context["context"] = await _build_pipe4_context(
        approval.kind,
        dict(run.node_states or {}),
        session=session,
        run_id=run.id,
    )
    return pipe4_context


async def _decide_content_draft_approval(
    session: AsyncSession,
    *,
    approval: Approval,
    decision: str,
    decided_by: str,
    decision_payload: dict[str, Any],
) -> dict[str, Any]:
    _, resume = await _decide_pipe4_gate_approval(
        session,
        approval=approval,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
        dispatch_mode="background",
    )
    assert resume is not None
    return resume


async def _decide_pipe4_gate_approval(
    session: AsyncSession,
    *,
    approval: Approval,
    decision: str,
    decided_by: str,
    decision_payload: dict[str, Any],
    dispatch_mode: str,
) -> tuple[Approval, dict[str, Any]]:
    run_id, node_id = _parse_gate_subject_id(approval.subject_id)
    if run_id is None or node_id is None:
        raise bad_request(
            f"{approval.kind} approval is missing a PIPE4 gate subject_id",
            "approval_missing_gate_subject",
        )

    try:
        run = await pipeline_repo.get_pipeline_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_run_not_found") from exc

    sends_info: list[dict[str, Any]] = []
    if approval.kind == "content_draft":
        if run.target_candidate_id is None:
            raise bad_request(
                "content_draft approval is not attached to a campaign candidate",
                "approval_missing_target_candidate",
            )

        deliverables = await _load_candidate_deliverables(session, run.target_candidate_id)
        target_deliverables = [d for d in deliverables if d.status == DeliverableState.draft_ready]
        if not target_deliverables:
            raise bad_request(
                "No draft_ready deliverables are available for this Gate-2 decision",
                "approval_no_reviewable_deliverables",
            )

        target_state = _CONTENT_DRAFT_STATUS_MAP[decision]
        for deliverable in target_deliverables:
            await transition(
                session,
                "deliverable",
                deliverable.id,
                target_state,
                actor=decided_by,
                reason=f"content_draft_{decision}",
            )

    approval = await decide_approval(
        session,
        approval_id=approval.id,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
    )

    # Gate-1 (signal_brief) approval: promote this run's qualified signals to a
    # campaign candidate so that content_brief_assembler finds exactly one
    # uninitiated candidate for this pipeline_run_id.  This is the same side
    # effect the manual POST /api/signal-queue/{id}/approve path runs; by going
    # through promote_qualified_signals_for_run both paths share one code path
    # and cannot drift (the PIPE-1 class of bug).
    if approval.kind == "signal_brief" and decision == "approved":
        from artemis.marketing.repository import promote_qualified_signals_for_run

        await promote_qualified_signals_for_run(session, run_id)

    candidate: CampaignCandidate | None = None
    if approval.kind == "content_draft" and run.target_candidate_id is not None:
        candidate = await session.get(CampaignCandidate, run.target_candidate_id)
        if candidate is not None:
            if decision == "revision_requested":
                await transition(
                    session,
                    "workspace",
                    candidate.id,
                    WorkspaceState.revision_needed,
                    actor=decided_by,
                    reason="content_draft_revision_requested",
                )
            else:
                await recompute_workspace_state_from_deliverables(
                    session,
                    candidate.id,
                    actor=decided_by,
                    reason="content_draft_decision",
                )

        # SEND2-B: enqueue send rows for each deliverable transitioned to 'approved'.
        # Guarded behind the outbound-send feature flag so Gate-2 can remain an
        # internal review-only workflow until Artemis is ready to expose sends.
        if settings.outbound_send_enabled and decision == "approved" and candidate is not None:
            from artemis.marketing.sends import enqueue_send_for_deliverable

            fresh_candidate = await session.get(CampaignCandidate, candidate.id)
            fresh_deliverables = await _load_candidate_deliverables(session, candidate.id)
            if fresh_candidate is not None:
                for deliverable in fresh_deliverables:
                    if deliverable.status == DeliverableState.approved.value:
                        send = await enqueue_send_for_deliverable(
                            session,
                            candidate=fresh_candidate,
                            deliverable=deliverable,
                            actor=decided_by,
                        )
                        sends_info.append(
                            {
                                "send_id": send.id,
                                "status": send.status,
                                "recipient_count": len(send.recipients)
                                if isinstance(send.recipients, list)
                                else 0,
                                "skip_reason": send.skip_reason,
                            }
                        )

    # Extract rejection reason from decision_payload defensively (payload is dict[str, Any])
    _content_rejection_reason: str | None = None
    if isinstance(decision_payload, dict):
        raw_reason = decision_payload.get("reason")
        if isinstance(raw_reason, str) and raw_reason:
            _content_rejection_reason = raw_reason

    pipeline_decision = (
        decision
        if approval.kind != "content_draft"
        else ("approved" if decision == "approved" else "rejected")
    )
    post_commit_status = await _resume_pipe4_gate_run(
        session,
        run=run,
        approval_kind=approval.kind,
        run_id=run_id,
        node_id=node_id,
        decision=pipeline_decision,
        decided_by=decided_by,
        rejection_reason=_content_rejection_reason,
        dispatch_mode=dispatch_mode,
    )

    return approval, {
        "runId": run_id,
        "nodeId": node_id,
        "pipelineDecision": pipeline_decision,
        "resumed": True,
        "runStatus": post_commit_status,
        "sends": sends_info,
    }


async def _resume_pipe4_gate_run(
    session: AsyncSession,
    *,
    run: Any,
    approval_kind: str,
    run_id: str,
    node_id: str,
    decision: str,
    decided_by: str,
    rejection_reason: str | None,
    dispatch_mode: str,
) -> str:
    """Commit one PIPE4 gate decision, continue execution, and enqueue MC4."""
    from artemis.pipelines.routes import _dispatch_execution, _prepare_pipeline_resume

    agent_slug = await _resolve_pipe4_agent_slug(
        session,
        pipeline_id=run.pipeline_id,
        node_id=node_id,
        approval_kind=approval_kind,
    )
    run, _ = await _prepare_pipeline_resume(
        session,
        run_id,
        node_id=node_id,
        decision=decision,
        actor=decided_by,
        reason=rejection_reason,
    )
    await session.commit()
    _cancel_gate_timeout(run_id, node_id)

    if dispatch_mode == "inline":
        import artemis.db as _db
        from artemis.pipelines.executor import PipelineExecutor

        async with _db.SessionLocal() as resume_session:
            executor = PipelineExecutor(run_id)
            await executor.run(resume_session)
            await resume_session.commit()
            refreshed_run = await pipeline_repo.get_pipeline_run(resume_session, run_id)
            post_commit_status = refreshed_run.status
    else:
        _dispatch_execution(run_id)
        post_commit_status = run.status

    if settings.env != "test":
        asyncio.create_task(
            _fire_mc4_pipe4_decision(
                run=run,
                node_id=node_id,
                decision=decision,
                decided_by=decided_by,
                rejection_reason=rejection_reason,
                agent_slug=agent_slug,
            )
        )
    return post_commit_status


async def _resolve_pipe4_agent_slug(
    session: AsyncSession,
    *,
    pipeline_id: str,
    node_id: str,
    approval_kind: str,
) -> str | None:
    from artemis.pipelines.routes import _resolve_upstream_agent_slug

    try:
        pipeline = await pipeline_repo.get_pipeline(session, pipeline_id)
        return _resolve_upstream_agent_slug(node_id, pipeline.nodes or [], pipeline.edges or [])
    except Exception:
        if approval_kind == "content_draft":
            return "marketing.content.writing_studio_adapter"
        return None


async def _fire_mc4_pipe4_decision(
    *,
    run: Any,
    node_id: str,
    decision: str,
    decided_by: str,
    rejection_reason: str | None,
    agent_slug: str | None,
) -> None:
    """Fire-and-forget MC4 observation for any PIPE4 gate decision."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id=run.id,
        pipeline_id=run.pipeline_id,
        node_id=node_id,
        decision=decision,
        decided_by=decided_by,
        decision_payload={"pipeline_name": run.pipeline_id},
        rejection_reason=rejection_reason,
        agent_slug=agent_slug,
    )


async def _load_candidate_deliverables(
    session: AsyncSession,
    candidate_id: int,
) -> list[CampaignDeliverable]:
    result = await session.execute(
        select(CampaignDeliverable)
        .where(CampaignDeliverable.candidate_id == candidate_id)
        .order_by(CampaignDeliverable.id)
    )
    return list(result.scalars().all())


def _parse_gate_subject_id(subject_id: str) -> tuple[str | None, str | None]:
    if ":" not in subject_id:
        return None, None
    run_id, node_id = subject_id.split(":", 1)
    return run_id or None, node_id or None


def _is_pipe4_gate_approval(approval: Approval) -> bool:
    run_id, node_id = _parse_gate_subject_id(approval.subject_id)
    return approval.kind in _PIPE4_GATE_KINDS and run_id is not None and node_id is not None


def _cancel_gate_timeout(run_id: str, node_id: str) -> None:
    try:
        import contextlib

        from artemis.pipelines.scheduler import get_pipeline_scheduler

        scheduler = get_pipeline_scheduler()
        if scheduler.running:
            with contextlib.suppress(Exception):
                scheduler.remove_job(f"gate_timeout_{run_id}_{node_id}")
    except Exception:
        logger.warning("Could not cancel timeout job for run %s gate %s", run_id, node_id)
