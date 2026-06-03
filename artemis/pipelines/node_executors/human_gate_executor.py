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


async def _build_signal_gate_context_from_db(
    approval_kind: str,
    session: AsyncSession,
    run_id: str,
) -> dict[str, Any]:
    """Build a signal-gate card context from committed signal_queue rows.

    Reads ``signal_queue`` rows for this run whose ``signal_status`` is
    ``'qualified'`` and aggregates the five UI-contract fields (signal_count,
    reason_codes, districts, evidence_quote, brief_preview) from them. A run
    with zero qualified signals yields the clean empty context (no error).
    """
    from sqlalchemy import select

    from artemis.marketing.models import SignalQueue

    ctx: dict[str, Any] = {
        "approval_kind": approval_kind,
        "signal_count": 0,
        "reason_codes": [],
        "districts": [],
        "evidence_quote": None,
        "brief_preview": None,
        "draft_summary": None,
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
    districts: set[str] = set()
    for row in rows:
        raw_codes = row.reason_codes if isinstance(row.reason_codes, list) else []
        for rc in raw_codes:
            code = rc.get("code", "") if isinstance(rc, dict) else str(rc)
            if code:
                codes.add(str(code))
        geo = row.district_id or row.state
        if geo:
            districts.add(str(geo))
        if not ctx["evidence_quote"]:
            quote = _brief_field(row.qualification_json, "evidence_quote")
            if quote:
                ctx["evidence_quote"] = str(quote)[:_PREVIEW_MAX]

    ctx["reason_codes"] = sorted(codes)
    ctx["districts"] = sorted(districts)

    # Brief preview: prefer the top signal's brief preview, then its body.
    top = rows[0].qualification_json
    preview = _brief_field(top, "preview") or _brief_field(top, "body")
    if not preview:
        for row in rows[1:]:
            preview = _brief_field(row.qualification_json, "preview") or _brief_field(
                row.qualification_json, "body"
            )
            if preview:
                break
    if preview:
        ctx["brief_preview"] = str(preview)[:_PREVIEW_MAX]

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
        deliverable_cards.append(
            {
                "id": deliverable.id,
                "status": deliverable.status,
                "title": title,
                "campaignId": deliverable.campaign_id,
                "externalDraftId": metadata.get("externalDraftId") or deliverable.deliverable_id,
                "deliverableTypeSlug": metadata.get("deliverableTypeSlug")
                or metadata.get("deliverable_type_slug"),
                "draftPreview": preview,
                "updatedAt": (
                    deliverable.updated_at.isoformat() if deliverable.updated_at else None
                ),
            }
        )
        if first_preview is None and preview:
            first_preview = preview

    ctx["deliverables"] = deliverable_cards
    ctx["deliverable_ids"] = [card["id"] for card in deliverable_cards]
    ctx["deliverable_count"] = len(deliverable_cards)
    ctx["ready_deliverable_count"] = sum(
        1 for card in deliverable_cards if card["status"] == "draft_ready"
    )
    ctx["draft_summary"] = first_preview

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
    ctx["reason_codes"] = sorted(reason_codes)
    ctx["districts"] = districts
    ctx["district_label"] = districts[0] if districts else None

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
    """Return True if all upstream nodes for this gate have succeeded."""
    node_id: str = node.get("id", "")
    config: dict[str, Any] = node.get("config") or {}
    wait_for_all: bool = config.get("wait_for_all_upstream", True)

    upstream_ids = [e["source_node_id"] for e in all_edges if e.get("target_node_id") == node_id]

    if not upstream_ids:
        return True  # No upstream — fire immediately

    upstream_statuses = [node_states.get(uid, {}).get("status", "pending") for uid in upstream_ids]

    if wait_for_all:
        return all(s == "succeeded" for s in upstream_statuses)
    else:
        return any(s == "succeeded" for s in upstream_statuses)


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
                node_state["decision"] = "escalation_timeout"
                node_states[node_id] = node_state
                run.node_states = node_states
                run.status = "failed"
                run.error_message = (
                    f"Gate {node_id}: escalation also timed out — needs manual resolution"
                )
                run.completed_at = datetime.now(UTC)
                await session.flush()

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
