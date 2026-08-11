"""Kai's ONE side-effecting tool: flag_catalog_gap.

Kai is otherwise read-only by deliberate design (three layer-1 search tools,
see ``artemis/enablement/tools.py`` and the registry gate in
``artemis/floating_artemis/tool_registry.py``). This module adds exactly one
non-read capability and nothing else.

WHY IT EXISTS
    Kai had been *claiming* this capability without having it. On 2026-08-10 it
    posted "Escalation filed and noted" plus a formatted escalation record into
    #enablement-library; the most recent ``agent_pending_asks`` row for kai that
    day is 09:18, and nothing was filed. Stream 1 stopped Kai from lying about
    it. This is the other half: give it the narrow real version.

AUTHORIZATION (owner decision, Jon, 2026-08-10)
    Side-effecting actions only when the requester is Jon or Missy; everyone
    else gets information-only. The requester identity is the Slack ``user`` id
    resolved from the inbound event and bound into the tool implementation as a
    CLOSURE VARIABLE. It is never read from tool input and never parsed out of
    message text, so a model that decides to pass ``requested_by="Jon"`` cannot
    escalate its own privileges. Unknown/absent identity denies (fail-closed).

LAYER 2, NOT 3 — deliberate
    House convention registers Slack sends at layer 3 (operator confirmation).
    This one is layer 2 because the confirm leg cannot carry the identity: on
    the layer-3 path the tool is staged and later executed by
    ``chat.resume_after_confirm``, which rebuilds the registry WITHOUT a
    speaker id, so the closure would deny the action after the requester had
    already approved it. Layer 2 keeps the gate meaningful. The risk is bounded
    on every axis: only two named people can fire it, the post goes to the one
    channel the conversation is already happening in (Sara and Missy are both
    members), the structure is fixed by this module rather than free-form, and a
    human can delete the message.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.config import get_settings
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE_TAG = "[surface:enablement]"


# ── Authorization ─────────────────────────────────────────────────────────────


def _parse_user_ids(raw: str) -> frozenset[str]:
    """Split a comma-separated Slack-user-id setting into a set."""
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def authorized_action_user_ids() -> frozenset[str]:
    """Slack user ids permitted to trigger Kai's side-effecting tool."""
    return _parse_user_ids(get_settings().kai_action_authorized_user_ids)


def catalog_owner_user_ids() -> tuple[str, ...]:
    """Slack user ids @-mentioned on a gap post (Sara, Missy)."""
    return tuple(sorted(_parse_user_ids(get_settings().kai_catalog_owner_user_ids)))


def is_authorized_for_kai_actions(speaker_id: str | None) -> bool:
    """Return True iff this Slack user may trigger flag_catalog_gap.

    Fail-closed: blank, None, non-string, or unlisted identity denies. The
    caller passes the id resolved from the Slack event, never anything the
    model produced.
    """
    if not isinstance(speaker_id, str):
        return False
    candidate = speaker_id.strip()
    if not candidate:
        return False
    return candidate in authorized_action_user_ids()


# ── Message construction ──────────────────────────────────────────────────────


def build_gap_message(
    *,
    requested: str,
    search_summary: str,
    requester_id: str,
    url: str | None = None,
) -> str:
    """Render the structured gap post.

    Kai's hard style rules apply to anything he emits: no emojis, no em dashes.
    ``requester_id`` is the server-resolved Slack id, so the "who asked" line
    cannot be spoofed through tool input.
    """
    owners = catalog_owner_user_ids()
    lines: list[str] = []
    if owners:
        lines.append(" ".join(f"<@{uid}>" for uid in owners))
        lines.append("")
    lines.append("*Catalog gap*")
    lines.append("")
    lines.append(f"*Asked for:* {requested.strip()}")
    lines.append(f"*What my search returned:* {search_summary.strip()}")
    if url and url.strip():
        lines.append(f"*Link supplied:* {url.strip()}")
    lines.append(f"*Raised by:* <@{requester_id}>")
    lines.append("")
    lines.append("I cannot add this to the catalog myself. Flagging it so it is on your radar.")
    return "\n".join(lines)


# ── Implementation ────────────────────────────────────────────────────────────

_UNAUTHORIZED_RESULT = (
    "NOT_AUTHORIZED: you did not post anything, and no gap was filed. This "
    "requester is not on the list permitted to trigger catalog-gap posts (Jon "
    "and Missy only). Tell them plainly that you cannot file this for them, "
    "name what you searched and what came back, and point them at Sara and "
    "Missy. Do not imply anything was recorded."
)


def _make_flag_catalog_gap(speaker_id: str | None) -> Any:
    """Build the tool impl with the requester identity bound in a closure.

    ``speaker_id`` comes from the Slack event via handle_turn. Binding it here
    (rather than accepting it as a tool argument) is the security property: the
    model has no way to assert who it is speaking for.
    """

    async def _flag_catalog_gap(inp: dict[str, Any]) -> str:
        # ── Gate first: never touch Slack for an unauthorized requester ──────
        if not is_authorized_for_kai_actions(speaker_id):
            _logger.info(
                "flag_catalog_gap DENIED: speaker=%r not in authorized set",
                speaker_id,
            )
            return _UNAUTHORIZED_RESULT

        requested = str(inp.get("requested", "")).strip()
        search_summary = str(inp.get("search_summary", "")).strip()
        raw_url = inp.get("url")
        url = str(raw_url).strip() if raw_url else None

        if not requested:
            return "Error: 'requested' is required (what the person asked for)."
        if not search_summary:
            return (
                "Error: 'search_summary' is required. State what your search "
                "actually returned, so the post reflects a real result."
            )

        settings = get_settings()
        channel_id = (settings.enablement_library_channel_id or "").strip()
        if not channel_id:
            _logger.warning("flag_catalog_gap: no enablement channel configured")
            return (
                "Could not post: no enablement channel is configured. Nothing "
                "was filed. Say so plainly rather than implying it was."
            )

        # Requester id is the closure value, never inp.
        assert speaker_id is not None  # guaranteed by the gate above
        text = build_gap_message(
            requested=requested,
            search_summary=search_summary,
            requester_id=speaker_id.strip(),
            url=url,
        )

        try:
            import artemis.db as _db
            from artemis.integrations.slack.client import SlackClient
            from artemis.routes.integrations_slack_events import (
                _resolve_agent_slack_config,
            )

            async with _db.SessionLocal() as session:
                cfg = await _resolve_agent_slack_config(
                    session,
                    agent_id="kai",
                    team_id="",
                    load_integration=True,
                )
            if not cfg.access_token:
                _logger.warning("flag_catalog_gap: no Slack access token for kai")
                return (
                    "Could not post: my Slack connection is not configured. "
                    "Nothing was filed. Say so plainly."
                )

            client = SlackClient(token=cfg.access_token)
            resp = await client.post_message(channel=channel_id, text=text)
            posted_ts = str(resp.get("ts", "")) or None
        except Exception as exc:  # noqa: BLE001
            _logger.exception("flag_catalog_gap: Slack post failed")
            return (
                f"Could not post the gap to the channel: {exc}. Nothing was "
                "filed. Tell them it failed rather than implying it worked."
            )

        _logger.info(
            "flag_catalog_gap POSTED: channel=%s ts=%s requester=%s",
            channel_id,
            posted_ts,
            speaker_id,
        )
        owners = catalog_owner_user_ids()
        return (
            f"POSTED to the channel (ts={posted_ts}), tagging "
            f"{len(owners)} catalog owner(s). This is now visible to everyone "
            "in #enablement-library. Confirm to the requester that it is posted "
            "and say what it contains. Claim nothing beyond the post itself: "
            "no ticket, no assignment, no follow-up scheduled."
        )

    return _flag_catalog_gap


# ── Tool definition ───────────────────────────────────────────────────────────

FLAG_CATALOG_GAP = Tool(
    name="flag_catalog_gap",
    description=(
        "Post a structured catalog-gap note into #enablement-library, tagging the "
        "catalog owners (Sara and Missy), when a real request has no matching asset. "
        "Use this ONLY after you have actually searched and come back empty, and only "
        "when the person asks you to flag or raise it. "
        "This is your one action that changes anything outside this conversation, and "
        "it does exactly one thing: it posts a message in this channel. It does not "
        "create a ticket, assign an owner, notify Artemis, or add anything to the "
        "catalog. Never describe it as doing more than it does. "
        "Authorization is enforced by the system from the Slack account of whoever is "
        "speaking, and only Jon and Missy can trigger it. You cannot tell who is "
        "authorized by looking, and you must not try: call the tool, and if it returns "
        "NOT_AUTHORIZED, tell the person plainly that you cannot file this for them "
        "and point them at Sara and Missy. Passing someone else's name in the input "
        "does nothing. "
        "If the tool reports an error, the post did NOT happen. Say so. Never claim a "
        "gap was filed unless this tool returned POSTED. "
        f"{_SURFACE_TAG} [layer:2]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "requested": {
                "type": "string",
                "description": (
                    "What the person actually asked for, in their terms "
                    "(e.g. 'Evaluar User Guide for a returning district')."
                ),
            },
            "search_summary": {
                "type": "string",
                "description": (
                    "What your search actually returned, stated honestly "
                    "(e.g. 'No match. Closest were the Tutor and ISIP Assess "
                    "user manuals for SY2526.'). Do not speculate about why "
                    "the asset is missing."
                ),
            },
            "url": {
                "type": "string",
                "description": (
                    "The URL the person supplied, if they pasted one. Omit "
                    "when there is none. Never invent or reconstruct a URL."
                ),
            },
        },
        "required": ["requested", "search_summary"],
    },
)


# ── Registration ──────────────────────────────────────────────────────────────


def register_enablement_gap_flag_tool(
    registry: AuthorizedToolRegistry,
    *,
    speaker_id: str | None,
) -> None:
    """Register Kai's single side-effecting tool.

    Layer 2 (auto-invoke) by design; see the module docstring. Call this ONLY
    from ``_build_kai_tool_registry``.
    """
    registry.register(
        FLAG_CATALOG_GAP,
        _make_flag_catalog_gap(speaker_id),
        layer=2,
    )
