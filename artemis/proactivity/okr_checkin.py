"""Friday OKR check-in: proposal generator + Slack delivery.

Flow:
  1. Gather this week's evidence from OKR activity, Jira (closed this week),
     meeting action items, and OKR current state.
  2. For each active KR, attempt to ground a proposed change in at least one
     source. If no source is found for a KR, no change is proposed for it.
  3. Post the proposal to Jon's DM (informational push — writes are NOT made here).
  4. Jon's reply flows through the existing P1 DM agent loop.  The now-gated
     update_okr_kr tool (layer 3) fires only on his explicit confirm.

Design constraints:
  - The Friday job writes NO OKR by itself.  It only posts a proposal.
  - Every proposed KR change MUST cite a basis.
  - Reuses the morning-brief reservation pattern (delivery_kind='okr_checkin',
    once-per-Friday idempotency keyed on ISO week number).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.okr import repository as okr_repo

logger = logging.getLogger(__name__)

# Re-export so callers don't need to know the internal split.
__all__ = [
    "gather_checkin_sources",
    "build_okr_checkin_proposal",
    "format_checkin_for_slack",
]


# ── Source gathering ──────────────────────────────────────────────────────────


async def _safe_jira_done_this_week(session: AsyncSession) -> list[dict[str, Any]]:
    """Return Jira issues closed/updated this week.  Returns [] on any error."""
    try:
        from artemis.integrations import repository as integration_repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.jira.client import JiraClient
        from artemis.integrations.models import Integration

        config = await integration_repo.get_provider_config(session, "jira") or {}
        site_url = str(config.get("site_url") or "").strip()
        if not site_url:
            return []

        result = await session.execute(
            select(Integration)
            .where(
                Integration.provider == "jira",
                Integration.status == "active",
            )
            .order_by(Integration.connected_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return []

        creds = decrypt_credentials(bytes(row.encrypted_credentials))
        email = str(creds.get("email") or "").strip()
        api_token = str(creds.get("api_token") or "").strip()
        if not email or not api_token:
            return []

        client = JiraClient(site_url=site_url, email=email, api_token=api_token)
        # Last 7 days of done/closed issues.
        week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
        import httpx

        jql = f'status IN ("Done", "Closed", "Resolved") AND updated >= "{week_ago}" ORDER BY updated DESC'
        async with httpx.AsyncClient(timeout=20) as http_client:
            items = await client._fetch_column(http_client, jql, max_items=30)
        return items
    except Exception:
        logger.debug("Jira done-this-week source unavailable", exc_info=True)
        return []


async def _safe_meeting_action_items(session: AsyncSession) -> list[dict[str, Any]]:
    """Return action items from meetings summarised in the last 7 days."""
    try:
        from artemis.meetings.models import MeetingSummary

        cutoff = datetime.now(UTC) - timedelta(days=7)
        result = await session.execute(
            select(MeetingSummary)
            .where(MeetingSummary.created_at >= cutoff)
            .order_by(MeetingSummary.created_at.desc())
            .limit(20)
        )
        rows = list(result.scalars())
        items: list[dict[str, Any]] = []
        for ms in rows:
            if not ms.action_items:
                continue
            ai = ms.action_items
            if isinstance(ai, list):
                for entry in ai:
                    items.append(
                        {
                            "meeting": ms.title,
                            "item": str(entry) if not isinstance(entry, dict) else entry,
                        }
                    )
            elif isinstance(ai, dict):
                for entry in ai.get("items") or ai.get("action_items") or []:
                    items.append({"meeting": ms.title, "item": entry})
        return items
    except Exception:
        logger.debug("Meeting action items unavailable", exc_info=True)
        return []


async def gather_checkin_sources(session: AsyncSession) -> dict[str, Any]:
    """Collect OKR state + evidence for the Friday check-in proposal.

    Each source is gathered with its own session to avoid concurrent-session
    collisions (same pattern as brief/sources.py).
    """
    import asyncio

    import artemis.db as _db

    async def _own(fn: Any) -> Any:
        async with _db.SessionLocal() as s:
            return await fn(s)

    results: list[Any] = list(
        await asyncio.gather(
            _own(lambda s: okr_repo.list_objectives(s, include_archived=False)),
            _own(lambda s: okr_repo.list_activity(s, limit=100)),
            _own(_safe_jira_done_this_week),
            _own(_safe_meeting_action_items),
            return_exceptions=True,
        )
    )

    def _unwrap(val: Any, default: Any) -> Any:
        return default if isinstance(val, BaseException) else val

    return {
        "objectives": _unwrap(results[0], []),
        "activity": _unwrap(results[1], []),
        "jira_done": _unwrap(results[2], []),
        "action_items": _unwrap(results[3], []),
    }


# ── Proposal builder ──────────────────────────────────────────────────────────


def _week_cutoff() -> datetime:
    """Return the start of the current week (Monday 00:00 UTC)."""
    now = datetime.now(UTC)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def build_okr_checkin_proposal(sources: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of proposed KR updates, each with a cited basis.

    Only KRs that can be grounded in at least one source are included.
    Never fabricates a change.

    Each item:
      {
        "kr_id": int,
        "kr_title": str,
        "objective_title": str,
        "current_prog": int,
        "basis": list[str],  # cited evidence sentences
      }
    """
    cutoff = _week_cutoff()

    objectives = sources.get("objectives") or []
    activity_rows = sources.get("activity") or []
    jira_done = sources.get("jira_done") or []
    action_items = sources.get("action_items") or []

    # Index activity by kr_id, keeping only this-week entries.
    activity_by_kr: dict[int, list[str]] = {}
    for act in activity_rows:
        # act may be an ORM object or a dict depending on how sources were gathered.
        if hasattr(act, "created_at"):
            created_at = act.created_at
            kr_id = act.kr_id
            text = act.text
        else:
            continue
        if created_at is None:
            continue
        # Normalise naive datetimes.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < cutoff:
            continue
        if kr_id is not None:
            activity_by_kr.setdefault(int(kr_id), []).append(str(text))

    # Build keyword set from Jira done-this-week titles.
    jira_keywords: list[str] = [
        str(issue.get("title") or issue.get("summary") or issue.get("key") or "").lower()
        for issue in jira_done
        if issue
    ]

    # Build action-item phrases.
    action_phrases: list[str] = []
    for ai in action_items:
        item = ai.get("item")
        if not item:
            continue
        text_str = item if isinstance(item, str) else str(item.get("text") or item.get("action") or item)
        action_phrases.append(text_str.lower())

    proposals: list[dict[str, Any]] = []

    for obj in objectives:
        krs = getattr(obj, "key_results", []) or []
        for kr in krs:
            if getattr(kr, "archived_at", None) is not None:
                continue

            kr_id = int(kr.id)
            kr_title_lower = kr.title.lower()
            basis: list[str] = []

            # 1. OKR activity this week for this KR.
            for act_text in activity_by_kr.get(kr_id, []):
                basis.append(f"OKR activity: {act_text}")

            # 2. Jira issues whose title overlaps with the KR title.
            words = {w for w in kr_title_lower.split() if len(w) > 3}
            for kw in jira_keywords:
                if words and any(w in kw for w in words):
                    basis.append(f"Jira closed this week: {kw[:80]}")
                    break  # one Jira citation per KR is enough

            # 3. Meeting action items that mention words from the KR title.
            for phrase in action_phrases:
                if words and any(w in phrase for w in words):
                    basis.append(f"Meeting action item: {phrase[:120]}")
                    break

            # Only propose if grounded.
            if not basis:
                continue

            proposals.append(
                {
                    "kr_id": kr_id,
                    "kr_title": kr.title,
                    "objective_title": obj.title,
                    "current_prog": int(kr.prog),
                    "basis": basis,
                }
            )

    return proposals


# ── Slack formatter ───────────────────────────────────────────────────────────


def format_checkin_for_slack(
    proposals: list[dict[str, Any]],
    *,
    delivery_date: date,
) -> str:
    """Format the OKR check-in proposal for Slack delivery.

    This is informational — no OKR writes happen here.
    Jon's reply (approve + word-dump) flows through the DM agent loop,
    which calls update_okr_kr (layer 3) only on his explicit confirm.
    """
    day_label = f"{delivery_date.strftime('%A')}, {delivery_date.strftime('%B')} {delivery_date.day}"
    lines: list[str] = [
        f"*Friday OKR check-in for {day_label}*",
        "",
        "Here is what I found this week grounded in actual evidence. "
        "Reply with any corrections + a word-dump of what you accomplished "
        "and I'll update the KRs only once you say go.",
        "",
    ]

    if not proposals:
        lines.append(
            "I could not ground any KR updates in this week's activity, Jira, or meeting notes. "
            "Send me a word-dump of what you got done and I'll map it to KRs."
        )
    else:
        for p in proposals:
            lines.append(f"*{p['objective_title']}* > {p['kr_title']} (current: {p['current_prog']}%)")
            for b in p["basis"]:
                lines.append(f"  - {b}")
            lines.append("")

    lines.append(
        "_Nothing will change until you explicitly approve. "
        "Reply with corrections + your word-dump, then say 'go' when ready._"
    )

    try:
        from artemis.writing_rules import lint_agent_text

        return str(lint_agent_text("\n".join(lines).strip()))
    except Exception:
        return "\n".join(lines).strip()
