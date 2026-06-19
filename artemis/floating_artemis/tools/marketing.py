"""Marketing OS tools for Floating Artemis.

Authority layers:
  1: list_signals, get_signal, list_candidates, list_scout_runs,
     get_active_rulesets, list_content_assets
  2: qualify_signal, snooze_signal, fire_scout
  3: approve_signal, reject_signal, assemble_brief,
     submit_draft_for_review, decide_approval,
     propose_ruleset_change, link_content_asset

[surface:marketing-os] — all tools in this module are gated by the
marketing-os surface availability.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.context import floating_session_id_var

_SURFACE = "[surface:marketing-os]"
_CALLIE_CAMPAIGN_SIGNALS_CHANNEL = "C0B9CHVC7KQ"


def _truncate(text: str | None, *, limit: int = 140) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _age_text(when: datetime | None) -> str:
    if when is None:
        return "unknown"
    current = datetime.now(UTC)
    stamp = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
    delta = current - stamp.astimezone(UTC)
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 3600:
        minutes = max(seconds // 60, 1)
        return f"{minutes}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _parse_id_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value]
    else:
        items = []
    ordered: list[str] = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def _default_callie_channel_ids() -> tuple[str, ...]:
    from artemis.config import settings

    ordered = [_CALLIE_CAMPAIGN_SIGNALS_CHANNEL]
    marketing_campaigns_channel = settings.marketing_campaigns_slack_channel.strip()
    if marketing_campaigns_channel and marketing_campaigns_channel not in ordered:
        ordered.append(marketing_campaigns_channel)
    return tuple(ordered)


def _resolve_callie_channel(requested: object, allowed_channel_ids: tuple[str, ...]) -> str | None:
    if not allowed_channel_ids:
        return None

    if requested is None or str(requested).strip() == "":
        return allowed_channel_ids[0]

    raw = str(requested).strip()
    if raw in allowed_channel_ids:
        return raw

    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "campaign_signals": _CALLIE_CAMPAIGN_SIGNALS_CHANNEL,
    }
    marketing_campaigns_channel = next(
        (channel for channel in allowed_channel_ids if channel != _CALLIE_CAMPAIGN_SIGNALS_CHANNEL),
        None,
    )
    if marketing_campaigns_channel is not None:
        alias_map["marketing_campaigns"] = marketing_campaigns_channel

    resolved = alias_map.get(normalized)
    if resolved in allowed_channel_ids:
        return resolved
    return None


async def _resolve_active_writing_profile_id(session: Any) -> int | None:
    from artemis.marketing.routes.claims import _resolve_profile_id

    return await _resolve_profile_id(session, None, required=False)


async def _list_candidate_related_pipeline_runs(session: Any, candidate_id: int) -> list[Any]:
    from sqlalchemy import select

    from artemis.marketing import repository as marketing_repo
    from artemis.pipelines import repository as pipeline_repo
    from artemis.pipelines.models import PipelineRun

    signal_rows = await marketing_repo.get_candidate_signal_rows(session, candidate_id)
    source_run_ids = {row.pipeline_run_id for row in signal_rows if row.pipeline_run_id}
    runs_by_id: dict[str, Any] = {}
    pipeline_ids: set[str] = set()

    for run_id in source_run_ids:
        try:
            run = await pipeline_repo.get_pipeline_run(session, run_id)
        except ValueError:
            continue
        runs_by_id[run.id] = run
        pipeline_ids.add(run.pipeline_id)

    result = await session.execute(
        select(PipelineRun.pipeline_id)
        .where(PipelineRun.target_candidate_id == candidate_id)
        .distinct()
    )
    pipeline_ids.update(str(row[0]) for row in result.all() if row[0])

    for pipeline_id in pipeline_ids:
        pipeline_runs = await pipeline_repo.list_pipeline_runs(session, pipeline_id, limit=20)
        for run in pipeline_runs:
            if run.id in source_run_ids or run.target_candidate_id == candidate_id:
                runs_by_id.setdefault(run.id, run)

    return sorted(
        runs_by_id.values(),
        key=lambda run: run.created_at or run.started_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


async def _resolve_callie_slack_posting(
    session: Any,
) -> tuple[str, tuple[str, ...]] | tuple[None, None]:
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import decrypt_credentials

    integrations = await repo.list_active(session, provider="slack")
    callie_row = next(
        (row for row in integrations if str(getattr(row, "agent_id", "")) == "callie"), None
    )
    if callie_row is None:
        return None, None

    creds_raw = decrypt_credentials(bytes(callie_row.encrypted_credentials))
    creds = creds_raw if isinstance(creds_raw, dict) else {}
    token = str(creds.get("access_token") or creds.get("bot_token") or creds.get("token") or "")
    if not token:
        return None, None

    metadata_raw = getattr(callie_row, "metadata_", {}) or {}
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    allowed_channel_ids = _parse_id_list(
        metadata.get("allowed_channel_ids") or creds.get("allowed_channel_ids")
    )
    if not allowed_channel_ids:
        allowed_channel_ids = _default_callie_channel_ids()

    return token, allowed_channel_ids


# ── Implementations ───────────────────────────────────────────────────────────


async def _get_message_compass(inp: dict[str, Any]) -> str:  # noqa: ARG001
    try:
        import artemis.db as _db
        from artemis.writing_rules import repository as wr_repo

        async with _db.SessionLocal() as session:
            profile_id = await _resolve_active_writing_profile_id(session)
            if profile_id is None:
                return "No active writing profile. Message Compass is unavailable."
            source = await wr_repo.get_source_by_profile_key(
                session, profile_id, "01_MESSAGE_COMPASS"
            )
        if source is None:
            return "Message Compass not found for the active writing profile."
        return source.normalized_content or source.original_content
    except Exception as exc:
        return f"get_message_compass failed: {exc}"


async def _search_claims_register(inp: dict[str, Any]) -> str:
    query = str(inp.get("query", "")).strip().lower()
    tier_raw = inp.get("tier")
    tier: int | None = None
    if isinstance(tier_raw, int):
        tier = tier_raw
    elif isinstance(tier_raw, str) and tier_raw.strip():
        tier = int(tier_raw)
    limit = int(inp.get("limit", 10))
    try:
        import artemis.db as _db
        from artemis.writing_rules import repository as wr_repo

        async with _db.SessionLocal() as session:
            profile_id = await _resolve_active_writing_profile_id(session)
            if profile_id is None:
                return "No active writing profile. Claims Register is unavailable."
            claims = await wr_repo.list_claims(session, profile_id, status="approved")

        filtered: list[Any] = []
        for claim in claims:
            if tier is not None and claim.tier != tier:
                continue
            if query:
                haystack = " ".join(
                    [
                        str(claim.claim_code or ""),
                        str(claim.category or ""),
                        str(claim.approved_phrasing or ""),
                        str(claim.notes or ""),
                    ]
                ).lower()
                if query not in haystack:
                    continue
            filtered.append(claim)

        if not filtered:
            details: list[str] = []
            if query:
                details.append(f"query={query!r}")
            if tier is not None:
                details.append(f"tier={tier}")
            suffix = f" ({', '.join(details)})" if details else ""
            return f"No approved claims found{suffix}."

        lines = []
        for claim in filtered[:limit]:
            tier_text = f"Tier {claim.tier}" if claim.tier is not None else "Tier ?"
            phrasing = _truncate(claim.approved_phrasing, limit=160)
            lines.append(f"{claim.claim_code} | {tier_text} | {phrasing}")
        return "\n".join(lines)
    except Exception as exc:
        return f"search_claims_register failed: {exc}"


async def _find_by_keyword(inp: dict[str, Any]) -> str:
    query = str(inp.get("query", "")).strip()
    if not query:
        return "Error: query is required"
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            result = await repo.find_signals_and_candidates_by_keyword(session, query, limit=limit)

        lines: list[str] = []
        if result.signals:
            lines.append("signals:")
            for sig_id, urgency_tier, signal_status, headline in result.signals:
                lines.append(f"  #{sig_id} [{urgency_tier}] [{signal_status}] {headline}")
        if result.candidates:
            lines.append("campaigns:")
            for cand_id, name, decision_state in result.candidates:
                lines.append(f"  #{cand_id} [{decision_state}] {name}")
        if not lines:
            return f"No signals or campaigns matched {query!r}."
        return "\n".join(lines)
    except Exception as exc:
        return f"find_by_keyword failed: {exc}"


async def _get_campaign_performance(inp: dict[str, Any]) -> str:
    candidate_id_raw = inp.get("candidate_id")
    campaign_family = inp.get("campaign_family")
    limit = int(inp.get("limit", 10))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as marketing_repo

        async with _db.SessionLocal() as session:
            if candidate_id_raw not in (None, ""):
                try:
                    candidate_id = (
                        candidate_id_raw
                        if isinstance(candidate_id_raw, int)
                        else int(str(candidate_id_raw))
                    )
                    candidates = [await marketing_repo.get_candidate(session, candidate_id)]
                except ValueError:
                    return f"No campaign candidate id={candidate_id_raw}."
            else:
                candidates = await marketing_repo.list_candidates(
                    session,
                    campaign_family=str(campaign_family) if campaign_family else None,
                    limit=limit,
                )
            if not candidates:
                return "No campaign candidates found."

            lines = [
                "Raw campaign reads only — status, age, signal counts, and run state. Not aggregated KPIs."
            ]
            for candidate in candidates:
                signal_links = await marketing_repo.get_candidate_signals(session, candidate.id)
                brief = await marketing_repo.get_campaign_brief(session, candidate.id)
                runs = await _list_candidate_related_pipeline_runs(session, candidate.id)
                latest_run = runs[0] if runs else None
                run_summary = "none"
                if runs:
                    counts: dict[str, int] = {}
                    for run in runs:
                        counts[run.status] = counts.get(run.status, 0) + 1
                    run_summary = ", ".join(
                        f"{status} x{count}" for status, count in sorted(counts.items())
                    )
                lines.append(
                    " | ".join(
                        [
                            f"campaign #{candidate.id}",
                            candidate.campaign_family,
                            f"decision={candidate.decision_state}",
                            f"workspace={candidate.workspace_state}",
                            f"age={_age_text(getattr(candidate, 'created_at', None))}",
                            f"signals={len(signal_links)}",
                            f"brief={'yes' if brief is not None else 'no'}",
                            f"latest_run={latest_run.status if latest_run is not None else 'none'}",
                            f"run_age={_age_text(latest_run.created_at if latest_run is not None else None)}",
                            f"runs={run_summary}",
                        ]
                    )
                )
        return "\n".join(lines)
    except Exception as exc:
        return f"get_campaign_performance failed: {exc}"


async def _post_analyst_message(inp: dict[str, Any]) -> str:
    text = str(inp.get("text", "")).strip()
    thread_ts = str(inp["thread_ts"]) if inp.get("thread_ts") else None
    if not text:
        return "Error: text is required"
    try:
        import artemis.db as _db
        from artemis.integrations.slack.client import SlackClient
        from artemis.writing_rules import lint_agent_text

        async with _db.SessionLocal() as session:
            token, allowed_channel_ids = await _resolve_callie_slack_posting(session)
        if not token or not allowed_channel_ids:
            return "Callie's Slack integration is not configured for analyst posting."

        resolved_channel = _resolve_callie_channel(
            inp.get("channel") or inp.get("channel_id"), allowed_channel_ids
        )
        if resolved_channel is None:
            allowed = ", ".join(allowed_channel_ids)
            return f"Error: channel must be one of Callie's configured channels: {allowed}"

        outbound_text = lint_agent_text(text)
        if not outbound_text.strip():
            return "Error: text is empty after linting"

        result = await SlackClient(token).post_message(
            resolved_channel,
            outbound_text,
            thread_ts=thread_ts,
        )
        ts = str(result.get("ts", ""))
        suffix = f" (ts={ts})" if ts else ""
        return f"Posted analyst message to {resolved_channel} as Callie{suffix}."
    except Exception as exc:
        return f"post_analyst_message failed: {exc}"


async def _list_signals(inp: dict[str, Any]) -> str:
    status = inp.get("status", "pending_qualification")
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            signals = await repo.list_signals(session, status=status, limit=limit)
        if not signals:
            return f"No signals with status='{status}'."
        lines = [
            f"{s.id}: [{s.signal_status}] {s.headline}"
            f" | district={s.district_id or ''}"
            f" state={s.state or ''}"
            f" url={s.source_url or ''}"
            for s in signals
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_signals failed: {exc}"


async def _get_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            signal = await repo.get_signal(session, int(signal_id))
        prov = signal.provenance or {}
        why_flagged = prov.get("why_flagged") if isinstance(prov, dict) else None
        return json.dumps(
            {
                "id": signal.id,
                "signal_status": signal.signal_status,
                "headline": signal.headline,
                "campaign_family": signal.campaign_family,
                "source_url": signal.source_url,
                "district_id": signal.district_id,
                "resolved_district_id": signal.resolved_district_id,
                "state": signal.state,
                "urgency_tier": signal.urgency_tier,
                "reason_codes": signal.reason_codes or [],
                "why_flagged": why_flagged,
            }
        )
    except Exception as exc:
        return f"get_signal failed: {exc}"


async def _qualify_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    qualification = inp.get("qualification")
    if qualification is None and "score" in inp:
        qualification = {"fitScore": inp.get("score")}
    if qualification is None:
        qualification = {}
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.save_signal_qualification(session, int(signal_id), qualification)
            await session.commit()
        return f"Signal {signal_id} qualified."
    except Exception as exc:
        return f"qualify_signal failed: {exc}"


async def _approve_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_signal(session, int(signal_id), signal_status="approved")
            await session.commit()

        # MC5: fire-and-forget memory carryover (failure must not break approval)
        import asyncio as _asyncio

        from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

        fa_session_id = str(
            inp.get("session_id")
            or inp.get("fa_session_id")
            or floating_session_id_var.get()
            or "unknown"
        )
        user_directive = str(inp.get("directive") or inp.get("user_directive") or "")
        _asyncio.create_task(
            write_fa_marketing_approval_observation(
                signal_id=int(signal_id),
                new_status="approved",
                fa_session_id=fa_session_id,
                user_directive=user_directive or None,
            )
        )

        return f"Signal {signal_id} approved."
    except Exception as exc:
        return f"approve_signal failed: {exc}"


async def _reject_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    reason = inp.get("reason", "")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo
        from artemis.marketing.state_machine import SignalState, transition

        async with _db.SessionLocal() as session:
            if reason and reason.strip():
                await repo.update_signal(
                    session, int(signal_id), rejected_reason=reason
                )
            updated = await transition(
                session, "signal", int(signal_id), SignalState.REJECTED_AT_GATE_1
            )
            await session.commit()
            await session.refresh(updated)

            # Engagement learning: only when a non-empty reason was given.
            # Reason-less rejects are ambiguous — do not down-weight anything.
            if reason and reason.strip():
                from artemis.marketing.callie_push import record_signal_engagement

                reason_codes = [
                    rc.get("code", "")
                    for rc in (updated.reason_codes or [])
                    if isinstance(rc, dict)
                ]
                await record_signal_engagement(
                    session,
                    signal_id=updated.id,
                    outcome="rejected",
                    reason_codes=[c for c in reason_codes if c],
                    campaign_family=updated.campaign_family,
                    district_type=None,
                )
                await session.commit()

        return f"Signal {signal_id} rejected."
    except Exception as exc:
        return f"reject_signal failed: {exc}"


async def _snooze_signal(inp: dict[str, Any]) -> str:
    signal_id = inp.get("signal_id")
    until = inp.get("until")
    if not signal_id:
        return "Error: signal_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_signal(
                session, int(signal_id), signal_status="snoozed", snoozed_until=until
            )
            await session.commit()
        msg = f"Signal {signal_id} snoozed"
        if until:
            msg += f" until {until}"
        return msg + "."
    except Exception as exc:
        return f"snooze_signal failed: {exc}"


async def _list_candidates(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            candidates = await repo.list_candidates(session, limit=limit)
        if not candidates:
            return "No campaign candidates."
        lines = [
            f"{c.id}: {c.campaign_family or 'unknown'} — {c.decision_state}" for c in candidates
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_candidates failed: {exc}"


async def _assemble_brief(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    brief_content = inp.get("content", {})
    if not candidate_id:
        return "Error: candidate_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            brief = await repo.create_campaign_brief(
                session, candidate_id=int(candidate_id), content=brief_content
            )
            await session.commit()
        return f"Brief assembled for candidate {candidate_id}: brief_id={brief.id}"
    except Exception as exc:
        return f"assemble_brief failed: {exc}"


async def _submit_draft_for_review(inp: dict[str, Any]) -> str:
    deliverable_id = inp.get("deliverable_id")
    if not deliverable_id:
        return "Error: deliverable_id is required"
    try:
        import artemis.db as _db
        from artemis.marketing.writing_studio import invoke as ws_invoke

        async with _db.SessionLocal() as session:
            approval = await ws_invoke.submit_draft_for_review(session, int(deliverable_id))
        return f"Deliverable {deliverable_id} submitted for review: approval_id={approval.id}"
    except Exception as exc:
        return f"submit_draft_for_review failed: {exc}"


async def _decide_approval(inp: dict[str, Any]) -> str:
    approval_id = inp.get("approval_id")
    decision = inp.get("decision")
    decided_by = inp.get("decided_by", "artemis")
    if not approval_id or decision not in ("approve", "reject"):
        return "Error: approval_id and decision (approve|reject) are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.decide_approval(
                session, int(approval_id), decision=decision, decided_by=decided_by
            )
            await session.commit()
        return f"Approval {approval_id}: decision={decision}"
    except Exception as exc:
        return f"decide_approval failed: {exc}"


async def _list_scout_runs(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            runs = await repo.list_scout_runs(session, limit=limit)
        if not runs:
            return "No scout runs."
        lines = [f"{r.id}: {r.scout_type} — {r.status} @ {r.started_at}" for r in runs]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_scout_runs failed: {exc}"


async def _fire_scout(inp: dict[str, Any]) -> str:
    scout_type = inp.get("scout_type") or inp.get("scout_id")
    if not scout_type:
        return "Error: scout_type is required"
    try:
        import uuid

        import artemis.db as _db
        from artemis.marketing import repository as repo

        run_id = f"scout_run_{uuid.uuid4().hex[:8]}"
        async with _db.SessionLocal() as session:
            run = await repo.create_scout_run(session, run_id=run_id, scout_type=scout_type)
            await session.commit()
        return f"Scout {scout_type} fired: run_id={run.id}"
    except Exception as exc:
        return f"fire_scout failed: {exc}"


async def _get_active_rulesets(inp: dict[str, Any]) -> str:  # noqa: ARG001
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            rulesets = await repo.list_ruleset_versions(session)
        if not rulesets:
            return "No rulesets."
        lines = [f"{r.id}: {r.family} v{r.version_tag}" for r in rulesets]
        return "\n".join(lines)
    except Exception as exc:
        return f"get_active_rulesets failed: {exc}"


async def _propose_ruleset_change(inp: dict[str, Any]) -> str:
    ruleset_id = inp.get("ruleset_id")
    changes = inp.get("changes", {})
    if not ruleset_id:
        return "Error: ruleset_id is required"
    proposal = {
        "type": "ruleset_change_proposal",
        "ruleset_id": ruleset_id,
        "changes": changes,
    }
    try:
        from sqlalchemy import select

        import artemis.db as _db
        from artemis.marketing import repository as repo
        from artemis.marketing.models import Ruleset

        async with _db.SessionLocal() as session:
            existing = await session.execute(
                select(Ruleset.id).where(Ruleset.id == int(ruleset_id))
            )
            if existing.scalar_one_or_none() is None:
                return f"Error: ruleset_id {ruleset_id} not found"
            row = await repo.create_approval(
                session,
                kind="ruleset_change",
                subject_id=str(ruleset_id),
                decision_payload=proposal,
            )
            await session.commit()
        return (
            f"Ruleset change proposal saved: approval_id={row.id}\n{json.dumps(proposal, indent=2)}"
        )
    except Exception as exc:
        return f"propose_ruleset_change failed: {exc}"


async def _list_content_assets(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    status = inp.get("status")
    asset_type = inp.get("asset_type") or inp.get("assetType")
    campaign_family = inp.get("campaign_family")
    try:
        from sqlalchemy import select

        import artemis.db as _db
        from artemis.marketing.models import ContentAsset

        async with _db.SessionLocal() as session:
            stmt = select(ContentAsset)
            if status:
                stmt = stmt.where(ContentAsset.status == status)
            if asset_type:
                stmt = stmt.where(ContentAsset.asset_type == asset_type)
            if campaign_family:
                stmt = stmt.where(
                    ContentAsset.asset_metadata["campaign_family"].as_string()
                    == str(campaign_family)
                )
            stmt = stmt.order_by(ContentAsset.id.desc()).limit(limit)
            result = await session.execute(stmt)
            assets = list(result.scalars().all())
        if not assets:
            return "No content assets found."
        lines = [
            f"{asset.id}: [{asset.status}] {asset.asset_type} — {asset.summary or '(no summary)'}"
            for asset in assets
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_content_assets failed: {exc}"


async def _link_content_asset(inp: dict[str, Any]) -> str:
    candidate_id = inp.get("candidate_id")
    asset_id = inp.get("asset_id")
    if not candidate_id or not asset_id:
        return "Error: candidate_id and asset_id are required"
    try:
        import artemis.db as _db
        from artemis.marketing import repository as repo

        async with _db.SessionLocal() as session:
            await repo.link_content_asset_to_candidate(
                session,
                candidate_id=int(candidate_id),
                asset_id=int(asset_id),
                link_role=str(inp.get("role")) if inp.get("role") is not None else None,
            )
            await session.commit()
        return f"Asset {asset_id} linked to candidate {candidate_id}."
    except Exception as exc:
        return f"link_content_asset failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────

_s = _SURFACE  # shorthand

FIND_BY_KEYWORD = Tool(
    name="find_by_keyword",
    description=(
        f"Search signals and campaigns by keyword or bill number. "
        f"Matches signal_queue.headline and campaign_candidates.name "
        f"case-insensitively (substring). Returns matching signals "
        f"(id, urgency_tier, signal_status, headline) and campaigns "
        f"(id, name, decision_state). {_s} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword or bill number to search for (e.g. 'HB27').",
            },
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["query"],
    },
)

GET_MESSAGE_COMPASS = Tool(
    name="get_message_compass",
    description=f"Read the active Message Compass source of truth for marketing messaging. {_s} [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

SEARCH_CLAIMS_REGISTER = Tool(
    name="search_claims_register",
    description=f"Search approved claims from the active Claims Register by substring and optional tier. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tier": {"type": "integer"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
)

GET_CAMPAIGN_PERFORMANCE = Tool(
    name="get_campaign_performance",
    description=f"Summarize raw campaign status, age, signal volume, and linked pipeline run state. Not aggregated KPIs. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "candidate_id": {"type": "integer"},
            "campaign_family": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
)

LIST_SIGNALS = Tool(
    name="list_signals",
    description=f"List marketing signals from the signal queue. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "default": "pending_qualification"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
)

GET_SIGNAL = Tool(
    name="get_signal",
    description=f"Get a single marketing signal by ID. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"signal_id": {"type": "integer"}},
        "required": ["signal_id"],
    },
)

QUALIFY_SIGNAL = Tool(
    name="qualify_signal",
    description=f"Mark a signal as qualified (idempotent score update). {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "qualification": {"type": "object", "default": {}},
            "score": {
                "type": "number",
                "description": "Legacy shorthand; stored as qualification.fitScore when qualification is omitted.",
            },
        },
        "required": ["signal_id"],
    },
)

APPROVE_SIGNAL = Tool(
    name="approve_signal",
    description=f"Approve a signal (side-effect: status change + downstream triggers). {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "fa_session_id": {"type": "string"},
            "directive": {"type": "string"},
        },
        "required": ["signal_id"],
    },
)

REJECT_SIGNAL = Tool(
    name="reject_signal",
    description=f"Reject a signal. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["signal_id"],
    },
)

SNOOZE_SIGNAL = Tool(
    name="snooze_signal",
    description=f"Snooze a signal until a later time. {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "signal_id": {"type": "integer"},
            "until": {"type": "string", "description": "ISO datetime or description"},
        },
        "required": ["signal_id"],
    },
)

LIST_CANDIDATES = Tool(
    name="list_candidates",
    description=f"List campaign candidates. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

ASSEMBLE_BRIEF = Tool(
    name="assemble_brief",
    description=f"Assemble a campaign brief for a candidate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "candidate_id": {"type": "integer"},
            "content": {"type": "object", "default": {}},
        },
        "required": ["candidate_id"],
    },
)

SUBMIT_DRAFT_FOR_REVIEW = Tool(
    name="submit_draft_for_review",
    description=f"Submit a draft deliverable for review. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {"deliverable_id": {"type": "integer"}},
        "required": ["deliverable_id"],
    },
)

DECIDE_APPROVAL = Tool(
    name="decide_approval",
    description=f"Record an approve or reject decision for an approval gate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "integer"},
            "decision": {"type": "string", "enum": ["approve", "reject"]},
            "decided_by": {"type": "string", "default": "artemis"},
        },
        "required": ["approval_id", "decision"],
    },
)

LIST_SCOUT_RUNS = Tool(
    name="list_scout_runs",
    description=f"List recent scout runs. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

FIRE_SCOUT = Tool(
    name="fire_scout",
    description=f"Trigger a scout run immediately. {_s} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "scout_type": {"type": "string"},
            "scout_id": {
                "type": "string",
                "description": "Legacy alias for scout_type.",
            },
        },
        "required": [],
    },
)

GET_ACTIVE_RULESETS = Tool(
    name="get_active_rulesets",
    description=f"Get current active signal qualification rulesets. {_s} [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

PROPOSE_RULESET_CHANGE = Tool(
    name="propose_ruleset_change",
    description=f"Propose a change to a qualification ruleset. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "ruleset_id": {"type": "integer"},
            "changes": {"type": "object"},
        },
        "required": ["ruleset_id", "changes"],
    },
)

LIST_CONTENT_ASSETS = Tool(
    name="list_content_assets",
    description=f"List content assets in the library. {_s} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20},
            "status": {"type": "string"},
            "asset_type": {"type": "string"},
            "campaign_family": {"type": "string"},
        },
        "required": [],
    },
)

LINK_CONTENT_ASSET = Tool(
    name="link_content_asset",
    description=f"Link a content asset to a campaign candidate. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "candidate_id": {"type": "integer"},
            "asset_id": {"type": "integer"},
            "role": {"type": "string", "default": "reference"},
        },
        "required": ["candidate_id", "asset_id"],
    },
)

POST_ANALYST_MESSAGE = Tool(
    name="post_analyst_message",
    description=f"Post a synthesized analyst update to one of Callie's configured Slack channels using Callie's bot identity. Requires confirmation. {_s} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Configured Callie channel ID or alias (campaign_signals, marketing_campaigns). Defaults to campaign_signals.",
            },
            "channel_id": {
                "type": "string",
                "description": "Alias for channel.",
            },
            "text": {"type": "string"},
            "thread_ts": {"type": "string"},
        },
        "required": ["text"],
    },
)


def register_marketing_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all marketing tools into the provided registry."""
    registry.register(FIND_BY_KEYWORD, _find_by_keyword, layer=1)
    registry.register(GET_MESSAGE_COMPASS, _get_message_compass, layer=1)
    registry.register(SEARCH_CLAIMS_REGISTER, _search_claims_register, layer=1)
    registry.register(GET_CAMPAIGN_PERFORMANCE, _get_campaign_performance, layer=1)
    registry.register(LIST_SIGNALS, _list_signals, layer=1)
    registry.register(GET_SIGNAL, _get_signal, layer=1)
    registry.register(QUALIFY_SIGNAL, _qualify_signal, layer=2)
    registry.register(APPROVE_SIGNAL, _approve_signal, layer=3)
    registry.register(REJECT_SIGNAL, _reject_signal, layer=3)
    registry.register(SNOOZE_SIGNAL, _snooze_signal, layer=2)
    registry.register(LIST_CANDIDATES, _list_candidates, layer=1)
    registry.register(ASSEMBLE_BRIEF, _assemble_brief, layer=3)
    registry.register(SUBMIT_DRAFT_FOR_REVIEW, _submit_draft_for_review, layer=3)
    registry.register(DECIDE_APPROVAL, _decide_approval, layer=3)
    registry.register(LIST_SCOUT_RUNS, _list_scout_runs, layer=1)
    registry.register(FIRE_SCOUT, _fire_scout, layer=2)
    registry.register(GET_ACTIVE_RULESETS, _get_active_rulesets, layer=1)
    registry.register(PROPOSE_RULESET_CHANGE, _propose_ruleset_change, layer=3)
    registry.register(LIST_CONTENT_ASSETS, _list_content_assets, layer=1)
    registry.register(LINK_CONTENT_ASSET, _link_content_asset, layer=3)
    registry.register(POST_ANALYST_MESSAGE, _post_analyst_message, layer=3)
