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
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import Approval
from artemis.marketing.repository import decide_approval
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/approvals",
    tags=["approvals"],
    dependencies=[Depends(require_token)],
)

_VALID_DECISIONS = {"approved", "rejected"}


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
    return [_serialize(a) for a in result.scalars().all()]


@router.get("/{approval_id}")
async def get_approval_route(
    approval_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single approval."""
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise not_found("Approval not found", "approval_not_found")  # noqa: B904
    return _serialize(approval)


@router.post("/{approval_id}/decision")
async def decide(
    approval_id: int,
    body: dict[str, Any],
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
    raw_decision = body.get("status") or body.get("decision") or ""
    decision_map = {"approve": "approved", "reject": "rejected"}
    decision = decision_map.get(raw_decision, raw_decision)

    if decision not in _VALID_DECISIONS:
        raise bad_request(
            "status must be 'approved' or 'rejected' (or 'approve'/'reject')",
            "approval_invalid_decision",
        )

    decided_by = body.get("decidedBy") or body.get("decided_by") or "unknown"
    decision_payload = body.get("decisionPayload") or body.get("decision_payload")

    updated = await decide_approval(
        session,
        approval_id=approval_id,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
    )
    await session.commit()

    # OP1 approval-resume side effect: if this approval is for an automation_run
    # and it was just approved, dispatch the run in-process.
    if decision == "approved" and approval.kind == "automation_run":
        asyncio.create_task(_resume_automation_run(approval.subject_id))

    # MC2: memory carryover for signal-brief gate approvals via generic route.
    # signal_queue.py already fires carryover for /{signal_id}/approve; this
    # covers the same Gate-1 surface when reached via the generic approvals API.
    _mc2_signal_kinds = frozenset({"signal_brief", "signal_gate1", "gate1"})
    if approval.kind in _mc2_signal_kinds:
        from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

        # subject_id may be int or str depending on context
        try:
            signal_id_for_mc2 = int(approval.subject_id.split(":")[0])
        except Exception:
            signal_id_for_mc2 = None
        if signal_id_for_mc2 is not None:
            asyncio.create_task(
                write_signal_gate1_approval_observation(
                    signal_id=signal_id_for_mc2,
                    new_status=decision,
                    decided_by=decided_by,
                    decision_payload=decision_payload,
                )
            )

    return _serialize(updated)


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
