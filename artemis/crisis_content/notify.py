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

**Per-card test lane (CCA13).** ``transition.is_test`` -- set by
``artemis.crisis_content.transitions.record_observation`` from the tab
resolved for this card, see ``artemis.crisis_content.tab_resolution`` --
overrides ``crisis_content_notify_destination`` for THIS card only: a test
card always DMs Jon with the ``⚠️ Testing`` footer, exactly like the
``dm_jon`` override, no matter what ``crisis_content_notify_destination`` is
set to. This is checked FIRST in ``post_transition_card``, before the
global destination setting, because it is a property of the card (which
Google Docs tab it lives on), not a global rollback switch -- a live
channel that is otherwise routing normally must still never see Jon's
duplicated test card or @-mention the external vendor about it.

Every Slack user (Jon, or a copy approver) is resolved by email
(``users.lookupByEmail`` via ``SlackClient.lookup_user_by_email``), never by
listing users and filtering -- that call paginates and silently misses
people past the first page. Copy-approver resolutions are cached at module
level (``_approver_slack_id_cache``) so a 2-minute poll tick does not repeat
three lookups it already has the answer to; only successful resolutions are
cached, so a currently-unresolvable email (e.g. a new hire mid-onboarding)
is retried on the next tick rather than permanently assumed missing.

**Buttons (CCA5, ``Edit in doc`` replaces ``Request changes`` in CCA12).**
``render_transition_blocks`` wraps ``render_transition_message``'s UNCHANGED
text in a ``section`` block and appends an ``actions`` block with the two
decision buttons -- the card body itself is not restyled (Jon has approved
the current format). The button ``value`` is ``f"{card_id}:{route}"`` --
enough to identify the target, and deliberately carrying no identity: see
``artemis/routes/integrations_slack_interactivity.py`` and
``artemis/crisis_content/slack_actions.py`` for why identity comes only from
the verified payload's ``user.id``, never from Block Kit content.

**CCA12: ``Edit in doc`` carries BOTH ``url`` and ``action_id``.** Per
Slack's own docs for the button element's ``url`` field: "If you're using
``url``, you'll still receive an interaction payload and will need to send
an acknowledgement response." One tap opens the doc in the browser AND
delivers the same ``block_actions`` interaction ``Approve`` gets, which is
what lets ``artemis/crisis_content/slack_actions.py`` record a decision
without asking anyone to type prose -- see that module's docstring for what
happens on click, and its "Do NOT notify Jen" section for the one thing this
click deliberately does NOT do. The ``url`` is
``f"{_DOC_URL}?tab={transition.tab_id}"`` when ``transition.tab_id`` is set,
else the bare ``_DOC_URL`` -- see ``Transition.tab_id``'s docstring
(``artemis.crisis_content.transitions``) for how CCA13's tab resolution
populates it (and why it can still be ``None`` -- no tab resolution
attempted, or this specific card could not be positively located this
tick). The modal this button replaced (``views.open``, its ``view_submission``
handler, ``private_metadata``, ``CRISIS_CONTENT_VIEW_CALLBACK_ID``) has been
deleted entirely, along with the tests that covered it.

**Opener voice (CCA8).** The old bare ``"📝 Copy ready for review -- X"`` /
``"🎨 Asset ready for review -- X"`` line is now a conversational, varied
opener -- see ``render_opener`` and the ``_COPY_OPENERS`` / ``_ASSET_OPENERS``
constants. Selection is a stable ``sha256`` hash of the card's
``identity_key`` plus route, reduced modulo the variant count --
deliberately NOT ``random``: the card is re-rendered when it repaints after a
decision (see ``render_decision_message`` callers), and a random pick would
rewrite the opener under the reader's eyes at exactly the moment they are
acting on it. The platform, previously named in that line, is now folded into
the date line right beneath it (``"{date} · {title} -- {platform}"``) so it
stays visible without a second machine-toned line.

Jen is named in plain text on every ready-for-review opener, never
``@``-mentioned -- she is an external Slack Connect vendor, and a real ping
on every single card would be grating well before card forty. A real
``<@…>`` mention is only for the moment she must genuinely act: CCA9's
change-request notification, via ``jen_mention()``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.crisis_content.export_client import TARGET_DOCUMENT_ID
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.transitions import ReopenedAfterApproval, Transition, find_card_id
from artemis.integrations.slack.client import SlackAPIError, SlackClient
from artemis.proactivity.radar import JON_EMAIL
from artemis.routes.integrations_slack_events import _resolve_agent_slack_config

logger = logging.getLogger(__name__)

__all__ = [
    "TESTING_LINE",
    "TESTING_LINE_ASSET",
    "ACTION_APPROVE",
    "ACTION_EDIT_IN_DOC",
    "testing_line_for_route",
    "render_char_count_line",
    "render_opener",
    "render_reopened_banner",
    "jen_mention",
    "render_transition_message",
    "render_transition_blocks",
    "render_decision_message",
    "render_editing_in_doc_message",
    "PostedCardMessage",
    "post_transition_card",
    "copy_mention_emails",
    "reset_notify_caches_for_tests",
]

# Block Kit action_ids for the two decision buttons. Defined here (the
# rendering module) rather than in slack_actions.py, which imports them --
# slack_actions.py also imports render_decision_message from this module, so
# defining them there instead would make the two modules import each other.
ACTION_APPROVE = "crisis_content_approve"
# CCA12: replaces the old ACTION_REQUEST_CHANGES ("crisis_content_request_changes"),
# which opened the now-deleted modal. Renamed, not aliased, so nothing can
# accidentally still reference the retired action_id.
ACTION_EDIT_IN_DOC = "crisis_content_edit_in_doc"

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


# CCA8: the conversational opener that replaces the old bare
# "📝 Copy ready for review -- X" / "🎨 Asset ready for review -- X" line.
# Module constants, not inline in a render function, so adding or editing a
# variant is a one-line change. Jon wrote #1 of each list; the rest follow
# its register -- warm, brief, human, no exclamation-mark spam.
#
# `{approvers}` is the only placeholder, and only the copy list has one --
# the asset route has no approver list to fold in (Jon is the only asset
# approver, and naming him in a DM to himself would be odd; see
# `render_opener` below).
_COPY_OPENERS: tuple[str, ...] = (
    "Thanks Jen — {approvers}, we've got another copy piece ready to approve.",
    "New one in from Jen. {approvers} — ready for your eyes.",
    "Jen just sent this over. {approvers}, ready when one of you is.",
    "Fresh copy from Jen. {approvers} — whoever gets there first.",
    "Thanks Jen! {approvers}, another one ready to approve.",
    "Jen has this one ready. {approvers} — over to you.",
)
_ASSET_OPENERS: tuple[str, ...] = (
    "Thanks Jen — the visual's ready for your eyes.",
    "New visual in from Jen, ready when you are.",
    "Jen attached the asset for this one — over to you.",
)


def _select_variant(
    identity_key: tuple[str, str | None, int], route: str, variants: Sequence[str]
) -> str:
    """Deterministically pick one of ``variants`` for ``(identity_key, route)``.

    ``sha256(f"{identity_key}:{route}")`` reduced modulo the variant count --
    NOT ``random``. The card is re-rendered every time it repaints after a
    decision (see ``render_decision_message``'s callers), and a random pick
    would rewrite the opener under the reader's eyes at exactly the moment
    they are acting on it. Same card + route always lands on the same
    variant; different cards land on different variants (different sha256
    digests), so a channel full of cards does not read like a mail merge.
    """
    digest = hashlib.sha256(f"{identity_key}:{route}".encode()).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def render_opener(
    identity_key: tuple[str, str | None, int], route: str, *, approvers: str = ""
) -> str:
    """The conversational opener line for one card, deterministically chosen.

    ``route == "asset"`` picks from ``_ASSET_OPENERS`` (addressed to Jon
    alone; no approver list). Anything else picks from ``_COPY_OPENERS`` and
    fills ``{approvers}`` with the already-resolved mention text (the same
    ``"<@U…>, <@U…> or <@U…>"`` style used for the live "Any one of ..."
    footer -- see ``_join_mentions``). An empty ``approvers`` (the `dm_jon`
    rollback override does not resolve the copy approvers a second time --
    the ``⚠️ Testing`` footer it restores already names them) falls back to
    the generic "the team" rather than leaving a dangling comma.

    Jen is always the plain word "Jen" here, on purpose -- see the module
    docstring's "Opener voice (CCA8)" and ``jen_mention()``.
    """
    if route == "asset":
        return _select_variant(identity_key, route, _ASSET_OPENERS)
    template = _select_variant(identity_key, route, _COPY_OPENERS)
    return template.format(approvers=approvers or "the team")


def render_reopened_banner(reopened: ReopenedAfterApproval) -> str:
    """The "you are re-reviewing something already approved" banner (CCA11).

    Only ever rendered when ``Transition.reopened_after_approval`` is set --
    i.e. only for a re-fire following an ``approved`` decision, never for the
    expected ``changes_requested`` -> revision -> re-fire loop (CCA9), which
    stays silent about being a reopen at all. See
    ``briefs/cca11-reopen-on-post-approval-edit.md``, "The card must say why
    it is back": a re-fired card that looks identical to a first-time card
    is worse than no card, because the approver has no way to tell they are
    re-reviewing something they already signed off on.

    Date format (``"Aug 11"``) mirrors ``writeback.py``'s /
    ``image_link.py``'s own ``f"{dt.strftime('%b')} {dt.day}"`` convention,
    minus the time -- the brief's worked example shows a bare date, and the
    exact moment of the original approval is already on record in
    ``crisis_content_decisions`` for anyone who needs it.
    """
    stamp = f"{reopened.approved_at.strftime('%b')} {reopened.approved_at.day}"
    return (
        f"⚠️ Previously approved by {reopened.approved_by} on {stamp}, "
        "and the copy has changed since."
    )


def jen_mention() -> str:
    """A real ``<@…>`` mention for Jen -- for the moment she must genuinely act.

    Not used by any ready-for-review card (those use the plain word "Jen";
    see ``render_opener``). This is for CCA9's change-request notification,
    which this slice does not send -- it only adds the helper CCA9 will call.

    ``settings.crisis_content_jen_slack_user_id`` is CONFIGURED, not resolved
    by email -- see that setting's docstring for why ``users.lookupByEmail``
    silently returns ``None`` forever for her. An empty setting falls back to
    the plain word "Jen" rather than rendering a broken ``<@>`` mention.
    """
    jen_id = settings.crisis_content_jen_slack_user_id.strip()
    if not jen_id:
        return "Jen"
    return f"<@{jen_id}>"


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


def _join_mentions(mention_tags: list[str]) -> str:
    """``"<@U1>, <@U2> or <@U3>"`` for however many resolved.

    Feeds CCA8's opener ``{approvers}`` slot (``render_opener``). Used to
    ALSO build a redundant "Any one of ... can approve." footer
    (``_copy_live_footer``, CCA6) that named the approvers a second time on
    the same card; CCA9 removed that footer (the opener already carries who
    and the any-one-of rule) and this helper's only remaining caller is the
    opener.
    """
    if len(mention_tags) == 1:
        return mention_tags[0]
    return ", ".join(mention_tags[:-1]) + " or " + mention_tags[-1]


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


def _doc_url_for(tab_id: str | None) -> str:
    """The "Edit in doc" button's ``url`` -- ``?tab=<tab_id>`` when known.

    CCA12: Docs has no per-row anchor, so the tab is the best available
    precision -- this is deliberately NOT a per-card constant; it is
    computed from whatever ``tab_id`` the caller (``render_transition_blocks``,
    from ``transition.tab_id``) actually has for THIS card, never a
    hardcoded literal. ``None`` (no tab resolution attempted this tick, or
    this specific card could not be positively located -- see
    ``Transition.tab_id``'s docstring) falls back to the bare ``_DOC_URL``,
    identical to the doc link every card has always shown in its body text.
    """
    if tab_id:
        return f"{_DOC_URL}?tab={tab_id}"
    return _DOC_URL

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


def render_transition_message(transition: Transition, *, footer: str, approvers: str = "") -> str:
    """Render the full Slack DM/channel text for one transition.

    Full copy inline, never truncated -- the point is approving without
    opening the doc. Always notes the OTHER route's status too, so the
    reader can tell whether the post is actually shippable end to end.

    The card BODY (copy, char count, other-route status, doc link) is
    unchanged since CCA4 and stays that way (Jon has approved the current
    format). ``footer`` is the one part of the rendered text CCA6 makes
    caller-controlled: the trailing line was already route-specific before
    this slice (``testing_line_for_route``); CCA6 extends the same seam so a
    caller can pass the ``dm_jon`` testing line, a live "who can approve"
    line, or "" (no footer at all, for the live asset route) without
    touching anything above it. An empty ``footer`` omits the line AND the
    blank line that would otherwise separate it from the doc link.

    ``approvers`` is CCA8's equivalent seam for the opener's ``{approvers}``
    slot on a copy-route card (ignored for the asset route, which has no
    approver list) -- see ``render_opener``. The first line is now that
    conversational opener rather than the old bare "📝 Copy ready for review
    -- X" line; the platform it used to carry is folded into the date line
    right beneath it instead, so it stays visible without a second
    machine-toned line.

    ``transition.reopened_after_approval`` (CCA11) inserts one more line
    right after the opener, on BOTH routes -- the "previously approved"
    warning (``render_reopened_banner``). ``None`` (every transition except
    a reopen following an ``approved`` decision) adds nothing, so this stays
    a no-op for every case this function already covered.
    """
    card = transition.card
    platform = card.platform or "unspecified platform"
    heading_date = f"{card.date_text} · {card.title}" if card.date_text else card.title
    heading_line = f"{heading_date} — {platform}"
    opener = render_opener(card.identity_key, transition.route, approvers=approvers)

    lines: list[str] = [opener]
    if transition.reopened_after_approval is not None:
        lines.append(render_reopened_banner(transition.reopened_after_approval))
    lines.append(heading_line)
    lines.append("")
    lines.append(card.copy_body)
    lines.append("")
    lines.append(render_char_count_line(card.copy_body, card.platform))
    if transition.route == "copy":
        lines.append(f"Asset: {_asset_status_line(card)}")
    else:
        lines.append(f"Copy: {_copy_status_line(card)}")
        lines.append(f"Asset link: {card.asset_url}")

    lines.append(f"Open the doc: {_DOC_URL}")
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_transition_blocks(
    transition: Transition, card_id: int, *, footer: str, approvers: str = ""
) -> list[dict[str, Any]]:
    """Block Kit body for one transition: the card text, plus decision buttons.

    The ``section`` block's mrkdwn text is byte-for-byte
    ``render_transition_message(transition, footer=footer, approvers=approvers)``'s
    output -- this function only ADDS the ``actions`` block Slack needs to
    render working buttons; it never restyles the body (see the module
    docstring, "Buttons (CCA5)"). ``footer`` and ``approvers`` are passed
    straight through -- see ``render_transition_message`` for what each
    controls.

    The button ``value`` is ``f"{card_id}:{route}"``, carrying no identity --
    see ``artemis/routes/integrations_slack_interactivity.py`` and
    ``artemis/crisis_content/slack_actions.py`` for why the clicking user's
    identity comes ONLY from the verified payload's ``user.id``.

    ``Edit in doc`` (CCA12) carries BOTH ``url`` (``_doc_url_for(transition.tab_id)``
    -- see the module docstring's "CCA12" section) AND ``action_id`` --
    Slack still sends a ``block_actions`` interaction for a ``url`` button,
    which is what lets one tap both open the doc and record the decision.
    """
    value = f"{card_id}:{transition.route}"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": render_transition_message(transition, footer=footer, approvers=approvers),
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
                    "text": {"type": "plain_text", "text": "Edit in doc"},
                    "action_id": ACTION_EDIT_IN_DOC,
                    "url": _doc_url_for(transition.tab_id),
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


def render_editing_in_doc_message(
    *, actor_mention: str, decided_at: datetime
) -> tuple[str, list[dict[str, Any]]]:
    """Render the post-"Edit in doc"-click replacement for Callie's card (CCA12).

    Deliberately its OWN function rather than another branch of
    ``render_decision_message`` above: that function's ``"changes_requested"``
    wording ("Changes requested by X") describes the OLD modal flow, where an
    approver typed a description of what needed to change. This click
    records the same ``decision`` value in the DB (see
    ``artemis.crisis_content.slack_actions._handle_edit_in_doc``), but there
    is no description -- the approver went straight to the document -- so
    the card must say a different, accurate thing: who is editing, not what
    they asked for. Conflating the two wordings would make it look like a
    change was described when nothing was.

    ``actor_mention`` is a real Slack ``<@U…>`` mention (per
    ``briefs/cca12-edit-in-doc-button.md``'s literal example), not the
    email-preferring ``_display_label`` the Approve path uses -- this card
    renders in Slack, where a mention resolves to a display name, and the
    brief's worked example is explicit about the ``<@…>`` form.

    No ``note`` parameter: this decision's ``note`` is always ``NULL`` (see
    ``_handle_edit_in_doc``), so there is nothing to render as a second block.
    """
    stamp = decided_at.strftime("%-I:%M%p").lower()
    text = f"✏️ {actor_mention} is editing in the doc · {stamp}"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]
    return text, blocks


@dataclass(frozen=True)
class PostedCardMessage:
    """Where a transition's card actually landed (CCA9).

    Returned by ``post_transition_card`` so the caller (``artemis.crisis_content
    .poller``) can persist ``channel_id``/``message_ts`` onto the
    ``crisis_content_notifications`` row it writes via ``mark_notified`` --
    that row is the ONLY place a later Slack thread reply can be mapped back
    to this card (see ``artemis.crisis_content.thread_notes
    .find_card_thread_target``).

    Sourced from the REAL Slack API response's own ``channel``/``ts`` fields
    -- not from what we asked to post to, because a DM's channel id is only
    known once ``conversations.open`` resolves it internally (``post_dm``
    does that resolution and returns the resulting channel in its response).
    Either field is ``None`` when the response doesn't carry it (an
    unexpected Slack response shape, or a test double that doesn't echo one
    back) -- the notification row's columns are nullable for exactly this
    reason; a caller that can't map a reply back to this card is better than
    one that crashes.
    """

    channel_id: str | None
    message_ts: str | None


def _posted_message_from_response(response: dict[str, object]) -> PostedCardMessage:
    channel = response.get("channel")
    ts = response.get("ts")
    return PostedCardMessage(
        channel_id=str(channel) if channel else None,
        message_ts=str(ts) if ts else None,
    )


async def _post_dm_jon_override(
    client: SlackClient, transition: Transition, card_id: int
) -> PostedCardMessage:
    """The ``dm_jon`` rollback: every route DMs Jon, testing footer restored.

    Deliberately does not resolve the copy approvers a second time for the
    opener's ``{approvers}`` slot (CCA8) -- ``render_opener`` falls back to
    "the team" when ``approvers`` is empty, and the ``⚠️ Testing`` footer
    this path restores already names them by name ("Live: Angela, Hannah,
    Jaclyn"). This is the emergency-rollback path; it should not grow a new
    way to fail (an all-approvers-unresolvable case) that the live path
    doesn't already have to handle.
    """
    jon_slack_id = await _resolve_slack_id_cached(client, JON_EMAIL)
    if not jon_slack_id:
        raise RuntimeError(
            f"crisis_content: users.lookupByEmail found no Slack user for {JON_EMAIL!r}"
        )
    footer = testing_line_for_route(transition.route)
    text = render_transition_message(transition, footer=footer)
    blocks = render_transition_blocks(transition, card_id, footer=footer)
    response = await client.post_dm(user=jon_slack_id, text=text, blocks=blocks)
    logger.info(
        "crisis_content: posted %s-route card for card=%r to Jon (dm_jon override)",
        transition.route,
        transition.card.identity_key,
    )
    return _posted_message_from_response(response)


async def _post_live_asset(
    client: SlackClient, transition: Transition, card_id: int
) -> PostedCardMessage:
    """Live routing, ``asset`` route: DM Jon, no footer (he owns visuals)."""
    jon_slack_id = await _resolve_slack_id_cached(client, JON_EMAIL)
    if not jon_slack_id:
        raise RuntimeError(
            f"crisis_content: users.lookupByEmail found no Slack user for {JON_EMAIL!r}"
        )
    text = render_transition_message(transition, footer="")
    blocks = render_transition_blocks(transition, card_id, footer="")
    response = await client.post_dm(user=jon_slack_id, text=text, blocks=blocks)
    logger.info(
        "crisis_content: posted asset-route card for card=%r to Jon (live)",
        transition.card.identity_key,
    )
    return _posted_message_from_response(response)


async def _post_live_copy(
    client: SlackClient, transition: Transition, card_id: int
) -> PostedCardMessage:
    """Live routing, ``copy`` route: post to the channel, mentioning whoever resolved.

    One unresolvable approver must not kill the notification (CCA6) -- post
    with the ones that DID resolve and log a WARNING naming the rest. Only
    if NONE resolve do we refuse to post (a card naming nobody is not a
    notification), which surfaces as this function raising -- the caller
    (``post_transition_card``) never calls ``mark_notified`` for a raise, so
    the next tick retries.

    No footer (CCA9) -- the old "Any one of <@...> can approve." line
    (``_copy_live_footer``, CCA6) duplicated who could approve; CCA8's
    opener already names the same three approvers via ``approvers`` below,
    so the footer was purely redundant. Jon's call; see
    ``briefs/cca9-card-lifecycle.md`` section 1.
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
    approvers = _join_mentions(mention_tags)
    text = render_transition_message(transition, footer="", approvers=approvers)
    blocks = render_transition_blocks(transition, card_id, footer="", approvers=approvers)
    try:
        # SlackClient.post_message declares `blocks: list[object] | None`,
        # invariant -- `list[dict[str, Any]]` (what render_transition_blocks
        # returns) is not a `list[object]` under mypy's variance rules even
        # though it obviously satisfies the runtime contract. post_dm takes
        # the covariant `Sequence[object]` instead and needs no cast; this
        # cast is the narrower, local fix rather than widening
        # SlackClient.post_message's signature, which is out of this
        # slice's file scope.
        response = await client.post_message(
            channel=channel, text=text, blocks=cast("list[object]", blocks)
        )
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
    return _posted_message_from_response(response)


async def post_transition_card(session: AsyncSession, transition: Transition) -> PostedCardMessage:
    """Post ``transition``'s rendered card (text + decision buttons), as Callie.

    ``transition.is_test`` (CCA13) is checked FIRST and, if true, always
    wins: DM Jon with the ``⚠️ Testing`` footer, regardless of
    ``settings.crisis_content_notify_destination`` -- see the module
    docstring's "Per-card test lane (CCA13)" section. Otherwise destination
    is ``settings.crisis_content_notify_destination`` -- see the module
    docstring's "Destination (CCA6)" section for the two states.
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

    Returns the ``PostedCardMessage`` (CCA9) so the caller can persist WHERE
    this card landed onto the notification ledger.
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

    if transition.is_test:
        # CCA13: a per-card override that wins regardless of the global
        # destination setting -- see the module docstring's "Per-card test
        # lane" section. Reuses the existing dm_jon-override renderer
        # verbatim: same destination (Jon's DM), same restored footer.
        return await _post_dm_jon_override(client, transition, card_id)

    if settings.crisis_content_notify_destination == "dm_jon":
        return await _post_dm_jon_override(client, transition, card_id)

    if transition.route == "asset":
        return await _post_live_asset(client, transition, card_id)
    return await _post_live_copy(client, transition, card_id)
