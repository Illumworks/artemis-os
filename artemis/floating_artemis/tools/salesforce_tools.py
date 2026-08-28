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


async def _resolve_district_by_name(session: Any, district_name: str) -> tuple[Any, list[Any]]:
    """Resolve a district name to one row, or report the ambiguity.

    Returns ``(district, [])`` on a confident single match, ``(None, candidates)``
    when several districts share the normalized name, and ``(None, [])`` when
    nothing matches.

    The exact ``ILIKE`` this replaces matched only a byte-identical name. Josh hit
    that on 2026-08-28: Hillsborough County (FL) is stored as ``HILLSBOROUGH`` in
    our index, so every reasonable name he tried missed, and Prince George's
    missed because he said the short form. Reuses C5's
    ``normalize_district_name`` rather than growing a second, subtly different
    normalizer -- the two must stay in agreement or a district can be a target
    under one and unknown under the other.

    Ambiguity abstains and names the candidates. Attributing another district's
    sales activity to this one is the failure that matters here.
    """
    from sqlalchemy import select

    from artemis.marketing.models import District
    from artemis.marketing.targets.matching import normalize_district_name

    exact = (
        await session.execute(select(District).where(District.name.ilike(district_name)).limit(1))
    ).scalar_one_or_none()
    if exact is not None:
        return exact, []

    key = normalize_district_name(district_name)
    if not key:
        return None, []

    everything = [d for d in (await session.execute(select(District))).scalars().all() if d.name]

    matches = [d for d in everything if normalize_district_name(d.name) == key]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, matches

    # Nothing matched. Offer near misses rather than a dead end -- but as
    # SUGGESTIONS, never as an answer.
    #
    # This exists because our index stores NCES short forms: Hillsborough County
    # (FL) is literally "HILLSBOROUGH", so every reasonable name Josh tried
    # missed. The tempting fix is to add COUNTY to the shared normalizer, and it
    # is wrong -- "Jefferson County Schools" and "Jefferson Schools" are often
    # different districts, and stripping COUNTY without CITY is asymmetric in
    # every state that has both. Widening the normalizer to rescue this lookup
    # would quietly degrade C5 target matching, which was measured against the
    # live list. So the normalizer stays put and the ambiguity surfaces here.
    # Match the leading word as a WHOLE TOKEN, not a prefix. A prefix match on
    # "PRINCE" also pulls in Princeton and Princeville, burying the right answer
    # in noise -- and a suggestion list nobody trusts is the same as no list.
    lead = key.split(" ")[0] if key else ""
    if len(lead) >= 4:
        near = [d for d in everything if lead in normalize_district_name(d.name).split(" ")]
        if near:
            return None, near[:6]
    return None, []


async def _check_salesforce_activity(inp: dict[str, Any], *, session_factory: Any = None) -> str:
    """Report Salesforce activity for a district's known contacts.

    ``session_factory`` is a test seam. This opens its own session, so a test
    that seeds rows in its own uncommitted transaction is invisible to it —
    the injectable-factory pattern used elsewhere in this repo for exactly that
    reason. Production always passes None and gets ``artemis.db.SessionLocal``.
    """
    district_name = str(inp.get("district_name") or "").strip()
    district_id_raw = inp.get("district_id")
    if not district_name and district_id_raw is None:
        return "Error: provide district_name or district_id"

    try:
        import artemis.db as _db
        from artemis.marketing.contacts import list_contacts_for_district
        from artemis.marketing.models import District
        from artemis.marketing.salesforce_suppression import check_suppression

        _factory = session_factory or _db.SessionLocal
        async with _factory() as session:
            district: District | None = None
            if district_id_raw is not None:
                try:
                    district = await session.get(District, int(district_id_raw))
                except (TypeError, ValueError):
                    district = None
            else:
                district, candidates = await _resolve_district_by_name(session, district_name)
                if candidates:
                    names = ", ".join(f"{d.name} ({d.state}, id {d.id})" for d in candidates)
                    return (
                        f"No exact match for {district_name!r} in our district index. The "
                        f"closest entries are: {names}. Our index stores official short forms, "
                        "which often differ from how the district is written elsewhere. Tell me "
                        "which one is right (the id is cleanest) and I will run the check -- I "
                        "am not going to pick one, because attributing another district's sales "
                        "activity to this one is the error that matters here."
                    )

            if district is None:
                # Be precise about WHICH lookup failed. Callie told Josh on
                # 2026-08-28 that the Salesforce name might differ and asked him
                # for it -- but this lookup never touches Salesforce, so a perfect
                # Salesforce name would still have missed. Sending someone to
                # fetch a fact that cannot help is worse than saying "not found".
                return (
                    f"{district_name!r} is not in OUR district index, so there is nothing to "
                    "look up against Salesforce yet. This is a gap on our side, not a "
                    "Salesforce one -- the Salesforce account name will not change the "
                    "result. Give me the district id if you have it, or a closer form of "
                    "the name as it appears in district records."
                )

            contacts = await list_contacts_for_district(session, district.id)
            emailed_contacts = [c for c in contacts if c.email]
            if not emailed_contacts:
                return (
                    f"{district.name}: found in our index, but we hold no contacts with email "
                    "addresses for it, and this check runs per contact. So there is nothing "
                    "to check -- NOT a clean bill of health, and not a Salesforce failure. "
                    "Customer status and opportunity history stay unavailable for this "
                    "district until contacts are populated. Say that plainly rather than "
                    "implying the district looks clear."
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
