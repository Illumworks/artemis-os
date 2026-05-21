"""Automations registry routes (OP1) — /api/automations*.

Endpoints:
  GET    /api/automations       — list (latest_run embedded, single query)
  GET    /api/automations/      — no-slash compat alias
  POST   /api/automations/      — create
  GET    /api/automations/{id}  — detail (with latest run)
  PATCH  /api/automations/{id}  — update fields
  DELETE /api/automations/{id}  — soft delete (archive)
  POST   /api/automations/{id}/run          — manual trigger
  GET    /api/automations/{id}/runs         — run history
  POST   /api/automation-runs/{run_id}/cancel  — cancel in-flight run
  POST   /api/automation-runs/{run_id}/resume  — resume awaiting_approval run
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.automations import repository as repo
from artemis.automations.dispatch import dispatch_automation_run
from artemis.automations.models import Automation
from artemis.automations.scheduler import reregister_automation
from artemis.automations.schemas import (
    AutomationCreate,
    AutomationUpdate,
    RunRequest,
    automation_run_to_schema,
    automation_to_schema,
)
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

router = APIRouter(
    tags=["automations"],
    dependencies=[Depends(require_token)],
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_to_dict(run: Any) -> dict[str, Any]:
    return automation_run_to_schema(run).model_dump(by_alias=True)


def _auto_to_dict(auto: Automation, latest_run: Any | None = None) -> dict[str, Any]:
    return automation_to_schema(auto, latest_run).model_dump(by_alias=True)


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/api/automations")
@router.get("/api/automations/")
async def list_automations(
    status: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List automations with latest_run embedded (single LEFT JOIN query)."""
    rows = await repo.list_automations(
        session,
        status=status,
        owner_user_id=owner,
        limit=limit,
        cursor=cursor,
    )
    return [_auto_to_dict(auto, run) for auto, run in rows]


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/api/automations/", status_code=201)
async def create_automation(
    body: AutomationCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new automation definition."""
    auto = await repo.create_automation(
        session,
        name=body.name,
        description=body.description,
        status=body.status,
        trigger_type=body.trigger_type,
        schedule_config=body.schedule_config,
        target_type=body.target_type,
        target_id=body.target_id,
        model=body.model,
        provider=body.provider,
        fallback_provider=body.fallback_provider,
        fallback_model=body.fallback_model,
        approval_policy=body.approval_policy,
        output_config=body.output_config,
        metadata_=body.metadata,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    reregister_automation(auto)
    return _auto_to_dict(auto)


# ── Detail ────────────────────────────────────────────────────────────────────


@router.get("/api/automations/{automation_id}")
async def get_automation(
    automation_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single automation with its latest run embedded."""
    try:
        auto, run = await repo.get_automation_with_latest_run(session, automation_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904
    return _auto_to_dict(auto, run)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/api/automations/{automation_id}")
async def update_automation(
    automation_id: str,
    body: AutomationUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update mutable fields of an automation."""
    try:
        updates: dict[str, Any] = {}
        for field, _alias in [
            ("name", None),
            ("description", None),
            ("status", None),
            ("trigger_type", None),
            ("schedule_config", None),
            ("target_type", None),
            ("target_id", None),
            ("model", None),
            ("provider", None),
            ("fallback_provider", None),
            ("fallback_model", None),
            ("approval_policy", None),
            ("output_config", None),
            ("metadata", None),
        ]:
            val = getattr(body, field)
            if val is not None:
                updates[field] = val
        auto = await repo.update_automation(session, automation_id, **updates)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904
    await session.commit()
    reregister_automation(auto)
    return _auto_to_dict(auto)


# ── Soft delete ───────────────────────────────────────────────────────────────


@router.delete("/api/automations/{automation_id}", status_code=204)
async def delete_automation(
    automation_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Soft delete: set status=archived + archived_at. Row never removed from DB."""
    try:
        auto = await repo.archive_automation(session, automation_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904
    await session.commit()
    reregister_automation(auto)


# ── Manual trigger ────────────────────────────────────────────────────────────


@router.post("/api/automations/{automation_id}/run", status_code=202)
async def run_automation(
    automation_id: str,
    body: RunRequest | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Manually trigger an automation run.

    If approval_policy.required=true, creates an awaiting_approval run
    without dispatching. Otherwise dispatches immediately.
    """
    body = body or RunRequest()
    try:
        auto = await repo.get_automation(session, automation_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904

    if auto.status == "archived":
        raise bad_request("Cannot run an archived automation", "automation_archived")

    policy = auto.approval_policy or {}
    needs_approval = bool(policy.get("required", False))

    run = await repo.create_automation_run(
        session,
        automation_id=automation_id,
        status="awaiting_approval" if needs_approval else "queued",
        trigger="manual",
        triggered_by=body.triggered_by or "manual",
        metadata_=body.metadata,
    )
    await session.commit()

    if not needs_approval:
        # Fire-and-forget dispatch in background; don't block the HTTP response
        import asyncio

        asyncio.create_task(_dispatch_in_background(auto, run.id))

    return _run_to_dict(run)


async def _dispatch_in_background(auto: Automation, run_id: str) -> None:
    async with db.SessionLocal() as session:
        try:
            await dispatch_automation_run(session, auto, run_id)
            await session.commit()
        except Exception:
            logger.exception("Automation dispatch failed: automation=%s run=%s", auto.id, run_id)
            await session.rollback()
            try:
                await repo.update_automation_run(
                    session,
                    run_id,
                    status="failed",
                    completed_at=datetime.now(UTC),
                    error_message="Dispatch raised an exception; see server logs.",
                )
                await session.commit()
            except Exception:
                logger.exception("Failed to mark automation run failed: run=%s", run_id)


# ── Run history ───────────────────────────────────────────────────────────────


@router.get("/api/automations/{automation_id}/runs")
async def list_runs(
    automation_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return run history for an automation (cursor-paginated, newest first)."""
    try:
        await repo.get_automation(session, automation_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904
    runs = await repo.list_automation_runs(session, automation_id, limit=limit, cursor=cursor)
    return [_run_to_dict(r) for r in runs]


# ── Cancel ────────────────────────────────────────────────────────────────────


@router.post("/api/automation-runs/{run_id}/cancel", status_code=200)
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Cancel an in-flight or queued automation run."""
    try:
        run = await repo.get_automation_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_run_not_found")  # noqa: B904

    if run.status in ("succeeded", "failed", "cancelled"):
        raise bad_request(
            f"Cannot cancel a run with status '{run.status}'",
            "automation_run_already_terminal",
        )

    run = await repo.update_automation_run(
        session,
        run_id,
        status="cancelled",
        completed_at=datetime.now(UTC),
    )
    await session.commit()
    return _run_to_dict(run)


# ── Resume (approval callback) ────────────────────────────────────────────────


@router.post("/api/automation-runs/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Resume an awaiting_approval run. Called by the approval system on approve."""
    try:
        run = await repo.get_automation_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_run_not_found")  # noqa: B904

    if run.status != "awaiting_approval":
        raise bad_request(
            f"Run status is '{run.status}', not 'awaiting_approval'",
            "automation_run_not_awaiting",
        )

    try:
        auto = await repo.get_automation(session, run.automation_id)
    except ValueError as exc:
        raise not_found(str(exc), "automation_not_found")  # noqa: B904

    run = await repo.update_automation_run(session, run_id, status="queued")
    await session.commit()

    import asyncio

    asyncio.create_task(_dispatch_in_background(auto, run_id))

    return _run_to_dict(run)
