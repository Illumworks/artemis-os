"""Route a verified Starbridge webhook into the queue that fits the signal.

Split by what the signal *is*, not by where it came from. A live feed of 35
classified findings was 20 board-meeting items against 15 procurement and funding
ones; sending all of them to Josh's campaign queue would bury the Kansas
statewide screener RFP under twenty district strategic plans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import SignalQueue
from artemis.scouts.starbridge.client import signal_to_item
from artemis.scouts.starbridge.mapping import item_to_finding
from artemis.starbridge.webhook import is_actionable

logger = logging.getLogger(__name__)

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


def _infer_item_type(name: str, columns: dict[str, Any]) -> str:
    """Best-effort type for a webhook row, or "" when it cannot be told.

    Returning "" is deliberate: the mapper treats an unknown type as
    non-procurement rather than assuming, so a wrong guess here cannot promote a
    parking-lot bid into Josh's campaign queue. The feed path keeps Starbridge's
    authoritative `filterType` and never reaches this.
    """
    haystack = f"{name} {' '.join(str(c.get('value', '')) for c in columns.values() if isinstance(c, dict))}".lower()
    if any(marker in haystack for marker in _RFP_MARKERS):
        return "rfp"
    if any(marker in haystack for marker in _MEETING_MARKERS):
        return "meeting"
    return ""


def _webhook_to_signal(payload: dict[str, Any]) -> dict[str, Any] | None:
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
            "filterType": _infer_item_type(str(data.get("name") or ""), columns),
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
    signal = _webhook_to_signal(payload)
    if signal is None:
        return RouteResult("malformed", detail="payload has no data object")

    item = signal_to_item(signal)
    if not item.item_id:
        return RouteResult("malformed", detail="row carries no rowId")

    finding = item_to_finding(item)
    codes: list[str] = list(finding.get("reasonCodes") or [])
    if not codes:
        # Not a failure. We could not place it in Josh's registry, and a signal
        # carrying a confident wrong label is worse than one we never had.
        return RouteResult("unclassified", detail=item.title[:120])

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
        return RouteResult("duplicate", signal_id=int(existing), reason_codes=codes)

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
        reason_codes=codes,
        provenance={
            "starbridge_row_id": item.item_id,
            "starbridge_bridge": item.bridge_name,
            "match_score": item.match_score,
            "buyer_name": item.buyer_name,
            "delivery_type": payload.get("type"),
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
