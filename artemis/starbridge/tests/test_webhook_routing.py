"""A signed delivery, through the real route, into the queue that fits it.

Asserts the EFFECT -- a row in signal_queue, an observation in memory -- rather
than the endpoint's HTTP body. A 200 proves the handler returned; it does not
prove anything was written, and that gap is where the crisis-content approval
bugs lived.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import func, select

from artemis.marketing.models import SignalQueue
from artemis.starbridge.router import route_delivery

_PRIVATE = Ed25519PrivateKey.generate()


def _public_key_string() -> str:
    der = _PRIVATE.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return "whpk_" + base64.b64encode(der).decode()


def _body(
    *, row_id: str = "row-1", name: str, columns: dict[str, object], kind: str = "created"
) -> bytes:
    return json.dumps(
        {
            "type": f"bridge.row.{kind}",
            "timestamp": "2026-09-04T15:57:42.586897Z",
            "data": {
                "rowId": row_id,
                "name": name,
                "bridgeId": "bridge-1",
                "status": "Processed",
                "columns": {k: {"value": v, "status": "Processed"} for k, v in columns.items()},
            },
        }
    ).encode()


_RFP_COLUMNS = {
    "Match Score": 5,
    "Summarized Relevance": "- Statewide universal reading screener required for PreK-8.",
    "Buyer Name": "Kansas State Department of Education",
    "Source Url": "https://supplier.sok.ks.gov/bid",
}
_MEETING_COLUMNS = {
    "Match Score": 4,
    "Summarized Relevance": "- Board reviewed the district literacy strategic plan.",
    "Buyer Name": "Lammersville Unified School District",
}


@pytest.mark.asyncio
async def test_a_procurement_signal_lands_in_the_campaign_queue(db_session) -> None:
    payload = json.loads(
        _body(name="Universal Reading Screener-Public School Districts RFP", columns=_RFP_COLUMNS)
    )

    result = await route_delivery(db_session, payload)
    await db_session.flush()

    assert result.outcome == "queued"
    row = (
        await db_session.execute(select(SignalQueue).where(SignalQueue.id == result.signal_id))
    ).scalar_one()
    assert row.source_type == "starbridge"
    assert row.district_id == "Kansas State Department of Education"
    assert "PROCUREMENT_LITERACY_RFP" in row.reason_codes
    assert row.source_url == "https://supplier.sok.ks.gov/bid"
    assert row.provenance["match_score"] == 5


@pytest.mark.asyncio
async def test_board_meeting_context_does_not_reach_the_campaign_queue(db_session) -> None:
    """It goes to memory instead. 20 of 35 live findings were this shape."""
    before = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()

    payload = json.loads(
        _body(
            row_id="row-2",
            name="Lammersville USD Governing Board Meeting Minutes",
            columns=_MEETING_COLUMNS,
        )
    )
    result = await route_delivery(db_session, payload)
    await db_session.flush()

    assert result.outcome == "observation"
    after = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()
    assert after == before, "meeting context must not enter Josh's campaign queue"


@pytest.mark.asyncio
async def test_the_same_row_delivered_twice_is_queued_once(db_session) -> None:
    """Starbridge re-sends bridge.row.updated as later columns finish."""
    payload = json.loads(_body(row_id="row-3", name="Literacy screener RFP", columns=_RFP_COLUMNS))

    first = await route_delivery(db_session, payload)
    await db_session.flush()
    second = await route_delivery(db_session, payload)

    assert first.outcome == "queued"
    assert second.outcome == "duplicate"
    assert second.signal_id == first.signal_id


@pytest.mark.asyncio
async def test_an_update_delivery_for_a_queued_row_does_not_duplicate_it(db_session) -> None:
    created = json.loads(_body(row_id="row-4", name="Literacy RFP", columns=_RFP_COLUMNS))
    updated = json.loads(
        _body(row_id="row-4", name="Literacy RFP", columns=_RFP_COLUMNS, kind="updated")
    )

    await route_delivery(db_session, created)
    await db_session.flush()

    assert (await route_delivery(db_session, updated)).outcome == "duplicate"


@pytest.mark.asyncio
async def test_an_unclassifiable_signal_is_written_nowhere(db_session) -> None:
    """Worse than not having it: a signal carrying a confident wrong label."""
    before = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()

    payload = json.loads(
        _body(
            row_id="row-5",
            name="Parking lot resurfacing bid",
            columns={"Match Score": 5, "Buyer Name": "City of Fort Worth"},
        )
    )
    result = await route_delivery(db_session, payload)
    await db_session.flush()

    assert result.outcome == "unclassified"
    after = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_a_payload_with_no_data_object_is_reported_not_raised(db_session) -> None:
    """Starbridge retries >= 429 three times; a body we cannot read never improves."""
    assert (await route_delivery(db_session, {"type": "bridge.row.created"})).outcome == "malformed"


@pytest.mark.asyncio
async def test_a_row_without_an_id_is_malformed(db_session) -> None:
    assert (
        await route_delivery(db_session, {"data": {"name": "x", "columns": {}}})
    ).outcome == "malformed"


@pytest.mark.asyncio
async def test_one_rfp_matched_by_four_bridges_is_queued_once(db_session) -> None:
    """rowId cannot catch this, and it is the common case, not an edge case.

    The Kansas statewide screener arrived from four bridges -- Intervention
    Search, Assessment Search, Amira Learning Feed and RFPs - State & State DOE
    -- as four different rows with four different rowIds. Measured overlap
    between the RFP bridges runs 0-50%, so they are genuinely complementary and
    worth keeping; the price is that one event legitimately arrives several times
    and only the headline identifies it.
    """
    title = "Universal Reading Screener-Public School Districts RFP"
    outcomes = []
    for bridge_row in ("row-a", "row-b", "row-c", "row-d"):
        payload = json.loads(_body(row_id=bridge_row, name=title, columns=_RFP_COLUMNS))
        result = await route_delivery(db_session, payload)
        await db_session.flush()
        outcomes.append(result.outcome)

    assert outcomes == ["queued", "duplicate", "duplicate", "duplicate"], outcomes
    rows = (
        await db_session.execute(
            select(func.count()).select_from(SignalQueue).where(SignalQueue.headline == title)
        )
    ).scalar_one()
    assert rows == 1


@pytest.mark.asyncio
async def test_headline_matching_ignores_case(db_session) -> None:
    """Bridges phrase the same solicitation with different capitalisation."""
    await route_delivery(
        db_session,
        json.loads(_body(row_id="row-x", name="Literacy Screener RFP", columns=_RFP_COLUMNS)),
    )
    await db_session.flush()

    second = await route_delivery(
        db_session,
        json.loads(_body(row_id="row-y", name="literacy screener rfp", columns=_RFP_COLUMNS)),
    )
    assert second.outcome == "duplicate"


@pytest.mark.asyncio
async def test_two_genuinely_different_rfps_both_queue(db_session) -> None:
    """The dedupe must not swallow distinct solicitations."""
    a = await route_delivery(
        db_session,
        json.loads(_body(row_id="row-p", name="Kansas screener RFP", columns=_RFP_COLUMNS)),
    )
    await db_session.flush()
    b = await route_delivery(
        db_session,
        json.loads(_body(row_id="row-q", name="Ohio HQIM approved list RFP", columns=_RFP_COLUMNS)),
    )
    await db_session.flush()

    assert a.outcome == "queued"
    assert b.outcome == "queued"
    assert a.signal_id != b.signal_id


# ── near-duplicate headlines ─────────────────────────────────────────────────


def test_the_kansas_pair_collapses_to_one_key() -> None:
    """One solicitation, two bridges, three characters apart.

    Exact matching collapsed neither, so Kansas reached the queue twice in the
    live backfill.
    """
    from artemis.starbridge.router import normalize_headline

    assert normalize_headline("Universal Reading Screener-Public School Districts RFP") == (
        normalize_headline("Universal Reading Screener-Public School Districts")
    )


def test_procurement_vehicle_wording_does_not_make_two_signals() -> None:
    from artemis.starbridge.router import normalize_headline

    base = normalize_headline("Synchronous Online High Impact Tutorials")
    for variant in (
        "Synchronous Online High Impact Tutorials (Request for Proposal)",
        "Synchronous Online High Impact Tutorials RFP",
        "Synchronous Online High Impact Tutorials - Invitation to Bid",
        "Synchronous Online High Impact Tutorials  (RFQ)",
    ):
        assert normalize_headline(variant) == base, variant


def test_two_different_solicitations_stay_separate() -> None:
    """The case a fuzzy threshold would have broken.

    Two districts running separate screener RFPs is normal, and merging them
    loses a real opportunity — which is why this is a closed list of vehicle
    words rather than a similarity score.
    """
    from artemis.starbridge.router import normalize_headline

    assert normalize_headline("Kansas Universal Reading Screener RFP") != normalize_headline(
        "Ohio Universal Reading Screener RFP"
    )
    assert normalize_headline("K-3 Screener RFP") != normalize_headline("K-8 Screener RFP")


@pytest.mark.asyncio
async def test_a_near_duplicate_delivery_is_recognised(db_session) -> None:
    first = await route_delivery(
        db_session,
        json.loads(
            _body(
                row_id="r-1",
                name="Universal Reading Screener-Public School Districts RFP",
                columns=_RFP_COLUMNS,
            )
        ),
    )
    await db_session.flush()

    second = await route_delivery(
        db_session,
        json.loads(
            _body(
                row_id="r-2",
                name="Universal Reading Screener-Public School Districts",
                columns=_RFP_COLUMNS,
            )
        ),
    )

    assert first.outcome == "queued"
    assert second.outcome == "duplicate"
    assert second.signal_id == first.signal_id


# ── the bridge is the authority on its own type ──────────────────────────────


@pytest.mark.asyncio
async def test_a_meetings_bridge_row_goes_to_memory_not_the_campaign_queue(
    db_session, monkeypatch
) -> None:
    """21 board-minute rows reached Josh's queue in the live backfill.

    "Charleston County SD Allocates $2.2M for Amira" is intelligence about us,
    not a procurement trigger. The webhook body carries no filterType, so the
    keyword heuristic saw "grant" and routed it as actionable.
    """
    import artemis.starbridge.router as router_mod

    async def _meeting(_bridge_id: str) -> str:
        return "meeting"

    monkeypatch.setattr(router_mod, "resolve_bridge_type", _meeting)
    before = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()

    payload = json.loads(
        _body(
            row_id="r-bm",
            name="Charleston County SD Allocates $2.2M for Amira",
            columns={
                "Match Score": 4,
                "Summarized Relevance": "- Board approved literacy grant funding.",
                "Buyer Name": "Charleston County School District",
            },
        )
    )
    result = await route_delivery(db_session, payload)
    await db_session.flush()

    assert result.outcome == "observation"
    after = (await db_session.execute(select(func.count()).select_from(SignalQueue))).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_the_bridge_type_overrides_the_keyword_guess(db_session, monkeypatch) -> None:
    """ "Pasadena Unified SD RFP for Security Patrols" was tagged a literacy RFP.

    The title contains "RFP" and the bridge's summary column mentions reading,
    so the heuristic promoted a security contract into the campaign queue.
    """
    import artemis.starbridge.router as router_mod

    async def _meeting(_bridge_id: str) -> str:
        return "meeting"

    monkeypatch.setattr(router_mod, "resolve_bridge_type", _meeting)

    payload = json.loads(
        _body(
            row_id="r-sec",
            name="Pasadena Unified SD RFP for Security Patrols",
            columns={
                "Match Score": 4,
                "Summarized Relevance": "- Reading room patrols.",
                "Buyer Name": "Pasadena USD",
            },
        )
    )
    result = await route_delivery(db_session, payload)

    assert result.outcome != "queued", "a security contract must not enter the campaign queue"


@pytest.mark.asyncio
async def test_an_unresolvable_bridge_falls_back_rather_than_dropping(
    db_session, monkeypatch
) -> None:
    """A Starbridge outage must degrade, not lose deliveries."""
    import artemis.starbridge.router as router_mod

    async def _unresolved(_bridge_id: str) -> str:
        return ""

    monkeypatch.setattr(router_mod, "resolve_bridge_type", _unresolved)

    result = await route_delivery(
        db_session,
        json.loads(
            _body(row_id="r-fb", name="Statewide Literacy Screener RFP", columns=_RFP_COLUMNS)
        ),
    )
    assert result.outcome == "queued"
