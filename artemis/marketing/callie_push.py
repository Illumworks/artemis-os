"""Callie proactive top-signal push + engagement learning.

#1 — Proactive push
====================
When a signal qualifies at top-tier (urgency_tier == "hot" AND fit score >=
``settings.callie_proactive_min_score``), ``push_top_tier_signal`` posts to the
marketing Slack channel (or the configured override channel) as Callie with:
  - the signal headline + district
  - a recommended angle (from the best-scoring campaign family)
  - an offer to dig deeper with Argus or kick off a brief

Dedup: a push for the same signal_id is never repeated.
Frequency cap: at most ``settings.callie_proactive_daily_cap`` pushes per UTC
day (rolling calendar day, not 24h window).

Both limits use scoped memory OBSERVATIONS (scope_kind="agent",
scope_id="callie") as the durable store — no new tables / migrations.

#2 — Engagement learning (v1)
===============================
``record_signal_engagement`` writes a lightweight scoped observation when Jon
acts on a Callie push (asks to dig deeper or kicks off a brief = "acted") vs
when a signal is processed without engagement ("ignored").  The observation is
keyed by the signal's reason codes, campaign family, and district attributes so
the learning is additive across similar signals.

``get_engagement_weights`` reads those observations back and returns a simple
weighting dict: {attribute_key: float} where values > 1.0 = favoured,
< 1.0 = down-ranked, 1.0 = neutral.  Callers (e.g. worklist ranking) can
multiply their base score by the weight for any matching attribute.

The pattern is intentionally simple so it can be mirrored for Artemis's
learning loop without diverging into two different systems.

Integration points
==================
- ``push_top_tier_signal`` is called from
  ``artemis.marketing.qualification.run_and_store_qualification`` after the
  signal transitions to ``qualified`` and has urgency_tier == "hot".
- ``record_signal_engagement`` is called from
  ``artemis.marketing.routes.signal_queue`` on the ``/{signal_id}/approve``
  and ``/{signal_id}/argus-dispatch`` paths.
- The "dig deeper" button at ``POST /api/signal-queue/{signal_id}/argus-dispatch``
  is registered on the existing ``signal_queue.router`` (no main.py edit needed).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.memory.models import MemoryObservation, MemoryScope
from artemis.memory.schemas import Scope

_log = logging.getLogger(__name__)

# ── Scope constants ────────────────────────────────────────────────────────────
_CALLIE_SCOPE = Scope(scope_kind="agent", scope_id="callie")

# Observation category for proactive push dedup / freq cap records.
_PUSH_CATEGORY = "callie_signal_push"

# Observation category for engagement events.
_ENGAGE_CATEGORY = "callie_signal_engagement"

# Prefix used when searching observations for today's push count.
_PUSH_OBS_PREFIX = "callie_push:"

# Prefix for engagement observations.
_ENGAGE_OBS_PREFIX = "callie_engage:"


# ── Dedup / freq-cap helpers ──────────────────────────────────────────────────


async def _push_already_sent(session: AsyncSession, signal_id: int) -> bool:
    """True if an observation recording a push for this signal_id already exists."""
    marker = f"{_PUSH_OBS_PREFIX}{signal_id}:"
    result = await session.execute(
        select(MemoryObservation)
        .join(MemoryScope, MemoryScope.scope_kind == MemoryObservation.scope_kind)
        .where(
            MemoryObservation.scope_kind == _CALLIE_SCOPE.scope_kind,
            MemoryObservation.scope_id == _CALLIE_SCOPE.scope_id,
            MemoryObservation.content.like(f"{marker}%"),
            MemoryObservation.superseded_by.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def _pushes_today(session: AsyncSession) -> int:
    """Count pushes already recorded today (UTC calendar day)."""
    today = datetime.now(UTC).date().isoformat()  # e.g. "2026-06-18"
    # The day anchor is embedded inside the content string after the signal-id
    # prefix, so we use a substring search (% on both sides).
    day_fragment = f"{_PUSH_OBS_PREFIX}day:{today}:"
    result = await session.execute(
        select(MemoryObservation)
        .where(
            MemoryObservation.scope_kind == _CALLIE_SCOPE.scope_kind,
            MemoryObservation.scope_id == _CALLIE_SCOPE.scope_id,
            MemoryObservation.content.like(f"%{day_fragment}%"),
            MemoryObservation.superseded_by.is_(None),
        )
    )
    return len(result.scalars().all())


async def _record_push_observation(
    session: AsyncSession, signal_id: int, signal_headline: str
) -> None:
    """Write a durable observation marking this signal as pushed today."""
    from artemis.memory.store import write_observation

    today = datetime.now(UTC).date().isoformat()
    content = (
        f"{_PUSH_OBS_PREFIX}{signal_id}:{_PUSH_OBS_PREFIX}day:{today}:"
        f"signal_id={signal_id} headline={signal_headline!r}"
    )
    await write_observation(
        session,
        scope=_CALLIE_SCOPE,
        content=content,
        category=_PUSH_CATEGORY,
        source_quality=0.9,
        raw_actor="callie_push",
    )


# ── Slack post helper ──────────────────────────────────────────────────────────


def _build_push_text(
    signal_id: int,
    headline: str,
    district_id: str | None,
    state: str | None,
    campaign_family: str | None,
    top_score: float,
    reason_codes: list[dict[str, Any]],
    app_base_url: str,
) -> str:
    """Build the Callie-voiced top-tier signal push message (Slack mrkdwn).

    The message structure:
      1. Signal headline + district context
      2. Recommended angle (campaign family + fit score)
      3. Reason codes (top 3)
      4. Offer: dig deeper with Argus, or kick off a brief
    """
    district_label = district_id or ""
    state_label = f" ({state})" if state else ""
    where = f"{district_label}{state_label}".strip() if (district_label or state_label) else ""

    family_label = (campaign_family or "").replace("_", " ").title() if campaign_family else "Campaign"
    score_pct = int(top_score * 100)

    code_list = [rc.get("code", "") for rc in (reason_codes or []) if rc.get("code")][:3]
    codes_text = ", ".join(f"`{c}`" for c in code_list) if code_list else ""

    lines: list[str] = []
    lines.append(f"*Top-tier signal:* {headline}")
    if where:
        lines.append(f"*Where:* {where}")
    lines.append(
        f"*Best angle:* {family_label} campaign ({score_pct}% fit)"
    )
    if codes_text:
        lines.append(f"*Signals:* {codes_text}")

    # Deep-link into signal detail
    signal_url = f"{app_base_url}/#marketing?signal={signal_id}" if app_base_url else ""
    view_part = f"  <{signal_url}|View signal>" if signal_url else ""

    lines.append(
        f"Want me to dig deeper with Argus, or kick off a brief?{view_part}"
    )

    return "\n".join(lines)


# ── Main push entry point ──────────────────────────────────────────────────────


async def push_top_tier_signal(
    session: AsyncSession,
    *,
    signal_id: int,
    headline: str,
    district_id: str | None,
    state: str | None,
    campaign_family: str | None,
    top_score: float,
    reason_codes: list[dict[str, Any]],
) -> bool:
    """Post a top-tier signal to the marketing Slack channel as Callie.

    Returns True if a push was dispatched, False if skipped (dedup / cap /
    no channel configured).

    Constraints enforced here:
    - dedup: never re-post the same signal
    - daily cap: at most ``settings.callie_proactive_daily_cap`` pushes per UTC day
    - score gate: only post if ``top_score >= settings.callie_proactive_min_score``
    - channel: ``settings.callie_proactive_channel`` (falls back to
      ``settings.marketing_campaigns_slack_channel``); skip if neither set

    Non-fatal: any Slack/DB error is logged as WARNING; the calling
    qualification flow is never interrupted.
    """
    # Score gate
    if top_score < settings.callie_proactive_min_score:
        _log.debug(
            "callie_push: signal %s score %.2f below gate %.2f — skip",
            signal_id,
            top_score,
            settings.callie_proactive_min_score,
        )
        return False

    # Channel resolution
    channel = settings.callie_proactive_channel or settings.marketing_campaigns_slack_channel
    if not channel:
        _log.debug("callie_push: no channel configured — skip")
        return False

    try:
        # Dedup check
        if await _push_already_sent(session, signal_id):
            _log.debug("callie_push: signal %s already pushed — skip (dedup)", signal_id)
            return False

        # Frequency cap
        today_count = await _pushes_today(session)
        if today_count >= settings.callie_proactive_daily_cap:
            _log.info(
                "callie_push: daily cap %d reached (%d today) — skip",
                settings.callie_proactive_daily_cap,
                today_count,
            )
            return False

        # Build message
        text = _build_push_text(
            signal_id=signal_id,
            headline=headline,
            district_id=district_id,
            state=state,
            campaign_family=campaign_family,
            top_score=top_score,
            reason_codes=reason_codes,
            app_base_url=settings.app_base_url,
        )

        # Post via Callie's Slack token
        from artemis.integrations.slack.client import SlackClient
        from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

        agent_cfg = await _resolve_agent_slack_config(session, agent_id="callie", team_id=None)
        if not agent_cfg.access_token:
            _log.warning(
                "callie_push: no Slack access token for Callie — cannot push signal %s",
                signal_id,
            )
            return False

        client = SlackClient(token=agent_cfg.access_token)
        await client.post_message(channel=channel, text=text)

        # Record push observation (dedup + freq-cap anchor)
        await _record_push_observation(session, signal_id, headline)
        _log.info(
            "callie_push: pushed signal %s to channel %s (score=%.2f)",
            signal_id,
            channel,
            top_score,
        )
        return True

    except Exception:
        _log.warning(
            "callie_push: non-fatal error pushing signal %s to Slack",
            signal_id,
            exc_info=True,
        )
        return False


# ── Engagement learning ────────────────────────────────────────────────────────


def _engagement_obs_content(
    signal_id: int,
    outcome: str,  # "acted" | "ignored"
    reason_codes: list[str],
    campaign_family: str | None,
    district_type: str | None,
) -> str:
    """Deterministic content string for a signal engagement observation.

    Keyed by signal attributes so aggregation across similar signals is natural.
    Format is human-readable and grep-able:

        callie_engage:acted:signal=123:family=obc:codes=LEADER_TRANSITION_FORMAL,...:dtype=large
    """
    codes_slug = ",".join(sorted(reason_codes))[:200]  # cap length
    family_slug = (campaign_family or "unknown").replace(" ", "_").lower()
    dtype_slug = (district_type or "unknown").lower()
    return (
        f"{_ENGAGE_OBS_PREFIX}{outcome}:signal={signal_id}"
        f":family={family_slug}:codes={codes_slug}:dtype={dtype_slug}"
    )


async def record_signal_engagement(
    session: AsyncSession,
    *,
    signal_id: int,
    outcome: str,
    reason_codes: list[str],
    campaign_family: str | None,
    district_type: str | None,
) -> None:
    """Record a lightweight engagement event as a scoped memory observation.

    ``outcome`` should be "acted" when Jon interacts with a Callie push
    (digs deeper or kicks off a brief) and "ignored" when the signal was
    processed without engagement (e.g., approved/rejected without prior
    Callie interaction).

    This is the v1 signal-capture half of the engagement learning loop.
    ``get_engagement_weights`` reads these back to compute attribute-level
    weights for ranking future proactive pushes.

    Pattern note: the observation is scoped to agent:callie, category
    "callie_signal_engagement". The content is deterministic so
    near-duplicate suppression won't accidentally collapse it.
    Source quality is 1.0 for "acted" (human-confirmed) and 0.6 for
    "ignored" (implicit signal).
    """
    from artemis.memory.store import write_observation

    content = _engagement_obs_content(
        signal_id=signal_id,
        outcome=outcome,
        reason_codes=reason_codes,
        campaign_family=campaign_family,
        district_type=district_type,
    )
    quality = 1.0 if outcome == "acted" else 0.6
    try:
        await write_observation(
            session,
            scope=_CALLIE_SCOPE,
            content=content,
            category=_ENGAGE_CATEGORY,
            source_quality=quality,
            raw_actor="callie_engage",
        )
    except Exception:
        _log.warning(
            "record_signal_engagement: failed for signal %s outcome=%s (non-fatal)",
            signal_id,
            outcome,
            exc_info=True,
        )


async def get_engagement_weights(
    session: AsyncSession,
) -> dict[str, float]:
    """Return a simple {attribute_key: weight} dict from past engagement observations.

    Algorithm (v1 — intentionally simple):
      For each unique (outcome, attribute_key) pair observed, compute:
        weight = (acted_count + 1) / (total_count + 1)  — Laplace-smoothed

    attribute_key examples: "family:obc", "code:LEADER_TRANSITION_FORMAL"

    Values > 0.5 = above average engagement; callers multiply their base
    score by the weight.  v1 is capture + simple weighting; a more
    sophisticated trace-based version is slated for P6.
    """
    result = await session.execute(
        select(MemoryObservation)
        .where(
            MemoryObservation.scope_kind == _CALLIE_SCOPE.scope_kind,
            MemoryObservation.scope_id == _CALLIE_SCOPE.scope_id,
            MemoryObservation.content.like(f"{_ENGAGE_OBS_PREFIX}%"),
            MemoryObservation.superseded_by.is_(None),
        )
    )
    rows = result.scalars().all()

    # Tally acted / total per attribute key
    acted: dict[str, int] = {}
    total: dict[str, int] = {}

    for obs in rows:
        content = str(obs.content)
        # Determine outcome from content prefix
        is_acted = f"{_ENGAGE_OBS_PREFIX}acted:" in content

        # Extract family and codes from content
        # Format: callie_engage:acted:signal=<N>:family=<f>:codes=<c1,...>:dtype=<d>
        attrs: list[str] = []
        for part in content.split(":"):
            if part.startswith("family="):
                attrs.append(f"family:{part[7:]}")
            elif part.startswith("codes="):
                for code in part[6:].split(","):
                    if code:
                        attrs.append(f"code:{code}")
            elif part.startswith("dtype="):
                attrs.append(f"dtype:{part[6:]}")

        for attr in attrs:
            total[attr] = total.get(attr, 0) + 1
            if is_acted:
                acted[attr] = acted.get(attr, 0) + 1

    # Laplace-smoothed weight: (acted + 1) / (total + 1)
    weights: dict[str, float] = {}
    for attr, tot in total.items():
        act = acted.get(attr, 0)
        weights[attr] = (act + 1) / (tot + 1)

    return weights
