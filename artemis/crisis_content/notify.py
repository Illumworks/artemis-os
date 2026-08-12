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

**Destination (CCA6).** ``settings.crisis_content_notify_destination`` picks
between the two states in ``docs/crisis-content-approval-pipeline.md``
"Routing":

- ``"live"`` (default): ``asset`` DMs Jon; ``copy`` posts to
  ``settings.crisis_content_copy_notify_channel`` (``C0BM9TL63TL``),
  @-mentioning the copy approvers. No ``TESTING_LINE*`` footer either way.
- ``"dm_jon"``: the CCA4/CCA5 override -- everything DMs Jon, with the
  ``TESTING_LINE*`` footer restored. This is the rollback: flip the setting,
  no deploy, to instantly undo a bad channel-routing change.

Every Slack user (Jon, or a copy approver) is resolved by email
(``users.lookupByEmail`` via ``SlackClient.lookup_user_by_email``), never by
listing users and filtering -- that call paginates and silently misses
people past the first page. Copy-approver resolutions are cached at module
level (``_approver_slack_id_cache``) so a 2-minute poll tick does not repeat
three lookups it already has the answer to; only successful resolutions are
cached, so a currently-unresolvable email (e.g. a new hire mid-onboarding)
is retried on the next tick rather than permanently assumed missing.

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
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.transitions import Transition, find_card_id
from artemis.integrations.slack.client import SlackAPIError, SlackClient
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
    "copy_mention_emails",
    "reset_notify_caches_for_tests",
]

# Block Kit action_ids for the two decision buttons. Defined here (the
# rendering module) rather than in slack_actions.py, which imports them --
# slack_actions.py also imports render_decision_message from this module, so
# defining them there instead would make the two modules import each other.
ACTION_APPROVE = "crisis_content_approve"
ACTION_REQUEST_CHANGES = "crisis_content_request_changes"

# The guard against anyone mistaking testing traffic for the live workflow.
# Used only under the `dm_jon` rollback override (CCA6) -- see
# `_post_dm_jon_override` below. Kept, word-for-word, so the rollback path
# stays honest about what it is: a real card that only Jon can see, not the
# live audience.
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


# Email -> Slack user id. Module-level and process-lifetime on purpose: the
# poller ticks every `crisis_content_poll_interval_minutes` (default 2), and
# CCA6 explicitly asks not to re-resolve the three copy approvers on every
# tick. Only successful resolutions go in -- see `_resolve_slack_id_cached`.
_approver_slack_id_cache: dict[str, str] = {}


def reset_notify_caches_for_tests() -> None:
    """Test-only: clear the module-level Slack-id cache between tests."""
    _approver_slack_id_cache.clear()


async def _resolve_slack_id_cached(client: SlackClient, email: str) -> str | None:
    """Resolve ``email`` to a Slack user id via ``lookup_user_by_email``, caching hits.

    Only a successful resolution is cached. Caching a miss would risk
    permanently hiding an approver whose Slack account shows up later (e.g.
    mid-onboarding); the cost of not caching a miss is one extra
    ``users.lookupByEmail`` call on the next poll tick, which is cheap.
    """
    cached = _approver_slack_id_cache.get(email)
    if cached is not None:
        return cached
    slack_id = await client.lookup_user_by_email(email)
    if slack_id:
        _approver_slack_id_cache[email] = slack_id
    return slack_id


def copy_mention_emails() -> list[str]:
    """Emails to @-mention on a live copy-route card, in configured order.

    This is ``settings.crisis_content_copy_approver_emails`` (the CCA5
    authorization allowlist) MINUS Jon's email. Jon was added to that
    allowlist on 2026-08-11 as a standing authorization backstop -- he CAN
    click Approve on a copy card if all three primaries are unavailable --
    but the card stays *addressed* to Angela, Hannah, and Jaclyn only (see
    docs/crisis-content-approval-pipeline.md "Routing" and
    settings.crisis_content_copy_approver_emails' own docstring). Mentioning
    him here would misstate him as a fourth routine approver.
    """
    emails = [e.strip() for e in settings.crisis_content_copy_approver_emails.split(",") if e.strip()]
    return [e for e in emails if e.lower() != JON_EMAIL.lower()]


def _copy_live_footer(mention_tags: list[str]) -> str:
    """``"Any one of <@U1>, <@U2> or <@U3> can approve."`` for however many resolved."""
    if len(mention_tags) == 1:
        who = mention_tags[0]
    else:
        who = ", ".join(mention_tags[:-1]) + " or " + mention_tags[-1]
    return f"Any one of {who} can approve."


async def _resolve_copy_mentions(client: SlackClient) -> tuple[list[str], list[str]]:
    """Resolve ``copy_mention_emails()`` to Slack mention tags.

    Returns ``(mention_tags, unresolved_emails)`` -- ``mention_tags`` are
    ``"<@U...>"`` strings ready to drop into mrkdwn text, in the same order
    as ``copy_mention_emails()``; an email that fails to resolve is placed
    in ``unresolved_emails`` instead, never as a placeholder in
    ``mention_tags``, so the caller can post with whoever DID resolve
    without further filtering (CCA6: "one unresolvable approver must not
    kill the notification").
    """
    mention_tags: list[str] = []
    unresolved: list[str] = []
    for email in copy_mention_emails():
        slack_id = await _resolve_slack_id_cached(client, email)
        if slack_id:
            mention_tags.append(f"<@{slack_id}>")
        else:
            unresolved.append(email)
    return mention_tags, unresolved


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


def render_transition_message(transition: Transition, *, footer: str) -> str:
    """Render the full Slack DM/channel text for one transition.

    Full copy inline, never truncated -- the point is approving without
    opening the doc. Always notes the OTHER route's status too, so the
    reader can tell whether the post is actually shippable end to end.

    The card BODY (heading, copy, char count, other-route status, doc link)
    is unchanged since CCA4 and stays that way (Jon has approved the current
    format). ``footer`` is the one part of the rendered text CCA6 makes
    caller-controlled: the trailing line was already route-specific before
    this slice (``testing_line_for_route``); CCA6 extends the same seam so a
    caller can pass the ``dm_jon`` testing line, a live "who can approve"
    line, or "" (no footer at all, for the live asset route) without
    touching anything above it. An empty ``footer`` omits the line AND the
    blank line that would otherwise separate it from the doc link.
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
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_transition_blocks(
    transition: Transition, card_id: int, *, footer: str
) -> list[dict[str, Any]]:
    """Block Kit body for one transition: the card text, plus decision buttons.

    The ``section`` block's mrkdwn text is byte-for-byte
    ``render_transition_message(transition, footer=footer)``'s output -- this
    function only ADDS the ``actions`` block Slack needs to render working
    buttons; it never restyles the body (see the module docstring, "Buttons
    (CCA5)"). ``footer`` is passed straight through -- see
    ``render_transition_message`` for what it controls.

    The button ``value`` is ``f"{card_id}:{route}"``, carrying no identity --
    see ``artemis/routes/integrations_slack_interactivity.py`` and
    ``artemis/crisis_content/slack_actions.py`` for why the clicking user's
    identity comes ONLY from the verified payload's ``user.id``.
    """
    value = f"{card_id}:{transition.route}"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": render_transition_message(transition, footer=footer),
            },
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


async def _post_dm_jon_override(
    client: SlackClient, transition: Transition, card_id: int
) -> None:
    """The ``dm_jon`` rollback: every route DMs Jon, testing footer restored."""
    jon_slack_id = await _resolve_slack_id_cached(client, JON_EMAIL)
    if not jon_slack_id:
        raise RuntimeError(
            f"crisis_content: users.lookupByEmail found no Slack user for {JON_EMAIL!r}"
        )
    footer = testing_line_for_route(transition.route)
    text = render_transition_message(transition, footer=footer)
    blocks = render_transition_blocks(transition, card_id, footer=footer)
    await client.post_dm(user=jon_slack_id, text=text, blocks=blocks)
    logger.info(
        "crisis_content: posted %s-route card for card=%r to Jon (dm_jon override)",
        transition.route,
        transition.card.identity_key,
    )


async def _post_live_asset(
    client: SlackClient, transition: Transition, card_id: int
) -> None:
    """Live routing, ``asset`` route: DM Jon, no footer (he owns visuals)."""
    jon_slack_id = await _resolve_slack_id_cached(client, JON_EMAIL)
    if not jon_slack_id:
        raise RuntimeError(
            f"crisis_content: users.lookupByEmail found no Slack user for {JON_EMAIL!r}"
        )
    text = render_transition_message(transition, footer="")
    blocks = render_transition_blocks(transition, card_id, footer="")
    await client.post_dm(user=jon_slack_id, text=text, blocks=blocks)
    logger.info(
        "crisis_content: posted asset-route card for card=%r to Jon (live)",
        transition.card.identity_key,
    )


async def _post_live_copy(
    client: SlackClient, transition: Transition, card_id: int
) -> None:
    """Live routing, ``copy`` route: post to the channel, mentioning whoever resolved.

    One unresolvable approver must not kill the notification (CCA6) -- post
    with the ones that DID resolve and log a WARNING naming the rest. Only
    if NONE resolve do we refuse to post (a card naming nobody is not a
    notification), which surfaces as this function raising -- the caller
    (``post_transition_card``) never calls ``mark_notified`` for a raise, so
    the next tick retries.
    """
    mention_tags, unresolved = await _resolve_copy_mentions(client)
    for email in unresolved:
        logger.warning(
            "crisis_content: could not resolve a Slack id for copy approver "
            "%r via users.lookupByEmail -- posting without mentioning them; "
            "%d of %d approver(s) resolved and will still be mentioned.",
            email,
            len(mention_tags),
            len(mention_tags) + len(unresolved),
        )
    if not mention_tags:
        raise RuntimeError(
            "crisis_content: none of the copy approvers "
            f"({', '.join(copy_mention_emails())}) resolved to a Slack id via "
            "users.lookupByEmail -- refusing to post a copy-route card that "
            "would name nobody"
        )

    channel = settings.crisis_content_copy_notify_channel
    footer = _copy_live_footer(mention_tags)
    text = render_transition_message(transition, footer=footer)
    blocks = render_transition_blocks(transition, card_id, footer=footer)
    try:
        # SlackClient.post_message declares `blocks: list[object] | None`,
        # invariant -- `list[dict[str, Any]]` (what render_transition_blocks
        # returns) is not a `list[object]` under mypy's variance rules even
        # though it obviously satisfies the runtime contract. post_dm takes
        # the covariant `Sequence[object]` instead and needs no cast; this
        # cast is the narrower, local fix rather than widening
        # SlackClient.post_message's signature, which is out of this
        # slice's file scope.
        await client.post_message(channel=channel, text=text, blocks=cast("list[object]", blocks))
    except SlackAPIError as exc:
        if exc.error == "not_in_channel":
            raise RuntimeError(
                f"crisis_content: Callie's bot user is not a member of channel "
                f"{channel!r} -- chat.postMessage failed with 'not_in_channel'. "
                "Invite Callie to the channel; every copy-route notification "
                "will fail and retry until she is."
            ) from exc
        raise
    logger.info(
        "crisis_content: posted copy-route card for card=%r to channel %s "
        "(live, mentioned=%s, unresolved=%s)",
        transition.card.identity_key,
        channel,
        mention_tags,
        unresolved,
    )


async def post_transition_card(session: AsyncSession, transition: Transition) -> None:
    """Post ``transition``'s rendered card (text + decision buttons), as Callie.

    Destination is ``settings.crisis_content_notify_destination`` -- see the
    module docstring's "Destination (CCA6)" section for the two states.
    Raises (never swallows) on any failure -- missing Callie token, a failed
    ``users.lookupByEmail`` for Jon (both DM paths) or for EVERY one of the
    copy approvers (the live copy path tolerates one or two failing to
    resolve -- see ``_post_live_copy``), no persisted card row, or a Slack
    API error -- so the caller does not call ``mark_notified`` for a post
    that did not actually happen.

    Requires ``transition.card`` to already have a persisted
    ``CrisisContentCard`` row (i.e. this must be called after
    ``record_observation`` has upserted it) -- the button values need the
    row's id, and there is no card to decide on if it doesn't exist yet.
    Every real caller (``artemis/crisis_content/poller.py``) satisfies this
    by construction.
    """
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

    if settings.crisis_content_notify_destination == "dm_jon":
        await _post_dm_jon_override(client, transition, card_id)
        return

    if transition.route == "asset":
        await _post_live_asset(client, transition, card_id)
    else:
        await _post_live_copy(client, transition, card_id)
