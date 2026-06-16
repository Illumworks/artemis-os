"""P2a morning-brief scheduler + Slack delivery."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as _db
from artemis.brief.generator import generate_brief
from artemis.config import settings
from artemis.integrations import repository as integration_repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.models import Integration
from artemis.integrations.slack.client import SlackClient
from artemis.marketing.writing_studio.review_escalation import send_stale_review_escalations
from artemis.proactivity import radar_repository
from artemis.proactivity import repository as repo
from artemis.proactivity.commitments import (
    send_commitment_followups,
    send_commitment_proposals_digest,
)
from artemis.proactivity.okr_checkin import (
    build_checkin_digest,
    build_kr_snapshot,
    build_okr_checkin_proposal,
    format_checkin_for_slack,
    gather_checkin_sources,
)
from artemis.proactivity.radar import format_radar_nudge, gather_radar_items
from artemis.proactivity.voice_render import render_brief_with_voice, render_checkin_with_voice
from artemis.writing_rules import lint_agent_text

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_MORNING_BRIEF_JOB_ID = "proactivity_morning_brief"
_OKR_CHECKIN_JOB_ID = "proactivity_okr_checkin"
_STALE_REVIEW_ESCALATION_JOB_ID = "proactivity_stale_review_escalation"
_COMMITMENTS_FOLLOWUP_JOB_ID = "proactivity_commitments_followup"
_COMMITMENTS_PROPOSALS_DIGEST_JOB_ID = "proactivity_commitments_proposals_digest"
_ARTEMIS_AGENT_ID = "artemis"
_OWNER_SLACK_ID_FALLBACK = "U09F3EPJXSQ"


def get_proactivity_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_proactivity_scheduler() -> None:
    """Register and start scheduled proactive jobs."""
    scheduler = get_proactivity_scheduler()
    _register_morning_brief_job(scheduler)
    _register_okr_checkin_job(scheduler)
    _register_stale_review_escalation_job(scheduler)
    _register_commitments_followup_job(scheduler)
    _register_commitments_proposals_digest_job(scheduler)
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "Proactivity scheduler started (morning brief cron=%r tz=%s, okr checkin cron=%r, "
            "review escalation cron=%r, commitments cron=%r, proposals digest cron=%r)",
            settings.morning_brief_cron,
            settings.morning_brief_tz,
            settings.okr_checkin_cron,
            settings.review_escalation_cron,
            settings.commitments_followup_cron,
            settings.commitments_proposals_digest_cron,
        )


def stop_proactivity_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Proactivity scheduler stopped")
    _scheduler = None


def _register_morning_brief_job(scheduler: AsyncIOScheduler) -> None:
    trigger = CronTrigger.from_crontab(
        settings.morning_brief_cron,
        timezone=settings.morning_brief_tz,
    )
    scheduler.add_job(
        _fire_morning_brief,
        trigger=trigger,
        id=_MORNING_BRIEF_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def _register_okr_checkin_job(scheduler: AsyncIOScheduler) -> None:
    """Register the Friday 4pm OKR check-in job.

    Uses the same timezone as the morning brief so both fire in Jon's local time.
    """
    trigger = CronTrigger.from_crontab(
        settings.okr_checkin_cron,
        timezone=settings.morning_brief_tz,
    )
    scheduler.add_job(
        _fire_okr_checkin,
        trigger=trigger,
        id=_OKR_CHECKIN_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def _register_stale_review_escalation_job(scheduler: AsyncIOScheduler) -> None:
    trigger = CronTrigger.from_crontab(
        settings.review_escalation_cron,
        timezone=settings.review_escalation_tz,
    )
    scheduler.add_job(
        _fire_stale_review_escalation,
        trigger=trigger,
        id=_STALE_REVIEW_ESCALATION_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def _register_commitments_followup_job(scheduler: AsyncIOScheduler) -> None:
    trigger = CronTrigger.from_crontab(
        settings.commitments_followup_cron,
        timezone=settings.commitments_followup_tz,
    )
    scheduler.add_job(
        _fire_commitments_followup,
        trigger=trigger,
        id=_COMMITMENTS_FOLLOWUP_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def _register_commitments_proposals_digest_job(scheduler: AsyncIOScheduler) -> None:
    """Register the daily proposals digest job (fires only when proposed items exist)."""
    trigger = CronTrigger.from_crontab(
        settings.commitments_proposals_digest_cron,
        timezone=settings.commitments_proposals_digest_tz,
    )
    scheduler.add_job(
        _fire_commitments_proposals_digest,
        trigger=trigger,
        id=_COMMITMENTS_PROPOSALS_DIGEST_JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


async def _fire_okr_checkin() -> None:
    """Gather OKR evidence, build a cited proposal, DM Jon, and leave a breadcrumb.

    SAFETY: This function NEVER writes any OKR.  It only gathers evidence and
    posts an informational proposal to Jon's Slack DM.  OKR writes happen only
    when Jon explicitly approves via the DM agent loop (update_okr_kr layer 3).

    Part A: After posting, a breadcrumb row is persisted keyed to the recipient
    with a KR snapshot and TTL (expires end of following Monday). handle_turn
    reads this breadcrumb to inject OKR-reconcile context into the next DM turn.
    """
    async with _db.SessionLocal() as session:
        delivery_id: int | None = None
        try:
            recipient_id = await _resolve_morning_brief_recipient(session)
            delivery_date = datetime.now(ZoneInfo(settings.morning_brief_tz)).date()

            delivery_row, created = await repo.reserve_okr_checkin_delivery(
                session,
                recipient_id=recipient_id,
                delivery_date=delivery_date,
            )
            delivery_id = delivery_row.id
            await session.commit()

            if not created:
                logger.info(
                    "OKR check-in already reserved for %s -> %s (status=%s); skipping",
                    delivery_date.isoformat(),
                    recipient_id,
                    delivery_row.status,
                )
                return

            # Gather evidence — this opens its own sessions internally.
            sources = await gather_checkin_sources(session)
            proposals = build_okr_checkin_proposal(sources)
            objectives = sources.get("objectives") or []

            # Build KR snapshot for breadcrumb + digest for the opener.
            kr_snapshot = build_kr_snapshot(objectives)
            digest = build_checkin_digest(sources, today=delivery_date)

            # Attempt voice rendering pass first; fall back to plain rendering on failure.
            voice_text = await render_checkin_with_voice(
                proposals,
                delivery_date,
                session_id=f"checkin-{delivery_date.isoformat()}",
                kr_snapshot=kr_snapshot,
                digest=digest,
            )
            if voice_text:
                slack_text = voice_text
            else:
                slack_text = format_checkin_for_slack(
                    proposals,
                    delivery_date=delivery_date,
                    objectives=objectives,
                    digest=digest,
                )

            token = await _get_slack_token_for_agent(session, agent_id=_ARTEMIS_AGENT_ID)
            if not token:
                raise RuntimeError("No active Slack token found for agent_id='artemis'")

            # Post the proposal to Jon's DM.  No OKR write happens here.
            await SlackClient(token=token).post_dm(user=recipient_id, text=slack_text)

            await repo.mark_okr_checkin_delivery_sent(
                session,
                delivery_id=delivery_id,
            )

            # Part A: leave a breadcrumb so handle_turn can inject OKR-reconcile context.
            # TTL: expires end of the following Monday (survives the weekend).
            now_utc = datetime.now(UTC)
            # delivery_date is a Friday; following Monday = + 3 days.
            following_monday = delivery_date + timedelta(days=3)
            expires_at = datetime(
                following_monday.year,
                following_monday.month,
                following_monday.day,
                23,
                59,
                59,
                tzinfo=UTC,
            )
            await repo.create_okr_checkin_breadcrumb(
                session,
                recipient_id=recipient_id,
                kr_snapshot=kr_snapshot,
                proposal_text=slack_text,
                expires_at=expires_at,
            )
            _ = now_utc  # used for reference only; SQLAlchemy server_default handles created_at

            await session.commit()
            logger.info(
                "OKR check-in proposal delivered to Slack user %s for %s (%d KR proposals, %d KRs snapped)",
                recipient_id,
                delivery_date.isoformat(),
                len(proposals),
                len(kr_snapshot),
            )
        except Exception as exc:
            logger.exception("OKR check-in delivery failed")
            await session.rollback()
            if delivery_id is not None:
                await repo.mark_okr_checkin_delivery_failed(
                    session,
                    delivery_id=delivery_id,
                    error=str(exc),
                )
                await session.commit()


async def _fire_morning_brief() -> None:
    """Generate and deliver the morning brief once per local calendar day."""
    async with _db.SessionLocal() as session:
        delivery_id: int | None = None
        try:
            recipient_id = await _resolve_morning_brief_recipient(session)
            delivery_date = datetime.now(ZoneInfo(settings.morning_brief_tz)).date()

            delivery_row, created = await repo.reserve_morning_brief_delivery(
                session,
                recipient_id=recipient_id,
                delivery_date=delivery_date,
            )
            delivery_id = delivery_row.id
            await session.commit()

            if not created:
                logger.info(
                    "Morning brief already reserved for %s -> %s (status=%s); skipping",
                    delivery_date.isoformat(),
                    recipient_id,
                    delivery_row.status,
                )
                return

            brief = await generate_brief(session)

            # ── Awaiting-reply radar (Lane R) ─────────────────────────────────
            # Gather Slack + Gmail items.  Failures are swallowed gracefully so
            # a missing user-token / Gmail credential never blocks the brief.
            radar_nudge = ""
            try:
                radar_items = await gather_radar_items(session)
                # Upsert into dedup ledger and filter dismissed / too-recent items.
                from datetime import UTC

                now_utc = datetime.now(UTC)
                due_items = []
                for item in radar_items:
                    row, _ = await radar_repository.upsert_surfaced(
                        session,
                        item_type=item.item_type,
                        item_key=item.item_key,
                        label=f"{item.sender} in {item.where}"[:120],
                        permalink=item.permalink,
                        now=now_utc,
                    )
                    if row.dismissed_at is None:
                        due_items.append(item)
                await session.flush()
                radar_nudge = format_radar_nudge(due_items)
            except Exception:
                logger.warning(
                    "Morning brief: radar gather failed — skipping radar section", exc_info=True
                )

            # Attempt voice rendering pass first; fall back to plain rendering on failure.
            voice_text = await render_brief_with_voice(
                brief,
                delivery_date,
                session_id=f"brief-{delivery_date.isoformat()}",
            )
            slack_text = voice_text or _format_brief_for_slack(brief, delivery_date=delivery_date)
            if radar_nudge:
                slack_text = slack_text + "\n\n" + radar_nudge
            token = await _get_slack_token_for_agent(session, agent_id=_ARTEMIS_AGENT_ID)
            if not token:
                raise RuntimeError("No active Slack token found for agent_id='artemis'")

            await SlackClient(token=token).post_dm(user=recipient_id, text=slack_text)
            await repo.mark_morning_brief_delivery_sent(
                session,
                delivery_id=delivery_id,
                snapshot_id=_coerce_snapshot_id(brief.get("_snapshotId")),
            )
            await session.commit()
            logger.info(
                "Morning brief delivered to Slack user %s for %s",
                recipient_id,
                delivery_date.isoformat(),
            )
        except Exception as exc:
            logger.exception("Morning brief delivery failed")
            await session.rollback()
            if delivery_id is not None:
                await repo.mark_morning_brief_delivery_failed(
                    session,
                    delivery_id=delivery_id,
                    error=str(exc),
                )
                await session.commit()


async def _fire_stale_review_escalation() -> None:
    """Find stale pending reviews and DM each reviewer once."""
    async with _db.SessionLocal() as session:
        summary = await send_stale_review_escalations(
            session,
            stale_after=timedelta(hours=settings.review_escalation_age_hours),
        )
    logger.info(
        "Stale review escalation sweep finished (checked=%d eligible=%d sent=%d failed=%d)",
        summary.checked,
        summary.eligible,
        summary.sent,
        summary.failed,
    )


async def _fire_commitments_followup() -> None:
    """Find due/open commitments and route deterministic follow-ups."""
    async with _db.SessionLocal() as session:
        summary = await send_commitment_followups(session)
    logger.info(
        "Commitments follow-up sweep finished (checked=%d eligible=%d sent=%d failed=%d)",
        summary.checked,
        summary.eligible,
        summary.sent,
        summary.failed,
    )


async def _fire_commitments_proposals_digest() -> None:
    """Post the daily proposals digest to Jon's DM if proposed items exist."""
    async with _db.SessionLocal() as session:
        try:
            summary = await send_commitment_proposals_digest(session)
        except Exception:
            logger.exception("Commitments proposals digest delivery failed")
            return
    if summary.sent:
        logger.info(
            "Commitments proposals digest delivered (%d items proposed)",
            summary.proposed_count,
        )
    elif summary.proposed_count == 0:
        logger.debug("Commitments proposals digest: no proposed items -- skipped")
    else:
        logger.info(
            "Commitments proposals digest skipped (unanswered digest already exists or dry_run=%s)",
            summary.dry_run,
        )


async def _get_slack_token_for_agent(
    session: AsyncSession,
    *,
    agent_id: str,
) -> str | None:
    """Return a Slack access token for the requested agent, with safe fallback."""
    result = await session.execute(
        select(Integration)
        .where(
            Integration.provider == "slack",
            Integration.status == "active",
            Integration.agent_id == agent_id,
        )
        .order_by(Integration.connected_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()

    if row is None:
        result = await session.execute(
            select(Integration)
            .where(
                Integration.provider == "slack",
                Integration.status == "active",
            )
            .order_by(Integration.connected_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    if row is None:
        return None

    creds = decrypt_credentials(bytes(row.encrypted_credentials))
    token = creds.get("bot_token") or creds.get("token") or creds.get("access_token")
    return str(token) if token else None


async def _resolve_morning_brief_recipient(session: AsyncSession) -> str:
    """Resolve Jon's Slack user id from provider config, env, then constant fallback."""
    stored = await integration_repo.get_provider_config(session, "slack") or {}
    authed_user_id = str(stored.get("authed_user_id") or "").strip()
    env_user_id = os.environ.get("SLACK_AUTHED_USER_ID", "").strip()
    recipient_id = authed_user_id or env_user_id or _OWNER_SLACK_ID_FALLBACK
    return recipient_id


def _format_brief_for_slack(brief: dict[str, Any], *, delivery_date: date) -> str:
    """Render the stored brief JSON into Slack-friendly plain text.

    Supports both the new trimmed schema (top_priorities, waiting_on_you, okr_at_risk)
    and the old schema (priorities, next_actions, highlights, risks, okr_status) for
    backward-compat with any snapshots persisted before the schema change.
    """
    date_label = (
        f"{delivery_date.strftime('%A')}, {delivery_date.strftime('%B')} {delivery_date.day}"
    )
    lines: list[str] = [f"*Morning brief for {date_label}*"]

    # Summary (1-2 sentences, always first)
    summary = _clean_string(brief.get("summary"))
    if summary:
        lines.extend(["", summary])

    # Top priorities (new schema: top_priorities; old schema fallback: priorities + next_actions)
    top_priorities = brief.get("top_priorities") or []
    old_priorities = brief.get("priorities") or []
    priority_items = top_priorities if top_priorities else old_priorities
    if isinstance(priority_items, list) and priority_items:
        lines.extend(["", "*Top priorities*"])
        for item in priority_items[:3]:
            if not isinstance(item, dict):
                continue
            label = _clean_string(item.get("item") or item.get("action") or "")
            rationale = _clean_string(item.get("rationale"))
            if not label:
                continue
            extras = [segment for segment in [rationale] if segment]
            lines.append("- " + label + (f": {'; '.join(extras)}" if extras else ""))

    # Waiting on you (new schema: waiting_on_you)
    waiting = brief.get("waiting_on_you") or []
    if isinstance(waiting, list) and waiting:
        lines.extend(["", "*Waiting on you*"])
        for item in waiting[:8]:
            if not isinstance(item, dict):
                continue
            who = _clean_string(item.get("who"))
            context = _clean_string(item.get("context"))
            if not who:
                continue
            lines.append("- " + who + (f": {context}" if context else ""))

    # OKR at risk (new schema: okr_at_risk; old schema fallback: okr_status)
    okr_at_risk = _clean_string(brief.get("okr_at_risk") or brief.get("okr_status"))
    if okr_at_risk:
        lines.extend(["", "*OKRs at risk*", okr_at_risk])

    if len(lines) == 1:
        lines.extend(["", "No major items surfaced yet."])

    return str(lint_agent_text("\n".join(lines).strip()))


def _clean_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_snapshot_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
