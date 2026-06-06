"""Human gate node executor.

Handles: human_gate nodes — creates approval row, sends Slack DMs, suspends pipeline.

Config shape:
  {
    "approval_kind":   str,              # signal_brief | content_draft
    "approvers":       list[str],        # list of approver email addresses
    "timeout_hours":   int,              # hours before on_timeout fires (default 72)
    "on_timeout":      str,              # auto_approve | auto_reject | escalate (default auto_approve)
    "escalation_to":   list[str] | None, # escalation approvers (if on_timeout=escalate)
    "wait_for_all_upstream": bool        # default True; gate waits for ALL upstream nodes
  }

Behaviour:
1. Check fan-in: if wait_for_all_upstream, block until all upstream nodes succeeded.
   Returns {"status": "waiting_for_upstream"} if not all done.
2. Create row in existing `approvals` table (kind, subject_id=run_id:node_id)
3. For each approver email: look up Slack user and send DM with approve/reject buttons
   - Slack failure is non-fatal; falls back to in-app queue with delivery_log entry
4. Schedule timeout job via APScheduler
5. Returns {"status": "suspended"} — executor stops and pipeline goes to awaiting_approval

Returns:
  {"status": "suspended", "delivery_log": [...]}  — gate pending
  {"status": "waiting_for_upstream"}              — fan-in not ready yet
  {"status": "succeeded", "decision": "approved"/"rejected"}  — already resolved (resume path)
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_HOURS = 72


async def _lookup_slack_user_id(
    email: str,
    token: str,
) -> str | None:
    """Look up Slack user ID by email. Returns None if not found."""
    from artemis.integrations.slack.client import SlackAPIError, SlackClient

    client = SlackClient(token)
    try:
        return await client.lookup_user_by_email(email)
    except SlackAPIError:
        logger.warning("Slack user lookup failed for %r", email)
    except Exception:
        logger.exception("Unexpected error looking up Slack user %r", email)
    return None


async def _send_approval_dm(
    *,
    email: str,
    token: str,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
    context: dict[str, Any] | None = None,
    app_base_url: str = "",
    escalation: bool = False,
    original_approvers: list[str] | None = None,
    timeout_hours: int = _DEFAULT_TIMEOUT_HOURS,
) -> dict[str, Any]:
    """Send approval DM to one approver. Returns delivery log entry."""
    from artemis.integrations.slack.client import SlackAPIError, SlackClient
    from artemis.integrations.slack.messages import (
        build_approval_dm_blocks,
        build_escalation_dm_blocks,
        build_plain_approval_text,
    )

    log_entry: dict[str, Any] = {
        "email": email,
        "sent_at": datetime.now(UTC).isoformat(),
        "channel": None,
        "error": None,
        "fallback": False,
    }

    user_id = await _lookup_slack_user_id(email, token)
    if not user_id:
        log_entry["error"] = f"Slack user not found for {email!r}"
        log_entry["fallback"] = True
        logger.warning(
            "Slack DM skipped for %r (user not found); falling back to in-app queue", email
        )
        return log_entry

    if escalation:
        blocks = build_escalation_dm_blocks(
            pipeline_name=pipeline_name,
            node_label=node_label,
            run_id=run_id,
            node_id=node_id,
            context=context,
            original_approvers=original_approvers,
            timeout_hours=timeout_hours,
            app_base_url=app_base_url,
        )
    else:
        blocks = build_approval_dm_blocks(
            pipeline_name=pipeline_name,
            node_label=node_label,
            run_id=run_id,
            node_id=node_id,
            context=context,
            app_base_url=app_base_url,
        )

    fallback_text = build_plain_approval_text(
        pipeline_name=pipeline_name,
        node_label=node_label,
        run_id=run_id,
        node_id=node_id,
    )

    client = SlackClient(token)
    try:
        # Open DM channel and send
        from artemis.integrations.slack.client import _SLACK_API_BASE  # noqa: F401

        open_resp = await client._post("conversations.open", users=user_id)
        channel_raw = open_resp.get("channel", {})
        channel_id = str(channel_raw["id"]) if isinstance(channel_raw, dict) else str(channel_raw)
        await client._post(
            "chat.postMessage",
            channel=channel_id,
            text=fallback_text,
            blocks=blocks,
        )
        log_entry["channel"] = channel_id
        logger.info(
            "Sent approval DM to %r (channel=%s) for run %s node %s",
            email,
            channel_id,
            run_id,
            node_id,
        )
    except SlackAPIError as exc:
        log_entry["error"] = f"Slack API error: {exc}"
        log_entry["fallback"] = True
        logger.warning("Slack DM failed for %r: %s; falling back to in-app queue", email, exc)
    except Exception as exc:
        log_entry["error"] = f"Unexpected error: {exc}"
        log_entry["fallback"] = True
        logger.exception("Unexpected Slack DM error for %r", email)

    return log_entry


# Marketing approval gates that should also post to the shared marketing channel.
_MARKETING_CHANNEL_KINDS = frozenset({"content_draft", "signal_brief", "campaign_initiation"})


async def _post_approval_to_channel(
    *,
    channel_id: str,
    token: str,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
    context: dict[str, Any] | None = None,
    app_base_url: str = "",
) -> dict[str, Any]:
    """Post the approval review notification to a shared Slack channel (once, not per-approver).

    Mirrors the DM blocks (approve/reject + Edit-in-Writing-Studio) so the team's channel is the
    shared review surface. Non-fatal: a failure logs + falls back, never breaks the gate.
    """
    from artemis.integrations.slack.client import SlackAPIError, SlackClient
    from artemis.integrations.slack.messages import (
        build_approval_dm_blocks,
        build_plain_approval_text,
    )

    log_entry: dict[str, Any] = {
        "target": "channel",
        "channel": channel_id,
        "sent_at": datetime.now(UTC).isoformat(),
        "error": None,
        "fallback": False,
    }
    blocks = build_approval_dm_blocks(
        pipeline_name=pipeline_name,
        node_label=node_label,
        run_id=run_id,
        node_id=node_id,
        context=context,
        app_base_url=app_base_url,
    )
    fallback_text = build_plain_approval_text(
        pipeline_name=pipeline_name,
        node_label=node_label,
        run_id=run_id,
        node_id=node_id,
    )
    try:
        await SlackClient(token)._post(
            "chat.postMessage",
            channel=channel_id,
            text=fallback_text,
            blocks=blocks,
        )
        logger.info(
            "Posted approval notification to channel %s for run %s node %s",
            channel_id,
            run_id,
            node_id,
        )
    except SlackAPIError as exc:
        log_entry["error"] = f"Slack API error: {exc}"
        log_entry["fallback"] = True
        logger.warning("Channel approval post failed for %s: %s", channel_id, exc)
    except Exception as exc:
        log_entry["error"] = f"Unexpected error: {exc}"
        log_entry["fallback"] = True
        logger.exception("Unexpected channel approval post error for %s", channel_id)
    return log_entry


async def _get_slack_token(session: AsyncSession) -> str | None:
    """Retrieve active Slack bot_token from integrations table."""
    try:
        from sqlalchemy import select

        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.models import Integration

        result = await session.execute(
            select(Integration)
            .where(
                Integration.provider == "slack",
                Integration.status == "active",
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        creds = decrypt_credentials(row.encrypted_credentials)
        bot_token = creds.get("bot_token") or creds.get("token") or creds.get("access_token")
        return str(bot_token) if bot_token else None
    except Exception:
        logger.exception("Failed to load Slack token")
        return None


# Gate kinds whose approval card renders qualified signals. For these, in the
# MCP era the agents' real effects live in signal_queue (committed via tool calls),
# not in node_states — so the card context must be READ FROM THE DB.
_SIGNAL_GATE_KINDS = frozenset({"signal_brief"})
_CONTENT_GATE_KINDS = frozenset({"content_draft"})

_PREVIEW_MAX = 400
# Upper bound for the full draft body stored in context (content_draft cards).
# Slack section blocks cap at ~3000 chars each; the builder chunks beyond that.
# We store up to 10 000 chars here — more than any realistic outreach email.
_DRAFT_BODY_MAX = 10_000


async def _build_pipe4_context(
    approval_kind: str,
    node_states: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the PIPE4 rendering context dict for an approval card.

    Returns a context dict suitable for ``approval.pipe4_context["context"]``.

    For signal-family gates (``approval_kind`` in :data:`_SIGNAL_GATE_KINDS`)
    with a ``session`` and ``run_id`` supplied, the context is read from
    ``signal_queue`` — the qualified signals committed by the scout/qualifier
    agents via MCP tool calls for this run. This is the source of truth in the
    MCP era; agents only return ``output_summary`` text into ``node_states``.

    For content/draft gates, or when no session/run_id is available, this falls
    back to the original ``node_states``-based extraction (preserved verbatim),
    which still serves content_draft gates and existing tests.
    """
    if session is not None and run_id is not None and approval_kind in _SIGNAL_GATE_KINDS:
        return await _build_signal_gate_context_from_db(approval_kind, session, run_id)
    if session is not None and run_id is not None and approval_kind in _CONTENT_GATE_KINDS:
        ctx = await _build_content_gate_context_from_db(approval_kind, session, run_id)
        if ctx.get("candidate_id") is not None:
            return ctx
    return _build_pipe4_context_from_node_states(approval_kind, node_states)


def _cluster_score(
    signals: list[Any],
    *,
    now_utc: datetime,
    recency_days: int = 7,
) -> tuple[float, str]:
    """Compute a deterministic cluster score and reason string.

    Score formula (documented here, mirrors brief spec):
      base      = mean of signal fit_score (default 0.5 if missing)
      stacking  = +0.05 * (signal_count - 1), capped at +0.20
      recency   = +0.10 if any signal captured within `recency_days` days
      final     = clamp(base + stacking + recency, 0.0, 1.0)

    Reason string:
      "{n} stacked signals" or "1 signal"
      + " + recent activity"  if recency bonus fired
      + " + high fit"         if mean fit_score >= 0.75
    """
    n = len(signals)
    fit_scores: list[float] = []
    has_recent = False
    cutoff = now_utc - timedelta(days=recency_days)

    for sig in signals:
        # fit_score from qualification_json.adjustedScore / rawScore, default 0.5
        qual = sig.qualification_json or {}
        if isinstance(qual, dict):
            raw = qual.get("fit_score") or qual.get("adjustedScore") or qual.get("rawScore")
            try:
                fit_scores.append(float(raw))
            except (TypeError, ValueError):
                fit_scores.append(0.5)
        else:
            fit_scores.append(0.5)

        # Recency: use captured_at if available, fall back to created_at
        captured_raw = (
            (sig.qualification_json or {}).get("captured_at")
            if isinstance(sig.qualification_json, dict)
            else None
        )
        captured_dt: datetime | None = None
        if captured_raw:
            try:
                captured_dt = datetime.fromisoformat(str(captured_raw))
                if captured_dt.tzinfo is None:
                    captured_dt = captured_dt.replace(tzinfo=UTC)
            except Exception:
                captured_dt = None
        if captured_dt is None and sig.created_at is not None:
            captured_dt = sig.created_at
            if captured_dt.tzinfo is None:
                captured_dt = captured_dt.replace(tzinfo=UTC)
        if captured_dt is not None and captured_dt >= cutoff:
            has_recent = True

    mean_fit = sum(fit_scores) / len(fit_scores) if fit_scores else 0.5
    stacking_bonus = min(0.05 * (n - 1), 0.20)
    recency_bonus = 0.10 if has_recent else 0.0
    score = max(0.0, min(1.0, mean_fit + stacking_bonus + recency_bonus))

    # Compose reason string
    reason_parts: list[str] = []
    reason_parts.append(f"{n} stacked signals" if n > 1 else "1 signal")
    if has_recent:
        reason_parts.append("recent activity")
    if mean_fit >= 0.75:
        reason_parts.append("high fit")
    reason = " + ".join(reason_parts)

    return score, reason


def _build_clusters(
    rows: list[Any],
    district_cache: dict[int, Any],
) -> list[dict[str, Any]]:
    """Group qualified signals into cluster objects by (resolved_district_id, campaign_family).

    Returns a list of cluster dicts, with exactly one having ``suggested=True``
    (the highest-scoring cluster, ties broken by signal count then cluster_key alpha).
    """
    from collections import defaultdict

    now_utc = datetime.now(UTC)

    # Group rows into clusters
    cluster_map: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        dist_id = row.resolved_district_id
        family = row.campaign_family or ""
        key = f"{dist_id}|{family}"
        cluster_map[key].append(row)

    clusters: list[dict[str, Any]] = []
    for cluster_key, signals in cluster_map.items():
        # Sort signals: highest fit_score first (primary), then by id
        def _sig_fit(s: Any) -> float:
            qual = s.qualification_json or {}
            if isinstance(qual, dict):
                raw = qual.get("fit_score") or qual.get("adjustedScore") or qual.get("rawScore")
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
            return 0.0

        sorted_signals = sorted(signals, key=lambda s: (-_sig_fit(s), s.id))

        # District label
        dist_id_part, family_part = cluster_key.split("|", 1)
        district_label: str = dist_id_part  # fallback = raw id string
        try:
            dist_id_int = int(dist_id_part)
            district_obj = district_cache.get(dist_id_int)
            if district_obj is not None:
                district_label = (
                    f"{district_obj.name} ({district_obj.state})"
                    if district_obj.state
                    else district_obj.name
                )
        except (ValueError, TypeError):
            pass

        score, score_reason = _cluster_score(signals, now_utc=now_utc)

        # Build signal list
        signal_items: list[dict[str, Any]] = []
        for idx, sig in enumerate(sorted_signals):
            qual = sig.qualification_json or {}
            fit_val: float | None = None
            evidence_quote_val: str | None = None
            source_val: str | None = None
            captured_at_val: str | None = None
            if isinstance(qual, dict):
                raw_fit = qual.get("fit_score") or qual.get("adjustedScore") or qual.get("rawScore")
                with contextlib.suppress(TypeError, ValueError):
                    fit_val = float(raw_fit)  # type: ignore[arg-type]
                brief = qual.get("brief") or {}
                if isinstance(brief, dict):
                    evidence_quote_val = brief.get("evidence_quote") or None
                source_val = qual.get("source_url") or None
                captured_at_val = qual.get("captured_at") or None
            signal_items.append(
                {
                    "id": sig.id,
                    "role": "primary" if idx == 0 else "corroborating",
                    "headline": sig.headline or None,
                    "evidence_quote": evidence_quote_val,
                    "source": sig.source_url or source_val,
                    "fit_score": fit_val,
                    "urgency": sig.urgency_tier or None,
                    "captured_at": captured_at_val,
                }
            )

        clusters.append(
            {
                "cluster_key": cluster_key,
                "district_label": district_label,
                "campaign_family": family_part,
                "score": score,
                "score_reason": score_reason,
                "suggested": False,  # filled below
                "signals": signal_items,
            }
        )

    # Determine suggested cluster: highest score, tie-break by (most signals, lowest key alpha)
    if clusters:
        clusters.sort(key=lambda c: (-c["score"], -len(c["signals"]), c["cluster_key"]))
        clusters[0]["suggested"] = True

    return clusters


async def _build_signal_gate_context_from_db(
    approval_kind: str,
    session: AsyncSession,
    run_id: str,
) -> dict[str, Any]:
    """Build a signal-gate card context from committed signal_queue rows.

    Reads ``signal_queue`` rows for this run whose ``signal_status`` is
    ``'qualified'`` and aggregates the five UI-contract fields (signal_count,
    reason_codes, districts, evidence_quote, brief_preview) from them.  A run
    with zero qualified signals yields the clean empty context (no error).

    Also adds a ``clusters`` key — a list of cluster objects grouping signals
    by (resolved_district_id, campaign_family) with deterministic scoring and a
    single ``suggested=True`` entry. Existing flat fields are preserved for
    backward compatibility.
    """
    from sqlalchemy import select

    from artemis.marketing.models import District, SignalQueue

    ctx: dict[str, Any] = {
        "approval_kind": approval_kind,
        "signal_count": 0,
        "reason_codes": [],
        "districts": [],
        "district_label": None,
        "headline": None,
        "urgency": None,
        "score": None,
        "evidence_quote": None,
        "evidence_snippets": [],
        "brief_preview": None,
        "brief_body": None,
        "draft_summary": None,
        "clusters": [],
    }

    rows = (
        (
            await session.execute(
                select(SignalQueue)
                .where(
                    SignalQueue.pipeline_run_id == run_id,
                    SignalQueue.signal_status == "qualified",
                )
                .order_by(SignalQueue.id)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return ctx

    ctx["signal_count"] = len(rows)
    codes: set[str] = set()
    raw_districts: list[str] = []
    evidence_snippets: list[str] = []

    # Pre-load district objects for all distinct resolved_district_ids
    district_ids = {
        row.resolved_district_id for row in rows if row.resolved_district_id is not None
    }
    district_cache: dict[int, Any] = {}
    for dist_id in district_ids:
        district_obj = await session.get(District, dist_id)
        if district_obj is not None:
            district_cache[dist_id] = district_obj

    # Resolve district labels for the primary signal (first row).
    top_row = rows[0]
    ctx["headline"] = top_row.headline or None
    ctx["urgency"] = top_row.urgency_tier or None

    # Look up District row for the primary signal to get a human label.
    if top_row.resolved_district_id is not None:
        district_obj = district_cache.get(top_row.resolved_district_id)
        if district_obj is not None:
            label = (
                f"{district_obj.name} ({district_obj.state})"
                if district_obj.state
                else district_obj.name
            )
            ctx["district_label"] = label
    if ctx["district_label"] is None and (top_row.district_id or top_row.state):
        ctx["district_label"] = top_row.district_id or top_row.state

    # Score from qualification_json (top signal).
    top_qual = top_row.qualification_json or {}
    if isinstance(top_qual, dict):
        score_val = top_qual.get("adjustedScore") or top_qual.get("rawScore")
        if score_val is not None:
            ctx["score"] = score_val

    for row in rows:
        raw_codes = row.reason_codes if isinstance(row.reason_codes, list) else []
        for rc in raw_codes:
            code = rc.get("code", "") if isinstance(rc, dict) else str(rc)
            if code:
                codes.add(str(code))
        geo = row.district_id or row.state
        if geo and geo not in raw_districts:
            raw_districts.append(str(geo))
        # Collect all evidence quotes (one per signal, deduplicated by content).
        quote = _brief_field(row.qualification_json, "evidence_quote")
        if quote and str(quote) not in evidence_snippets:
            evidence_snippets.append(str(quote))

    ctx["reason_codes"] = sorted(codes)
    ctx["districts"] = sorted(raw_districts)
    if evidence_snippets:
        ctx["evidence_quote"] = evidence_snippets[0]
    ctx["evidence_snippets"] = evidence_snippets

    # Brief preview and full body: prefer the top signal's brief.
    top = rows[0].qualification_json
    preview = _brief_field(top, "preview")
    body = _brief_field(top, "body")
    if not preview and not body:
        for row in rows[1:]:
            preview = _brief_field(row.qualification_json, "preview")
            body = _brief_field(row.qualification_json, "body")
            if preview or body:
                break
    if preview or body:
        ctx["brief_preview"] = str(preview or body)[:_PREVIEW_MAX]
    if body:
        ctx["brief_body"] = str(body)[:_PREVIEW_MAX]

    # Build cluster objects (new — backward-compatible addition)
    ctx["clusters"] = _build_clusters(rows, district_cache)

    return ctx


def _draft_preview_from_metadata(metadata: dict[str, Any]) -> str | None:
    """Return the best available real draft preview from deliverable metadata."""
    versions = metadata.get("versions")
    if isinstance(versions, list):
        for version in versions:
            if isinstance(version, dict):
                content = version.get("content")
                if isinstance(content, str) and content.strip():
                    return _compact_preview(content)
    for key in ("draftBody", "content", "brief"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _compact_preview(value)
    return None


def _compact_preview(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:_PREVIEW_MAX] if normalized else ""


async def _build_content_gate_context_from_db(
    approval_kind: str,
    session: AsyncSession,
    run_id: str,
) -> dict[str, Any]:
    """Build a content-review gate card context from campaign deliverables + candidate state."""
    from sqlalchemy import select

    from artemis.marketing.models import (
        CampaignBrief,
        CampaignCandidate,
        CampaignCandidateSignal,
        CampaignDeliverable,
        District,
        SignalQueue,
    )
    from artemis.pipelines.models import PipelineRun

    ctx: dict[str, Any] = {
        "approval_kind": approval_kind,
        "candidate_id": None,
        "campaign_name": None,
        "campaign_family": None,
        "workspace_state": None,
        "deliverable_count": 0,
        "ready_deliverable_count": 0,
        "deliverables": [],
        "signal_count": 0,
        "reason_codes": [],
        "districts": [],
        "district_label": None,
        "brief_preview": None,
        "draft_summary": None,
        "draft_title": None,
        "draft_body": None,
        "deliverable_type_slug": None,
        "deliverable_ids": [],
    }

    run = await session.get(PipelineRun, run_id)
    if run is None or run.target_candidate_id is None:
        return ctx

    candidate = await session.get(CampaignCandidate, run.target_candidate_id)
    if candidate is None:
        return ctx

    ctx["candidate_id"] = candidate.id
    ctx["campaign_name"] = candidate.name or f"{candidate.campaign_family} campaign"
    ctx["campaign_family"] = candidate.campaign_family
    ctx["workspace_state"] = candidate.workspace_state

    deliverables = (
        (
            await session.execute(
                select(CampaignDeliverable)
                .where(CampaignDeliverable.candidate_id == candidate.id)
                .order_by(CampaignDeliverable.id)
            )
        )
        .scalars()
        .all()
    )
    deliverable_cards: list[dict[str, Any]] = []
    first_preview: str | None = None
    first_draft_title: str | None = None
    first_draft_body: str | None = None
    first_deliverable_type_slug: str | None = None
    for deliverable in deliverables:
        metadata = (
            dict(deliverable.deliverable_metadata)
            if isinstance(deliverable.deliverable_metadata, dict)
            else {}
        )
        preview = _draft_preview_from_metadata(metadata)
        title = (
            metadata.get("draftTitle")
            or metadata.get("title")
            or metadata.get("externalTitle")
            or f"Draft {deliverable.id}"
        )
        type_slug = metadata.get("deliverableTypeSlug") or metadata.get("deliverable_type_slug")
        # Full draft body — written by writing_studio.enqueue into draftBody.
        raw_body = metadata.get("draftBody") or metadata.get("content")
        draft_body_full: str | None = None
        if isinstance(raw_body, str) and raw_body.strip():
            draft_body_full = raw_body.strip()[:_DRAFT_BODY_MAX]
        deliverable_cards.append(
            {
                "id": deliverable.id,
                "status": deliverable.status,
                "title": title,
                "campaignId": deliverable.campaign_id,
                "externalDraftId": metadata.get("externalDraftId") or deliverable.deliverable_id,
                "deliverableTypeSlug": type_slug,
                "draftPreview": preview,
                "draftBody": draft_body_full,
                "updatedAt": (
                    deliverable.updated_at.isoformat() if deliverable.updated_at else None
                ),
            }
        )
        if first_preview is None and preview:
            first_preview = preview
        if first_draft_title is None:
            first_draft_title = title
        if first_draft_body is None and draft_body_full:
            first_draft_body = draft_body_full
        if first_deliverable_type_slug is None and type_slug:
            first_deliverable_type_slug = type_slug

    ctx["deliverables"] = deliverable_cards
    ctx["deliverable_ids"] = [card["id"] for card in deliverable_cards]
    ctx["deliverable_count"] = len(deliverable_cards)
    ctx["ready_deliverable_count"] = sum(
        1 for card in deliverable_cards if card["status"] == "draft_ready"
    )
    ctx["draft_summary"] = first_preview
    ctx["draft_title"] = first_draft_title
    ctx["draft_body"] = first_draft_body
    ctx["deliverable_type_slug"] = first_deliverable_type_slug

    signal_rows = (
        await session.execute(
            select(SignalQueue, CampaignCandidateSignal, District)
            .join(
                CampaignCandidateSignal,
                CampaignCandidateSignal.signal_id == SignalQueue.id,
            )
            .outerjoin(District, District.id == SignalQueue.resolved_district_id)
            .where(CampaignCandidateSignal.candidate_id == candidate.id)
            .order_by(CampaignCandidateSignal.is_primary.desc(), SignalQueue.id.asc())
        )
    ).all()
    ctx["signal_count"] = len(signal_rows)
    reason_codes: set[str] = set()
    districts: list[str] = []
    for signal, _, district in signal_rows:
        raw_codes = signal.reason_codes if isinstance(signal.reason_codes, list) else []
        for code in raw_codes:
            label = code.get("code") if isinstance(code, dict) else code
            if label:
                reason_codes.add(str(label))
        district_label = None
        if district is not None:
            district_label = (
                f"{district.name} ({district.state})" if district.state else district.name
            )
        elif signal.state:
            district_label = signal.state
        if district_label and district_label not in districts:
            districts.append(district_label)
    sorted_districts = sorted(districts)
    ctx["reason_codes"] = sorted(reason_codes)
    ctx["districts"] = sorted_districts
    ctx["district_label"] = sorted_districts[0] if sorted_districts else None

    latest_brief = (
        await session.execute(
            select(CampaignBrief)
            .where(CampaignBrief.candidate_id == candidate.id)
            .order_by(CampaignBrief.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_brief is not None and isinstance(latest_brief.content, dict):
        preview = latest_brief.content.get("preview") or latest_brief.content.get("body")
        if isinstance(preview, str) and preview.strip():
            ctx["brief_preview"] = _compact_preview(preview)

    return ctx


def _content_gate_error(
    *,
    candidate_id: int | None,
    deliverable_ids: list[Any] | None,
    deliverables: list[dict[str, Any]] | None,
) -> str | None:
    if candidate_id is None:
        return "content_draft gate requires a target candidate"

    if not deliverable_ids:
        return (
            "content_draft gate cannot open review: "
            f"no deliverables exist for target candidate {candidate_id}"
        )

    reviewable = False
    for deliverable in deliverables or []:
        preview = deliverable.get("draftPreview")
        body = deliverable.get("draftBody")
        if isinstance(preview, str) and preview.strip():
            reviewable = True
            break
        if isinstance(body, str) and body.strip():
            reviewable = True
            break

    if not reviewable:
        return (
            "content_draft gate cannot open review: "
            f"deliverables for target candidate {candidate_id} have no draft content"
        )

    return None


def _brief_field(qualification_json: Any, field: str) -> Any:
    """Safely read ``qualification_json['brief'][field]`` (None if absent)."""
    if not isinstance(qualification_json, dict):
        return None
    brief = qualification_json.get("brief")
    if not isinstance(brief, dict):
        return None
    return brief.get(field)


async def rebuild_gate_context(session: AsyncSession, approval_id: int) -> dict[str, Any]:
    """Idempotently rebuild a pending gate's pipe4 context from the DB.

    Loads the ``Approval`` row, reads its ``pipeline_run_id`` from
    ``pipe4_context``, re-runs the DB-reading signal-gate logic, writes the
    rebuilt context back into ``approval.pipe4_context["context"]``, commits,
    and returns the new context. Used to backfill approvals created before the
    gate card read from the DB.
    """
    from artemis.marketing.models import Approval

    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise ValueError(f"no approval with id={approval_id}")

    p4 = dict(approval.pipe4_context) if isinstance(approval.pipe4_context, dict) else {}
    run_id = p4.get("pipeline_run_id")
    if not run_id:
        raise ValueError(f"approval {approval_id} has no pipeline_run_id in pipe4_context")

    new_ctx = await _build_signal_gate_context_from_db(approval.kind, session, str(run_id))

    # Reassign the dict so SQLAlchemy flags the JSONB column dirty (in-place
    # mutation of a nested dict is not detected by the default JSONB type).
    p4["context"] = new_ctx
    approval.pipe4_context = p4

    await session.commit()
    return new_ctx


def _build_pipe4_context_from_node_states(
    approval_kind: str,
    node_states: dict[str, Any],
) -> dict[str, Any]:
    """Build the PIPE4 rendering context dict from upstream node_states.

    Pure function — no I/O. Returns a context dict suitable for:
      approval.pipe4_context["context"]

    Signal data is extracted from node_states entries that contain
    ``qualified_signals`` (list of signal dicts). Brief/draft previews are
    extracted from ``brief_data`` / ``brief`` / ``draft_data`` / ``draft``
    keys. Falls back to ``output_summary`` from qualifier nodes when no
    structured signal data is present.

    This is the legacy path, retained for content/draft gates and any caller
    that has no DB session.
    """
    ctx: dict[str, Any] = {
        "approval_kind": approval_kind,
        "signal_count": 0,
        "reason_codes": [],
        "districts": [],
        "evidence_quote": None,
        "brief_preview": None,
        "draft_summary": None,
    }
    for state_val in node_states.values():
        if not isinstance(state_val, dict):
            continue
        qualified = state_val.get("qualified_signals")
        if isinstance(qualified, list) and qualified:
            ctx["signal_count"] = len(qualified)
            codes: set[str] = set()
            districts: set[str] = set()
            for sig in qualified:
                if isinstance(sig, dict):
                    for rc in sig.get("reason_codes", []):
                        if isinstance(rc, dict):
                            codes.add(str(rc.get("code", "")))
                        else:
                            codes.add(str(rc))
                    geo = sig.get("geography") or {}
                    d = geo.get("district") if isinstance(geo, dict) else None
                    if d:
                        districts.add(str(d))
                    if not ctx["evidence_quote"]:
                        src = sig.get("source") or {}
                        snippet = src.get("verbatim_snippet") if isinstance(src, dict) else None
                        if snippet:
                            ctx["evidence_quote"] = str(snippet)[:400]
            ctx["reason_codes"] = sorted(codes)
            ctx["districts"] = sorted(districts)
        brief = state_val.get("brief_data") or state_val.get("brief")
        if isinstance(brief, dict) and brief.get("preview") and not ctx["brief_preview"]:
            ctx["brief_preview"] = str(brief["preview"])[:400]
        draft = state_val.get("draft_data") or state_val.get("draft")
        if isinstance(draft, dict) and draft.get("summary") and not ctx["draft_summary"]:
            ctx["draft_summary"] = str(draft["summary"])[:400]
        if not ctx["evidence_quote"] and "qualifier" in str(state_val):
            summary = state_val.get("output_summary", "")
            if summary:
                ctx["evidence_quote"] = str(summary)[:300]
    return ctx


def _check_fan_in(
    node: dict[str, Any],
    node_states: dict[str, Any],
    all_nodes: list[dict[str, Any]],
    all_edges: list[dict[str, Any]],
) -> bool:
    """Return True if all upstream nodes for this gate have reached a terminal state.

    A node is considered "done" if its status is "succeeded" OR if it is "failed"
    with ``continue_on_failure=true`` in its config (i.e. it is an optional node
    that was tolerated by the executor).  This ensures that a gate downstream of
    optional parallel feeders (e.g. scout nodes) fires as soon as all feeders have
    finished — regardless of whether individual optional feeders timed out.
    """
    node_id: str = node.get("id", "")
    config: dict[str, Any] = node.get("config") or {}
    wait_for_all: bool = config.get("wait_for_all_upstream", True)

    upstream_ids = [e["source_node_id"] for e in all_edges if e.get("target_node_id") == node_id]

    if not upstream_ids:
        return True  # No upstream — fire immediately

    # Build a lookup from node_id → node config for optional-node detection.
    optional_node_ids: set[str] = {
        n["id"] for n in all_nodes if (n.get("config") or {}).get("continue_on_failure")
    }

    def _is_done(uid: str) -> bool:
        status = node_states.get(uid, {}).get("status", "pending")
        # A tolerated (optional) failure counts as done for fan-in purposes.
        return status == "succeeded" or (status == "failed" and uid in optional_node_ids)

    if wait_for_all:
        return all(_is_done(uid) for uid in upstream_ids)
    else:
        return any(node_states.get(uid, {}).get("status") == "succeeded" for uid in upstream_ids)


async def execute_human_gate_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    all_nodes: list[dict[str, Any]],
    all_edges: list[dict[str, Any]],
    session: AsyncSession,
    run_id: str,
    pipeline_name: str = "",
    escalation: bool = False,
    original_approvers: list[str] | None = None,
) -> dict[str, Any]:
    """Execute a human_gate node.

    Args:
        node:               Node dict (id, type, config, label).
        node_states:        Current node_states for this run.
        all_nodes:          Full node list (for fan-in check context).
        all_edges:          Full edge list (for fan-in upstream lookup).
        session:            Async DB session (caller owns commit boundary).
        run_id:             Pipeline run ID.
        pipeline_name:      Pipeline name (for Slack DM subject line).
        escalation:         True if this is a re-send to escalation_to approvers.
        original_approvers: Original approver emails (for escalation DM context).

    Returns:
        NodeState-compatible dict.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from artemis.marketing.models import Approval

    node_id: str = node.get("id", "")
    config: dict[str, Any] = node.get("config") or {}
    node_label: str = node.get("label", node_id)

    # Fan-in check: don't fire until all upstream nodes are ready
    if not _check_fan_in(node, node_states, all_nodes, all_edges):
        return {
            "status": "waiting_for_upstream",
            "output_summary": "Waiting for all upstream nodes to complete",
            "cost_usd": 0.0,
        }

    # Check if gate is already resolved in node_states (resume path)
    existing_state = node_states.get(node_id, {})
    if existing_state.get("decision") in ("approved", "rejected", "auto_approved", "auto_rejected"):
        return {
            "status": "succeeded",
            "decision": existing_state["decision"],
            "output_summary": f"Gate previously resolved: {existing_state['decision']}",
            "cost_usd": 0.0,
        }

    kind: str = config.get("approval_kind", "signal_brief")
    approvers: list[str] = config.get("approvers", [])
    timeout_hours: int = int(config.get("timeout_hours", _DEFAULT_TIMEOUT_HOURS))

    if escalation:
        approvers = original_approvers or config.get("escalation_to", []) or approvers

    subject_id = f"{run_id}:{node_id}"
    timeout_at = datetime.now(UTC) + timedelta(hours=timeout_hours)

    # Create approval row (reuse existing approvals table)
    existing_approval = (
        await session.execute(
            select(Approval)
            .where(
                Approval.kind == kind,
                Approval.subject_id == subject_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    # Build PIPE4 rendering context. For signal-family gates this READS FROM THE
    # DB (signal_queue), since the scout/qualifier agents commit their real
    # effects there via MCP tool calls and only return summary text into
    # node_states. Content/draft gates fall back to the node_states path.
    pipe4_ctx = await _build_pipe4_context(kind, node_states, session=session, run_id=run_id)
    if kind == "content_draft":
        gate_error = _content_gate_error(
            candidate_id=pipe4_ctx.get("candidate_id"),
            deliverable_ids=pipe4_ctx.get("deliverable_ids"),
            deliverables=pipe4_ctx.get("deliverables"),
        )
        if gate_error is not None:
            return {
                "status": "failed",
                "error": gate_error,
                "output_summary": "",
                "cost_usd": 0.0,
            }

    if existing_approval is None:
        approval = Approval(
            kind=kind,
            subject_id=subject_id,
            status="pending",
            decision_payload={
                "run_id": run_id,
                "node_id": node_id,
                "pipeline_name": pipeline_name,
                "approvers": approvers,
                "timeout_at": timeout_at.isoformat(),
                "escalation": escalation,
            },
            pipe4_context={
                "pipeline_run_id": run_id,
                "pipeline_name": pipeline_name,
                "node_id": node_id,
                "node_label": node_label,
                "context": pipe4_ctx,
            },
        )
        session.add(approval)
        await session.flush()
        approval_id = approval.id
    else:
        approval_id = existing_approval.id

    # Reuse the pipe4_ctx as dm_context for Slack DMs
    dm_context: dict[str, Any] = pipe4_ctx

    # Send Slack DMs
    slack_token = await _get_slack_token(session)
    delivery_log: list[dict[str, Any]] = []

    if slack_token:
        app_base_url = settings.app_base_url.rstrip("/")
        # TEST/STAGING override: route DMs to a single person instead of the real approvers,
        # so testing doesn't ping the configured reviewers. Channel post is unaffected.
        notify_override = settings.approval_notify_override.strip()
        notify_emails = [notify_override] if notify_override else approvers
        for email in notify_emails:
            entry = await _send_approval_dm(
                email=email,
                token=slack_token,
                pipeline_name=pipeline_name,
                node_label=node_label,
                run_id=run_id,
                node_id=node_id,
                context=dm_context,
                app_base_url=app_base_url,
                escalation=escalation,
                original_approvers=original_approvers,
                timeout_hours=timeout_hours,
            )
            delivery_log.append(entry)
        # Post ONCE to the shared marketing channel (in addition to DMs) for marketing gates.
        channel_id = settings.marketing_campaigns_slack_channel.strip()
        if channel_id and kind in _MARKETING_CHANNEL_KINDS:
            delivery_log.append(
                await _post_approval_to_channel(
                    channel_id=channel_id,
                    token=slack_token,
                    pipeline_name=pipeline_name,
                    node_label=node_label,
                    run_id=run_id,
                    node_id=node_id,
                    context=dm_context,
                    app_base_url=app_base_url,
                )
            )
    else:
        # No Slack configured — log fallback for all approvers
        for email in approvers:
            delivery_log.append(
                {
                    "email": email,
                    "sent_at": datetime.now(UTC).isoformat(),
                    "channel": None,
                    "error": "Slack integration not configured",
                    "fallback": True,
                }
            )
        logger.info(
            "No Slack token available for approval DMs on run %s node %s; using in-app queue only",
            run_id,
            node_id,
        )

    # Schedule timeout job
    _schedule_timeout(run_id=run_id, node_id=node_id, timeout_at=timeout_at, config=config)

    return {
        "status": "suspended",
        "output_summary": f"Gate awaiting approval from {', '.join(approvers)}",
        "approval_id": approval_id,
        "delivery_log": delivery_log,
        "timeout_at": timeout_at.isoformat(),
        "cost_usd": 0.0,
    }


def _schedule_timeout(
    *,
    run_id: str,
    node_id: str,
    timeout_at: datetime,
    config: dict[str, Any],
) -> None:
    """Register an APScheduler one-shot job to fire on_timeout at timeout_at."""
    try:
        from artemis.pipelines.scheduler import get_pipeline_scheduler

        scheduler = get_pipeline_scheduler()
        if not scheduler.running:
            logger.warning(
                "Pipeline scheduler not running; timeout for run %s node %s will not fire",
                run_id,
                node_id,
            )
            return

        job_id = f"gate_timeout_{run_id}_{node_id}"
        on_timeout = config.get("on_timeout", "auto_approve")

        scheduler.add_job(
            _fire_gate_timeout,
            trigger="date",
            run_date=timeout_at,
            id=job_id,
            args=[run_id, node_id, on_timeout, config],
            replace_existing=True,
            max_instances=1,
        )
        logger.info("Scheduled gate timeout job %s at %s", job_id, timeout_at.isoformat())
    except Exception:
        logger.exception("Failed to schedule gate timeout for run %s node %s", run_id, node_id)


async def _fire_gate_timeout(
    run_id: str,
    node_id: str,
    on_timeout: str,
    config: dict[str, Any],
) -> None:
    """APScheduler callback: fire on_timeout for a human gate."""
    import artemis.db as _db

    logger.info("Gate timeout fired: run=%s node=%s on_timeout=%s", run_id, node_id, on_timeout)

    async with _db.SessionLocal() as session:
        try:
            from artemis.pipelines import repository as repo
            from artemis.pipelines.audit import audit_log

            run = await repo.get_pipeline_run(session, run_id)
            if run.status not in ("awaiting_approval", "running"):
                logger.info("Run %s no longer awaiting approval; skip timeout", run_id)
                return

            node_states: dict[str, Any] = dict(run.node_states or {})
            node_state = dict(node_states.get(node_id, {}))

            # Already resolved?
            if node_state.get("decision"):
                return

            started_at_str = node_state.get("started_at")
            elapsed = None
            if started_at_str:
                try:
                    started = datetime.fromisoformat(started_at_str)
                    elapsed = (datetime.now(UTC) - started).total_seconds()
                except Exception:
                    pass

            timeout_hours = config.get("timeout_hours", _DEFAULT_TIMEOUT_HOURS)

            if on_timeout == "escalate":
                escalation_to: list[str] = config.get("escalation_to") or []
                original_approvers: list[str] = config.get("approvers", [])

                await audit_log(
                    session,
                    {
                        "kind": "escalation_sent",
                        "pipeline_run_id": run_id,
                        "node_id": node_id,
                        "decision": "escalated",
                        "reason": f"timeout_after_{timeout_hours}h",
                        "configured_approvers": original_approvers,
                        "escalation_to": escalation_to,
                        "elapsed_seconds": elapsed,
                    },
                )

                # Trigger re-send to escalation approvers
                if escalation_to:
                    # Re-send via a new resume-like call
                    # Re-read node_states after audit_log (which modifies run.node_states in place)
                    node_states = dict(run.node_states or {})
                    node_state = dict(node_states.get(node_id, {}))
                    node_state["escalated"] = True
                    node_state["escalated_at"] = datetime.now(UTC).isoformat()
                    node_states[node_id] = node_state
                    run.node_states = node_states
                    await session.flush()

                    # Schedule second-level timeout
                    escalation_timeout_at = datetime.now(UTC) + timedelta(hours=timeout_hours)
                    escalation_config = {
                        **config,
                        "on_timeout": config.get(
                            "escalation_fallback_on_timeout", "escalation_timeout"
                        ),
                    }
                    _schedule_timeout(
                        run_id=run_id,
                        node_id=node_id,
                        timeout_at=escalation_timeout_at,
                        config=escalation_config,
                    )

                    # Send DMs to escalation approvers
                    pipeline = await repo.get_pipeline(session, run.pipeline_id)
                    pipeline_nodes = pipeline.nodes or []
                    this_node: dict[str, Any] = next(
                        (n for n in pipeline_nodes if n.get("id") == node_id), {}
                    )

                    slack_token = await _get_slack_token(session)
                    if slack_token:
                        for email in escalation_to:
                            await _send_approval_dm(
                                email=email,
                                token=slack_token,
                                pipeline_name=pipeline.name,
                                node_label=this_node.get("label", node_id),
                                run_id=run_id,
                                node_id=node_id,
                                context={
                                    "approval_kind": config.get("approval_kind", "signal_brief")
                                },
                                escalation=True,
                                original_approvers=original_approvers,
                                timeout_hours=timeout_hours,
                            )
                else:
                    # No escalation_to configured; treat as escalation_timeout
                    if await _apply_timeout_pipe4_decision(
                        session,
                        run_id=run_id,
                        node_id=node_id,
                        decision="rejected",
                        decided_by="system:timeout",
                        reason=f"escalation_timeout_after_{timeout_hours}h",
                        timeout_action="escalation_timeout",
                    ):
                        return

                    node_state["decision"] = "escalation_timeout"
                    node_states[node_id] = node_state
                    run.node_states = node_states
                    run.status = "failed"
                    run.error_message = (
                        f"Gate {node_id}: escalation timeout — no escalation approvers configured"
                    )
                    run.completed_at = datetime.now(UTC)
                    await session.flush()

            elif on_timeout == "escalation_timeout":
                # Second-level timeout; mark as needs-manual-resolution
                await audit_log(
                    session,
                    {
                        "kind": "gate_auto_decision",
                        "pipeline_run_id": run_id,
                        "node_id": node_id,
                        "decision": "escalation_timeout",
                        "reason": f"escalation_timeout_after_{timeout_hours}h",
                        "configured_approvers": config.get("escalation_to", []),
                        "elapsed_seconds": elapsed,
                    },
                )

                if await _apply_timeout_pipe4_decision(
                    session,
                    run_id=run_id,
                    node_id=node_id,
                    decision="rejected",
                    decided_by="system:timeout",
                    reason=f"escalation_timeout_after_{timeout_hours}h",
                    timeout_action="escalation_timeout",
                ):
                    return

                node_state["decision"] = "escalation_timeout"
                node_states[node_id] = node_state
                run.node_states = node_states
                run.status = "failed"
                run.error_message = (
                    f"Gate {node_id}: escalation also timed out — needs manual resolution"
                )
                run.completed_at = datetime.now(UTC)
                await session.flush()

            else:
                # auto_approve or auto_reject
                auto_decision = "auto_approved" if on_timeout == "auto_approve" else "auto_rejected"

                await audit_log(
                    session,
                    {
                        "kind": "gate_auto_decision",
                        "pipeline_run_id": run_id,
                        "node_id": node_id,
                        "decision": auto_decision,
                        "reason": f"timeout_after_{timeout_hours}h",
                        "configured_approvers": config.get("approvers", []),
                        "elapsed_seconds": elapsed,
                    },
                )

                if await _apply_timeout_pipe4_decision(
                    session,
                    run_id=run_id,
                    node_id=node_id,
                    decision="approved" if on_timeout == "auto_approve" else "rejected",
                    decided_by="system:timeout",
                    reason=f"timeout_after_{timeout_hours}h",
                    timeout_action=auto_decision,
                ):
                    return

                # Re-read node_states after audit_log (which modifies run.node_states in place)
                node_states = dict(run.node_states or {})
                node_state = dict(node_states.get(node_id, {}))
                node_state["decision"] = auto_decision
                node_state["decided_at"] = datetime.now(UTC).isoformat()
                node_state["decided_by"] = "system:timeout"
                node_states[node_id] = node_state
                run.node_states = node_states
                await session.flush()

                # Resume pipeline execution from next node
                from artemis.pipelines.executor import PipelineExecutor

                await session.commit()
                async with _db.SessionLocal() as resume_session:
                    executor = PipelineExecutor(run_id)
                    await executor.run(resume_session)
                    await resume_session.commit()
                return

            await session.commit()
        except Exception:
            logger.exception("Gate timeout handler failed for run %s node %s", run_id, node_id)
            await session.rollback()


async def _apply_timeout_pipe4_decision(
    session: AsyncSession,
    *,
    run_id: str,
    node_id: str,
    decision: str,
    decided_by: str,
    reason: str,
    timeout_action: str,
) -> bool:
    """Close one PIPE4 approval through the shared decision processor on timeout."""
    from artemis.marketing.routes.approvals import (
        _decide_pipe4_gate_approval,
        find_pending_pipe4_approval,
    )

    approval = await find_pending_pipe4_approval(session, subject_id=f"{run_id}:{node_id}")
    if approval is None:
        return False

    decision_payload = (
        dict(approval.decision_payload) if isinstance(approval.decision_payload, dict) else {}
    )
    decision_payload.update(
        {
            "decision": decision,
            "decided_by": decided_by,
            "decided_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "source": "timeout",
            "timeout_action": timeout_action,
        }
    )
    await _decide_pipe4_gate_approval(
        session,
        approval=approval,
        decision=decision,
        decided_by=decided_by,
        decision_payload=decision_payload,
        dispatch_mode="inline",
    )
    return True
