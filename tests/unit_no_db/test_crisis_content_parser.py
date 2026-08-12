"""Unit tests for the crisis-content doc-card reader/parser (CCA1, slice A).

Pure parsing tests against fixtures under ``tests/fixtures/crisis_content/``.
No network, no database -- ``artemis/crisis_content/parser.py`` has zero
network/DB imports, and these tests exercise it (and the pure helpers in
``export_client.py``) directly against HTML strings.

Fixture ``four_cards.html`` is genuine export shape: card 0 is the LinkedIn
"Welcome Back blog" card exactly as verified against the live doc (see
``briefs/cca1-doc-card-reader.md``), with realistic long ``style``/``class``
noise kept intact on that card (the brief's own sample had that noise
stripped for readability; this fixture restores it on one card so the
parser is actually exercised against it). Cards 1-3 and the three
non-card tables are constructed to the same shape to cover the other
required scenarios without cluttering a single card with every edge case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artemis.crisis_content.parser import (
    NoReviewCardsFoundError,
    SignInPageError,
    classify_status,
    parse_review_cards,
    unwrap_google_redirect_url,
)

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "crisis_content"


def _load(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _by_header_platform(cards: list) -> dict[tuple[str, str | None], object]:
    return {(card.header, card.platform): card for card in cards}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_four_card_fixture_parses_to_exactly_four_cards_none_from_strategy_tables() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)

    assert len(cards) == 4
    headers = {card.header for card in cards}
    # None of the strategy/content-plan/framework tables leaked through.
    assert all("Strategy Plan" not in header for header in headers)
    assert all("Content Plan Draft" not in header for header in headers)
    assert all("Repeatable Framework" not in header for header in headers)
    assert headers == {
        "August XX, 2026 - Welcome Back blog",
        "August XX, 2026 - Fall Enrollment push",
    }


def test_linkedin_card_fields() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    linkedin = next(card for card in cards if card.platform == "LinkedIn")

    assert linkedin.platform == "LinkedIn"
    assert linkedin.copy_status == "Ready"
    assert linkedin.asset_status == "Draft"
    assert linkedin.date_text == "August XX, 2026"
    assert linkedin.title == "Welcome Back blog"


def test_real_asset_hyperlink_yields_unwrapped_url() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    instagram = next(card for card in cards if card.platform == "Instagram")

    assert instagram.asset_url == "https://drive.google.com/file/d/EXAMPLE-IG-CREATIVE/view"
    assert "google.com/url" not in instagram.asset_url
    assert "usg=" not in instagram.asset_url


def test_link_placeholder_with_no_anchor_yields_none_asset_url_no_exception() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    linkedin = next(card for card in cards if card.platform == "LinkedIn")

    # "LINK" with no <a> is a legitimate placeholder, not an error.
    assert linkedin.asset_url is None


def test_copy_body_preserves_paragraph_breaks_and_unescapes_entities() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    linkedin = next(card for card in cards if card.platform == "LinkedIn")

    # Two <p> paragraphs in the copy cell -> one line break between them.
    lines = linkedin.copy_body.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("New from Reading Between the Lines")
    assert lines[1].startswith("Read it:")

    # &#39; -> ' , &quot; -> ", entities are gone from the rendered text.
    assert "Jaclyn Brown Wright's team" in linkedin.copy_body
    assert '"Welcome Back: ...' in linkedin.copy_body
    assert "&#39;" not in linkedin.copy_body
    assert "&quot;" not in linkedin.copy_body
    assert "&amp;" not in linkedin.copy_body


# ---------------------------------------------------------------------------
# Failure and edge modes
# ---------------------------------------------------------------------------


def test_seven_table_fixture_yields_only_four_signature_matches() -> None:
    html = _load("four_cards.html")
    raw_table_count = html.lower().count("<table")
    assert raw_table_count == 7

    cards = parse_review_cards(html)
    assert len(cards) == 4


def test_unset_chip_when_label_immediately_followed_by_label() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    facebook = next(card for card in cards if card.platform == "Facebook")

    # "Asset for review - LINK" is immediately followed by the "Copy review"
    # label with no status line of its own -> asset_status is unset.
    assert facebook.asset_status is None
    # And "Copy review" must still be parsed as its own label on the next
    # loop turn -- its value must NOT have been consumed as asset_status.
    assert facebook.copy_status == "Needs legal"


def test_three_cards_sharing_header_differ_by_identity_key() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    shared_header = "August XX, 2026 - Welcome Back blog"
    sharing = [card for card in cards if card.header == shared_header]

    assert len(sharing) == 3
    identity_keys = {card.identity_key for card in sharing}
    assert len(identity_keys) == 3  # all distinct
    platforms = {card.platform for card in sharing}
    assert platforms == {"LinkedIn", "Instagram", "Facebook"}


def test_ordinal_disambiguates_true_duplicate_header_and_platform() -> None:
    # Bonus coverage beyond the required list: two cards sharing BOTH header
    # AND platform (not just header) is the scenario the ordinal exists for.
    html = _load("duplicate_header_platform.html")
    cards = parse_review_cards(html)

    assert len(cards) == 2
    assert cards[0].header == cards[1].header
    assert cards[0].platform == cards[1].platform == "LinkedIn"
    assert cards[0].identity_key[2] == 0
    assert cards[1].identity_key[2] == 1
    assert cards[0].identity_key != cards[1].identity_key


def test_identity_stable_across_different_tracking_params() -> None:
    first_pass = parse_review_cards(_load("four_cards.html"))
    second_pass = parse_review_cards(_load("four_cards_tracking_params_changed.html"))

    assert len(first_pass) == len(second_pass) == 4
    for first_card, second_card in zip(first_pass, second_pass, strict=True):
        assert first_card.identity_key == second_card.identity_key
        assert first_card.copy_hash == second_card.copy_hash
        assert first_card.asset_url == second_card.asset_url


def test_identity_stable_under_card_reordering() -> None:
    baseline = parse_review_cards(_load("four_cards.html"))
    reordered = parse_review_cards(_load("four_cards_reordered.html"))

    assert len(baseline) == len(reordered) == 4
    baseline_by_key = _by_header_platform(baseline)
    reordered_by_key = _by_header_platform(reordered)

    assert set(baseline_by_key) == set(reordered_by_key)
    for key, baseline_card in baseline_by_key.items():
        reordered_card = reordered_by_key[key]
        assert baseline_card.identity_key == reordered_card.identity_key
        assert baseline_card.copy_hash == reordered_card.copy_hash


def test_unknown_status_value_is_carried_through_not_dropped() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    facebook = next(card for card in cards if card.platform == "Facebook")

    assert facebook.copy_status == "Needs legal"


def test_classify_status_terminal_actionable_and_unknown() -> None:
    assert classify_status("Approved") == "terminal"
    assert classify_status("Published") == "terminal"
    assert classify_status("Draft") == "actionable"
    assert classify_status("Ready") == "actionable"
    assert classify_status("Needs legal") == "unknown"
    assert classify_status(None) == "unknown"


def test_multi_platform_combo_survives_intact() -> None:
    html = _load("four_cards.html")
    cards = parse_review_cards(html)
    enrollment = next(card for card in cards if card.header.endswith("Fall Enrollment push"))

    assert enrollment.platform == "FB, LI, & X"
    assert enrollment.asset_status == "Approved"
    assert enrollment.copy_status == "Published"


def test_sign_in_page_raises_typed_error() -> None:
    html = _load("sign_in_page.html")
    with pytest.raises(SignInPageError):
        parse_review_cards(html)


def test_zero_cards_raises_loudly_and_is_distinct_from_sign_in_error() -> None:
    html = _load("no_cards.html")
    with pytest.raises(NoReviewCardsFoundError) as exc_info:
        parse_review_cards(html)

    assert not isinstance(exc_info.value, SignInPageError)


# ---------------------------------------------------------------------------
# unwrap_google_redirect_url -- pure helper, exercised directly too
# ---------------------------------------------------------------------------


def test_unwrap_google_redirect_url_extracts_q_param() -> None:
    wrapped = (
        "https://www.google.com/url?q=https://example.com/asset&sa=D&source=editors&ust=123&usg=abc"
    )
    assert unwrap_google_redirect_url(wrapped) == "https://example.com/asset"


def test_unwrap_google_redirect_url_leaves_plain_url_unchanged() -> None:
    plain = "https://example.com/asset"
    assert unwrap_google_redirect_url(plain) == plain


def test_one_malformed_card_does_not_kill_the_whole_parse() -> None:
    """A single bad card must be skipped and reported, not take the pipeline down.

    Production incident 2026-08-12: a card was duplicated into a test tab with
    only its body row — the "August XX, 2026 - Title" header row was left
    behind. That single-row table matched the card signature, failed to build,
    and the exception escaped a list comprehension, killing every poll tick.
    Ten well-formed cards went unprocessed and the pipeline sat silently dead.
    """
    good = (
        "<table><tr><td colspan='2'><p><span>August XX, 2026 - Good card</span></p></td></tr>"
        "<tr><td><p><span>Platform: LinkedIn</span></p>"
        "<p><span>Asset for review - LINK</span></p><p><span>Draft</span></p>"
        "<p><span>Copy review</span></p><p><span>Ready</span></p></td>"
        "<td><p><span>Body copy here.</span></p></td></tr></table>"
    )
    # Signature-matching but header-less: exactly the shape that caused the outage.
    malformed = (
        "<table><tr><td><p><span>Platform: X</span></p>"
        "<p><span>Asset for review - LINK</span></p><p><span>TEST</span></p>"
        "<p><span>Copy review</span></p><p><span>Draft</span></p></td>"
        "<td><p><span>Orphaned test card.</span></p></td></tr></table>"
    )

    skipped: list[str] = []
    cards = parse_review_cards(f"<html><body>{malformed}{good}</body></html>", skipped=skipped)

    assert len(cards) == 1, "the well-formed card must still be parsed"
    assert cards[0].platform == "LinkedIn"
    assert len(skipped) == 1, "the malformed card must be reported, not silently dropped"
    assert "fewer than 2 rows" in skipped[0]


def test_all_cards_malformed_yields_no_cards_and_reports_each() -> None:
    """If every card is malformed the caller gets zero cards — which upstream
    already treats as loud — plus one report per skipped card."""
    malformed = (
        "<table><tr><td><p><span>Platform: X</span></p>"
        "<p><span>Copy review</span></p><p><span>Draft</span></p></td></tr></table>"
    )
    skipped: list[str] = []
    cards = parse_review_cards(f"<html><body>{malformed}{malformed}</body></html>", skipped=skipped)

    assert cards == []
    assert len(skipped) == 2


def test_comment_anchors_are_stripped_from_titles_and_copy() -> None:
    """Google Docs comment anchors must never reach a title or the copy body.

    The export renders each comment as ``<sup><a href="#cmnt1">[a]</a></sup>``
    inline in the text. Production incident 2026-08-12: with 60 of these in the
    doc after the team reviewed it, one Instagram post was tracked as three
    separate cards ("Welcome Back blog", "...blog[u]", "...blog[u][v]") — and
    because the markers land in the copy body too, adding a COMMENT changed
    copy_hash, which the reopen logic reads as "the wording changed since
    approval". Commenting on an approved post would have reopened it.
    """
    anchor = '<sup><a href="#cmnt1" id="cmnt_ref1">[u]</a></sup>'
    table = (
        "<table><tr><td colspan='2'><p><span>August XX, 2026 - Welcome Back blog</span>"
        f"{anchor}</p></td></tr>"
        "<tr><td><p><span>Platform: Instagram</span></p>"
        "<p><span>Asset for review - LINK</span></p><p><span>Draft</span></p>"
        "<p><span>Copy review</span></p><p><span>Ready</span></p></td>"
        f"<td><p><span>The real copy.</span>{anchor}</p></td></tr></table>"
    )
    cards = parse_review_cards(f"<html><body>{table}</body></html>")

    assert len(cards) == 1
    card = cards[0]
    assert card.title == "Welcome Back blog", f"anchor leaked into the title: {card.title!r}"
    assert "[u]" not in card.header
    assert "[u]" not in card.copy_body
    assert card.copy_body.strip() == "The real copy."


def test_adding_a_comment_does_not_change_the_copy_hash() -> None:
    """The identity-stability half of the same bug, asserted directly.

    Two exports of the same card, one before a comment and one after, must
    produce the SAME copy_hash and the SAME identity_key — otherwise a comment
    reopens an approved post and fragments its history.
    """

    def build(anchor: str) -> str:
        return (
            "<table><tr><td colspan='2'><p><span>August XX, 2026 - Not off script</span>"
            f"{anchor}</p></td></tr>"
            "<tr><td><p><span>Platform: X</span></p>"
            "<p><span>Asset for review - LINK</span></p><p><span>Draft</span></p>"
            "<p><span>Copy review</span></p><p><span>Ready</span></p></td>"
            f"<td><p><span>Body text that nobody edited.</span>{anchor}</p></td></tr></table>"
        )

    commented = '<sup><a href="#cmnt7">[c]</a></sup>'
    before = parse_review_cards("<html><body>" + build("") + "</body></html>")[0]
    after = parse_review_cards("<html><body>" + build(commented) + "</body></html>")[0]

    assert before.copy_hash == after.copy_hash, "a comment must not look like an edit"
    assert before.identity_key == after.identity_key
