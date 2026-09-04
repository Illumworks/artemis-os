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

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)

CHECK_SALESFORCE_ACTIVITY = "check_salesforce_activity"


async def _render_people(client: Any, account_id: str) -> str:
    """Render the decision-makers at an account, conflicts first.

    Conflicts lead because they change what happens next: a contact a seller is
    actively sequencing is one marketing must not write to, and burying that
    under a roster is how it gets missed.

    An empty list is reported as "none returned", never as "nobody is being
    worked" -- absence of evidence is not evidence of absence, and this is the
    check standing between a marketing send and an open sales conversation.
    """
    from artemis.marketing.salesforce_account_lookup import fetch_account_contacts

    people = await fetch_account_contacts(client, account_id, limit=25)
    if not people:
        return (
            "Contacts: none returned for this account. That is not a clean bill of "
            "health — treat outreach conflict as UNKNOWN rather than clear."
        )

    conflicted = [p for p in people if p.conflicted]
    lines: list[str] = []

    if conflicted:
        lines.append(f"⚠ DO NOT SEND to {len(conflicted)} contact(s) — already in active outreach:")
        lines.extend(f"   {p.describe()}" for p in conflicted)
        lines.append("")

    clear = [p for p in people if not p.conflicted]
    titled = [p for p in clear if p.title]
    untitled = [p for p in clear if not p.title]

    lines.append(f"Contacts ({len(people)} total, {len(conflicted)} in active outreach):")
    # Titled contacts first: a title is what makes someone a decision-maker
    # rather than a name, and it is what the outreach is pitched at.
    for person in titled[:12]:
        lines.append(f"   {person.describe()}")
    if untitled:
        names = ", ".join(p.name for p in untitled[:6])
        lines.append(f"   (+{len(untitled)} without a title on file: {names})")
    return "\n".join(lines)


def _joined(*parts: Any) -> str:
    """Join the non-empty pieces of an answer with blank lines between them."""
    flat: list[str] = []
    for part in parts:
        if isinstance(part, list):
            flat.extend(str(p) for p in part if p)
        elif part:
            flat.append(str(part))
    return "\n\n".join(flat)


def _target_conflict(sf_match: Any, district_name: str) -> str:
    """Warn when Salesforce contradicts the new-business target list.

    Found live on 2026-08-28: Prince George's County Public Schools sits on the
    demand-gen target list as a D1 NEW BUSINESS account, while Salesforce carries
    it as a Pilot with an open opportunity. Either the exported list is stale or
    a pilot is not excluded from it — and a "new business" sequence into an
    account sales is already piloting is precisely the toe-stepping the whole
    suppression guard exists to prevent.

    This is a warning, never a block. The target list is Josh's to define; the
    job here is to make sure the contradiction is seen before anything sends.
    """
    if sf_match is None or not getattr(sf_match, "customer_status", None):
        return ""
    status = str(sf_match.customer_status)
    if status.strip().lower() in {"", "none", "prospect"}:
        return ""
    return (
        f"⚠ Conflict to resolve before drafting: Salesforce carries this account as "
        f"{status!r}. If {district_name!r} is also on the new-business target list, one of "
        "the two is wrong — check with the account owner rather than sending into it."
    )


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
            # ── Salesforce FIRST ──────────────────────────────────────────
            # The account question ("is this a customer, what is the history?")
            # is answered by Salesforce, not by us. Until 2026-08-28 this
            # resolved against our own index first and then required local
            # contacts, so a district we had never met returned nothing even
            # when Salesforce held a complete record. Houston ISD sat there as
            # Customer with 10 open opportunities the whole time.
            sf_lines: list[str] = []
            sf_match = None
            try:
                from artemis.marketing.salesforce_account_lookup import lookup_district
                from artemis.marketing.salesforce_suppression import _get_client

                sf_client = await _get_client(session)
                lookup = await lookup_district(sf_client, district_name)
                if lookup.error:
                    sf_lines.append(f"Salesforce: {lookup.error}")
                elif lookup.matched is not None:
                    sf_match = lookup.matched
                    sf_lines.append(f"Salesforce: {sf_match.describe()}")
                    # The people, and who is already working them. This is the
                    # half Josh asked for first -- the superintendent, the CAO,
                    # the curriculum leads -- and it lives in Salesforce, not in
                    # our own contact table (which holds 7 rows).
                    sf_lines.append(await _render_people(sf_client, sf_match.account_id))
                elif lookup.candidates:
                    names = "; ".join(c.describe() for c in lookup.candidates)
                    sf_lines.append(
                        f"Salesforce holds {len(lookup.candidates)} accounts matching "
                        f"{district_name!r} — {names}. Tell me which one before I treat any "
                        "of them as this district."
                    )
                else:
                    sf_lines.append(
                        f"Salesforce: no account matching {district_name!r}. That is a real "
                        "absence, not a lookup failure."
                    )
            except Exception as exc:
                logger.debug("salesforce account lookup failed", exc_info=True)
                sf_lines.append(
                    f"Salesforce: lookup failed ({type(exc).__name__}) — customer status is "
                    "UNKNOWN, not confirmed absent."
                )

            district: District | None = None
            if district_id_raw is not None:
                try:
                    district = await session.get(District, int(district_id_raw))
                except (TypeError, ValueError):
                    district = None
            else:
                district, candidates = await _resolve_district_by_name(session, district_name)
                if candidates:
                    # When Salesforce already answered, the local-index miss is a
                    # footnote, not a question. Ending a complete answer with a
                    # paragraph asking the reader to pick a district id reads as
                    # failure and buries what we just told them.
                    if sf_match is not None:
                        return _joined(
                            sf_lines,
                            "(Our own district index has no matching entry, so there is no "
                            "extra local detail to add. The Salesforce answer above stands "
                            "on its own.)",
                        )
                    names = ", ".join(f"{d.name} ({d.state}, id {d.id})" for d in candidates)
                    return _joined(
                        sf_lines,
                        f"Our district index has no exact match for {district_name!r}. The "
                        f"closest entries are: {names}. Our index stores official short forms, "
                        "which often differ from how the district is written elsewhere. Tell me "
                        "which one is right (the id is cleanest) and I will run the check -- I "
                        "am not going to pick one, because attributing another district's "
                        "sales activity to this one is the error that matters here.",
                    )

            if district is None:
                # Salesforce may well have answered even though our index did not.
                # Be precise about WHICH lookup failed. Callie told Josh on
                # 2026-08-28 that the Salesforce name might differ and asked him
                # for it -- but this lookup never touches Salesforce, so a perfect
                # Salesforce name would still have missed. Sending someone to
                # fetch a fact that cannot help is worse than saying "not found".
                return _joined(
                    sf_lines,
                    f"Our own index has no entry for {district_name!r}, so there is no "
                    "contact-level suppression detail to add — but the Salesforce answer "
                    "above stands on its own.",
                )

            contacts = await list_contacts_for_district(session, district.id)
            emailed_contacts = [c for c in contacts if c.email]
            if not emailed_contacts:
                return _joined(
                    sf_lines,
                    _target_conflict(sf_match, district_name),
                    f"{district.name}: no contacts with email addresses on file, so there is no "
                    "per-contact check to run. That is a gap in our contact data — NOT a clean "
                    "bill of health, and not a Salesforce failure.",
                )

            lines: list[str] = []
            lines.extend(sf_lines)
            conflict = _target_conflict(sf_match, district_name)
            if conflict:
                lines.append(conflict)
            lines.append(f"{district.name}: {len(emailed_contacts)} contact(s) checked.")
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
                "Read-only district brief from Salesforce: customer status, open "
                "opportunities, the decision-makers (superintendent, chief academic "
                "officer, curriculum leads) with their titles, and WHICH CONTACTS A "
                "SELLER IS ALREADY WORKING. Use this before drafting any outreach. "
                "Contacts flagged as in active outreach must not be written to -- say so "
                "and stop. Fails closed: reports 'could not verify' rather than a false "
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
