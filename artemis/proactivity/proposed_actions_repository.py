"""Repository for ProposedAction CRUD and state transitions.

State machine: proposed → approved → executed
                       → rejected
                       → failed  (from approved, if executor errors)
              proposed → expired  (TTL enforcement, applied by scheduler)

Rules enforced here:
- Only rows in status='proposed' can be approved or rejected.
- Only rows in status='approved' can be executed or failed.
- Every transition appends an audit entry with actor + timestamp.
- A second 'yes' on a non-proposed row is a no-op (returns None to caller).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import ProposedAction

logger = logging.getLogger(__name__)

_DEFAULT_TTL_HOURS = 24


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _audit_entry(actor: str, action: str, note: str = "") -> dict[str, str]:
    return {
        "actor": actor,
        "action": action,
        "at": _now_utc().isoformat(),
        "note": note,
    }


async def create_proposed_action(
    session: AsyncSession,
    *,
    action_type: str,
    payload: dict[str, Any],
    preview: str,
    requested_by: str,
    target_user_id: str,
    ttl_hours: int = _DEFAULT_TTL_HOURS,
) -> ProposedAction:
    """Insert a new proposal in 'proposed' state."""
    now = _now_utc()
    expires_at = now + timedelta(hours=ttl_hours)
    action = ProposedAction(
        action_type=action_type,
        payload=payload,
        preview=preview,
        status="proposed",
        requested_by=requested_by,
        target_user_id=target_user_id,
        executed_result=None,
        audit=[_audit_entry(requested_by, "proposed", f"action_type={action_type}")],
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    session.add(action)
    await session.flush()
    await session.refresh(action)
    return action


async def get_proposed_action(
    session: AsyncSession,
    action_id: int,
) -> ProposedAction | None:
    """Fetch a single ProposedAction by PK."""
    return await session.get(ProposedAction, action_id)


async def list_pending_for_user(
    session: AsyncSession,
    *,
    target_user_id: str,
    now: datetime | None = None,
) -> list[ProposedAction]:
    """Return all non-expired 'proposed' rows for this user, oldest first."""
    current = now or _now_utc()
    result = await session.execute(
        select(ProposedAction)
        .where(
            ProposedAction.target_user_id == target_user_id,
            ProposedAction.status == "proposed",
            ProposedAction.expires_at > current,
        )
        .order_by(ProposedAction.created_at.asc())
    )
    return list(result.scalars().all())


async def approve_proposed_action(
    session: AsyncSession,
    *,
    action_id: int,
    actor: str,
    now: datetime | None = None,
) -> ProposedAction | None:
    """Transition proposed → approved.

    Returns the updated row, or None if the row doesn't exist or is not
    in 'proposed' state (caller should treat None as a no-op / stale reply).
    """
    current = now or _now_utc()
    row = await session.get(ProposedAction, action_id)
    if row is None or row.status != "proposed":
        return None

    audit_list: list[Any] = list(row.audit or [])
    audit_list.append(_audit_entry(actor, "approved"))

    await session.execute(
        update(ProposedAction)
        .where(ProposedAction.id == action_id, ProposedAction.status == "proposed")
        .values(status="approved", audit=audit_list, updated_at=current)
    )
    await session.refresh(row)
    return row


async def reject_proposed_action(
    session: AsyncSession,
    *,
    action_id: int,
    actor: str,
    now: datetime | None = None,
) -> ProposedAction | None:
    """Transition proposed → rejected."""
    current = now or _now_utc()
    row = await session.get(ProposedAction, action_id)
    if row is None or row.status != "proposed":
        return None

    audit_list: list[Any] = list(row.audit or [])
    audit_list.append(_audit_entry(actor, "rejected"))

    await session.execute(
        update(ProposedAction)
        .where(ProposedAction.id == action_id, ProposedAction.status == "proposed")
        .values(status="rejected", audit=audit_list, updated_at=current)
    )
    await session.refresh(row)
    return row


async def mark_executed(
    session: AsyncSession,
    *,
    action_id: int,
    result: dict[str, Any],
    actor: str,
    now: datetime | None = None,
) -> ProposedAction:
    """Transition approved → executed.  Only valid from 'approved' state.

    Raises ValueError if the row is not in 'approved' state — this is a
    programming error, not a user error; callers must gate on approved status.
    """
    current = now or _now_utc()
    row = await session.get(ProposedAction, action_id)
    if row is None:
        raise ValueError(f"ProposedAction id={action_id} not found")
    if row.status != "approved":
        raise ValueError(
            f"ProposedAction id={action_id} is in status={row.status!r}, "
            "can only execute from 'approved'"
        )

    audit_list: list[Any] = list(row.audit or [])
    audit_list.append(_audit_entry(actor, "executed", note=str(result)[:200]))

    await session.execute(
        update(ProposedAction)
        .where(ProposedAction.id == action_id, ProposedAction.status == "approved")
        .values(
            status="executed",
            executed_result=result,
            audit=audit_list,
            updated_at=current,
        )
    )
    await session.refresh(row)
    return row


async def mark_failed(
    session: AsyncSession,
    *,
    action_id: int,
    error: str,
    actor: str,
    now: datetime | None = None,
) -> ProposedAction:
    """Transition approved → failed."""
    current = now or _now_utc()
    row = await session.get(ProposedAction, action_id)
    if row is None:
        raise ValueError(f"ProposedAction id={action_id} not found")
    if row.status != "approved":
        raise ValueError(
            f"ProposedAction id={action_id} is in status={row.status!r}, "
            "can only fail from 'approved'"
        )

    audit_list: list[Any] = list(row.audit or [])
    audit_list.append(_audit_entry(actor, "failed", note=error[:500]))

    await session.execute(
        update(ProposedAction)
        .where(ProposedAction.id == action_id, ProposedAction.status == "approved")
        .values(
            status="failed",
            executed_result={"error": error},
            audit=audit_list,
            updated_at=current,
        )
    )
    await session.refresh(row)
    return row


async def expire_stale_proposals(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    actor: str = "scheduler",
) -> int:
    """Transition all proposed rows past expires_at to 'expired'.

    Returns count of rows expired.
    """
    current = now or _now_utc()
    result = await session.execute(
        select(ProposedAction).where(
            ProposedAction.status == "proposed",
            ProposedAction.expires_at <= current,
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        audit_list: list[Any] = list(row.audit or [])
        audit_list.append(_audit_entry(actor, "expired", note="TTL elapsed"))
        await session.execute(
            update(ProposedAction)
            .where(ProposedAction.id == row.id, ProposedAction.status == "proposed")
            .values(status="expired", audit=audit_list, updated_at=current)
        )
    return len(rows)
