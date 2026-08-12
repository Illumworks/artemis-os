"""Tests for CCA8: the conversational card opener + Jen's mention policy.

Covers every item in ``briefs/cca8-card-voice.md`` "Tests" section. Purely a
rendering-layer slice -- no DB, no Slack, no network. Every test here calls
``render_opener`` / ``render_transition_message`` / ``render_transition_blocks``
/ ``jen_mention`` directly against synthetic ``ReviewCard`` / ``Transition``
objects, the same way ``tests/test_crisis_content_routing.py``'s
``test_live_routing_footers_never_contain_the_testing_line`` already tests
the pure renderer without a database. The end-to-end ``post_transition_card``
wiring (real Slack calls faked, real DB) is covered by
``tests/test_crisis_content_routing.py`` and ``tests/test_crisis_content_poller.py``,
which this brief also requires to keep passing unmodified.

Also covers the pure-rendering half of CCA11's "previously approved" reopen
banner (``render_reopened_banner`` and its insertion point in
``render_transition_message``) -- the reopen-DETECTION tests (does it fire,
does it name the right approver and date, do routes reopen independently)
live in ``tests/test_crisis_content_transitions.py``, which owns the DB
fixtures that logic actually needs.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from artemis.config import settings
from artemis.crisis_content import notify
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.transitions import ReopenedAfterApproval, Transition

_NOTIFY_PATH = Path(__file__).resolve().parent.parent / "artemis" / "crisis_content" / "notify.py"

# A stand-in for what `_resolve_copy_mentions` + `_join_mentions` would
# actually hand `render_transition_message` in production -- three resolved
# Slack ids in the existing "Any one of ..." join style.
_APPROVERS = "<@U_ANGELA>, <@U_HANNAH> or <@U_JACLYN>"


def _make_card(
    *,
    header: str = "August XX, 2026 - Welcome Back blog",
    platform: str | None = "LinkedIn",
    ordinal: int = 0,
    title: str = "Welcome Back blog",
    asset_status: str | None = "Draft",
    copy_status: str | None = "Ready",
    asset_url: str | None = None,
    copy_body: str = "Default copy body.",
) -> ReviewCard:
    """Build a ``ReviewCard`` the way the real parser would, minus the HTML.

    Mirrors ``tests/test_crisis_content_routing.py``'s helper of the same
    name. Kept as its own copy rather than a shared import -- this file has
    no database fixtures in common with that one and importing across
    crisis-content test modules isn't an existing pattern in this suite.
    """
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()
    return ReviewCard(
        header=header,
        date_text="August XX, 2026",
        title=title,
        platform=platform,
        asset_status=asset_status,
        copy_status=copy_status,
        asset_url=asset_url,
        copy_body=copy_body,
        identity_key=(header, platform, ordinal),
        copy_hash=copy_hash,
    )


def _copy_transition(**kwargs: Any) -> Transition:
    kwargs.setdefault("copy_status", "Ready")
    card = _make_card(**kwargs)
    return Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )


def _asset_transition(**kwargs: Any) -> Transition:
    kwargs.setdefault("asset_status", "Ready")
    kwargs.setdefault("asset_url", "https://example.com/asset.png")
    kwargs.setdefault("copy_status", "Draft")
    card = _make_card(**kwargs)
    return Transition(
        card=card, route="asset", previous_status="Draft", new_status="Ready", is_new_card=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Determinism -- never random
# ─────────────────────────────────────────────────────────────────────────────


def test_same_card_and_route_render_the_same_opener_every_time() -> None:
    card = _make_card()
    renders = [
        notify.render_opener(card.identity_key, "copy", approvers=_APPROVERS) for _ in range(5)
    ]
    assert len(set(renders)) == 1


def test_same_transition_rendered_twice_is_byte_for_byte_identical() -> None:
    """The exact repaint scenario the brief describes: the card is re-rendered
    when it repaints after a decision. A second render of the SAME
    transition, before any decision changes it, must read identically --
    otherwise the opener would visibly change under the reader mid-decision.
    """
    transition = _copy_transition()
    first = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    second = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    assert first == second

    asset_transition = _asset_transition()
    first_asset = notify.render_transition_message(asset_transition, footer="")
    second_asset = notify.render_transition_message(asset_transition, footer="")
    assert first_asset == second_asset


def test_different_cards_exercise_the_full_variant_set() -> None:
    """Different cards must land on different variants -- a channel full of
    cards reading identically would defeat the point of having six of them.

    Deterministic, not statistical: sha256 over 40 distinct identity keys is
    verified (see the CCA8 implementation notes) to cover every one of the
    six copy variants and all three asset variants, so this is not a flaky
    "probably distinct" check.
    """
    copy_openers = {
        notify.render_opener((f"header {i}", "LinkedIn", i), "copy", approvers=_APPROVERS)
        for i in range(40)
    }
    asset_openers = {notify.render_opener((f"header {i}", "LinkedIn", i), "asset") for i in range(40)}

    expected_copy = {template.format(approvers=_APPROVERS) for template in notify._COPY_OPENERS}
    assert copy_openers == expected_copy
    assert asset_openers == set(notify._ASSET_OPENERS)


def test_no_random_import_anywhere_in_notify() -> None:
    """Enforces the brief's hard constraint by parsing the module's AST
    (rather than grepping text) so a re-flow of the import block can't
    accidentally dodge a regex: no ``import random`` and no
    ``from random import ...`` anywhere in ``notify.py``.
    """
    tree = ast.parse(_NOTIFY_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name == "random" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "random"


# ─────────────────────────────────────────────────────────────────────────────
# Copy-route opener: names the approvers, names Jen in plain text only
# ─────────────────────────────────────────────────────────────────────────────


def test_copy_route_opener_contains_the_three_approver_mentions() -> None:
    opener = notify.render_opener(("header", "LinkedIn", 0), "copy", approvers=_APPROVERS)
    assert "<@U_ANGELA>" in opener
    assert "<@U_HANNAH>" in opener
    assert "<@U_JACLYN>" in opener


def test_copy_route_opener_says_jen_in_plain_text_and_never_her_slack_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "U016P00LP08")
    transition = _copy_transition()
    text = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    opener_line = text.splitlines()[0]

    # The opener names Jen in plain text, and DOES mention the three copy
    # approvers (that is the whole point of {approvers}) -- what must never
    # appear anywhere on a ready-for-review card is a mention of JEN
    # specifically, i.e. her configured id ever appearing as `<@U016P00LP08>`.
    assert "Jen" in opener_line
    assert "U016P00LP08" not in text


def test_copy_route_opener_variants_all_say_jen_never_a_mention() -> None:
    for template in notify._COPY_OPENERS:
        rendered = template.format(approvers=_APPROVERS)
        assert "Jen" in rendered
        assert "U016P00LP08" not in rendered


# ─────────────────────────────────────────────────────────────────────────────
# Asset-route opener: addresses Jon, no approver list
# ─────────────────────────────────────────────────────────────────────────────


def test_asset_route_opener_has_no_approver_list() -> None:
    opener = notify.render_opener(("header", None, 0), "asset")
    assert opener in notify._ASSET_OPENERS
    assert "<@" not in opener
    assert "Angela" not in opener
    assert "Hannah" not in opener
    assert "Jaclyn" not in opener


def test_asset_route_opener_variants_all_say_jen_never_a_mention() -> None:
    for template in notify._ASSET_OPENERS:
        assert "Jen" in template
        assert "<@" not in template


# ─────────────────────────────────────────────────────────────────────────────
# jen_mention()
# ─────────────────────────────────────────────────────────────────────────────


def test_jen_mention_returns_a_real_mention_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "U016P00LP08")
    assert notify.jen_mention() == "<@U016P00LP08>"


def test_jen_mention_falls_back_to_the_plain_word_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "")
    mention = notify.jen_mention()
    assert mention == "Jen"
    assert "<@" not in mention


def test_jen_mention_falls_back_on_whitespace_only_setting_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "   ")
    assert notify.jen_mention() == "Jen"


def test_jen_slack_user_id_setting_defaults_to_the_confirmed_external_id() -> None:
    """``U016P00LP08`` is verified (users.info) as jen@digigeeks.com on the
    external Slack Connect team ``TUQ6KJT0V`` -- ``users.lookupByEmail`` will
    never find her, so this must stay a configured default, not a lookup.
    """
    assert settings.crisis_content_jen_slack_user_id == "U016P00LP08"


# ─────────────────────────────────────────────────────────────────────────────
# Platform stays visible
# ─────────────────────────────────────────────────────────────────────────────


def test_platform_still_appears_on_a_copy_card() -> None:
    transition = _copy_transition(platform="LinkedIn")
    text = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    card = transition.card
    expected_heading_line = f"{card.date_text} · {card.title} — {card.platform}"
    assert expected_heading_line in text.splitlines()


def test_platform_still_appears_on_an_asset_card() -> None:
    transition = _asset_transition(platform="X")
    text = notify.render_transition_message(transition, footer="")
    card = transition.card
    expected_heading_line = f"{card.date_text} · {card.title} — {card.platform}"
    assert expected_heading_line in text.splitlines()


def test_unspecified_platform_still_shows_a_platform_marker() -> None:
    """No platform set at all -- the heading line must still say SOMETHING
    rather than silently dropping the marker.
    """
    transition = _copy_transition(platform=None)
    text = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    assert "unspecified platform" in text


# ─────────────────────────────────────────────────────────────────────────────
# Body, char-count line, other-route status, doc link: unchanged
# ─────────────────────────────────────────────────────────────────────────────


def test_copy_route_body_char_count_asset_status_and_doc_link_are_unchanged() -> None:
    copy_body = "Some approved copy that Jon has already signed off on the format for."
    transition = _copy_transition(copy_body=copy_body, platform="X", asset_status=None, asset_url=None)
    text = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    lines = text.splitlines()

    assert copy_body in lines
    assert notify.render_char_count_line(copy_body, "X") in lines
    assert "Asset: not set" in lines
    assert f"Open the doc: {notify._DOC_URL}" in lines


def test_asset_route_body_char_count_copy_status_and_links_are_unchanged() -> None:
    copy_body = "Some copy that is still in progress."
    transition = _asset_transition(
        copy_body=copy_body,
        copy_status="Draft",
        asset_url="https://example.com/asset.png",
        platform="LinkedIn",
    )
    text = notify.render_transition_message(transition, footer="")
    lines = text.splitlines()

    assert copy_body in lines
    assert notify.render_char_count_line(copy_body, "LinkedIn") in lines
    assert "Copy: Draft" in lines
    assert "Asset link: https://example.com/asset.png" in lines
    assert f"Open the doc: {notify._DOC_URL}" in lines


def test_buttons_and_action_ids_are_unchanged() -> None:
    transition = _copy_transition()
    blocks = notify.render_transition_blocks(transition, card_id=42, footer="", approvers=_APPROVERS)

    assert blocks[0]["type"] == "section"
    actions_block = blocks[1]
    assert actions_block["type"] == "actions"
    approve, request_changes = actions_block["elements"]

    assert notify.ACTION_APPROVE == "crisis_content_approve"
    assert notify.ACTION_REQUEST_CHANGES == "crisis_content_request_changes"
    assert approve["action_id"] == notify.ACTION_APPROVE
    assert approve["text"]["text"] == "Approve"
    assert approve["style"] == "primary"
    assert request_changes["action_id"] == notify.ACTION_REQUEST_CHANGES
    assert request_changes["text"]["text"] == "Request changes"
    assert approve["value"] == request_changes["value"] == "42:copy"


# ─────────────────────────────────────────────────────────────────────────────
# dm_jon override: both ⚠️ Testing footers still render
# ─────────────────────────────────────────────────────────────────────────────


def test_dm_jon_testing_footers_still_render_for_both_routes() -> None:
    copy_transition = _copy_transition()
    copy_text = notify.render_transition_message(
        copy_transition, footer=notify.testing_line_for_route("copy")
    )
    assert notify.TESTING_LINE in copy_text

    asset_transition = _asset_transition()
    asset_text = notify.render_transition_message(
        asset_transition, footer=notify.testing_line_for_route("asset")
    )
    assert notify.TESTING_LINE_ASSET in asset_text


# ─────────────────────────────────────────────────────────────────────────────
# CCA11: the "previously approved" reopen banner
# ─────────────────────────────────────────────────────────────────────────────

_REOPENED = ReopenedAfterApproval(
    approved_by="angela.miata@amiralearning.com",
    approved_at=datetime(2026, 8, 11, 15, 30, tzinfo=UTC),
)


def test_render_reopened_banner_names_approver_and_date() -> None:
    banner = notify.render_reopened_banner(_REOPENED)
    assert banner == (
        "⚠️ Previously approved by angela.miata@amiralearning.com on Aug 11, "
        "and the copy has changed since."
    )


def test_transition_message_inserts_banner_right_after_the_opener_on_both_routes() -> None:
    """The banner is line 2 -- right after the opener, before anything else --
    on BOTH routes, mirroring rather than special-casing (same rule notify.py
    already follows for the rest of the card body).
    """
    copy_transition = _copy_transition()
    reopened_copy = copy_transition.model_copy(update={"reopened_after_approval": _REOPENED})
    copy_lines = notify.render_transition_message(
        reopened_copy, footer="", approvers=_APPROVERS
    ).splitlines()
    assert copy_lines[0] == notify.render_opener(
        copy_transition.card.identity_key, "copy", approvers=_APPROVERS
    )
    assert copy_lines[1] == notify.render_reopened_banner(_REOPENED)

    asset_transition = _asset_transition()
    reopened_asset = asset_transition.model_copy(update={"reopened_after_approval": _REOPENED})
    asset_lines = notify.render_transition_message(reopened_asset, footer="").splitlines()
    assert asset_lines[0] == notify.render_opener(asset_transition.card.identity_key, "asset")
    assert asset_lines[1] == notify.render_reopened_banner(_REOPENED)


def test_transition_message_omits_banner_when_reopened_after_approval_is_none() -> None:
    """Every transition this brief doesn't apply to (a first-time card, and a
    changes_requested reopen) leaves ``reopened_after_approval`` unset by
    default -- confirms that default renders no banner at all, not an empty
    line.
    """
    transition = _copy_transition()
    assert transition.reopened_after_approval is None
    text = notify.render_transition_message(transition, footer="", approvers=_APPROVERS)
    assert "Previously approved" not in text
    assert "⚠️" not in text


def test_copy_opener_falls_back_to_a_generic_phrase_when_no_approvers_supplied() -> None:
    """The `dm_jon` override path deliberately does not re-resolve the copy
    approvers for the opener (see `_post_dm_jon_override`'s docstring) --
    confirms the fallback reads cleanly instead of leaving a dangling comma
    or emitting a stray mention.
    """
    transition = _copy_transition()
    text = notify.render_transition_message(transition, footer=notify.testing_line_for_route("copy"))
    opener_line = text.splitlines()[0]
    assert "the team" in opener_line
    assert "<@" not in opener_line
