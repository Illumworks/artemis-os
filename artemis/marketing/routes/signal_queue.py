"""Signal Queue router — /api/signal-queue.

Endpoints:
  POST   /intake          — structured ingestion seam (scouts / operators)
  GET    /                — list signals (filtered, paginated)
  GET    /{id}            — single signal
  POST   /{id}/qualify    — on-demand re-qualification (C3 real implementation)
  POST   /{id}/approve    — Gate 1: promote to candidate
  POST   /{id}/reject     — reject and record reason
  POST   /{id}/snooze     — snooze for N days
  POST   /{id}/ask        — archive a signal
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import Ruleset, SignalQueue, SignalReasonCode, TerritoryConfig
from artemis.marketing.qualifier import (
    RulesetInput,
    SignalInput,
    TerritoryEntry,
    qualify_signal,
)
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_signal,
    find_signal_by_dedupe_key,
    get_active_ruleset_version,
    get_signal,
    list_signals,
    save_signal_qualification,
    update_signal,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.marketing.scout_intake import normalize_intake_payload

log = logging.getLogger(__name__)

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

    C3: intake now calls normalize_intake_payload for full validation, then
    attempts best-effort qualification (non-fatal — signal creation wins).
    """
    dry_run: bool = bool(body.pop("dryRun", False))

    # Determine scout_type for anti-spoof; default to sourceType or "manual"
    scout_type = (
        body.get("scoutType")
        or body.get("scout_type")
        or body.get("sourceType")
        or body.get("source_type")
        or "manual"
    )

    # Default sourceType to "manual" when absent (Node behavior: sourceType optional)
    if not body.get("sourceType") and not body.get("source_type"):
        body = {**body, "sourceType": "manual"}

    # Use scout_intake for full validation (strict mode)
    validation_error: str | None = None
    try:
        normalized = normalize_intake_payload(body, scout_type=scout_type)
    except ValueError as exc:
        validation_error = str(exc)

    # FK validation: reject unknown reason codes against the registry (active only).
    # Runs after normalization, on non-dry-run paths only (dry-run also checks).
    unknown_codes: list[str] = []
    if not validation_error and normalized is not None:
        codes_in_payload = [
            rc["code"] for rc in normalized.reason_codes if isinstance(rc.get("code"), str)
        ]
        if codes_in_payload:
            result = await session.execute(
                select(SignalReasonCode.code).where(
                    SignalReasonCode.code.in_(codes_in_payload),
                    SignalReasonCode.is_active.is_(True),
                )
            )
            active_codes = set(result.scalars().all())
            unknown_codes = [c for c in codes_in_payload if c not in active_codes]

    if dry_run:
        if validation_error:
            return {
                "dryRun": True,
                "valid": False,
                "errors": [validation_error],
                "wouldCreate": None,
                "duplicate": None,
            }
        if unknown_codes:
            return {
                "dryRun": True,
                "valid": False,
                "errors": [f"unknown reason codes: {unknown_codes}"],
                "wouldCreate": None,
                "duplicate": None,
            }
        assert normalized is not None  # mypy: validation_error is None → normalized set
        dup = await find_signal_by_dedupe_key(
            session,
            source_url=normalized.source_url or "",
            headline=normalized.headline or "",
        )
        return {
            "dryRun": True,
            "valid": True,
            "wouldCreate": body,
            "duplicate": _serialize_signal(dup) if dup else None,
        }

    if validation_error:
        raise bad_request(validation_error)  # noqa: B904

    if unknown_codes:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown reason codes", "codes": unknown_codes},
        )

    assert normalized is not None  # mypy: validation_error is None → normalized set
    dup = await find_signal_by_dedupe_key(
        session,
        source_url=normalized.source_url or "",
        headline=normalized.headline or "",
    )
    if dup is not None:
        raise conflict(
            "duplicate_signal",
            code="duplicate_signal",
        )

    signal = await create_signal(
        session,
        headline=normalized.headline,
        campaign_family=normalized.campaign_family,
        source_type=normalized.source_type,
        source_url=normalized.source_url,
        summary=normalized.why_flagged or "",
        urgency_tier=normalized.urgency_tier,
        discovered_by=normalized.discovered_by,
        state=normalized.state_code,
        district_id=normalized.district,
        reason_codes=normalized.reason_codes,
    )
    await session.commit()
    await session.refresh(signal)

    # Best-effort, non-fatal auto-qualification after intake
    try:
        await _run_and_store_qualification(session, signal)
        await session.commit()
        await session.refresh(signal)
    except Exception:  # noqa: BLE001
        log.warning("Auto-qualification failed for signal id=%s (non-fatal)", signal.id)

    response.status_code = 201
    return {"signal": _serialize_signal(signal)}


@router.post("")
async def intake_compat(
    body: dict[str, Any],
    response: Response,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Compat: frontend posts signal creation to /api/signal-queue."""
    return await intake(body, response, session)


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("")
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


# ── Qualify (C3 real implementation) ─────────────────────────────────────────


@router.post("/{signal_id}/qualify")
async def qualify_signal_route(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """On-demand re-qualification using the deterministic scorer.

    Loads all active rulesets + territory configs, runs qualify_signal(),
    and stores the result on the signal. 422 if no active rulesets exist.
    """
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    result = await _run_and_store_qualification(session, signal)
    if result is None:
        raise bad_request(
            "No active rulesets found — cannot qualify signal",
            "no_active_rulesets",
        )
    await session.commit()
    await session.refresh(signal)
    return result


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


# ── Archive ───────────────────────────────────────────────────────────────────


@router.post("/{signal_id}/archive")
async def archive_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Archive a signal."""
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


@router.post("/{signal_id}/ask")
async def ask_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Any:
    """Deprecated alias for /archive kept for one frontend release cycle."""
    return await archive_signal(signal_id, session)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _run_and_store_qualification(
    session: AsyncSession,
    signal: SignalQueue,
) -> dict[str, Any] | None:
    """Load active rulesets + territory configs, run qualify_signal(), store result.

    Returns the serialized qualification dict, or None if no active rulesets exist.
    Callers own commit/rollback. Raises on unexpected DB errors.
    """
    # Load all active rulesets
    result = await session.execute(select(Ruleset).where(Ruleset.state == "active"))
    active_rulesets_rows = list(result.scalars().all())

    if not active_rulesets_rows:
        return None

    # Build RulesetInput list
    ruleset_inputs = [
        RulesetInput(
            campaign_family=row.family,
            version_number=row.version_tag,
            min_fit_score=0.5,  # default; rulesets don't have a min_fit_score column yet
            hard_filters=row.hard_filters or [],
            weighted_signals=row.weighted_signals or [],
        )
        for row in active_rulesets_rows
    ]

    # Load territory configs for the families we're scoring
    families = [r.family for r in active_rulesets_rows]
    tc_result = await session.execute(
        select(TerritoryConfig).where(TerritoryConfig.family.in_(families))
    )
    territory_rows = list(tc_result.scalars().all())

    # Build territories_by_family using JSONB hot_states / standard_states arrays
    territories_by_family: dict[str, list[TerritoryEntry]] = {}
    for tc in territory_rows:
        entries: list[TerritoryEntry] = []
        for state in tc.hot_states or []:
            entries.append(TerritoryEntry(state_code=str(state).upper(), priority_tier="hot"))
        for state in tc.standard_states or []:
            entries.append(TerritoryEntry(state_code=str(state).upper(), priority_tier="standard"))
        territories_by_family[tc.family] = entries

    # Build SignalInput from ORM row
    signal_input = SignalInput(
        state_code=signal.state,
        reason_codes=signal.reason_codes or [],
        campaign_family=signal.campaign_family,
        urgency_tier=signal.urgency_tier,
    )

    qual = qualify_signal(signal_input, ruleset_inputs, territories_by_family)
    qual_dict = qual.to_dict()

    # Store on signal
    await save_signal_qualification(session, signal.id, qual_dict)
    return qual_dict


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
