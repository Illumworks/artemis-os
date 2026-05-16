"""Signal Queue router — /api/signal-queue.

Endpoints:
  POST   /intake          — structured ingestion seam (scouts / operators)
  GET    /                — list signals (filtered, paginated)
  GET    /{id}            — single signal
  POST   /{id}/qualify    — on-demand re-qualification (stub until C3)
  POST   /{id}/approve    — Gate 1: promote to candidate
  POST   /{id}/reject     — reject and record reason
  POST   /{id}/snooze     — snooze for N days
  POST   /{id}/ask        — archive a signal
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import SignalQueue
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_signal,
    find_signal_by_dedupe_key,
    get_active_ruleset_version,
    get_signal,
    list_signals,
    update_signal,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/signal-queue",
    tags=["signal-queue"],
    dependencies=[Depends(require_token)],
)

_VALID_STATUSES = {"in_inbox", "approved", "rejected", "snoozed", "archived", "expired"}


# ── Intake ────────────────────────────────────────────────────────────────────


@router.post("/intake")
async def intake(
    body: dict[str, Any],
    response: Response,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Structured ingestion seam for scouts / operators.

    Mirrors Node's /api/signal-queue/intake endpoint including dry-run mode.
    Dry-run returns 200; successful creation returns 201.
    """
    dry_run: bool = bool(body.pop("dryRun", False))

    errors = _validate_intake(body)

    if dry_run:
        # Dry-run always returns 200 regardless of validation outcome
        if errors:
            return {
                "dryRun": True,
                "valid": False,
                "errors": errors,
                "wouldCreate": None,
                "duplicate": None,
            }
        dup = await find_signal_by_dedupe_key(
            session,
            source_url=body.get("sourceUrl") or body.get("source_url") or "",
            headline=body.get("headline") or "",
        )
        return {
            "dryRun": True,
            "valid": True,
            "wouldCreate": body,
            "duplicate": _serialize_signal(dup) if dup else None,
        }

    if errors:
        raise bad_request(errors[0])  # noqa: B904

    dup = await find_signal_by_dedupe_key(
        session,
        source_url=body.get("sourceUrl") or body.get("source_url") or "",
        headline=body.get("headline") or "",
    )
    if dup is not None:
        raise conflict(
            "duplicate_signal",
            code="duplicate_signal",
        )

    signal = await create_signal(session, **_normalize_intake(body))
    await session.commit()
    await session.refresh(signal)
    response.status_code = 201
    return {"signal": _serialize_signal(signal)}


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/")
async def list_queue(
    status: str | None = Query(default=None),
    campaign_family: str | None = Query(default=None, alias="campaignFamily"),
    urgency_tier: str | None = Query(default=None, alias="urgencyTier"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List signals with optional filters (cursor-paginated)."""
    safe_status = status if status in _VALID_STATUSES else None
    signals = await list_signals(
        session,
        status=safe_status,
        campaign_family=campaign_family,
        limit=limit,
        cursor=cursor,
    )
    return {
        "signals": [_serialize_signal(s) for s in signals],
        "total": len(signals),
    }


# ── Single signal ─────────────────────────────────────────────────────────────


@router.get("/{signal_id}")
async def get_signal_route(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Return a single signal by ID."""
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904
    return _serialize_signal(signal)


# ── Qualify (stub — C3 replaces with real logic) ──────────────────────────────


@router.post("/{signal_id}/qualify")
async def qualify_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """On-demand re-qualification stub.

    Returns a minimal stub shape. C3 will replace this with the deterministic
    scorer port from the Node app's signal-qualifier.js.
    """
    try:
        await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904
    now = datetime.now(UTC).isoformat()
    return {"qualifiedAt": now, "scores": []}


# ── Approve ───────────────────────────────────────────────────────────────────


@router.post("/{signal_id}/approve")
async def approve_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Gate 1: approve signal and promote to campaign candidate."""
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    if signal.signal_status != "in_inbox":
        raise conflict(
            "invalid_transition",
            code="invalid_transition",
        )

    # Resolve ruleset version tag (non-fatal if missing)
    ruleset_version_tag: str | None = None
    qual = signal.qualification_json
    if qual and isinstance(qual, dict):
        versions_used = qual.get("rulesetVersionsUsed", {})
        if isinstance(versions_used, dict):
            ruleset_version_tag = versions_used.get(signal.campaign_family)

    if not ruleset_version_tag:
        try:
            active = await get_active_ruleset_version(session, signal.campaign_family)
            if active:
                ruleset_version_tag = active.version_tag
        except Exception:
            pass

    # Build qualification summary for candidate metrics_json
    qualification_summary: dict[str, Any] | None = None
    if qual and isinstance(qual, dict):
        scores = qual.get("scores", [])
        primary = next(
            (s for s in scores if s.get("campaignFamily") == signal.campaign_family),
            None,
        )
        qualification_summary = {
            "adjustedScore": primary.get("adjustedScore") if primary else None,
            "recommendedFamilies": qual.get("recommendedFamilies", []),
            "qualifiedAt": qual.get("qualifiedAt"),
            "rulesetVersionsUsed": qual.get("rulesetVersionsUsed", {}),
        }

    candidate = await create_campaign_candidate_from_signal(
        session,
        signal_id=signal_id,
        ruleset_version_tag=ruleset_version_tag or "",
        qualification_summary=qualification_summary,
    )

    # Update signal to approved
    updated = await update_signal(
        session,
        signal_id,
        signal_status="approved",
    )
    await session.commit()
    await session.refresh(updated)
    await session.refresh(candidate)

    return {
        "signal": _serialize_signal(updated),
        "candidateId": candidate.id,
        "rulesetVersionAtQualification": ruleset_version_tag,
    }


# ── Reject ────────────────────────────────────────────────────────────────────


@router.post("/{signal_id}/reject")
async def reject_signal(
    signal_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Reject a signal."""
    body = body or {}
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    if signal.signal_status != "in_inbox":
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    reason = body.get("reason") or body.get("trainingNotes")
    updated = await update_signal(
        session,
        signal_id,
        signal_status="rejected",
        rejected_reason=reason,
    )
    await session.commit()
    await session.refresh(updated)
    return _serialize_signal(updated)


# ── Snooze ────────────────────────────────────────────────────────────────────


@router.post("/{signal_id}/snooze")
async def snooze_signal(
    signal_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Snooze a signal for N days (1–90)."""
    body = body or {}
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    if signal.signal_status != "in_inbox":
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    days_raw = body.get("days", 14)
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        raise bad_request("days must be an integer between 1 and 90")  # noqa: B904

    if days < 1 or days > 90:
        raise bad_request("days must be an integer between 1 and 90")  # noqa: B904

    snoozed_until = datetime.now(UTC) + timedelta(days=days)
    updated = await update_signal(
        session,
        signal_id,
        signal_status="snoozed",
        snoozed_until=snoozed_until,
    )
    await session.commit()
    await session.refresh(updated)
    return _serialize_signal(updated)


# ── Ask (archive) ─────────────────────────────────────────────────────────────


@router.post("/{signal_id}/ask")
async def ask_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Archive a signal (maps to the Node /ask endpoint which archives signals)."""
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    if signal.signal_status == "archived":
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    updated = await update_signal(session, signal_id, signal_status="archived")
    await session.commit()
    await session.refresh(updated)
    return _serialize_signal(updated)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _validate_intake(body: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    headline = body.get("headline") or ""
    family = body.get("campaignFamily") or body.get("campaign_family") or ""
    if not str(headline).strip():
        errors.append("headline is required")
    if not str(family).strip():
        errors.append("campaignFamily is required")
    return errors


def _normalize_intake(body: dict[str, Any]) -> dict[str, Any]:
    """Map camelCase intake payload to ORM snake_case kwargs."""
    return {
        "headline": body.get("headline", ""),
        "campaign_family": body.get("campaignFamily") or body.get("campaign_family", ""),
        "source_type": body.get("sourceType") or body.get("source_type", "manual"),
        "source_url": body.get("sourceUrl") or body.get("source_url"),
        "source_id": body.get("sourceId") or body.get("source_id"),
        "summary": body.get("summary", ""),
        "urgency_tier": body.get("urgencyTier") or body.get("urgency_tier", "standard"),
        "discovered_by": body.get("discoveredBy") or body.get("discovered_by", "manual"),
        "district_id": body.get("districtId") or body.get("district_id"),
        "state": body.get("state"),
        "reason_codes": body.get("reasonCodes") or body.get("reason_codes") or [],
        "provenance": body.get("provenance"),
        "owner_user_id": body.get("ownerUserId") or body.get("owner_user_id"),
    }


def _serialize_signal(signal: SignalQueue) -> dict[str, Any]:
    return {
        "id": signal.id,
        "sourceType": signal.source_type,
        "sourceUrl": signal.source_url,
        "sourceId": signal.source_id,
        "headline": signal.headline,
        "summary": signal.summary,
        "campaignFamily": signal.campaign_family,
        "urgencyTier": signal.urgency_tier,
        "discoveredBy": signal.discovered_by,
        "districtId": signal.district_id,
        "state": signal.state,
        "reasonCodes": signal.reason_codes or [],
        "provenance": signal.provenance,
        "qualificationJson": signal.qualification_json,
        "signalStatus": signal.signal_status,
        "snoozedUntil": signal.snoozed_until.isoformat() if signal.snoozed_until else None,
        "rejectedReason": signal.rejected_reason,
        "ownerUserId": signal.owner_user_id,
        "createdAt": signal.created_at.isoformat(),
        "updatedAt": signal.updated_at.isoformat(),
    }
