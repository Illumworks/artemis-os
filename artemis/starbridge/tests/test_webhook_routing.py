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
