"""Salesforce read tool for Floating Artemis (SFDC-1).

One tool, layer 1 (read-only): ``check_salesforce_activity``. Answers "is
this district already in play?" -- existing customer, open opportunity, or a
contact recently emailed by sales -- so Callie can check BEFORE drafting
outreach, using the exact same fail-closed Salesforce logic as the
send-suppression guard (artemis.marketing.salesforce_suppression).

Registered ONLY inside
``artemis.floating_artemis.tool_registry._build_callie_tool_registry``
(CALLIE-2's scoped registry) -- see that function's docstring for the
keep/drop discipline this must not break. This tool is not registered on the
general fallthrough path, so no other agent (Artemis, Kai, Ares) gets it
unless a future brief explicitly adds it there too.

Read-only in both directions: it never writes to Salesforce (structurally
impossible -- see artemis.integrations.salesforce.client's docstring), and
it calls check_suppression with enrich=False, so it never writes to
district_contacts either. A layer-1 "just tell me" tool having a DB side
effect would be a surprising thing for a reviewer to have to notice; keeping
it enrich=False makes the layer-1 classification true, not just labeled.
"""

from __future__ import annotations

from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

CHECK_SALESFORCE_ACTIVITY = "check_salesforce_activity"


async def _check_salesforce_activity(inp: dict[str, Any]) -> str:
    district_name = str(inp.get("district_name") or "").strip()
    district_id_raw = inp.get("district_id")
    if not district_name and district_id_raw is None:
        return "Error: provide district_name or district_id"

    try:
        from sqlalchemy import select

        import artemis.db as _db
        from artemis.marketing.contacts import list_contacts_for_district
        from artemis.marketing.models import District
        from artemis.marketing.salesforce_suppression import check_suppression

        async with _db.SessionLocal() as session:
            district: District | None = None
            if district_id_raw is not None:
                try:
                    district = await session.get(District, int(district_id_raw))
                except (TypeError, ValueError):
                    district = None
            else:
                result = await session.execute(
                    select(District).where(District.name.ilike(district_name)).limit(1)
                )
                district = result.scalar_one_or_none()

            if district is None:
                return f"No district found matching {district_name or district_id_raw!r}."

            contacts = await list_contacts_for_district(session, district.id)
            emailed_contacts = [c for c in contacts if c.email]
            if not emailed_contacts:
                return (
                    f"{district.name}: no contacts on file with an email address -- "
                    "nothing to check against Salesforce."
                )

            lines: list[str] = [f"{district.name}: {len(emailed_contacts)} contact(s) checked."]
            any_unavailable = False
            for contact in emailed_contacts:
                check = await check_suppression(
                    session,
                    district_id=district.id,
                    email=contact.email or "",
                    enrich=False,
                )
                if check.skip_reason == "salesforce_unavailable":
                    any_unavailable = True
                    lines.append(
                        f"  {contact.name} ({contact.email}): could not verify -- {check.detail}"
                    )
                elif check.suppressed:
                    lines.append(
                        f"  {contact.name} ({contact.email}): {check.skip_reason} -- {check.detail}"
                    )
                else:
                    lines.append(f"  {contact.name} ({contact.email}): clear -- {check.detail}")

            if any_unavailable:
                lines.append(
                    "Salesforce was unreachable for at least one contact -- treat this "
                    "district as UNVERIFIED, not as clear to contact."
                )
            return "\n".join(lines)
    except Exception as exc:
        return f"check_salesforce_activity failed: {exc}"


def register_salesforce_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(
        Tool(
            name=CHECK_SALESFORCE_ACTIVITY,
            description=(
                "Read-only: check Salesforce for a district's known contacts -- existing "
                "customer, open opportunity, or recently emailed by sales. Use before "
                "drafting outreach so marketing does not step on an active sales "
                "conversation. Fails closed: reports 'could not verify' rather than a false "
                "'clear to contact' if Salesforce cannot be reached."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "district_name": {
                        "type": "string",
                        "description": "District name to look up",
                    },
                    "district_id": {
                        "type": "integer",
                        "description": "District id, if already known",
                    },
                },
            },
        ),
        _check_salesforce_activity,
        layer=1,
    )
