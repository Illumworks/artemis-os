"""Render + post Callie's crisis-content review card (slice B2b, CCA4).

Pure rendering (``render_char_count_line``, ``render_transition_message``)
plus the one function that actually talks to Slack (``post_transition_card``).
No persistence here -- ``artemis/crisis_content/poller.py`` is the only
caller, and it is the one that decides when ``mark_notified`` may be called
(only after ``post_transition_card`` returns without raising; see
``artemis/crisis_content/transitions.py`` module docstring for why order
matters).

**Destination.** Only ``settings.crisis_content_notify_destination ==
"dm_jon"`` is implemented. Channel routing to ``C0BM9TL63TL`` plus the real
copy approvers (Angela, Hannah, Jaclyn) is explicitly out of scope for this
slice -- see ``briefs/cca4-poller-and-callie-card.md`` "OUT" list and
``docs/crisis-content-approval-pipeline.md`` "Routing". Jon's Slack user is
resolved by email (``users.lookupByEmail`` via ``SlackClient.lookup_user_by_email``),
never by listing users and filtering -- that call paginates and silently
misses people past the first page.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.transitions import Transition
from artemis.integrations.slack.client import SlackClient
from artemis.proactivity.radar import JON_EMAIL
from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

logger = logging.getLogger(__name__)

__all__ = [
    "TESTING_LINE",
    "TESTING_LINE_ASSET",
    "testing_line_for_route",
    "render_char_count_line",
    "render_transition_message",
    "post_transition_card",
]

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
    tell whether the post is actually shippable end to end. No blocks, no
    ``action_id`` buttons -- CCA3 (Slack interactivity) is a separate slice
    that must not be blocked on, and buttons with no consumer today would be
    dead controls.
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


async def post_transition_card(session: AsyncSession, transition: Transition) -> None:
    """Post ``transition``'s rendered card as a Slack DM to Jon, as Callie.

    Raises (never swallows) on any failure -- missing Callie token, a failed
    ``users.lookupByEmail``, or a Slack API error -- so the caller does not
    call ``mark_notified`` for a post that did not actually happen. Never
    posts to a channel: only ``crisis_content_notify_destination == "dm_jon"``
    is implemented; any other value is logged as an ERROR and treated as
    ``dm_jon`` rather than silently doing nothing or guessing at a channel to
    use.
    """
    destination = settings.crisis_content_notify_destination
    if destination != "dm_jon":
        logger.error(
            "crisis_content: notify destination %r is not implemented in this "
            "slice (only 'dm_jon' is wired -- channel + real-approver routing "
            "is a later slice); falling back to dm_jon",
            destination,
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
    await client.post_dm(user=jon_slack_id, text=text)
    logger.info(
        "crisis_content: posted %s-route card for card=%r to Jon (dm_jon)",
        transition.route,
        transition.card.identity_key,
    )
