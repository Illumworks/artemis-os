"""Callie reports the Screen-Time Watch "real moves" to #policy-watch.

This is a NEW *consumer* of the ``screentime_signals`` table (Brief 1's engine).
It does NOT change Callie's campaign push behavior — ``callie_push.py`` is left
untouched. We reuse Callie's Slack posting path (her resolved bot token +
``SlackClient``) so the reports come from her, in her voice.

Two report modes
================
- ``post_screentime_digest``  — the weekly digest (cron). Selects real-move
  signals NOT yet reported, groups them by stance/state, composes one concise
  Callie-voiced message (so-what first, source-linked), posts it, and marks each
  included signal reported. Skips silently if the channel is unset or nothing is
  new. Returns the count of signals reported.
- ``maybe_alert_big_move``    — the immediate alert. Hooked into the runner's
  store step (gated on the channel being set). If a freshly-stored signal meets
  the config-driven big-move thresholds (a passed blanket restriction in a large
  state, or a new evidence-based carve-out), it posts an immediate alert and
  marks the signal reported.

Dedup
=====
A signal is reported AT MOST ONCE across both modes. We mirror
``callie_push._record_push_observation``: a durable scoped memory OBSERVATION
(scope agent:callie) acts as the "already reported" marker — no new table, no
migration. The marker content is screentime-scoped
(``screentime_report:<signal_id>:``) so it never collides with the campaign push
marker (``callie_push:...``). The big-move alert and the digest both check and
write the SAME marker, so an alerted signal is excluded from the next digest.

Failure-safe
============
Every public entry point is wrapped: any Slack/DB/provider error is logged at
WARNING and swallowed so a cron run or a screentime sweep never raises out.
Provider/Slack imports are lazy (inside functions) to stay circular-import safe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from artemis.config import settings
from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope
from artemis.screentime.models import (
    STANCE_FAVORABLE,
    STANCE_UNFAVORABLE,
    ScreentimeSignal,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

# ── Scope + marker constants ────────────────────────────────────────────────
# Posts come FROM Callie, so the marker lives in her scope — but the marker
# PREFIX is screentime-specific so it can never collide with callie_push's
# campaign markers (which use the "callie_push:" prefix).
_CALLIE_SCOPE = Scope(scope_kind="agent", scope_id="callie")

# Observation category. Reuses the same "agent reported a thing" semantics as
# callie_push's _PUSH_CATEGORY; it is not in KNOWN_CATEGORIES (decays at the
# default factor — fine, the marker only needs to outlive a reporting cycle),
# exactly as callie_push's category behaves.
_REPORT_CATEGORY = "screentime_report"

# Marker prefix — screentime-scoped, distinct from callie_push:.
_REPORT_OBS_PREFIX = "screentime_report:"

# How many signals a single digest will include (newest first). Keeps one Slack
# message readable; older un-reported ones roll into the following digest.
_DIGEST_MAX_SIGNALS = 25


# ── Dedup marker (mirrors callie_push._record_push_observation) ──────────────


def _report_marker(signal_id: int) -> str:
    return f"{_REPORT_OBS_PREFIX}{signal_id}:"


async def _already_reported_ids(session: AsyncSession, signal_ids: list[int]) -> set[int]:
    """Return the subset of *signal_ids* that already have a reported-marker.

    One query for the whole batch (vs. N point lookups). Matches the marker
    substring inside the observation content, scoped to agent:callie.
    """
    if not signal_ids:
        return set()
    result = await session.execute(
        select(MemoryObservation.content).where(
            MemoryObservation.scope_kind == _CALLIE_SCOPE.scope_kind,
            MemoryObservation.scope_id == _CALLIE_SCOPE.scope_id,
            MemoryObservation.content.like(f"{_REPORT_OBS_PREFIX}%"),
            MemoryObservation.superseded_by.is_(None),
        )
    )
    contents = [str(c) for (c,) in result.all()]
    reported: set[int] = set()
    for sid in signal_ids:
        marker = _report_marker(sid)
        if any(marker in content for content in contents):
            reported.add(sid)
    return reported


async def _is_reported(session: AsyncSession, signal_id: int) -> bool:
    """True if this single signal already has a reported-marker."""
    marker = _report_marker(signal_id)
    result = await session.execute(
        select(MemoryObservation.id).where(
            MemoryObservation.scope_kind == _CALLIE_SCOPE.scope_kind,
            MemoryObservation.scope_id == _CALLIE_SCOPE.scope_id,
            MemoryObservation.content.like(f"%{marker}%"),
            MemoryObservation.superseded_by.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def _mark_reported(session: AsyncSession, signal_id: int, *, mode: str, title: str) -> None:
    """Write the durable "already reported" marker for *signal_id*.

    ``mode`` is "digest" or "alert" (recorded for audit; the marker is the same
    either way, so the OTHER mode will skip the signal next time).
    """
    from artemis.memory.store import write_observation

    content = f"{_report_marker(signal_id)}mode={mode}:signal_id={signal_id} title={title!r}"
    await write_observation(
        session,
        scope=_CALLIE_SCOPE,
        content=content,
        category=_REPORT_CATEGORY,
        source_quality=0.9,
        raw_actor="screentime_report",
    )


# ── Signal selection ────────────────────────────────────────────────────────


async def _select_unreported_real_moves(
    session: AsyncSession, *, limit: int = _DIGEST_MAX_SIGNALS
) -> list[ScreentimeSignal]:
    """Real-move signals not yet reported, newest first, capped at *limit*.

    Filters to ``is_real_move`` (the digest is "real moves, not headlines") and
    excludes anything already reported via the dedup marker.
    """
    rows = (
        (
            await session.execute(
                select(ScreentimeSignal)
                .where(ScreentimeSignal.is_real_move.is_(True))
                .order_by(ScreentimeSignal.discovered_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    reported = await _already_reported_ids(session, [s.id for s in rows])
    fresh = [s for s in rows if s.id not in reported]
    return fresh[:limit]


# ── Big-move threshold ──────────────────────────────────────────────────────


def _csv_set(raw: str) -> set[str]:
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}


def is_big_move(signal: ScreentimeSignal) -> bool:
    """Config-driven "big move" test (pure, no I/O — easy to unit test).

    A signal is a big move when EITHER:

    1. **Large-state blanket restriction:** the signal LANDED
       (``status`` in ``screentime_bigmove_statuses``), is UNFAVORABLE (a blanket
       restriction that could limit Amira), AND is in a large state
       (``screentime_bigmove_states``). These are the moves Angela's team must
       react to immediately.

    2. **New evidence-based carve-out** (when ``screentime_bigmove_favorable_alert``):
       the signal LANDED and is FAVORABLE (a carve-out for evidence-based /
       purpose-built tools) — strategically useful in ANY state, so no
       state gate applies.

    Tunable entirely via config; no code change to re-scope what counts as big.
    """
    landed_statuses = _csv_set(settings.screentime_bigmove_statuses)
    if (signal.status or "").strip().lower() not in landed_statuses:
        return False

    stance = (signal.stance or "").strip().lower()

    # 2. New evidence-based carve-out — any state.
    if settings.screentime_bigmove_favorable_alert and stance == STANCE_FAVORABLE:
        return True

    # 1. Large-state blanket restriction.
    if stance == STANCE_UNFAVORABLE:
        large_states = _csv_set(settings.screentime_bigmove_states)
        if (signal.state or "").strip().lower() in large_states:
            return True

    return False


# ── Voiced composition ──────────────────────────────────────────────────────

# Callie's reporting voice for #policy-watch. Concise, so-what first,
# source-linked, no headline-padding — same voice rules as her campaign posts.
_DIGEST_SYSTEM = (
    "You are Callie, Amira Learning's marketing-intelligence analyst, posting to "
    "the internal #policy-watch Slack channel for Angela's team. You report REAL "
    "MOVES on screen-time legislation/policy — actual legislative or board action, "
    "not press chatter. Voice rules: concise, so-what first, no headline-padding, "
    "no preamble. Lead with what the team should DO or KNOW. For each move give: "
    "what changed, which state, whether it is FAVORABLE or UNFAVORABLE for Amira "
    "(favorable = a carve-out for evidence-based/purpose-built tools; unfavorable "
    "= a blanket restriction with no carve-out), and the Amira angle (where Amira "
    "fits). Group by stance then state. Keep the WHOLE digest tight. Use Slack "
    "mrkdwn. Do NOT invent facts or sources — use only what is given. Return ONLY "
    "the message text, no JSON, no code fences."
)


def _signal_brief_line(signal: ScreentimeSignal) -> str:
    """A compact, factual one-liner the LLM (or fallback) composes from."""
    bits = [
        f"state={signal.state}",
        f"level={signal.level}",
        f"status={signal.status}",
        f"stance={signal.stance}",
        f"title={signal.title}",
    ]
    if signal.district_name:
        bits.append(f"district={signal.district_name}")
    if signal.summary:
        bits.append(f"summary={signal.summary}")
    if signal.amira_angle:
        bits.append(f"amira_angle={signal.amira_angle}")
    if signal.source_url:
        bits.append(f"source={signal.source_url}")
    return " | ".join(bits)


def _source_link(signal: ScreentimeSignal) -> str:
    """Slack mrkdwn link to the actual source (bill/policy), not a headline."""
    if signal.source_url:
        label = signal.title.strip() or "source"
        # Trim very long titles so the link stays readable.
        if len(label) > 80:
            label = label[:77] + "..."
        return f"<{signal.source_url}|{label}>"
    return signal.title.strip() or "(no source link)"


def _stance_emoji(stance: str) -> str:
    s = (stance or "").strip().lower()
    if s == STANCE_FAVORABLE:
        return "🟢"
    if s == STANCE_UNFAVORABLE:
        return "🔴"
    return "⚪"


def _fallback_digest_text(signals: list[ScreentimeSignal]) -> str:
    """Deterministic, source-linked digest used when no LLM composition is
    available (provider down / import failure). Still in Callie's structure:
    grouped by stance, so-what first, every item links the actual source.
    """
    fav = [s for s in signals if (s.stance or "").lower() == STANCE_FAVORABLE]
    unf = [s for s in signals if (s.stance or "").lower() == STANCE_UNFAVORABLE]
    other = [
        s for s in signals if (s.stance or "").lower() not in (STANCE_FAVORABLE, STANCE_UNFAVORABLE)
    ]

    lines: list[str] = []
    lines.append(f"*Screen-Time Watch — {len(signals)} new real move(s)*")

    def _group(label: str, group: list[ScreentimeSignal]) -> None:
        if not group:
            return
        lines.append(f"\n{label}")
        # Sort within a group by state for scan-ability.
        for s in sorted(group, key=lambda x: (x.state or "", x.status or "")):
            emoji = _stance_emoji(s.stance)
            where = s.state + (f"/{s.district_name}" if s.district_name else "")
            angle = f" — {s.amira_angle}" if s.amira_angle else ""
            lines.append(f"{emoji} *{where}* ({s.status}): {_source_link(s)}{angle}")

    _group("*Unfavorable (blanket restrictions — watch):*", unf)
    _group("*Favorable (carve-outs we can lean on):*", fav)
    _group("*Other:*", other)
    return "\n".join(lines)


def _fallback_alert_text(signal: ScreentimeSignal) -> str:
    """Deterministic immediate-alert text (LLM-free fallback)."""
    emoji = _stance_emoji(signal.stance)
    where = signal.state + (f"/{signal.district_name}" if signal.district_name else "")
    favorability = (
        "FAVORABLE for us"
        if (signal.stance or "").lower() == STANCE_FAVORABLE
        else "UNFAVORABLE for us"
        if (signal.stance or "").lower() == STANCE_UNFAVORABLE
        else "neutral"
    )
    angle = f"\n*Amira angle:* {signal.amira_angle}" if signal.amira_angle else ""
    return (
        f"{emoji} *Big move — {where}* ({signal.status}, {favorability})\n"
        f"{_source_link(signal)}{angle}"
    )


async def _compose_digest_text(session: AsyncSession, signals: list[ScreentimeSignal]) -> str:
    """Compose the digest in Callie's voice via a cheap provider; fall back to
    the deterministic builder on any failure (never raises).

    Cost: ``model=None`` lives INSIDE the CompletionRequest (never a kwarg to
    ``complete_with_fallback``), and we route primary="codex" (tool-less text
    work), exactly like the screentime classifier.
    """
    try:
        from artemis.agent.client import CompletionRequest
        from artemis.agent.types import Message, TextBlock
        from artemis.providers.fallback import complete_with_fallback
    except Exception:  # pragma: no cover - import guard
        _log.warning(
            "screentime_report: provider import failed; using deterministic digest",
            exc_info=True,
        )
        return _fallback_digest_text(signals)

    brief = "\n".join(f"- {_signal_brief_line(s)}" for s in signals)
    prompt = (
        "Compose the #policy-watch digest for these new real moves. "
        "Every item MUST link its source URL using Slack mrkdwn "
        "(<url|short label>) — never a bare headline. Moves:\n"
        f"{brief}"
    )
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        system=_DIGEST_SYSTEM,
        model=None,  # MUST be inside the request — codex/claude-code pick a default
        max_tokens=900,
        cache_system=False,
        cache_tools=False,
    )
    try:
        resp = await complete_with_fallback(
            req,
            primary="codex",
            fallback="claude-code",
            session=session,
            feature_tag="screentime_report_digest",
        )
    except Exception:
        _log.warning(
            "screentime_report: digest composition failed; using deterministic digest",
            exc_info=True,
        )
        return _fallback_digest_text(signals)

    text = ""
    for block in resp.message.content:
        if hasattr(block, "text"):
            text = block.text.strip()
            break
    # Guard: if the model returned nothing useful, or dropped every source link,
    # fall back to the deterministic builder so the digest is always source-linked.
    if not text:
        return _fallback_digest_text(signals)
    have_links = any(s.source_url for s in signals)
    if have_links and "http" not in text:
        _log.info("screentime_report: composed digest had no links; using fallback")
        return _fallback_digest_text(signals)
    return text


# ── Slack post helper (reuses Callie's resolved token + SlackClient) ─────────


async def _post_as_callie(
    session: AsyncSession,
    channel: str,
    text: str,
    thread_parts: list[str] | None = None,
    unfurl: bool = True,
) -> bool:
    """Post *text* to *channel* AS Callie, via her resolved Slack token.

    Reuses the exact resolution path callie_push uses
    (``_resolve_agent_slack_config(agent_id="callie")`` + ``SlackClient``) so
    these posts come from Callie. Returns True on a dispatched post, False if no
    token is available. Raises only if the Slack call itself errors (callers
    wrap it).
    """
    from artemis.integrations.slack.client import SlackClient
    from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

    agent_cfg = await _resolve_agent_slack_config(session, agent_id="callie", team_id=None)
    if not agent_cfg.access_token:
        _log.warning("screentime_report: no Slack token for Callie — cannot post")
        return False
    client = SlackClient(token=agent_cfg.access_token)
    # None leaves Slack's default; False suppresses the link previews.
    no_unfurl = None if unfurl else False
    response = await client.post_message(
        channel=channel, text=text, unfurl_links=no_unfurl, unfurl_media=no_unfurl
    )
    # Overflow goes in a thread rather than as more top-level messages -- see
    # ``sentiment.report.split_for_slack`` for why this matters.
    if thread_parts:
        parent_ts = response.get("ts")
        if isinstance(parent_ts, str):
            for part in thread_parts:
                await client.post_message(
                    channel=channel,
                    text=part,
                    thread_ts=parent_ts,
                    unfurl_links=no_unfurl,
                    unfurl_media=no_unfurl,
                )
        else:
            _log.warning("post_as_callie: no ts on parent message — thread parts dropped")
    return True


# ── Public entry points ──────────────────────────────────────────────────────


async def build_screentime_section(session: AsyncSession) -> str | None:
    """Return the screentime SECTION for the combined daily brief, or None.

    Jon's 2026-08-12 decision: #market-signals carries ONE combined daily brief
    from Callie (top campaign signals + crisis signals + screentime), not one
    post per feed. This is the screentime contribution to that brief; the
    combined composer owns the heading, ordering, the Josh/Angela mention and
    the "nothing today" case.

    Contract agreed with the crisis-content session:
      - returns the section text, or None when there is nothing to say today
      - marks its own items reported, so the caller does not have to and a
        re-run contributes nothing

    That idempotency is the valuable half and is unchanged from the standalone
    digest it was extracted from: an item reported once — by this section, by a
    big-move alert, or by a previous brief — is never reported again.

    Failure-safe: returns None on any error rather than raising into the
    composer, so one feed cannot take down the whole brief.
    """
    try:
        signals = await _select_unreported_real_moves(session)
        if not signals:
            _log.debug("screentime_report: no new real moves — no section today")
            return None

        text = await _compose_digest_text(session, signals)
        for s in signals:
            await _mark_reported(session, s.id, mode="digest", title=s.title)
        _log.info("screentime_report: built section covering %d signal(s)", len(signals))
        return text
    except Exception:
        _log.warning("screentime_report: non-fatal error building section", exc_info=True)
        return None


async def post_screentime_digest(session: AsyncSession) -> int:
    """Compose + post a STANDALONE Screen-Time digest as Callie.

    NOT used for #market-signals — that channel gets one combined brief (see
    ``build_screentime_section``). Kept for manual/one-off use and for any
    future channel that wants only this feed.

    Returns the number of signals reported. Returns 0 (silently) when:
      - ``screentime_report_channel`` is unset (feature OFF / dormant), or
      - there are no new real moves, or
      - Callie has no Slack token, or
      - any error occurs (failure-safe — never raises out of a cron).
    """
    channel = settings.screentime_report_channel
    if not channel:
        _log.debug("screentime_report: no channel set — feature off, skip digest")
        return 0
    try:
        signals = await _select_unreported_real_moves(session)
        if not signals:
            _log.debug("screentime_report: no new real moves — skip digest")
            return 0

        text = await _compose_digest_text(session, signals)
        posted = await _post_as_callie(session, channel, text)
        if not posted:
            return 0

        for s in signals:
            await _mark_reported(session, s.id, mode="digest", title=s.title)
        _log.info(
            "screentime_report: posted digest of %d signal(s) to %s",
            len(signals),
            channel,
        )
        return len(signals)
    except Exception:
        _log.warning("screentime_report: non-fatal error posting digest", exc_info=True)
        return 0


async def maybe_alert_big_move(session: AsyncSession, signal: ScreentimeSignal) -> bool:
    """Post an immediate big-move alert for *signal* if it qualifies.

    Hooked into the runner's store step (gated on the channel being set). Posts
    AS Callie and marks the signal reported so the next digest skips it (single
    report across modes). Returns True if an alert was posted, else False.

    Returns False (silently) when the channel is unset, the signal is not a big
    move, it was already reported, Callie has no token, or any error occurs
    (failure-safe — never raises out of a sweep).
    """
    channel = settings.screentime_report_channel
    if not channel:
        return False
    try:
        if not is_big_move(signal):
            return False
        if await _is_reported(session, signal.id):
            _log.debug(
                "screentime_report: big move signal %s already reported — skip",
                signal.id,
            )
            return False

        text = _fallback_alert_text(signal)
        posted = await _post_as_callie(session, channel, text)
        if not posted:
            return False

        await _mark_reported(session, signal.id, mode="alert", title=signal.title)
        _log.info(
            "screentime_report: posted big-move alert for signal %s (%s/%s) to %s",
            signal.id,
            signal.state,
            signal.status,
            channel,
        )
        return True
    except Exception:
        _log.warning(
            "screentime_report: non-fatal error alerting big move for signal %r",
            getattr(signal, "id", "?"),
            exc_info=True,
        )
        return False


async def maybe_alert_big_move_by_hash(session: AsyncSession, content_hash: str) -> bool:
    """Runner-friendly wrapper: load the just-stored signal by content_hash and
    consider it for a big-move alert.

    The runner's store step works in terms of ``content_hash`` (its dedup key)
    and does not hold the ORM row, so this fetches it. Gated + failure-safe like
    ``maybe_alert_big_move``. Returns True if an alert was posted.
    """
    if not settings.screentime_report_channel:
        return False
    try:
        signal = (
            await session.execute(
                select(ScreentimeSignal).where(ScreentimeSignal.content_hash == content_hash)
            )
        ).scalar_one_or_none()
        if signal is None:
            return False
        return await maybe_alert_big_move(session, signal)
    except Exception:
        _log.warning(
            "screentime_report: non-fatal error in big-move-by-hash for %r",
            content_hash,
            exc_info=True,
        )
        return False


__all__ = [
    # The combined-brief contract (see build_screentime_section docstring).
    "build_screentime_section",
    "post_screentime_digest",
    "maybe_alert_big_move",
    "maybe_alert_big_move_by_hash",
    "is_big_move",
]
