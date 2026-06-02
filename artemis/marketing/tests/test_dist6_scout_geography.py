"""DIST6 — scouts emit district geography.

Covers the DIST6 surface: the signal_queue.write tool now exposes `stateCode`,
and the intake normalizer carries districtId/stateCode through to the structured
fields (district / state_code) that DIST3's resolver consumes — without
fabricating a district when none is supplied.

These are pure-function tests (schema introspection + normalizer); the resolver
wiring itself is covered by test_dist3_classifier.py.
"""

from __future__ import annotations

from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.tools.signal_queue import _DEF


def _base_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sourceType": "news_article",
        "headline": "Fort Bend ISD launches dual-language program",
        "campaignFamily": "biliteracy",
        "urgencyTier": "standard",
        "reasonCodes": [{"code": "PROGRAM_LAUNCH"}],
        "evidence": "District press release, 2026-05-01.",
        "sourceUrl": "https://example.org/fort-bend-dll",
    }
    payload.update(overrides)
    return payload


def test_tool_schema_exposes_state_code() -> None:
    """signal_queue.write now advertises stateCode + a districtId description."""
    props = _DEF.input_schema["properties"]
    assert "stateCode" in props, "stateCode must be an advertised input (intake reads it)"
    assert "districtId" in props
    assert props["districtId"].get("description"), (
        "districtId needs a resolution-oriented description"
    )


def test_normalize_carries_district_and_state() -> None:
    """districtId + stateCode flow through to the structured fields the resolver uses."""
    result = normalize_intake_payload(
        _base_payload(districtId="Fort Bend ISD", stateCode="TX"),
        scout_type="news_article",
    )
    assert result.district == "Fort Bend ISD"
    assert result.state_code == "TX"


def test_normalize_uppercases_state() -> None:
    """A lowercase stateCode is normalized to the canonical 2-letter uppercase form."""
    result = normalize_intake_payload(
        _base_payload(districtId="Grosse Pointe Schools", stateCode="mi"),
        scout_type="news_article",
    )
    assert result.state_code == "MI"


def test_normalize_without_district_does_not_fabricate() -> None:
    """A federal/state-level signal with no district leaves district NULL — no fabrication."""
    result = normalize_intake_payload(
        _base_payload(headline="CLSD federal literacy grant window opens"),
        scout_type="federal_funding",
    )
    assert result.district is None
    assert result.state_code is None
