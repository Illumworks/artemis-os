"""Slack Events API receiver — /api/integrations/slack/events.

Handles three Slack callback types:
  - url_verification  : return challenge (no HMAC required)
  - event_callback    : HMAC-verified; dispatch app_mention / message+im to routing
  - everything else   : 200 OK, no-op
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.config import settings
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import _parse_allowed_user_ids
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.models import Integration
from artemis.writing_rules import lint_agent_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/slack", tags=["slack-events"])

# Regex to strip the leading bot-mention from a message text, e.g. "<@U12345> hello"
_BOT_MENTION_RE = re.compile(r"^<@[A-Z0-9]+>\s*", re.IGNORECASE)
_CALLIE_CAMPAIGN_SIGNALS_CHANNEL = "C0B9CHVC7KQ"

# Idle gap after which a re-engagement reply should @mention the asker again.
# During active flow (last message < this threshold ago) the ping is suppressed.
_REENGAGE_PING_GAP = timedelta(minutes=5)

# Model used for the cheap "should I respond?" gate — haiku-tier so the
# classifier never costs more than a rounding error vs staying silent.
_GATE_MODEL = "claude-haiku-4-5-20251001"
_GATE_SYSTEM = (
    "Answer only YES or NO: is this Slack message a marketing question "
    "addressed to Callie (a marketing analyst assistant) that she should answer? "
    "Reply YES only if the message is clearly asking for marketing help. "
    "Otherwise reply NO."
)

# ── Pure gate helpers ─────────────────────────────────────────────────────────

# Type alias for an injected classifier callable used by should_respond_to_channel_message.
# Signature: (text: str) -> Awaitable[bool]
ChannelClassifier = Callable[[str], Awaitable[bool]]

# Type alias for an injected confirm-reply classifier.
# Signature: (text: str) -> Awaitable[Literal["YES", "NO", "NEITHER"]]
ConfirmClassifier = Callable[[str], Awaitable[str]]


def should_ping_asker(
    *,
    is_dm: bool,
    last_message_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Return True if the reply should be prefixed with <@user_id>.

    Rules:
    - DMs (1:1) never get the ping.
    - Cold start (no prior messages in session) → ping.
    - Re-engagement after idle gap > _REENGAGE_PING_GAP → ping.
    - Active flow (last message < _REENGAGE_PING_GAP ago) → no ping.

    This function is PURE — it does no I/O. Pass ``now`` explicitly in tests.
    """
    if is_dm:
        return False
    if last_message_at is None:
        # Cold start — no prior messages in this session
        return True
    effective_now = now if now is not None else datetime.now(UTC)
    # Normalise timezone: if last_message_at is naive, treat it as UTC
    if last_message_at.tzinfo is None:
        last_message_at = last_message_at.replace(tzinfo=UTC)
    gap = effective_now - last_message_at
    return gap >= _REENGAGE_PING_GAP


async def _default_channel_classifier(text: str) -> bool:
    """Real cheap haiku classifier — returns True iff the message should get a reply.

    Uses a ~5-token YES/NO completion; never runs a full agent turn.
    Any non-clear-YES answer → False (conservative default).
    """
    from artemis.agent.client import AnthropicAdapter, CompletionRequest
    from artemis.agent.types import Message, TextBlock

    adapter = AnthropicAdapter()
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
        system=_GATE_SYSTEM,
        model=_GATE_MODEL,
        max_tokens=5,
        cache_system=False,
        cache_tools=False,
    )
    try:
        resp = await adapter.complete(req)
        answer = ""
        for block in resp.message.content:
            if hasattr(block, "text"):
                answer = block.text.strip().upper()
                break
        return answer.startswith("YES")
    except Exception:
        logger.warning("channel gate classifier failed — defaulting to silent", exc_info=True)
        return False


# Deterministic confirm vocabulary. The confirm decision is a small, bounded
# choice, so keyword matching is more reliable (and has no API-key dependency —
# Artemis runs on the Claude Code subscription with NO Anthropic key, so any
# AnthropicAdapter call here would raise and default every reply to NEITHER,
# silently breaking the apply-on-"go" path).
_CONFIRM_AFFIRMATIVES = frozenset(
    {
        "go",
        "yes",
        "yep",
        "yeah",
        "yup",
        "ok",
        "okay",
        "sure",
        "approve",
        "approved",
        "confirm",
        "confirmed",
        "apply",
        "proceed",
        "ship",
    }
)
# Multi-word affirmative phrases (matched as substrings on the normalized text).
_CONFIRM_AFFIRMATIVE_PHRASES = (
    "good to go",
    "go ahead",
    "do it",
    "ship it",
    "send it",
    "sounds good",
    "looks good",
    "let's do it",
    "lets do it",
    "make it so",
    "go for it",
)
_CONFIRM_NEGATIVES = frozenset(
    {
        "no",
        "nope",
        "nah",
        "hold",
        "cancel",
        "stop",
        "don't",
        "dont",
        "wait",
        "discard",
        "scrap",
    }
)
_CONFIRM_NEGATIVE_PHRASES = ("not yet", "never mind", "nevermind", "hold off", "don't")


async def _default_confirm_classifier(text: str) -> str:
    """Classify a Slack reply as YES / NO / NEITHER for the layer-3 confirm flow.

    Deterministic, keyword-based — no LLM/API dependency (Artemis has no Anthropic
    API key; an API call here would raise and break apply-on-"go"). Negation wins
    over affirmation (cancel-safe). Mixed/none → NEITHER (falls through to a normal
    turn, which never applies anything).
    """
    normalized = text.strip().lower()
    if not normalized:
        return "NEITHER"

    # Word-boundary tokens for single-word matching (so "go" matches in "good to
    # go" but not in "going"; "no" matches as a word but not in "now"/"nothing").
    tokens = set(re.findall(r"[a-z']+", normalized))

    has_negative = bool(tokens & _CONFIRM_NEGATIVES) or any(
        phrase in normalized for phrase in _CONFIRM_NEGATIVE_PHRASES
    )
    has_affirmative = bool(tokens & _CONFIRM_AFFIRMATIVES) or any(
        phrase in normalized for phrase in _CONFIRM_AFFIRMATIVE_PHRASES
    )

    # Negation wins (cancel-safe): "no, not yet" or "go but wait" → do not apply.
    if has_negative:
        return "NO"
    if has_affirmative:
        return "YES"
    return "NEITHER"


async def should_respond_to_channel_message(
    *,
    is_mention: bool,
    session_id: str,
    text: str,
    classifier: ChannelClassifier | None = None,
) -> bool:
    """Return True iff Callie should respond to this channel message.

    Gate logic (in order — first True wins):
      (a) app_mention → always respond.
      (b) agent has prior history in this session → respond (continuity).
      (c) cheap classifier says YES → respond.
      default → stay silent.

    The ``classifier`` arg is injectable for tests (pass a fake to avoid LLM calls).
    The DB session for history check is opened here; it is read-only / non-transactional.
    """
    # (a) Direct mention — always respond
    if is_mention:
        return True

    # (b) Continuity — any prior message in this session means Callie is already engaged
    try:
        import artemis.db as _db
        from artemis.floating_artemis import repository as fa_repo

        async with _db.SessionLocal() as db_session:
            messages = await fa_repo.list_messages(db_session, session_id, limit=1)
            if messages:
                return True
    except Exception:
        logger.debug(
            "should_respond_to_channel_message: history check failed for session=%s — skipping continuity gate",
            session_id,
            exc_info=True,
        )

    # (c) Cheap classifier
    _classifier = classifier if classifier is not None else _default_channel_classifier
    return await _classifier(text)


async def _last_message_timestamp(session_id: str) -> datetime | None:
    """Fetch the created_at of the most recent message in a session, or None."""
    try:
        import artemis.db as _db
        from artemis.floating_artemis import repository as fa_repo

        async with _db.SessionLocal() as db_session:
            # list_messages_for_context returns newest-first when limit=1 and no cutoff
            rows = await fa_repo.list_messages_for_context(db_session, session_id, limit=1)
            if rows:
                return rows[0].created_at
    except Exception:
        logger.debug(
            "_last_message_timestamp: failed for session=%s",
            session_id,
            exc_info=True,
        )
    return None


@dataclass(frozen=True)
class _SlackAgentConfig:
    agent_id: str
    signing_secret: str
    access_token: str
    bot_user_id: str
    authed_user_id: str
    allowed_user_ids: tuple[str, ...]
    allowed_channel_ids: tuple[str, ...]
    listen_channel_messages: bool

    def is_user_allowed(self, user_id: str) -> bool:
        return bool(user_id) and user_id in self.allowed_user_ids

    def is_channel_allowed(self, channel_id: str) -> bool:
        return bool(channel_id) and channel_id in self.allowed_channel_ids


def _normalize_agent_id(agent_id: str | None) -> str:
    if isinstance(agent_id, str) and agent_id.strip():
        return agent_id.strip().lower()
    return "artemis"


def _row_agent_id(row: object) -> str:
    raw = getattr(row, "agent_id", "default")
    return _normalize_agent_id(raw if isinstance(raw, str) else "default")


def _legacy_agent_ids(agent_id: str) -> tuple[str, ...]:
    if agent_id == "artemis":
        return ("artemis", "default")
    return (agent_id,)


def _default_allowed_channel_ids(agent_id: str) -> tuple[str, ...]:
    if agent_id != "callie":
        return ()

    ordered: list[str] = [_CALLIE_CAMPAIGN_SIGNALS_CHANNEL]
    marketing_campaigns_channel = settings.marketing_campaigns_slack_channel.strip()
    if marketing_campaigns_channel and marketing_campaigns_channel not in ordered:
        ordered.append(marketing_campaigns_channel)
    return tuple(ordered)


async def _resolve_agent_slack_config(
    session: AsyncSession,
    *,
    agent_id: str,
    team_id: str | None = None,
    load_integration: bool = True,
) -> _SlackAgentConfig:
    normalized_agent = _normalize_agent_id(agent_id)
    authed_user_id = os.environ.get("SLACK_AUTHED_USER_ID", "")
    allowed_user_ids = tuple(
        dict.fromkeys(
            ([authed_user_id] if authed_user_id else [])
            + _parse_allowed_user_ids(os.environ.get("SLACK_ALLOWED_USER_IDS", ""))
        )
    )
    global_signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

    if normalized_agent == "artemis":
        with contextlib.suppress(Exception):
            from artemis.integrations.config_resolver import resolve_slack_config

            slack_cfg = await resolve_slack_config(session)
            authed_user_id = slack_cfg.authed_user_id
            allowed_user_ids = slack_cfg.allowed_user_ids
            global_signing_secret = slack_cfg.signing_secret

    stored_cfg: dict[str, object] = {}
    if not global_signing_secret and normalized_agent == "artemis":
        with contextlib.suppress(Exception):
            stored_cfg_raw = await repo.get_provider_config(session, "slack")
            if isinstance(stored_cfg_raw, dict):
                stored_cfg = stored_cfg_raw
        authed_user_id = str(stored_cfg.get("authed_user_id") or "") or authed_user_id
        extras = _parse_allowed_user_ids(stored_cfg.get("allowed_user_ids")) or list(
            allowed_user_ids
        )
        allowed_ordered: list[str] = []
        for candidate in ([authed_user_id] if authed_user_id else []) + extras:
            if candidate and candidate not in allowed_ordered:
                allowed_ordered.append(candidate)
        allowed_user_ids = tuple(allowed_ordered)
        global_signing_secret = str(stored_cfg.get("signing_secret") or "") or global_signing_secret

    rows: list[Integration] = []
    if load_integration:
        with contextlib.suppress(Exception):
            rows_raw = await repo.list_active(session, provider="slack")
            if isinstance(rows_raw, list):
                rows = rows_raw

    matching_rows: list[Integration] = []
    valid_agent_ids = set(_legacy_agent_ids(normalized_agent))
    for row in rows:
        row_workspace_id = str(getattr(row, "workspace_id", "") or "")
        if team_id and row_workspace_id and row_workspace_id != team_id:
            continue
        if _row_agent_id(row) in valid_agent_ids:
            matching_rows.append(row)

    integration = matching_rows[0] if matching_rows else None

    access_token = ""
    signing_secret = ""
    bot_user_id = ""
    allowed_channel_ids: tuple[str, ...] = _default_allowed_channel_ids(normalized_agent)
    listen_channel_messages = False

    if integration is not None:
        try:
            creds_raw = decrypt_credentials(bytes(integration.encrypted_credentials))
            creds = creds_raw if isinstance(creds_raw, dict) else {}
        except Exception:
            creds = {}

        metadata_raw = getattr(integration, "metadata_", {}) or {}
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

        access_token = str(
            creds.get("access_token") or creds.get("bot_token") or creds.get("token") or ""
        )
        signing_secret = str(
            creds.get("signing_secret") or metadata.get("signing_secret") or ""
        ).strip()
        bot_user_id = str(getattr(integration, "bot_user_id", "") or creds.get("bot_user_id") or "")
        configured_channels = _parse_allowed_user_ids(
            metadata.get("allowed_channel_ids") or creds.get("allowed_channel_ids")
        )
        if configured_channels:
            allowed_channel_ids = tuple(dict.fromkeys(configured_channels))
        listen_channel_messages = bool(
            metadata.get("listen_channel_messages", creds.get("listen_channel_messages", False))
        )

    if normalized_agent == "artemis" and not signing_secret:
        signing_secret = global_signing_secret

    if normalized_agent != "artemis" and not signing_secret:
        raise ValueError(f"Slack signing secret is not configured for agent={normalized_agent!r}")

    return _SlackAgentConfig(
        agent_id=normalized_agent,
        signing_secret=signing_secret,
        access_token=access_token,
        bot_user_id=bot_user_id,
        authed_user_id=authed_user_id,
        allowed_user_ids=allowed_user_ids,
        allowed_channel_ids=allowed_channel_ids,
        listen_channel_messages=listen_channel_messages,
    )


def _is_authorized_inbound(
    *,
    agent_cfg: _SlackAgentConfig,
    channel_id: str,
    channel_type: str,
    user_id: str,
) -> bool:
    is_dm = channel_type == "im" or channel_id.startswith("D")
    if agent_cfg.agent_id == "artemis":
        return agent_cfg.is_user_allowed(user_id)
    if is_dm:
        return True
    return agent_cfg.is_channel_allowed(channel_id)


def _should_handle_event(
    *,
    agent_cfg: _SlackAgentConfig,
    inner_type: str,
    channel_type: str,
) -> bool:
    if inner_type == "app_mention":
        return True
    if inner_type != "message":
        return False
    if channel_type == "im":
        return True
    return agent_cfg.listen_channel_messages


# ── HMAC verification ─────────────────────────────────────────────────────────


def _verify_slack_signature(
    body: bytes, timestamp: str, signature: str, signing_secret: str
) -> bool:
    """Return True iff the Slack HMAC-SHA256 signature is valid and the timestamp is fresh."""
    if not signing_secret:
        return False
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except ValueError:
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(signing_secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── route_inbound stub ────────────────────────────────────────────────────────


async def _post_slack_message(
    *,
    session_id: str,
    normalized_agent: str,
    team_id: str,
    channel_id: str,
    reply_thread_ts: str | None,
    outbound_text: str,
) -> None:
    """Post a message to Slack — shared by the normal path and the confirm path."""
    import artemis.db as _db

    try:
        async with _db.SessionLocal() as db_session:
            from artemis.integrations.slack.client import SlackClient

            agent_cfg = await _resolve_agent_slack_config(
                db_session,
                agent_id=normalized_agent,
                team_id=team_id,
            )
            if not agent_cfg.access_token:
                logger.warning(
                    "route_inbound: no Slack access token configured for agent=%s",
                    normalized_agent,
                )
                return
            client = SlackClient(token=agent_cfg.access_token)
            await client.post_message(
                channel=channel_id,
                text=outbound_text,
                thread_ts=reply_thread_ts,
            )
    except Exception:
        logger.exception("route_inbound: failed to post Slack reply for session %s", session_id)


async def route_inbound(
    event_data: dict[str, object],
    *,
    agent_id: str = "artemis",
    ping_user_id: str | None = None,
    confirm_classifier: ConfirmClassifier | None = None,
) -> None:
    """Route an inbound Slack event into a floating-artemis session.

    Session key: (team_id, channel_id, thread_ts or "_").
    The session is marked surface=slack so the web UI panel doesn't show it.
    After handle_turn completes, the response text is posted back in-thread.

    **Conversational layer-3 confirm flow (no buttons):**
    When a session has a pending layer-3 confirmation (from a previous turn
    that yielded), the inbound text is classified as YES / NO / NEITHER before
    running a new turn:
      - YES  -> resume_after_confirm("run")  -> post result text to Slack.
      - NO   -> resume_after_confirm("cancel") -> post brief ack.
      - NEITHER -> fall through to normal handle_turn (pending stays intact).

    **DB-backed staged-updates confirm flow (subscription/claude-code path):**
    When the speaker has a live breadcrumb with non-empty staged_updates (set by
    stage_okr_updates layer-1 tool), the inbound text is classified BEFORE running
    a new turn:
      - YES ("go") -> apply each staged update server-side (KR row + activity log),
        clear staged_updates, complete the breadcrumb, post result.
      - NO -> clear staged_updates, post brief ack.
      - NEITHER -> fall through to normal handle_turn (staged_updates remain).
    This path works regardless of adapter (main process, reads the DB directly).

    Args:
        event_data: Slack event fields (team_id, channel, user, text, ts, thread_ts).
        agent_id: Which agent to route to.
        ping_user_id: When set, prepend ``<@{ping_user_id}>`` to the outbound text
            (cold-start / re-engagement @mention).  None = no ping (active flow or DM).
        confirm_classifier: Injectable callable for the YES/NO/NEITHER classifier.
            Defaults to the real haiku-tier LLM classifier.  Pass a fake in tests.
    """
    team_id = str(event_data.get("team_id", ""))
    channel_id = str(event_data.get("channel", ""))
    thread_ts = event_data.get("thread_ts")
    text = str(event_data.get("text", ""))
    slack_user_id = str(event_data.get("user", ""))

    if not team_id or not channel_id or not text:
        logger.warning("route_inbound: missing required fields in event_data")
        return

    normalized_agent = _normalize_agent_id(agent_id)

    # Stable session key -- one FA session per Slack thread (or channel if not threaded)
    bucket = str(thread_ts) if thread_ts else "_"
    session_id = f"slack-{normalized_agent}-{team_id}-{channel_id}-{bucket}"
    # Reply in-thread ONLY when the source was already a thread reply.
    # Top-level mentions get top-level replies (no thread spam).
    reply_thread_ts = str(thread_ts) if thread_ts else None

    import artemis.db as _db
    from artemis.floating_artemis import repository as fa_repo
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.chat import handle_turn, resume_after_confirm

    # Resolve the speaker's display name from the J9b user cache so Artemis knows
    # who she's addressing.  Falls back to the raw id when the user isn't cached.
    speaker_name: str | None = None
    async with _db.SessionLocal() as db_session:
        if slack_user_id:
            cached = await repo.get_slack_user(db_session, slack_user_id)
            if cached is not None:
                speaker_name = cached.real_name or cached.name
        try:
            await fa_repo.get_session_by_id(db_session, session_id)
        except ValueError:
            await fa_repo.create_session(
                db_session,
                session_id=session_id,
                metadata={
                    "surface": "slack",
                    "agent_id": normalized_agent,
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "slack_user_id": slack_user_id,
                },
            )
            await db_session.commit()

    # ── Conversational confirm gate (in-process, web/intercepting path) ──────
    # If there is a pending layer-3 confirmation for this session, classify the
    # reply BEFORE running a new turn.  Only clear/unambiguous YES or NO resolves
    # the pending; anything else falls through to normal handle_turn.
    pendings = confirmation_store.list_for_session(session_id)
    if pendings:
        # Take the most recently added pending (last in list).
        pending = pendings[-1]
        _classifier = (
            confirm_classifier if confirm_classifier is not None else _default_confirm_classifier
        )
        verdict = await _classifier(text)
        logger.debug(
            "route_inbound: confirm classifier verdict=%r for session=%s tool_use_id=%s",
            verdict,
            session_id,
            pending.tool_use_id,
        )

        if verdict in ("YES", "NO"):
            decision = "run" if verdict == "YES" else "cancel"
            try:
                resume_result = await resume_after_confirm(
                    session_id=session_id,
                    tool_use_id=pending.tool_use_id,
                    decision=decision,
                )
            except Exception:
                logger.exception(
                    "route_inbound: resume_after_confirm failed for session=%s tool_use_id=%s",
                    session_id,
                    pending.tool_use_id,
                )
                return

            # Post the result text (or a fallback ack for cancel).
            if resume_result.response_text:
                outbound_text = lint_agent_text(resume_result.response_text)
            elif decision == "cancel":
                outbound_text = "Got it, cancelled."
            else:
                outbound_text = "Done."

            if outbound_text.strip():
                await _post_slack_message(
                    session_id=session_id,
                    normalized_agent=normalized_agent,
                    team_id=team_id,
                    channel_id=channel_id,
                    reply_thread_ts=reply_thread_ts,
                    outbound_text=outbound_text,
                )
            return

        # NEITHER -- fall through to normal turn; pending confirmation stays intact.
        logger.debug(
            "route_inbound: confirm classifier=NEITHER -- treating as new turn, pending stays for session=%s",
            session_id,
        )

    # ── DB-backed staged-updates gate (subscription/claude-code path) ────────
    # Check whether the speaker has a live breadcrumb with staged_updates.
    # This path runs regardless of adapter since it is in the main process.
    if slack_user_id:
        try:
            from artemis.proactivity.repository import (
                clear_staged_updates,
                complete_okr_checkin_breadcrumb,
                get_live_okr_checkin_breadcrumb,
            )

            async with _db.SessionLocal() as db_session:
                crumb = await get_live_okr_checkin_breadcrumb(db_session, slack_user_id)

            if crumb is not None and crumb.staged_updates:
                staged: list[dict[str, Any]] = list(crumb.staged_updates)
                _classifier = (
                    confirm_classifier
                    if confirm_classifier is not None
                    else _default_confirm_classifier
                )
                verdict = await _classifier(text)
                logger.debug(
                    "route_inbound: staged-updates classifier verdict=%r for session=%s speaker=%s",
                    verdict,
                    session_id,
                    slack_user_id,
                )

                if verdict == "YES":
                    # Apply staged updates server-side.
                    applied: list[str] = []
                    try:
                        from artemis.okr import repository as okr_repo

                        async with _db.SessionLocal() as db_session:
                            for item in staged:
                                kr_id = int(item["kr_id"])
                                # KR.prog is an Integer column; coerce to int.
                                progress = int(round(float(item["progress"])))
                                basis = str(item.get("basis") or "").strip()
                                # bullet: model-authored at stage time; fall back to
                                # a trimmed basis if no bullet was provided.
                                bullet = str(item.get("bullet") or "").strip() or basis[:200]
                                await okr_repo.update_key_result(db_session, kr_id, prog=progress)
                                activity_text = "updated via Friday check-in, approved by Jon" + (
                                    f" -- basis: {basis}" if basis else ""
                                )
                                await okr_repo.create_activity(
                                    db_session,
                                    kr_id=kr_id,
                                    text=activity_text,
                                    raw_text=basis or None,
                                )
                                # Append the accomplishment bullet to done_bullets (lossless).
                                await okr_repo.append_done_bullet(db_session, kr_id, bullet)
                                applied.append(f"KR {kr_id} -> {progress}")
                            await clear_staged_updates(db_session, crumb.id)
                            await complete_okr_checkin_breadcrumb(db_session, crumb.id)
                            await db_session.commit()

                        kr_summary = ", ".join(applied) if applied else "no changes"
                        outbound_text = f"Done. {kr_summary}."
                    except Exception:
                        logger.exception(
                            "route_inbound: staged-updates apply failed for session=%s speaker=%s",
                            session_id,
                            slack_user_id,
                        )
                        outbound_text = "Something went wrong applying the staged updates."

                    await _post_slack_message(
                        session_id=session_id,
                        normalized_agent=normalized_agent,
                        team_id=team_id,
                        channel_id=channel_id,
                        reply_thread_ts=reply_thread_ts,
                        outbound_text=outbound_text,
                    )
                    return

                elif verdict == "NO":
                    # Clear staged updates, nothing written.
                    try:
                        async with _db.SessionLocal() as db_session:
                            await clear_staged_updates(db_session, crumb.id)
                            await db_session.commit()
                    except Exception:
                        logger.exception(
                            "route_inbound: staged-updates clear failed for session=%s speaker=%s",
                            session_id,
                            slack_user_id,
                        )

                    await _post_slack_message(
                        session_id=session_id,
                        normalized_agent=normalized_agent,
                        team_id=team_id,
                        channel_id=channel_id,
                        reply_thread_ts=reply_thread_ts,
                        outbound_text="Cleared, nothing changed.",
                    )
                    return

                # NEITHER -- fall through to normal turn; staged_updates remain.
                logger.debug(
                    "route_inbound: staged-updates classifier=NEITHER -- "
                    "treating as new turn, staged intact for session=%s",
                    session_id,
                )
        except Exception:
            logger.exception(
                "route_inbound: staged-updates gate failed for session=%s speaker=%s -- continuing",
                session_id,
                slack_user_id,
            )

    # ── Deterministic commitment command path (DB-backed, no session state) ──
    # Follow-up messages include explicit `done <id>` / `snooze <id> ...`
    # commands so commitment lifecycle updates can execute in the main process
    # without relying on the reactive in-memory confirmation store.
    try:
        from artemis.proactivity.commitments import try_apply_commitment_reply

        async with _db.SessionLocal() as db_session:
            commitment_result = await try_apply_commitment_reply(
                db_session,
                text=text,
            )
        if commitment_result:
            await _post_slack_message(
                session_id=session_id,
                normalized_agent=normalized_agent,
                team_id=team_id,
                channel_id=channel_id,
                reply_thread_ts=reply_thread_ts,
                outbound_text=commitment_result,
            )
            return
    except Exception:
        logger.exception(
            "route_inbound: commitment command path failed for session=%s speaker=%s -- continuing",
            session_id,
            slack_user_id,
        )

    # ── Normal turn ───────────────────────────────────────────────────────────
    try:
        result = await handle_turn(
            session_id=session_id,
            user_text=text,
            speaker_name=speaker_name,
            speaker_id=slack_user_id if slack_user_id else None,
        )
    except Exception:
        logger.exception("route_inbound: handle_turn failed for session %s", session_id)
        return

    response_text = result.response_text
    if not response_text:
        return
    outbound_text = lint_agent_text(response_text)
    if not outbound_text.strip():
        logger.warning("route_inbound: linted Slack reply became empty for session %s", session_id)
        return

    # Prepend @mention AFTER linting — only on cold-start / re-engagement
    if ping_user_id:
        outbound_text = f"<@{ping_user_id}> {outbound_text}"

    await _post_slack_message(
        session_id=session_id,
        normalized_agent=normalized_agent,
        team_id=team_id,
        channel_id=channel_id,
        reply_thread_ts=reply_thread_ts,
        outbound_text=outbound_text,
    )


# ── Bot-self / non-human filter ────────────────────────────────────────────────


def _is_bot_authored(event: dict[str, object], bot_user_id: str) -> bool:
    """Return True if this event was authored by a bot (incl. Artemis herself).

    Without this guard, Artemis's own replies — delivered back to the app as
    `message.im` / `app_mention` events — would be re-dispatched into the agent
    loop, producing an infinite echo loop.  Slack marks bot-authored messages
    with a ``bot_id`` and/or ``subtype == "bot_message"``; we also match our own
    ``bot_user_id`` as a belt-and-suspenders check.
    """
    if event.get("bot_id"):
        return True
    if str(event.get("subtype", "")) == "bot_message":
        return True
    return bool(bot_user_id) and str(event.get("user", "")) == bot_user_id


# ── Event handler helper ──────────────────────────────────────────────────────


async def _handle_mentionable_event(
    payload: dict[str, object],
    event: dict[str, object],
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    *,
    agent_id: str,
    inner_type: str = "",
    channel_classifier: ChannelClassifier | None = None,
) -> None:
    """Dedupe and background-dispatch a mentionable event (app_mention / im message).

    Three guards stand between an inbound event and the agent loop:
      1. Bot-self filter — drop bot-authored events (kills the reply echo loop).
      2. Allowlist gate — only dispatch when the sender is permitted to converse.
         Fail-closed: when no allowlist is configured, nothing is routed.
         Non-allowed human messages are still recorded for audit/triage.
      3. Relevance gate (channel messages only) — for listen_channel_messages agents
         (e.g. Callie), channel ``message`` events are only dispatched when the
         agent is already in the thread OR the message is a marketing question.
         app_mention and DM messages bypass this gate.

    Args:
        inner_type: The Slack event ``type`` field (e.g. ``"app_mention"`` or ``"message"``).
            Used to decide whether to run the relevance gate.
        channel_classifier: Injectable callable for the relevance gate — default is
            the real cheap haiku classifier.  Pass a fake in tests.
    """
    from artemis.integrations.slack.triage import classify_mention_type

    event_id: str = str(payload.get("event_id", ""))
    team_id: str = str(payload.get("team_id", ""))
    channel_id: str = str(event.get("channel", ""))
    channel_type: str = str(event.get("channel_type", ""))
    user_id: str = str(event.get("user", ""))
    ts: str = str(event.get("ts", ""))
    thread_ts: str | None = str(event["thread_ts"]) if "thread_ts" in event else None
    raw_text: str | None = str(event["text"]) if "text" in event else None

    try:
        agent_cfg = await _resolve_agent_slack_config(
            session,
            agent_id=agent_id,
            team_id=team_id,
            load_integration=True,
        )
    except Exception:
        logger.exception("Slack config resolution failed for agent=%s", agent_id)
        return

    # ── Guard 1: bot-self filter — drop bot-authored events (no record, no loop) ─
    if _is_bot_authored(event, agent_cfg.bot_user_id):
        logger.debug("Slack event_id=%s is bot-authored — dropped (echo guard)", event_id)
        return

    # Strip bot-mention prefix from app_mention messages
    text: str | None = _BOT_MENTION_RE.sub("", raw_text).strip() if raw_text else raw_text

    mention_type = classify_mention_type(text or "", agent_cfg.authed_user_id)

    # Record every real (human) inbound for audit/triage, allowed or not.
    is_new = await repo.upsert_slack_inbound(
        session,
        event_id=event_id,
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        mention_type=mention_type,
    )
    await session.commit()

    if not is_new:
        logger.debug("Duplicate Slack event_id=%s — ignored", event_id)
        return

    # ── Guard 2: allowlist gate — only allowed senders reach the agent loop ─────
    if not _is_authorized_inbound(
        agent_cfg=agent_cfg,
        channel_id=channel_id,
        channel_type=channel_type,
        user_id=user_id,
    ):
        logger.info(
            "Slack inbound for agent=%s was recorded but not routed (user=%s channel=%s event_id=%s)",
            agent_cfg.agent_id,
            user_id,
            channel_id,
            event_id,
        )
        return

    # ── Guard 3: relevance gate (channel messages for listen_channel_messages agents) ─
    # app_mention and DM messages bypass this gate.
    is_dm = channel_type == "im" or channel_id.startswith("D")
    is_direct_mention = inner_type == "app_mention"
    needs_gate = (
        not is_direct_mention
        and not is_dm
        and inner_type == "message"
        and agent_cfg.listen_channel_messages
    )

    # Build session_id here (mirrors route_inbound logic) so the gate can check history.
    normalized_agent = _normalize_agent_id(agent_cfg.agent_id)
    bucket = str(thread_ts) if thread_ts else "_"
    session_id = f"slack-{normalized_agent}-{team_id}-{channel_id}-{bucket}"

    if needs_gate:
        should_respond = await should_respond_to_channel_message(
            is_mention=False,
            session_id=session_id,
            text=text or "",
            classifier=channel_classifier,
        )
        if not should_respond:
            logger.debug(
                "Slack channel message event_id=%s — relevance gate: silent (no prior participation, classifier=NO)",
                event_id,
            )
            return

    # ── Ping decision — @mention the asker on cold-start / re-engagement ─────────
    # Never ping in DMs. Never ping for Artemis personal DM path.
    ping_user_id: str | None = None
    if not is_dm and agent_cfg.agent_id != "artemis":
        last_ts = await _last_message_timestamp(session_id)
        if should_ping_asker(is_dm=False, last_message_at=last_ts):
            ping_user_id = user_id if user_id else None

    event_data: dict[str, object] = {
        "event_id": event_id,
        "team_id": team_id,
        "channel": channel_id,
        "user": user_id,
        "text": text,
        "ts": ts,
        "thread_ts": thread_ts,
    }
    background_tasks.add_task(
        route_inbound,
        event_data,
        agent_id=agent_cfg.agent_id,
        ping_user_id=ping_user_id,
    )


# ── Main endpoint ─────────────────────────────────────────────────────────────


async def _slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    *,
    agent_id: str,
) -> JSONResponse:
    """Handle all incoming Slack Events API callbacks."""
    raw_body: bytes = await request.body()
    normalized_agent = _normalize_agent_id(agent_id)

    # ── 1. Parse JSON early so we can check for url_verification ─────────────
    try:
        payload: dict[str, object] = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    event_type: str = str(payload.get("type", ""))

    # ── 2. url_verification — no HMAC required (Slack docs) ──────────────────
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        return JSONResponse(status_code=200, content={"challenge": challenge})

    # ── 3. Resolve signing secret (agent-aware, DB first with Artemis env fallback) ──
    try:
        agent_cfg = await _resolve_agent_slack_config(
            session,
            agent_id=normalized_agent,
            load_integration=normalized_agent != "artemis",
        )
    except Exception:
        agent_cfg = _SlackAgentConfig(
            agent_id=normalized_agent,
            signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
            access_token="",
            bot_user_id="",
            authed_user_id="",
            allowed_user_ids=(),
            allowed_channel_ids=_default_allowed_channel_ids(normalized_agent),
            listen_channel_messages=False,
        )

    # ── 4. HMAC verification ──────────────────────────────────────────────────
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(raw_body, timestamp, signature, agent_cfg.signing_secret):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    # ── 5. Dispatch by type ───────────────────────────────────────────────────
    if event_type == "event_callback":
        event: dict[str, object] = payload.get("event", {})  # type: ignore[assignment]
        if not isinstance(event, dict):
            event = {}
        inner_type: str = str(event.get("type", ""))
        channel_type: str = str(event.get("channel_type", ""))

        if _should_handle_event(
            agent_cfg=agent_cfg,
            inner_type=inner_type,
            channel_type=channel_type,
        ):
            await _handle_mentionable_event(
                payload,
                event,
                background_tasks,
                session,
                agent_id=agent_cfg.agent_id,
                inner_type=inner_type,
            )
        else:
            logger.debug(
                "Unhandled Slack inner event type=%r channel_type=%r — ignoring",
                inner_type,
                channel_type,
            )
    else:
        logger.debug("Unhandled Slack callback type=%r — ignoring", event_type)

    return JSONResponse(status_code=200, content={"ok": True})


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> JSONResponse:
    return await _slack_events(
        request,
        background_tasks,
        session,
        agent_id="artemis",
    )


@router.post("/events/{agent_id}")
async def slack_events_for_agent(
    agent_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> JSONResponse:
    return await _slack_events(
        request,
        background_tasks,
        session,
        agent_id=agent_id,
    )
