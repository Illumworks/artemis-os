"""Mapping a Starbridge signal onto Josh's reason-code registry.

The registry holds exactly 17 codes. This module used to emit four --
BILL_INTRODUCED, FEDERAL_GRANT_OPEN, STATE_DYSLEXIA_MANDATE,
STATE_OBC_LEGISLATION -- and **none of them is in it**. They were invented
alongside the rest of the fabricated integration, so every finding the scout
produced carried a code no downstream consumer recognises.

The live run that caught it labelled a Kansas *state* reading-screener RFP as
FEDERAL_GRANT_OPEN, with districtId STATE_NATIONAL, because the old default sent
every unclassified item to BILL_INTRODUCED and the feed never populates `state`.
"""

from __future__ import annotations

from artemis.marketing.josh_spec import parse_spec
from artemis.scouts.starbridge.client import StarbridgeItem
from artemis.scouts.starbridge.mapping import _district_id, _reason_codes, item_to_finding

VALID_CODES = {r.code for r in parse_spec().reason_codes}


def _item(**kw: object) -> StarbridgeItem:
    base: dict = {
        "item_id": "r-1",
        "title": "Universal Reading Screener-Public School Districts RFP",
        "summary": "Universal reading screener services required statewide.",
        "item_type": "rfp",
        "buyer_name": "Kansas State Department of Education",
        "match_score": 5,
    }
    base.update(kw)
    return StarbridgeItem(**base)


def test_every_code_this_module_can_emit_exists_in_the_registry() -> None:
    """The whole bug in one assertion."""
    samples = [
        _item(),
        _item(title="ELA curriculum adoption", item_type="rfp"),
        _item(title="Approved list of HQIM vendors", item_type="rfp"),
        _item(title="Literacy grant funding award", item_type="purchase"),
        _item(title="Dyslexia screening mandate", item_type="rfp"),
        _item(title="New superintendent appointment", item_type="meeting"),
        _item(title="District literacy strategic plan", item_type="meeting"),
    ]
    emitted = {code for item in samples for code in _reason_codes(item)}

    assert emitted, "the samples must actually exercise the mapping"
    assert emitted <= VALID_CODES, f"codes outside Josh's registry: {emitted - VALID_CODES}"


def test_the_invented_codes_are_gone() -> None:
    for dead in (
        "BILL_INTRODUCED",
        "FEDERAL_GRANT_OPEN",
        "STATE_DYSLEXIA_MANDATE",
        "STATE_OBC_LEGISLATION",
    ):
        assert dead not in VALID_CODES, "sanity: these were never real"


def test_a_state_procurement_notice_is_procurement_not_a_federal_grant() -> None:
    """The exact live finding that exposed the problem."""
    codes = _reason_codes(_item())

    assert "PROCUREMENT_LITERACY_RFP" in codes
    assert "FEDERAL_GRANT_OPEN" not in codes


def test_an_unclassifiable_signal_gets_no_code_rather_than_a_default() -> None:
    """The old default sent everything unrecognised to BILL_INTRODUCED.

    An unclassified signal is worth less than nothing once it carries a
    confident wrong label, so abstaining is the correct outcome and the scout
    drops it.
    """
    assert (
        _reason_codes(_item(title="Parking lot resurfacing bid", summary="", item_type="rfp")) == []
    )


def test_an_approved_vendor_list_is_recognised_as_one() -> None:
    codes = _reason_codes(_item(title="Approved List of Reading Intervention Vendors"))
    assert "VENDOR_APPROVED_LIST" in codes


def test_a_board_meeting_naming_a_new_superintendent_is_a_leader_transition() -> None:
    codes = _reason_codes(
        _item(title="Board approves new superintendent", summary="", item_type="meeting")
    )
    assert codes == ["LEADER_TRANSITION_FORMAL"]


def test_codes_are_deduplicated_while_keeping_order() -> None:
    codes = _reason_codes(_item(title="Literacy screener reading intervention RFP"))
    assert len(codes) == len(set(codes))


# ── identifying the buyer ────────────────────────────────────────────────────


def test_the_buyer_name_identifies_the_district_not_state_national() -> None:
    """Every live finding came back STATE_NATIONAL: the feed never sets `state`.

    Starbridge names the buyer on the row, which is far more use.
    """
    assert _district_id(_item()) == "Kansas State Department of Education"


def test_the_state_is_used_when_no_buyer_is_named() -> None:
    assert _district_id(_item(buyer_name=None, state="TX")) == "STATE_TX"


def test_national_remains_the_last_resort_only() -> None:
    assert _district_id(_item(buyer_name=None, state=None)) == "STATE_NATIONAL"


def test_evidence_is_never_paraphrased() -> None:
    finding = item_to_finding(_item())
    assert "Universal Reading Screener-Public School Districts RFP" in finding["evidence"]
    assert "Universal reading screener services required statewide." in finding["evidence"]
