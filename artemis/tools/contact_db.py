"""Tool: contact_db_stub.has_contact

Reads district_contacts; returns 'true' if an active contact exists for the district.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

districtId resolution:
  - Numeric string (e.g. "123"): queried directly as district_contacts.district_id.
  - Non-numeric string: look up signal_queue rows with matching district_id (text),
    read their resolved_district_id FK, then query contacts there.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import DistrictContact, SignalQueue
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="contact_db_stub.has_contact",
    description=(
        "Reads district_contacts; returns 'true' if an active contact exists for the district. "
        "Returns 'false' if no active contact is found."
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
            )
            .limit(1)
        )
        contact_id = (await ctx.session.execute(stmt_contacts)).scalar_one_or_none()

        result = "true" if contact_id is not None else "false"
        logger.debug(
            "contact_db_stub.has_contact: districtId=%r resolved=%r result=%s agent=%s",
            district_id_raw,
            resolved_district_ids,
            result,
            ctx.agent_id,
        )
        return result

    return (_DEF, _impl)


register_tool("contact_db_stub.has_contact", _factory)
