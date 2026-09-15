"""Tool: contact_db_stub.has_contact

Reads district_contacts; returns 'true' if an active contact with an email
exists for the district.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

districtId resolution:
  - Numeric string (e.g. "123"): queried directly as district_contacts.district_id.
  - Non-numeric string: look up signal_queue rows with matching district_id (text),
    read their resolved_district_id FK, then query contacts there.

CONTACTS-1: this tool means "is there someone we can actually reach" -- it
gates whether a signal is classified 'routable' (see artemis/tools/signal_queue.py).
Since 'argus' rows can be active with no email (a person Argus only knows the
name and title of), the query below requires email IS NOT NULL as well as
active, or a district with only an email-less Argus contact would be
misreported as routable when there is in fact nobody to write to.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.deliverable import deliverable_email_clause
from artemis.marketing.models import DistrictContact, SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="contact_db_stub.has_contact",
    description=(
        "Reads district_contacts. Returns 'true' if an active contact with an email "
        "exists for the district. Otherwise returns 'false' followed by WHY — "
        "either this district has none while others do, or the contact database is "
        "empty for every district. Those are different facts and must be reported "
        "differently: do not describe an empty database as a gap in one district, "
        "state, or region."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "districtId": {
                "type": "string",
                "description": "The district ID to look up.",
            }
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        district_id_raw = arguments.get("districtId", "")
        if not isinstance(district_id_raw, str):
            district_id_raw = str(district_id_raw)

        resolved_district_ids: list[int] = []

        if district_id_raw.lstrip("-").isdigit():
            # Numeric: use directly as an integer district primary key
            resolved_district_ids = [int(district_id_raw)]
        else:
            # Non-numeric text id: look up signal_queue rows that carry this
            # district_id string and collect their resolved_district_id FK.
            stmt = select(SignalQueue.resolved_district_id).where(
                SignalQueue.district_id == district_id_raw,
                SignalQueue.resolved_district_id.isnot(None),
            )
            rows = (await ctx.session.execute(stmt)).scalars().all()
            # Deduplicate while preserving insertion order
            seen: set[int] = set()
            for rid in rows:
                if rid is not None and rid not in seen:
                    seen.add(rid)
                    resolved_district_ids.append(rid)

        if not resolved_district_ids:
            logger.debug(
                "contact_db_stub.has_contact: no resolved district for districtId=%r agent=%s",
                district_id_raw,
                ctx.agent_id,
            )
            return "false"

        stmt_contacts = (
            select(DistrictContact.id)
            .where(
                DistrictContact.district_id.in_(resolved_district_ids),
                DistrictContact.active.is_(True),
                # Not merely "has an email": every address stored today is on an
                # RFC 2606 reserved domain left behind by tests, so a NOT NULL
                # check answered `true` for a district nobody can write to.
                deliverable_email_clause(DistrictContact.email),
            )
            .limit(1)
        )
        contact_id = (await ctx.session.execute(stmt_contacts)).scalar_one_or_none()

        if contact_id is not None:
            logger.debug(
                "contact_db_stub.has_contact: districtId=%r resolved=%r result=true agent=%s",
                district_id_raw,
                resolved_district_ids,
                ctx.agent_id,
            )
            return "true"

        # A bare "false" cannot say WHICH kind of no this is, and an agent asked
        # about one district will reasonably read it as a gap in that district.
        # On 2026-09-15 Callie was asked for a New Mexico contact and answered
        # "NMPED isn't in our district contact database and I'm not finding
        # anyone in Albuquerque or Santa Fe either" -- true, and misleading:
        # there were 0 usable contacts for ANY of the 13,466 districts. Jon
        # would have gone looking for New Mexico coverage for a system that had
        # no contact data at all.
        #
        # Same shape as the approval bug in CLAUDE.md, where a path that could
        # not tell "not permitted" from "I could not look you up" sent everyone
        # hunting an allowlist for what was a data problem.
        usable = (
            await ctx.session.execute(
                select(func.count(DistrictContact.id)).where(
                    DistrictContact.active.is_(True),
                    deliverable_email_clause(DistrictContact.email),
                )
            )
        ).scalar() or 0
        logger.debug(
            "contact_db_stub.has_contact: districtId=%r resolved=%r result=false "
            "usable_contacts_total=%d agent=%s",
            district_id_raw,
            resolved_district_ids,
            usable,
            ctx.agent_id,
        )
        if usable == 0:
            return (
                "false -- and NOT because of this district: the contact database holds "
                "0 usable contacts for ANY district. Report this as a system-wide gap "
                "in contact data, never as a gap for this district, state or region."
            )
        return f"false -- no contact for this district ({usable} exist for other districts)."

    return (_DEF, _impl)


register_tool("contact_db_stub.has_contact", _factory)
