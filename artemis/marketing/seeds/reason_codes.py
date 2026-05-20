"""Seed loader for Josh's canonical reason-code registry (spec v1).

One-shot, idempotent: uses INSERT … ON CONFLICT (code) DO NOTHING so re-running
is a no-op. Returns counts for diagnostics.

Usage (one-liner):
    uv run python -c "
    import asyncio
    from artemis.marketing.seeds.reason_codes import run_seed
    asyncio.run(run_seed())
    "
"""

from __future__ import annotations

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

# Verbatim from decisions/campaign-signal-spec-v1.md §2
JOSH_SPEC_V1: list[dict[str, str]] = [
    {
        "code": "POLICY_LIT_MANDATE",
        "domain": "POLICY",
        "description": "New state law passes requiring screening or literacy intervention",
        "what_scout_looks_for": "Bills with screening/intervention/dyslexia/structured-literacy keywords reaching INTRODUCED, PASSED_CHAMBER, or ENACTED in a priority state",
        "default_urgency": "hot at PASSED_CHAMBER or ENACTED; standard at INTRODUCED",
    },
    {
        "code": "POLICY_EDTECH_TIME_LIMIT",
        "domain": "POLICY",
        "description": "Legislation reducing time on ed tech, or public dissatisfaction with screen-time on ed tech",
        "what_scout_looks_for": "Bills, news, or board commentary citing screen-time caps or ed-tech-time reduction; Amira positioned as low-time / high-impact",
        "default_urgency": "standard; hot if bill is statewide and includes K–3",
    },
    {
        "code": "FUNDING_LITERACY_GRANT",
        "domain": "FUNDING",
        "description": "State publishes a literacy grant or funding announcement for high-impact tutoring",
        "what_scout_looks_for": "Grants.gov, Federal Register, or state DoE press releases announcing literacy / tutoring / HIT funding",
        "default_urgency": "hot if deadline ≤ 30 days; standard if 30–90; enrichment otherwise",
    },
    {
        "code": "FUNDING_DEADLINE_NEAR",
        "domain": "FUNDING",
        "description": "State notification or selection deadline within 90 days",
        "what_scout_looks_for": "Any active funding signal where days_until ≤ 90",
        "default_urgency": "hot ≤ 30 days, standard 30–90",
    },
    {
        "code": "FUNDING_HB2_ELIA",
        "domain": "FUNDING",
        "description": "District publicly discusses HB 2 Early Literacy Intervention Allotment ($250/student, K–3) spend",
        "what_scout_looks_for": "TX board minutes / budget docs referencing HB 2, ELIA, or Early Literacy Intervention Allotment",
        "default_urgency": "enrichment (context only — not a discrete event)",
    },
    {
        "code": "VENDOR_APPROVED_LIST",
        "domain": "VENDOR",
        "description": "State adds Amira to an approved-vendor list",
        "what_scout_looks_for": "State DoE procurement / approved-vendor list pages mentioning Amira (or category Amira qualifies for)",
        "default_urgency": "hot",
    },
    {
        "code": "VENDOR_DISSATISFACTION",
        "domain": "VENDOR",
        "description": "Public dissatisfaction with iReady, Lexia, UCSF Multitudes, or Amplify",
        "what_scout_looks_for": "News, board minutes, or LinkedIn posts naming the competitor with negative valence (efficacy, cost, fit, renewal)",
        "default_urgency": "standard; hot if board votes non-renewal or RFP follows",
    },
    {
        "code": "DISTRICT_STRATEGIC_LITERACY",
        "domain": "DISTRICT",
        "description": "District strategic plan names literacy as a top priority",
        "what_scout_looks_for": "Strategic plan PDFs, board adoption of plan with literacy as named pillar",
        "default_urgency": "standard",
    },
    {
        "code": "DISTRICT_PROFICIENCY_GAP",
        "domain": "DISTRICT",
        "description": "District publicly cites a literacy achievement gap or proficiency drop",
        "what_scout_looks_for": "Board minutes, press releases, or local news citing reading-proficiency decline, NAEP drop, or named gap",
        "default_urgency": "standard; hot if paired with vendor dissatisfaction or RFP",
    },
    {
        "code": "DISTRICT_DLL_EXPANSION",
        "domain": "DISTRICT",
        "description": "District announces bilingual or dual-language program expansion",
        "what_scout_looks_for": "Board votes, press releases, or strategic plan items naming DLL / dual-language / bilingual program expansion",
        "default_urgency": "standard",
    },
    {
        "code": "DISTRICT_MTSS_STRAIN",
        "domain": "DISTRICT",
        "description": "District announces MTSS or intervention staffing challenges",
        "what_scout_looks_for": "Board minutes or news citing intervention staffing shortages, MTSS gaps, Tier 2/3 capacity issues",
        "default_urgency": "standard",
    },
    {
        "code": "PROCUREMENT_ELA_ADOPTION",
        "domain": "PROCUREMENT",
        "description": "New core ELA adoption cycle opening",
        "what_scout_looks_for": "Adoption committee formation, public comment windows, ELA materials review on board agenda",
        "default_urgency": "standard; hot when RFP posts",
    },
    {
        "code": "PROCUREMENT_LITERACY_RFP",
        "domain": "PROCUREMENT",
        "description": "Active literacy/assessment/curriculum RFP",
        "what_scout_looks_for": "RFPs/RFIs on statewide portals or district sites; literacy / reading / assessment / tutoring scope",
        "default_urgency": "hot if days_to_close ≤ 14; standard 15–45; reject > 45 unless strategic",
    },
    {
        "code": "TX_HB1416_WAIVER",
        "domain": "TX",
        "description": "District pursues or is awarded an HB 1416 tutoring waiver",
        "what_scout_looks_for": "TEA waiver filings, board discussion of HB 1416 waiver, district press; Amira is TEA-approved for HB 1416",
        "default_urgency": "hot",
    },
    {
        "code": "TX_HB3_DYSLEXIA_COMPLIANCE",
        "domain": "TX",
        "description": "District flags HB 3 dyslexia reporting compliance challenges",
        "what_scout_looks_for": "Board minutes / TEA correspondence citing HB 3 dyslexia reporting friction; Amira is TEA-approved",
        "default_urgency": "hot",
    },
    {
        "code": "LEADER_TRANSITION_FORMAL",
        "domain": "LEADER",
        "description": "New superintendent, CAO, or curriculum director formally hired",
        "what_scout_looks_for": "Two-source confirmed formal hire — board vote OR district press release",
        "default_urgency": "hot for 90 days post-hire",
    },
    {
        "code": "LEADER_TRANSITION_INTERIM",
        "domain": "LEADER",
        "description": "Interim supe / CAO / curriculum lead named",
        "what_scout_looks_for": "Single-source interim announcement",
        "default_urgency": "standard",
    },
]


async def seed_reason_codes(session: AsyncSession) -> dict[str, int]:
    """Idempotent insert of all 17 Josh spec v1 reason codes.

    Uses INSERT … ON CONFLICT (code) DO NOTHING — safe to re-run.
    Returns {"inserted": N, "skipped": K}.
    """
    inserted = 0
    skipped = 0
    for row in JOSH_SPEC_V1:
        cursor: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            text(
                "INSERT INTO signal_reason_codes "
                "(code, domain, description, what_scout_looks_for, default_urgency, is_active) "
                "VALUES (:code, :domain, :description, :what_scout_looks_for, :default_urgency, true) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": row["code"],
                "domain": row["domain"],
                "description": row["description"],
                "what_scout_looks_for": row["what_scout_looks_for"],
                "default_urgency": row["default_urgency"],
            },
        )
        if cursor.rowcount == 1:
            inserted += 1
        else:
            skipped += 1
    await session.commit()
    return {"inserted": inserted, "skipped": skipped}


async def run_seed() -> None:
    """Entry point for CLI one-liner."""
    from artemis.db import SessionLocal

    async with SessionLocal() as session:
        counts = await seed_reason_codes(session)
        print(f"seed_reason_codes: inserted={counts['inserted']} skipped={counts['skipped']}")
