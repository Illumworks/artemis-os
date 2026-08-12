"""Render + post Callie's crisis-content review card (slice B2b/B2c, CCA4/CCA5).

Pure rendering (``render_char_count_line``, ``render_transition_message``,
``render_transition_blocks``, ``render_decision_message``) plus the one
function that actually talks to Slack (``post_transition_card``).
Persistence of the OBSERVED card is out of scope here --
``artemis/crisis_content/poller.py`` is the only caller of
``post_transition_card``, and it is the one that decides when
``mark_notified`` may be called (only after ``post_transition_card`` returns
without raising; see ``artemis/crisis_content/transitions.py`` module
docstring for why order matters). Persistence of the DECISION a click
produces is ``artemis.crisis_content.decisions`` / ``.slack_actions``, not
this module.

**Destination.** Only ``settings.crisis_content_notify_destination ==
"dm_jon"`` is implemented. Channel routing to ``C0BM9TL63TL`` plus the real
copy approvers (Angela, Hannah, Jaclyn) is explicitly out of scope for this
slice -- see ``briefs/cca4-poller-and-callie-card.md`` "OUT" list and
``docs/crisis-content-approval-pipeline.md`` "Routing". Jon's Slack user is
resolved by email (``users.lookupByEmail`` via ``SlackClient.lookup_user_by_email``),
never by listing users and filtering -- that call paginates and silently
misses people past the first page.

**Buttons (CCA5).** ``render_transition_blocks`` wraps
``render_transition_message``'s UNCHANGED text in a ``section`` block and
appends an ``actions`` block with the two decision buttons -- the card body
itself is not restyled (Jon has approved the current format). The button
``value`` is ``f"{card_id}:{route}"`` -- enough to identify the target, and
deliberately carrying no identity: see
``artemis/routes/integrations_slack_interactivity.py`` and
``artemis/crisis_content/slack_actions.py`` for why identity comes only from
the verified payload's ``user.id``, never from Block Kit content.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.transitions import Transition, find_card_id
from artemis.integrations.slack.client import SlackClient
from artemis.proactivity.radar import JON_EMAIL
from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

logger = logging.getLogger(__name__)

__all__ = [
    "TESTING_LINE",
    "TESTING_LINE_ASSET",
    "ACTION_APPROVE",
    "ACTION_REQUEST_CHANGES",
    "testing_line_for_route",
    "render_char_count_line",
    "render_transition_message",
    "render_transition_blocks",
    "render_decision_message",
    "post_transition_card",
]

# Block Kit action_ids for the two decision buttons. Defined here (the
# rendering module) rather than in slack_actions.py, which imports them --
# slack_actions.py also imports render_decision_message from this module, so
# defining them there instead would make the two modules import each other.
ACTION_APPROVE = "crisis_content_approve"
ACTION_REQUEST_CHANGES = "crisis_content_request_changes"

# The guard against anyone mistaking testing traffic for the live workflow.
# Stays on every card until routing is deliberately flipped (a future slice).
#
# Route-specific on purpose: per the routing table in
# docs/crisis-content-approval-pipeline.md, Jon approves visuals and the three
# copy approvers never see the asset route. A single shared line claiming
# "Live: Angela, Hannah, Jaclyn" on an asset card would state the opposite of
# the actual design.
TESTING_LINE = "⚠️ Testing — routed to you only. Live: Angela, Hannah, Jaclyn."
TESTING_LINE_ASSET = "⚠️ Testing — routed to you only. Live: you (visuals)."


def testing_line_for_route(route: str) -> str:
    """The testing footer for one route. See TESTING_LINE above for why."""
    return TESTING_LINE_ASSET if route == "asset" else TESTING_LINE


_DOC_URL = f"https://docs.google.com/document/d/{TARGET_DOCUMENT_ID}/edit"

# t.co wraps every URL in an X post to exactly 23 characters regardless of its
# real length. A naive len() on copy containing a long URL produces false
# "over 280" alarms -- see docs/crisis-content-approval-pipeline.md and the
# CCA4 brief's worked example (305 raw chars, 220 t.co-adjusted). This
# adjustment is X-specific: LinkedIn/Instagram/Facebook do not wrap links, so
# applying it there would understate the real length they actually enforce.
_URL_RE = re.compile(r"https?://\S+")
_TCO_URL_LENGTH = 23

# Only exact, known SINGLE platform values get a limit claim. Combo values
# (e.g. "FB, LI, & X"), "All", "TBD", and anything else Jen's user-editable
# dropdown produces are handled by the None branch in render_char_count_line
# -- raw count, no limit claim, never a guess at which limit applies.
_PLATFORM_LIMITS: dict[str, int] = {
    "X": 280,
    "Instagram": 2200,
    "LinkedIn": 3000,
    "Facebook": 63206,
}


def _tco_adjusted_length(text: str) -> int:
    """Length of ``text`` with every URL replaced by a 23-char placeholder."""
    return len(_URL_RE.sub("x" * _TCO_URL_LENGTH, text))


def render_char_count_line(copy_body: str, platform: str | None) -> str:
    """Render ``"<count> chars · fits <Platform> (<limit>)"``, or the over-limit form.

    X's count is t.co-adjusted before comparing against 280. Instagram/
    LinkedIn/Facebook use the raw length against their limit. An unset,
    unknown, or combo platform shows the raw count with no limit claim.
    """
    raw_count = len(copy_body)
    limit = _PLATFORM_LIMITS.get(platform) if platform else None
    if limit is None:
        if platform:
            return f"{raw_count} chars ({platform})"
        return f"{raw_count} chars"

    count = _tco_adjusted_length(copy_body) if platform == "X" else raw_count
    if count <= limit:
        return f"{count} chars · fits {platform} ({limit:,})"
    return f"{count} chars · OVER {platform}'s {limit:,} limit"


def _asset_status_line(card: ReviewCard) -> str:
    """The 'other route' status line shown on a COPY-route notification."""
    if card.asset_status is None:
        return "not set"
    if card.asset_url:
        return f"{card.asset_status} — {card.asset_url}"
    return f"still in {card.asset_status} — no visual attached yet"


def _copy_status_line(card: ReviewCard) -> str:
    """The 'other route' status line shown on an ASSET-route notification."""
    if card.copy_status is None:
        return "not set"
    return card.copy_status


def render_transition_message(transition: Transition) -> str:
    """Render the full Slack DM text for one transition.

    Full copy inline, never truncated -- the point is approving without
    opening the doc. Always notes the OTHER route's status too, so Jon can
    tell whether the post is actually shippable end to end.

    This is the card BODY only -- unchanged since CCA4, and it stays that
    way (Jon has approved the current format). ``render_transition_blocks``
    wraps this exact text in Block Kit and appends the decision buttons;
    this function never gained a ``blocks``/``action_id`` concept itself so
    that every existing caller of the plain-text body keeps working
    unchanged.
    """
    card = transition.card
    platform = card.platform or "unspecified platform"
    heading_date = f"{card.date_text} · {card.title}" if card.date_text else card.title

    lines: list[str] = []
    if transition.route == "copy":
        lines.append(f"📝 Copy ready for review — {platform}")
        lines.append(heading_date)
        lines.append("")
        lines.append(card.copy_body)
        lines.append("")
        lines.append(render_char_count_line(card.copy_body, card.platform))
        lines.append(f"Asset: {_asset_status_line(card)}")
    else:
        lines.append(f"🎨 Asset ready for review — {platform}")
        lines.append(heading_date)
        lines.append("")
        lines.append(card.copy_body)
        lines.append("")
        lines.append(render_char_count_line(card.copy_body, card.platform))
        lines.append(f"Copy: {_copy_status_line(card)}")
        lines.append(f"Asset link: {card.asset_url}")

    lines.append(f"Open the doc: {_DOC_URL}")
    lines.append("")
    lines.append(testing_line_for_route(transition.route))
    return "\n".join(lines)


def render_transition_blocks(transition: Transition, card_id: int) -> list[dict[str, Any]]:
    """Block Kit body for one transition: the card text, plus decision buttons.

    The ``section`` block's mrkdwn text is byte-for-byte
    ``render_transition_message``'s output -- this function only ADDS the
    ``actions`` block Slack needs to render working buttons; it never
    restyles the body (see the module docstring, "Buttons (CCA5)").

    The button ``value`` is ``f"{card_id}:{route}"``, carrying no identity --
    see ``artemis/routes/integrations_slack_interactivity.py`` and
    ``artemis/crisis_content/slack_actions.py`` for why the clicking user's
    identity comes ONLY from the verified payload's ``user.id``.
    """
    value = f"{card_id}:{transition.route}"
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": render_transition_message(transition)},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": ACTION_APPROVE,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request changes"},
                    "action_id": ACTION_REQUEST_CHANGES,
                    "value": value,
                },
            ],
        },
    ]


def render_decision_message(
    *,
    decision: Literal["approved", "changes_requested"],
    actor_label: str,
    decided_at: datetime,
    note: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Render the post-decision replacement for Callie's card.

    Mirrors the existing ``pipeline_approval_*`` ``replace_original``
    convention already shipped in
    ``artemis/routes/integrations_slack_interactivity.py`` -- a short
    outcome line replaces the interactive card entirely. Per
    ``briefs/cca5-approval-loop.md`` "After a decision", this IS the
    double-click guard and the clicker's only feedback that their tap
    registered -- it does not attempt to preserve the original copy/asset
    body, which already lives in ``crisis_content_decisions`` and (for
    copy) the copy-version log; nothing downstream reads it back off the
    Slack message itself.

    Returns ``(text, blocks)`` -- ``text`` is the plain-text fallback Slack
    shows in notifications/previews; ``blocks`` is what actually renders.
    """
    stamp = decided_at.strftime("%-I:%M%p").lower()
    if decision == "approved":
        emoji, label = "✅", "Approved"
    else:
        emoji, label = "✏️", "Changes requested"

    text = f"{emoji} {label} by {actor_label} · {stamp}"
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{label}* by {actor_label} · {stamp}"},
        }
    ]
    if note:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": note}})
    return text, blocks


async def post_transition_card(session: AsyncSession, transition: Transition) -> None:
    """Post ``transition``'s rendered card (text + decision buttons) as a Slack DM to Jon, as Callie.

    Raises (never swallows) on any failure -- missing Callie token, a failed
    ``users.lookupByEmail``, no persisted card row, or a Slack API error --
    so the caller does not call ``mark_notified`` for a post that did not
    actually happen. Never posts to a channel: only
    ``crisis_content_notify_destination == "dm_jon"`` is implemented; any
    other value is logged as an ERROR and treated as ``dm_jon`` rather than
    silently doing nothing or guessing at a channel to use.

    Requires ``transition.card`` to already have a persisted
    ``CrisisContentCard`` row (i.e. this must be called after
    ``record_observation`` has upserted it) -- the button values need the
    row's id, and there is no card to decide on if it doesn't exist yet.
    Every real caller (``artemis/crisis_content/poller.py``) satisfies this
    by construction.
    """
    destination = settings.crisis_content_notify_destination
    if destination != "dm_jon":
        logger.error(
            "crisis_content: notify destination %r is not implemented in this "
            "slice (only 'dm_jon' is wired -- channel + real-approver routing "
            "is a later slice); falling back to dm_jon",
            destination,
        )

    card_id = await find_card_id(session, transition.card)
    if card_id is None:
        raise RuntimeError(
            "crisis_content: no persisted CrisisContentCard row for "
            f"identity={transition.card.identity_key!r} -- post_transition_card "
            "must be called after record_observation has upserted the row"
        )

    agent_cfg = await _resolve_agent_slack_config(session, agent_id="callie", team_id=None)
    if not agent_cfg.access_token:
        raise RuntimeError("crisis_content: no active Slack token for agent_id='callie'")

    client = SlackClient(token=agent_cfg.access_token)
    jon_slack_id = await client.lookup_user_by_email(JON_EMAIL)
    if not jon_slack_id:
        raise RuntimeError(
            f"crisis_content: users.lookupByEmail found no Slack user for {JON_EMAIL!r}"
        )

    text = render_transition_message(transition)
    blocks = render_transition_blocks(transition, card_id)
    await client.post_dm(user=jon_slack_id, text=text, blocks=blocks)
    logger.info(
        "crisis_content: posted %s-route card for card=%r to Jon (dm_jon)",
        transition.route,
        transition.card.identity_key,
    )
