"""Classification rules for the Champions digest.

The expensive lesson behind this file: a keyword list that looked reasonable
flagged 45% of posts against a true rate of 1-3%, and the reason was never
visible in a unit test -- it was visible only when run against the real corpus.
So these tests pin the things that can be checked deterministically (the
tripwire, the vocabulary, the parsing, the staff exclusion) and the flag rate
itself is measured against live data instead of asserted here.
"""

from __future__ import annotations

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.champions.classify import (
    ESCALATION_TERMS,
    THEMES,
    Classification,
    classify_item,
    escalation_hits,
    strip_html,
)


def test_strip_html_unwraps_vanilla_bodies() -> None:
    assert strip_html("<p>Amira &amp; me</p>") == "Amira & me"
    assert strip_html("<div>a</div><div>b</div>") == "a b"
    assert strip_html(None) == ""


class TestEscalationTripwire:
    """A person is paged by these, so recall matters more than precision."""

    def test_catches_the_language_it_is_for(self) -> None:
        assert escalation_hits("I have a privacy concern about this") == ["privacy concern"]
        assert escalation_hits("taking it to the SCHOOL BOARD") == ["school board"]

    def test_outrag_is_a_stem_on_purpose(self) -> None:
        for word in ("outrage", "outraged", "outrageous"):
            assert escalation_hits(f"this is {word}") == ["outrag"]

    def test_does_not_fire_on_ordinary_community_talk(self) -> None:
        """The words that destroyed the old keyword list must stay silent here."""
        for text in (
            "my struggling readers love it",
            "the Amira Challenge was a hit",
            "here is the troubleshooting poster",
            "thanks Ms Buggs!",
            "reading difficulty is common at this age",
            "Monthly News is out",
        ):
            assert escalation_hits(text) == [], text

    def test_deliberate_omissions_stay_omitted(self) -> None:
        """`legal`, `publicly` and `superintendent` are too common innocently."""
        for word in ("legal", "publicly", "superintendent"):
            assert word not in ESCALATION_TERMS


class TestReplyParsing:
    def _parse(self, raw: str) -> Classification | None:
        from artemis.champions.classify import _coerce

        return _coerce(raw)

    def test_plain_json(self) -> None:
        got = self._parse(
            '{"summary":"x","theme":"Reports & data","product_issue":true,'
            '"adoption_friction":false}'
        )
        assert got == Classification("x", "Reports & data", True, False)

    def test_fenced_json_is_tolerated(self) -> None:
        got = self._parse(
            '```json\n{"summary":"x","theme":"Other","product_issue":false,'
            '"adoption_friction":false}\n```'
        )
        assert got is not None and got.summary == "x"

    def test_off_vocabulary_theme_becomes_other(self) -> None:
        """A free-text theme is what quietly destroys the week-over-week rollup."""
        got = self._parse('{"summary":"x","theme":"Login Problems","product_issue":false}')
        assert got is not None and got.theme == "Other"
        assert "Login Problems" not in THEMES

    def test_unusable_reply_is_none_not_a_default(self) -> None:
        """None means 'retry next run'. A fabricated default would be stored as
        though the model had judged it."""
        assert self._parse("I couldn't classify that.") is None
        assert self._parse('{"theme":"Other"}') is None  # no summary


@pytest.mark.asyncio
async def test_empty_body_is_not_sent_to_the_model() -> None:
    class _Boom:
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise AssertionError("should not have called the model")  # pragma: no cover

    assert await classify_item(title=None, body="", item_type="comment", adapter=_Boom()) is None
