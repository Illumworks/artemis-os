"""#79/#80 — campaign-family taxonomy reconciliation.

Canonical families (Josh's spec §3, slugified): obc, dyslexia, biliteracy, hit,
general_growth. Every label / canonical slug / legacy alias normalizes to the
canonical slug through josh_spec.normalize_campaign_family, and scout intake
stores the canonical slug.
"""

from __future__ import annotations

import pytest

from artemis.marketing.josh_spec import (
    CANONICAL_CAMPAIGN_FAMILIES,
    normalize_campaign_family,
)
from artemis.marketing.scout_intake import normalize_intake_payload


def test_canonical_set() -> None:
    assert CANONICAL_CAMPAIGN_FAMILIES == ("obc", "dyslexia", "biliteracy", "hit", "general_growth")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # spec labels -> slugs
        ("OBC", "obc"),
        ("Dyslexia / structured literacy", "dyslexia"),
        ("Biliteracy / DLL", "biliteracy"),
        ("High-impact tutoring (HIT)", "hit"),
        ("General growth", "general_growth"),
        # canonical slugs pass through
        ("dyslexia", "dyslexia"),
        ("hit", "hit"),
        ("general_growth", "general_growth"),
        # legacy aliases -> canonical
        ("reading_growth", "general_growth"),
        ("state_screener", "dyslexia"),
        # case / whitespace insensitive
        ("  dyslexia / STRUCTURED literacy  ", "dyslexia"),
        # unrecognized
        ("nonsense_family", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_campaign_family(value: str | None, expected: str | None) -> None:
    assert normalize_campaign_family(value) == expected


def _payload(family: str) -> dict[str, object]:
    return {
        "sourceType": "news_article",
        "headline": "District literacy update",
        "campaignFamily": family,
        "urgencyTier": "standard",
        "reasonCodes": [{"code": "PROGRAM_LAUNCH"}],
        "evidence": "Board press release.",
        "sourceUrl": "https://example.org/x",
    }


def test_intake_normalizes_spec_label_to_slug() -> None:
    """A scout emitting the spec LABEL now validates + stores the canonical slug."""
    result = normalize_intake_payload(
        _payload("High-impact tutoring (HIT)"), scout_type="news_article"
    )
    assert result.campaign_family == "hit"


def test_intake_accepts_legacy_alias() -> None:
    result = normalize_intake_payload(_payload("reading_growth"), scout_type="news_article")
    assert result.campaign_family == "general_growth"


def test_intake_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="campaignFamily must be one of"):
        normalize_intake_payload(_payload("totally_made_up"), scout_type="news_article")
