"""Signal Queue router — /api/signal-queue.

Endpoints:
  POST   /intake          — structured ingestion seam (scouts / operators)
  GET    /                — list signals (filtered, paginated)
  GET    /{id}            — single signal
  POST   /{id}/qualify    — on-demand re-qualification (C3 real implementation)
  POST   /{id}/approve    — Gate 1: promote to candidate
  POST   /{id}/reject     — reject and record reason
  POST   /{id}/snooze     — snooze for N days
  POST   /{id}/ask              — archive a signal
  POST   /{id}/argus-dispatch   — dig deeper button: fire async Argus dispatch for signal's district
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.marketing.intel.trends import compute_time_sensitivity, compute_velocity_ranking
from artemis.marketing.models import (
    Approval,
    CampaignCandidate,
    CampaignCandidateSignal,
    District,
    SignalQueue,
    SignalReasonCode,
)
from artemis.marketing.qualification import run_and_store_qualification
from artemis.marketing.repository import (
    create_signal,
    find_signal_by_dedupe_key,
    get_active_ruleset_version,
    get_candidate_signals,
    get_signal,
    list_signal_worklist_overrides,
    list_signals,
    promote_signal_cluster_to_candidate,
    promote_signal_to_candidate,
    update_signal,
    upsert_signal_worklist_override,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.marketing.routes.intel_prioritization import _build_combined
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.state_machine import SignalState, transition
from artemis.pipelines.models import Pipeline, PipelineRun
from artemis.pipelines.node_executors.human_gate_executor import _cluster_score

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


class WorklistPromoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_ids: list[int] = Field(default_factory=list, alias="signalIds")
    title: str | None = None
    updated_by: str | None = Field(default=None, alias="updatedBy")


class WorklistRemoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_id: int = Field(alias="signalId")
    updated_by: str | None = Field(default=None, alias="updatedBy")


class WorklistMergeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_ids: list[int] = Field(default_factory=list, alias="signalIds")
    target_cluster_key: str = Field(alias="targetClusterKey")
    updated_by: str | None = Field(default=None, alias="updatedBy")


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


_VALID_ROUTING_STATUSES = {"routable", "unrouted_no_contact"}


@router.get("")
@router.get("/")
async def list_queue(
    status: str | None = Query(default=None),
    campaign_family: str | None = Query(default=None, alias="campaignFamily"),
    urgency_tier: str | None = Query(default=None, alias="urgencyTier"),
    routing_status: str | None = Query(default=None, alias="routingStatus"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List signals with optional filters (cursor-paginated)."""
    safe_status = status if status in _VALID_STATUSES else None
    safe_routing_status = routing_status if routing_status in _VALID_ROUTING_STATUSES else None
    signals = await list_signals(
        session,
        status=safe_status,
        campaign_family=campaign_family,
        routing_status=safe_routing_status,
        limit=limit,
        cursor=cursor,
    )
    contexts = await _load_signal_contexts(session, signals)
    return {
        "signals": [_serialize_signal(s, contexts.get(s.id)) for s in signals],
        "total": len(signals),
    }


@router.get("/worklist")
async def get_worklist(
    window_days: int = Query(default=30, ge=7, le=180),
    horizon_days: int = Query(default=60, ge=7, le=180),
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return ranked Signals worklist cards grouped into actionable clusters."""
    as_of = datetime.now(UTC)
    signals = (
        (
            await session.execute(
                select(SignalQueue)
                .where(SignalQueue.signal_status == SignalState.qualified.value)
                .order_by(SignalQueue.created_at.desc(), SignalQueue.id.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    cards = await _build_signal_worklist_cards(
        session,
        signals=list(signals),
        as_of=as_of,
        window_days=window_days,
        horizon_days=horizon_days,
        limit=limit,
    )
    return {
        "asOf": as_of.isoformat(),
        "windowDays": window_days,
        "horizonDays": horizon_days,
        "cards": cards,
        "totalCards": len(cards),
        "qualifiedSignals": len(signals),
    }


@router.post("/clusters/promote")
@router.post("/worklist/promote")
async def promote_signal_cluster(
    body: WorklistPromoteRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Promote an arbitrary operator-picked signal set into one campaign candidate."""
    if not body.signal_ids:
        raise bad_request("signalIds must contain at least one signal")  # noqa: B904

    try:
        candidate = await promote_signal_cluster_to_candidate(session, body.signal_ids)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    await session.commit()
    await session.refresh(candidate)
    links = await get_candidate_signals(session, candidate.id)
    return {
        "candidateId": candidate.id,
        "candidateName": candidate.name or body.title or f"Campaign {candidate.id}",
        "workspaceState": candidate.workspace_state,
        "initiatedAt": candidate.initiated_at.isoformat() if candidate.initiated_at else None,
        "signalIds": body.signal_ids,
        "linkedSignalIds": [link.signal_id for link in links],
    }


@router.post("/worklist/remove")
async def remove_signal_from_worklist(
    body: WorklistRemoveRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Remove a signal from ranked worklist cards without deleting it."""
    signal = await get_signal(session, body.signal_id)
    await upsert_signal_worklist_override(
        session,
        signal.id,
        worklist_cluster_key=None,
        hidden_from_worklist=True,
        updated_by=body.updated_by or "operator",
    )
    await session.commit()
    return {"ok": True, "signalId": signal.id, "hiddenFromWorklist": True}


@router.post("/worklist/merge")
async def merge_signal_worklist_cards(
    body: WorklistMergeRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Merge one card's signals into another card's cluster key, losslessly."""
    if not body.signal_ids:
        raise bad_request("signalIds must contain at least one signal")  # noqa: B904
    for signal_id in body.signal_ids:
        await get_signal(session, signal_id)
        await upsert_signal_worklist_override(
            session,
            signal_id,
            worklist_cluster_key=body.target_cluster_key,
            hidden_from_worklist=False,
            updated_by=body.updated_by or "operator",
        )
    await session.commit()
    return {
        "ok": True,
        "mergedSignalCount": len(body.signal_ids),
        "targetClusterKey": body.target_cluster_key,
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
    # Status transition (pending_qualification → qualified) is now handled
    # inside run_and_store_qualification. Nothing extra needed here.
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

    # Shared promotion: cluster/create candidate + mark signal approved.
    # Both the manual path and the pipeline Gate-1 path go through
    # promote_signal_to_candidate so side effects cannot drift.
    promo = await promote_signal_to_candidate(session, signal)
    candidate = promo.candidate
    updated = await get_signal(session, signal_id)

    # Enrich the candidate with ruleset + metrics (manual-path-only metadata).
    if candidate.source_signal_id == signal_id:
        candidate.ruleset_version_at_qualification = ruleset_version_tag or ""
        candidate.metrics_json = qualification_summary
        await session.flush()

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
            agent_slug="marketing.qualifier.cross_reference",
        )
    )

    # Record engagement: if Callie had proactively pushed this signal, this
    # approval counts as Jon having "acted" on it (not just ignored it).
    # Non-fatal: any failure is swallowed so the approval response is unaffected.
    _asyncio.create_task(
        _record_engage_from_approval(session, updated)
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
    # Fallback agent_slug: the primary qualifier for all marketing signals.
    # If the signal was sourced from a specific qualifier agent, that slug would
    # ideally be used here; for now we default to the cross_reference qualifier
    # which is the primary qualifier per marketing_agents.py seed.
    _qualifier_slug = "marketing.qualifier.cross_reference"
    import asyncio as _asyncio

    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    _asyncio.create_task(
        write_signal_gate1_approval_observation(
            signal_id=signal_id,
            new_status=updated.signal_status,
            decided_by="operator",
            decision_payload={"headline": updated.headline},
            rejection_reason=reason,
            agent_slug=_qualifier_slug,
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


# ── Argus "dig deeper" dispatch ───────────────────────────────────────────────


@router.post("/{signal_id}/argus-dispatch")
async def dispatch_argus_for_signal(
    signal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Fire async Argus research for a signal's district (the "dig deeper" button).

    Reuses the existing ``_dispatch_research`` async dispatch from
    ``artemis.floating_artemis.tools.argus_tools`` — the same path Callie uses
    when Jon asks her to dig deeper in chat.  The endpoint only accepts top-tier
    (hot) qualified signals; 422 for anything else.

    Also records a "acted" engagement observation so the learning loop (#2)
    knows Jon engaged with this signal.
    """
    try:
        signal = await get_signal(session, signal_id)
    except ValueError:
        raise not_found("Signal not found", "signal_not_found")  # noqa: B904

    if signal.signal_status not in (SignalState.qualified.value, SignalState.APPROVED.value):
        raise conflict("signal_not_qualified", code="signal_not_qualified")  # noqa: B904

    if signal.urgency_tier != "hot":
        raise bad_request(
            "argus-dispatch is only available for top-tier (hot) signals",
            "not_top_tier",
        )

    district_key = signal.district_id or signal.headline[:60]

    # Record engagement so the learning loop knows Jon acted on this signal
    import asyncio as _asyncio

    _asyncio.create_task(
        _record_engage_from_signal(session, signal, outcome="acted")
    )

    # Fire async Argus dispatch (returns immediately with dispatched payload)
    from artemis.floating_artemis.tools.argus_tools import (
        _BACKGROUND_TASKS,
        _insert_pending_request,
        _safe_research_and_post,
    )

    # Resolve channel from the Callie proactive channel config (not a Slack session)
    channel = settings.callie_proactive_channel or settings.marketing_campaigns_slack_channel
    team_id = ""

    request_id = await _insert_pending_request(
        district_key=district_key,
        channel_id=channel or "",
        team_id=team_id,
        signal={
            "headline": signal.headline or "",
            "state": signal.state or "",
            "district_id": signal.district_id or "",
        },
        triggering_signal_id=str(signal_id),
    )

    if channel:
        loop = _asyncio.get_running_loop()
        task = loop.create_task(
            _safe_research_and_post(
                request_id=request_id,
                channel_id=channel,
                team_id=team_id,
                district_key=district_key,
                triggering_signal_id=str(signal_id),
                signal={
                    "headline": signal.headline or "",
                    "state": signal.state or "",
                    "district_id": signal.district_id or "",
                },
            ),
            name=f"argus_ui_bg_{district_key}",
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {
        "status": "dispatched",
        "district": district_key,
        "signalId": signal_id,
        "channel": channel or None,
    }


# ── Engagement helpers (for learning loop) ───────────────────────────────────


async def _record_engage_from_approval(
    session: AsyncSession,
    signal: SignalQueue,
) -> None:
    """Fire-and-forget: record 'acted' engagement when a Callie-pushed signal is approved."""
    try:
        from artemis.marketing.callie_push import record_signal_engagement

        reason_codes = [
            rc.get("code", "") for rc in (signal.reason_codes or []) if isinstance(rc, dict)
        ]
        await record_signal_engagement(
            session,
            signal_id=signal.id,
            outcome="acted",
            reason_codes=[c for c in reason_codes if c],
            campaign_family=signal.campaign_family,
            district_type=None,
        )
    except Exception:
        log.debug(
            "_record_engage_from_approval: non-fatal failure for signal %s",
            signal.id,
            exc_info=True,
        )


async def _record_engage_from_signal(
    session: AsyncSession,
    signal: SignalQueue,
    *,
    outcome: str,
) -> None:
    """Generic engagement recorder used by the argus-dispatch endpoint."""
    try:
        from artemis.marketing.callie_push import record_signal_engagement

        reason_codes = [
            rc.get("code", "") for rc in (signal.reason_codes or []) if isinstance(rc, dict)
        ]
        await record_signal_engagement(
            session,
            signal_id=signal.id,
            outcome=outcome,
            reason_codes=[c for c in reason_codes if c],
            campaign_family=signal.campaign_family,
            district_type=None,
        )
    except Exception:
        log.debug(
            "_record_engage_from_signal: non-fatal failure for signal %s",
            signal.id,
            exc_info=True,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _run_and_store_qualification(
    session: AsyncSession,
    signal: SignalQueue,
) -> dict[str, Any] | None:
    """Thin wrapper around the shared ``run_and_store_qualification`` helper.

    Delegates all logic to ``artemis.marketing.qualification`` so that the
    scout paths can call the same function without depending on this router.
    """
    return await run_and_store_qualification(session, signal)


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

    if wanted:
        candidate_rows = await session.execute(
            select(
                CampaignCandidateSignal.signal_id,
                CampaignCandidate.id,
                CampaignCandidate.name,
                CampaignCandidate.workspace_state,
                CampaignCandidate.initiated_at,
            )
            .join(
                CampaignCandidate,
                CampaignCandidate.id == CampaignCandidateSignal.candidate_id,
            )
            .where(CampaignCandidateSignal.signal_id.in_(list(wanted.values())))
            .order_by(CampaignCandidate.id.desc())
        )
        for signal_id, candidate_id, name, workspace_state, initiated_at in candidate_rows.all():
            if "campaign" in contexts[signal_id]:
                continue
            contexts[signal_id]["campaign"] = {
                "candidateId": candidate_id,
                "name": name,
                "workspaceState": workspace_state,
                "initiatedAt": initiated_at.isoformat() if initiated_at else None,
            }
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
        "routingStatus": signal.routing_status,
        "campaignCandidateId": context.get("campaign", {}).get("candidateId"),
        "campaignCandidateName": context.get("campaign", {}).get("name"),
        "campaignWorkspaceState": context.get("campaign", {}).get("workspaceState"),
        "campaignInitiatedAt": context.get("campaign", {}).get("initiatedAt"),
        "snoozedUntil": signal.snoozed_until.isoformat() if signal.snoozed_until else None,
        "rejectedReason": signal.rejected_reason,
        "ownerUserId": signal.owner_user_id,
        "createdAt": signal.created_at.isoformat(),
        "updatedAt": signal.updated_at.isoformat(),
    }


def _signal_fit_score(signal: SignalQueue) -> float:
    qual = signal.qualification_json or {}
    if isinstance(qual, dict):
        for key in ("fit_score", "adjustedScore", "rawScore"):
            value = qual.get(key)
            if value is not None:
                with suppress(TypeError, ValueError):
                    return float(value)
        scores = qual.get("scores")
        if isinstance(scores, list):
            for score in scores:
                if (
                    isinstance(score, dict)
                    and score.get("campaignFamily") == signal.campaign_family
                ):
                    score_value = score.get("adjustedScore")
                    if score_value is not None:
                        with suppress(TypeError, ValueError):
                            return float(score_value)
    return 0.0


def _effective_worklist_cluster_key(
    signal: SignalQueue,
    override: Any | None,
) -> str | None:
    if override is not None and override.hidden_from_worklist:
        return None
    if override is not None and override.worklist_cluster_key:
        return str(override.worklist_cluster_key)
    if signal.resolved_district_id is not None and signal.campaign_family:
        return f"{signal.resolved_district_id}|{signal.campaign_family}"
    return f"signal:{signal.id}"


async def _build_signal_worklist_cards(
    session: AsyncSession,
    *,
    signals: list[SignalQueue],
    as_of: datetime,
    window_days: int,
    horizon_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    if not signals:
        return []

    signal_ids = [signal.id for signal in signals]
    overrides = await list_signal_worklist_overrides(session, signal_ids)

    velocity = await compute_velocity_ranking(
        session,
        as_of=as_of,
        window_days=window_days,
        limit=max(limit * 4, 100),
    )
    time_sensitive = await compute_time_sensitivity(
        session,
        as_of=as_of,
        horizon_days=horizon_days,
        limit=max(limit * 4, 100),
    )
    combined = _build_combined(velocity, time_sensitive)
    priority_map = {row.district_id: idx for idx, row in enumerate(combined)}
    priority_rows = {row.district_id: row for row in combined}

    district_ids = sorted(
        {
            signal.resolved_district_id
            for signal in signals
            if signal.resolved_district_id is not None
        }
    )
    district_cache: dict[int, District] = {}
    for district_id in district_ids:
        district = await session.get(District, district_id)
        if district is not None:
            district_cache[district_id] = district

    groups: dict[str, list[SignalQueue]] = {}
    for signal in signals:
        cluster_key = _effective_worklist_cluster_key(signal, overrides.get(signal.id))
        if cluster_key is None:
            continue
        groups.setdefault(cluster_key, []).append(signal)

    cards: list[dict[str, Any]] = []
    recent_cutoff = as_of - timedelta(days=14)
    for cluster_key, group_signals in groups.items():
        sorted_group = sorted(
            group_signals,
            key=lambda signal: (
                -_signal_fit_score(signal),
                -signal.created_at.timestamp(),
                signal.id,
            ),
        )
        primary = sorted_group[0]
        cluster_district_id: int | None = None
        cluster_family = primary.campaign_family
        if "|" in cluster_key:
            dist_part, family_part = cluster_key.split("|", 1)
            with suppress(TypeError, ValueError):
                cluster_district_id = int(dist_part)
            if family_part:
                cluster_family = family_part
        district = (
            district_cache.get(cluster_district_id)
            if cluster_district_id is not None
            else (
                district_cache.get(primary.resolved_district_id)
                if primary.resolved_district_id is not None
                else None
            )
        )
        score, score_reason = _cluster_score(group_signals, now_utc=as_of)
        district_priority_index = (
            priority_map.get(cluster_district_id) if cluster_district_id is not None else None
        )
        priority_row = (
            priority_rows.get(cluster_district_id) if cluster_district_id is not None else None
        )
        signal_items = []
        for signal in sorted_group:
            signal_items.append(
                {
                    "id": signal.id,
                    "headline": signal.headline,
                    "summary": signal.summary,
                    "sourceType": signal.source_type,
                    "campaignFamily": signal.campaign_family,
                    "urgencyTier": signal.urgency_tier,
                    "createdAt": signal.created_at.isoformat(),
                    "fitScore": _signal_fit_score(signal),
                }
            )
        cards.append(
            {
                "clusterKey": cluster_key,
                "title": district.name
                if district is not None
                else (primary.district_id or primary.headline),
                "districtId": cluster_district_id
                if cluster_district_id is not None
                else primary.resolved_district_id,
                "districtLabel": (
                    f"{district.name} ({district.state})"
                    if district is not None and district.state
                    else (district.name if district is not None else (primary.district_id or None))
                ),
                "state": district.state if district is not None else primary.state,
                "tier": district.tier if district is not None else None,
                "campaignFamily": cluster_family,
                "score": score,
                "scoreReason": score_reason,
                "velocityScore": priority_row.velocity_score if priority_row is not None else None,
                "velocityRank": priority_row.velocity_rank if priority_row is not None else None,
                "timeSensitive": bool(
                    priority_row.has_time_sensitive_signal if priority_row is not None else False
                ),
                "earliestSignalCreatedAtIso": (
                    priority_row.earliest_signal_created_at_iso
                    if priority_row is not None
                    else None
                ),
                "signalCount": len(sorted_group),
                "recentSignalCount": sum(
                    1 for signal in sorted_group if signal.created_at >= recent_cutoff
                ),
                "hasHotSignal": any(signal.urgency_tier == "hot" for signal in sorted_group),
                "signalIds": [signal.id for signal in sorted_group],
                "signals": signal_items,
                "priorityIndex": district_priority_index
                if district_priority_index is not None
                else 9999,
            }
        )

    cards.sort(
        key=lambda card: (
            card["priorityIndex"],
            -(card["velocityScore"] or 0.0),
            -card["score"],
            card["clusterKey"],
        )
    )
    for idx, card in enumerate(cards, start=1):
        card["rank"] = idx
        card.pop("priorityIndex", None)
    return cards[:limit]
