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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import (
    Approval,
    Ruleset,
    SignalQueue,
    SignalReasonCode,
    TerritoryConfig,
)
from artemis.marketing.qualifier import (
    RulesetInput,
    SignalInput,
    TerritoryEntry,
    annotate_district_tier,
    qualify_signal,
)
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_signal,
    find_signal_by_dedupe_key,
    get_active_ruleset_version,
    get_district,
    get_signal,
    list_signals,
    save_signal_qualification,
    update_signal,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.state_machine import SignalState, transition
from artemis.pipelines.models import Pipeline, PipelineRun

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/signal-queue",
    tags=["signal-queue"],
    dependencies=[Depends(require_token)],
)

_VALID_STATUSES = {s.value for s in SignalState}
_RELATED_SIGNAL_STATUSES = {
    SignalState.pending_qualification.value,
    SignalState.qualified.value,
    SignalState.APPROVED.value,
}


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
        pipeline_run_id=body.get("pipelineRunId") or body.get("pipeline_run_id"),
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
    contexts = await _load_signal_contexts(session, signals)
    return {
        "signals": [_serialize_signal(s, contexts.get(s.id)) for s in signals],
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
    contexts = await _load_signal_contexts(session, [signal])
    return _serialize_signal(signal, contexts.get(signal.id))


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
    # Advance signal from pending_qualification → qualified if not already there
    if signal.signal_status == SignalState.pending_qualification:
        await transition(session, "signal", signal.id, SignalState.qualified)
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

    if signal.signal_status != SignalState.qualified:
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

    candidate = await cluster_or_create_candidate(session, signal)
    if candidate.source_signal_id == signal_id:
        candidate.ruleset_version_at_qualification = ruleset_version_tag or ""
        candidate.metrics_json = qualification_summary
        await session.flush()

    # Update signal to approved via state machine
    updated = await transition(session, "signal", signal_id, SignalState.APPROVED)
    await session.commit()
    await session.refresh(updated)
    await session.refresh(candidate)

    # MC2: fire-and-forget memory carryover (failure must not break approval)
    import asyncio as _asyncio

    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    _asyncio.create_task(
        write_signal_gate1_approval_observation(
            signal_id=signal_id,
            new_status=updated.signal_status,
            decided_by="operator",
            decision_payload={"headline": updated.headline},
        )
    )

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

    if signal.signal_status != SignalState.qualified:
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    reason = body.get("reason") or body.get("trainingNotes")
    if reason is not None:
        await update_signal(session, signal_id, rejected_reason=reason)
    updated = await transition(session, "signal", signal_id, SignalState.REJECTED_AT_GATE_1)
    await session.commit()
    await session.refresh(updated)

    # MC2: fire-and-forget memory carryover (failure must not break rejection)
    import asyncio as _asyncio

    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    _asyncio.create_task(
        write_signal_gate1_approval_observation(
            signal_id=signal_id,
            new_status=updated.signal_status,
            decided_by="operator",
            decision_payload={"headline": updated.headline},
        )
    )

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

    if signal.signal_status != SignalState.qualified:
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    days_raw = body.get("days", 14)
    try:
        days = int(days_raw)
    except (TypeError, ValueError):
        raise bad_request("days must be an integer between 1 and 90")  # noqa: B904

    if days < 1 or days > 90:
        raise bad_request("days must be an integer between 1 and 90")  # noqa: B904

    snoozed_until = datetime.now(UTC) + timedelta(days=days)
    await update_signal(session, signal_id, snoozed_until=snoozed_until)
    updated = await transition(session, "signal", signal_id, SignalState.SNOOZED)
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

    if signal.signal_status == SignalState.ARCHIVED:
        raise conflict("invalid_transition", code="invalid_transition")  # noqa: B904

    updated = await transition(session, "signal", signal_id, SignalState.ARCHIVED)
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

    # DIST4: annotate district tier soft-flag (no migration — stored in qualification_json)
    district = None
    if signal.resolved_district_id is not None:
        district = await get_district(session, signal.resolved_district_id)
    qual_dict = annotate_district_tier(
        qual_dict,
        district_id=district.id if district else None,
        district_name=district.name if district else None,
        district_state=district.state if district else None,
        district_tier=district.tier if district else None,
        district_enrollment=district.enrollment if district else None,
        district_supported=district.supported if district else None,
        district_on_skip_list=district.on_skip_list if district else None,
    )

    # Store on signal
    await save_signal_qualification(session, signal.id, qual_dict)
    return qual_dict


async def _load_signal_contexts(
    session: AsyncSession,
    signals: list[SignalQueue],
) -> dict[int, dict[str, Any]]:
    contexts: dict[int, dict[str, Any]] = {s.id: {} for s in signals}
    run_ids = [s.pipeline_run_id for s in signals if s.pipeline_run_id]
    if run_ids:
        rows = await session.execute(
            select(PipelineRun, Pipeline)
            .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
            .where(PipelineRun.id.in_(run_ids))
        )
        runs = {run.id: (run, pipe) for run, pipe in rows.all()}
        for signal in signals:
            if signal.pipeline_run_id in runs:
                run, pipe = runs[signal.pipeline_run_id]
                contexts[signal.id]["pipelineRun"] = {
                    "id": run.id,
                    "pipelineId": run.pipeline_id,
                    "pipelineName": pipe.name,
                    "status": run.status,
                    "startedAt": run.started_at.isoformat() if run.started_at else None,
                    "createdAt": run.created_at.isoformat(),
                }

    pending = await session.execute(select(Approval).where(Approval.status == "pending"))
    wanted = {str(s.id): s.id for s in signals}
    for approval in pending.scalars().all():
        payload = approval.decision_payload or {}
        metadata = payload.get("metadata") or payload.get("context") or payload
        signal_ids = metadata.get("signal_ids") or metadata.get("signalIds") or []
        for raw_id in signal_ids if isinstance(signal_ids, list) else []:
            signal_id = wanted.get(str(raw_id))
            if signal_id and "approval" not in contexts[signal_id]:
                contexts[signal_id]["approval"] = {
                    "id": approval.id,
                    "label": "Awaiting Gate 1",
                    "href": f"#approvals/{approval.id}",
                }

    dedupe_pairs: dict[tuple[str, str], list[int]] = {}
    for signal in signals:
        source_url = (signal.source_url or "").strip()
        headline = (signal.headline or "").strip()
        if not source_url or not headline:
            contexts[signal.id]["relatedSignalsCount"] = 0
            continue
        dedupe_pairs.setdefault((source_url, headline), []).append(signal.id)

    for (source_url, headline), signal_ids in dedupe_pairs.items():
        total = await session.scalar(
            select(func.count(SignalQueue.id)).where(
                SignalQueue.source_url == source_url,
                SignalQueue.headline == headline,
                SignalQueue.signal_status.in_(_RELATED_SIGNAL_STATUSES),
            )
        )
        related_count = max(int(total or 0) - 1, 0)
        for signal_id in signal_ids:
            contexts[signal_id]["relatedSignalsCount"] = related_count
    return contexts


def _serialize_signal(signal: SignalQueue, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    qual = signal.qualification_json
    # DIST4: surface districtContext from qualification_json (written by annotate_district_tier)
    district_context: dict[str, Any] | None = None
    if isinstance(qual, dict):
        district_context = qual.get("districtContext")
    return {
        "id": signal.id,
        "sourceType": signal.source_type,
        "sourceUrl": signal.source_url,
        "sourceId": signal.source_id,
        "pipelineRunId": signal.pipeline_run_id,
        "pipelineRun": context.get("pipelineRun"),
        "approval": context.get("approval"),
        "relatedSignalsCount": context.get("relatedSignalsCount", 0),
        "headline": signal.headline,
        "summary": signal.summary,
        "campaignFamily": signal.campaign_family,
        "urgencyTier": signal.urgency_tier,
        "discoveredBy": signal.discovered_by,
        "districtId": signal.district_id,
        "resolvedDistrictId": signal.resolved_district_id,
        "districtContext": district_context,
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
