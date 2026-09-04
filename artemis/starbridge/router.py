"""Route a verified Starbridge webhook into the queue that fits the signal.

Split by what the signal *is*, not by where it came from. A live feed of 35
classified findings was 20 board-meeting items against 15 procurement and funding
ones; sending all of them to Josh's campaign queue would bury the Kansas
statewide screener RFP under twenty district strategic plans.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import SignalQueue
from artemis.scouts.starbridge.client import signal_to_item
from artemis.scouts.starbridge.mapping import item_to_finding
from artemis.starbridge.webhook import is_actionable

logger = logging.getLogger(__name__)

#: How far back to look for the same headline from a different bridge. Long
#: enough to cover a solicitation matched by several bridges over a few days,
#: short enough that an annually recurring RFP with an identical title is not
#: silently swallowed a year later.
CROSS_BRIDGE_WINDOW = timedelta(days=30)

#: Noise words stripped before comparing two headlines. Kansas arrived twice as
#: "Universal Reading Screener-Public School Districts RFP" and "...Public School
#: Districts" -- one solicitation, two bridges, three characters apart, and an
#: exact match collapsed neither. Deliberately a short closed list rather than
#: fuzzy scoring: a similarity threshold high enough to catch this would also
#: merge genuinely different solicitations, and two districts running separate
#: screener RFPs is exactly the case we must not lose.
_HEADLINE_NOISE = re.compile(
    r"\b(rfps?|rfi|rfq|rfa|itb"
    r"|requests? for (proposals?|information|quotes?|applications?|qualifications?)"
    r"|invitations? (for|to) bids?|solicitation)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_headline(headline: str) -> str:
    """Reduce a solicitation title to a comparable key.

    Strips the procurement-vehicle words, then all punctuation and spacing, so
    "Universal Reading Screener-Public School Districts RFP" and the same title
    without "RFP" collapse to one key while two distinct RFPs stay distinct.
    """
    stripped = _HEADLINE_NOISE.sub(" ", headline or "")
    return _NON_ALNUM.sub("", stripped.lower())


SOURCE_TYPE = "starbridge"
DISCOVERED_BY = "starbridge_webhook"

#: Josh's campaign families, keyed by the reason code that implies them. Taken
#: from josh_spec's own mappings so the two cannot drift.
_FAMILY_BY_CODE: dict[str, str] = {
    "PROCUREMENT_LITERACY_RFP": "OBC",
    "PROCUREMENT_ELA_ADOPTION": "OBC",
    "VENDOR_APPROVED_LIST": "OBC",
    "FUNDING_LITERACY_GRANT": "High-impact tutoring (HIT)",
    "POLICY_LIT_MANDATE": "Dyslexia / structured literacy",
    "LEADER_TRANSITION_FORMAL": "General growth",
    "DISTRICT_STRATEGIC_LITERACY": "General growth",
}


@dataclass
class RouteResult:
    """What happened to one delivery. Every outcome is named, none is silent."""

    outcome: str
    signal_id: int | None = None
    reason_codes: list[str] | None = None
    detail: str = ""


#: The webhook body carries no `filterType`; the feed does. Without it every RFP
#: delivered by webhook classified as unclassified and was dropped -- the Kansas
#: statewide screener included. Inferred from the row's own words instead.
_RFP_MARKERS = (
    "rfp",
    "request for proposal",
    "request for quote",
    "invitation for bid",
    "invitation to bid",
    "solicitation",
    "itb",
    "rfq",
)
_MEETING_MARKERS = (
    "meeting minutes",
    "board meeting",
    "agenda",
    "governing board",
    "school board",
    "strategic plan",
    "improvement plan",
)


#: bridgeId -> filterType, resolved once from the API and reused.
#:
#: The webhook body names its bridge but not the bridge's type, and the bridge
#: is the authority: RFP, Meeting, Buyer, Contact, Purchase. Guessing from the
#: row's words instead put 21 board-minute rows into Josh's campaign queue --
#: "Charleston County SD Allocates $2.2M for Amira" is intelligence about us, not
#: a procurement trigger -- and classified "Pasadena Unified SD RFP for Security
#: Patrols" as a literacy RFP because the title contains "RFP" and the bridge's
#: summary column mentions reading.
_BRIDGE_TYPES: dict[str, str] = {}


async def resolve_bridge_type(bridge_id: str) -> str:
    """Return the bridge's own filterType, or "" if it cannot be looked up.

    Cached for the process lifetime: a bridge's type does not change, and a
    backfill delivers a thousand rows from five bridges in twenty seconds.
    Failure returns "" rather than raising, so a Starbridge outage degrades to
    the content heuristic instead of dropping deliveries.
    """
    if not bridge_id:
        return ""
    if bridge_id in _BRIDGE_TYPES:
        return _BRIDGE_TYPES[bridge_id]

    try:
        import os

        from artemis.scouts.starbridge.client import StarbridgeClient

        client = StarbridgeClient(api_key=os.getenv("STARBRIDGE_API_KEY", ""))
        data = await client._get(f"/api/external/bridge/{bridge_id}")
        filter_type = str(data.get("filterType") or "").lower()
    except Exception:
        logger.warning("starbridge: could not resolve bridge type for %s", bridge_id, exc_info=True)
        return ""

    _BRIDGE_TYPES[bridge_id] = filter_type
    return filter_type


def _infer_item_type(name: str, columns: dict[str, Any]) -> str:
    """Fallback only, used when the bridge's own type cannot be resolved.

    Returning "" is deliberate: the mapper treats an unknown type as
    non-procurement rather than assuming, so a wrong guess cannot promote a
    parking-lot bid into Josh's campaign queue.
    """
    haystack = f"{name} {' '.join(str(c.get('value', '')) for c in columns.values() if isinstance(c, dict))}".lower()
    if any(marker in haystack for marker in _RFP_MARKERS):
        return "rfp"
    if any(marker in haystack for marker in _MEETING_MARKERS):
        return "meeting"
    return ""


def _webhook_to_signal(payload: dict[str, Any], bridge_type: str = "") -> dict[str, Any] | None:
    """Reshape a webhook body into the feed shape the mapper already understands.

    The webhook sends ``data.columns`` keyed by column name with no bridge
    envelope, while the feed sends bridge + row. Normalising here means one
    mapper and one set of reason-code rules for both paths -- a second mapper is
    how the two would drift apart and start classifying the same RFP differently.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    columns = data.get("columns") or {}
    return {
        "bridge": {
            "name": str(data.get("bridgeId") or ""),
            # The bridge's own type when we have it; the heuristic only as a
            # fallback, because the bridge is the authority on what it collects.
            "filterType": bridge_type or _infer_item_type(str(data.get("name") or ""), columns),
            "columns": [],
        },
        "row": {
            "rowId": data.get("rowId"),
            "name": data.get("name"),
            "columns": columns,
        },
    }


async def route_delivery(session: AsyncSession, payload: dict[str, Any]) -> RouteResult:
    """Classify one delivery and write it to the queue that fits.

    Never raises for an unusable payload: Starbridge retries anything at or above
    429, and retrying a body we will never understand just repeats the failure
    three times. Unusable is reported, not thrown.
    """
    data = payload.get("data")
    bridge_type = await resolve_bridge_type(
        str(data.get("bridgeId") or "") if isinstance(data, dict) else ""
    )
    signal = _webhook_to_signal(payload, bridge_type)
    if signal is None:
        return RouteResult("malformed", detail="payload has no data object")

    item = signal_to_item(signal)
    if not item.item_id:
        return RouteResult("malformed", detail="row carries no rowId")

    # Dedupe BEFORE classifying. A duplicate is a duplicate whether or not this
    # particular copy can be classified, and the copies differ: Kansas arrived
    # once as "...Districts RFP" and once as "...Districts", and only the first
    # carries the word that identifies it as procurement. Classifying first meant
    # the second copy was dropped as unclassified rather than recognised as the
    # signal we already had.
    # Idempotency at the data layer, not just on webhook-id: Starbridge sends
    # bridge.row.updated for the same row repeatedly as later columns finish.
    existing = (
        await session.execute(
            select(SignalQueue.id).where(
                SignalQueue.source_type == SOURCE_TYPE,
                SignalQueue.source_id == item.item_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return RouteResult("duplicate", signal_id=int(existing))

    # Cross-bridge duplication, which rowId cannot catch. One RFP matched by four
    # bridges is four DIFFERENT rows with four different rowIds -- the Kansas
    # statewide screener arrived that way from Intervention Search, Assessment
    # Search, Amira Learning Feed and RFPs - State & State DOE. Measured overlap
    # between the RFP bridges runs 0-50%, so they are complementary and worth
    # keeping; the price is that the same event legitimately arrives several
    # times and only the headline identifies it.
    headline = item.title.strip()
    key = normalize_headline(headline)
    if key:
        # Compared in Python, not SQL: the normalisation is a regex the database
        # cannot express, and the candidate set is one window of Starbridge
        # signals rather than the whole table.
        recent = (
            (
                await session.execute(
                    select(SignalQueue.id, SignalQueue.headline).where(
                        SignalQueue.source_type == SOURCE_TYPE,
                        SignalQueue.created_at > datetime.now(UTC) - CROSS_BRIDGE_WINDOW,
                    )
                )
            )
            .tuples()
            .all()
        )
        for existing_id, existing_headline in recent:
            if normalize_headline(existing_headline or "") == key:
                return RouteResult("duplicate", signal_id=int(existing_id))

    finding = item_to_finding(item)
    codes: list[str] = list(finding.get("reasonCodes") or [])
    if not codes:
        # Not a failure. We could not place it in Josh's registry, and a signal
        # carrying a confident wrong label is worse than one we never had.
        return RouteResult("unclassified", detail=item.title[:120])

    if not is_actionable(codes):
        await _write_observation(session, item, finding, codes)
        return RouteResult("observation", reason_codes=codes, detail=item.title[:120])

    family = next((_FAMILY_BY_CODE[c] for c in codes if c in _FAMILY_BY_CODE), "General growth")
    row = SignalQueue(
        source_type=SOURCE_TYPE,
        source_url=item.source_url,
        source_id=item.item_id,
        headline=item.title[:500] or "(untitled Starbridge signal)",
        summary=item.summary or "",
        campaign_family=family,
        urgency_tier=str(finding.get("urgency") or "standard"),
        discovered_by=DISCOVERED_BY,
        district_id=str(finding.get("districtId") or "") or None,
        # `[{"code": ...}]`, not `["..."]`. This is the shape
        # `qualifier.qualify_signal` reads, and it reads `rc.get("code")` --
        # handed a bare string it raises AttributeError and the whole
        # qualification crashes. The first backfill wrote 1,021 rows as plain
        # strings, every one of which would have sat in pending_qualification
        # forever, never scored, never pushed, never seen by anyone.
        reason_codes=[{"code": c} for c in codes],
        provenance={
            "starbridge_row_id": item.item_id,
            "starbridge_bridge": item.bridge_name,
            "match_score": item.match_score,
            "buyer_name": item.buyer_name,
            "delivery_type": payload.get("type"),
            # The solicitation's closing date. Urgency is DERIVED from this and
            # then the date itself was thrown away, so a signal could say "hot"
            # while nobody could see what it was hot about -- and reconstructing
            # it meant paging the whole bridge back out of Starbridge.
            "due_date": item.deadline_date,
            "buyer_state": item.state,
        },
        # Enter at pending_qualification, never pre-qualified. Starbridge scores a
        # row against its own bridge, which is not the same question as whether it
        # qualifies for one of Josh's campaigns; skipping his qualifier would let a
        # vendor's relevance model decide our pipeline.
        signal_status="pending_qualification",
    )
    session.add(row)
    await session.flush()
    return RouteResult("queued", signal_id=int(row.id), reason_codes=codes)


async def _write_observation(
    session: AsyncSession, item: Any, finding: dict[str, Any], codes: list[str]
) -> None:
    """Board minutes and leader moves: context to retrieve later, not a trigger."""
    from artemis.memory.schemas import Scope
    from artemis.memory.store import write_observation

    await write_observation(
        session,
        Scope(scope_kind="workspace", scope_id="starbridge"),
        content=f"{item.title}\n\n{item.summary or ''}".strip(),
        category="starbridge_signal",
        source_quality=0.7,
        raw_payload={"finding": finding, "reason_codes": codes},
        raw_source_kind="starbridge_webhook",
        raw_source_id=item.item_id,
        raw_actor=DISCOVERED_BY,
    )
