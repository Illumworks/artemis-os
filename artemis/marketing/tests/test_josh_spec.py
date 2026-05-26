from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.seeds.reason_codes import run_seed

SPEC = parse_spec()


def test_parse_spec_reason_code_count() -> None:
    assert len(SPEC.reason_codes) == 17


def test_reason_codes_have_required_fields() -> None:
    for row in SPEC.reason_codes:
        assert all(
            (row.code, row.domain, row.description, row.what_scout_looks_for, row.default_urgency)
        )
        assert row.primary_scouts


def test_reason_codes_for_regional_news() -> None:
    codes = {row.code for row in reason_codes_for_scout(SPEC, "regional_news")}
    expected = set(  # noqa: SIM905
        "POLICY_EDTECH_TIME_LIMIT VENDOR_DISSATISFACTION DISTRICT_STRATEGIC_LITERACY "  # noqa: SIM905
        "DISTRICT_PROFICIENCY_GAP DISTRICT_DLL_EXPANSION DISTRICT_MTSS_STRAIN "
        "TX_HB1416_WAIVER LEADER_TRANSITION_FORMAL LEADER_TRANSITION_INTERIM".split()
    )
    assert expected <= codes
    assert len(codes) >= 5


def test_territory_config_priority_states() -> None:
    assert SPEC.territory_config.priority_states == ("FL", "IN", "MD", "MO", "IL", "TX")


def test_qualifier_rules_by_layer() -> None:
    assert all(
        sum(1 for row in SPEC.qualifier_rules if row.layer == layer) >= 3
        for layer in ("skip", "suppress", "boost")
    )


def test_state_nuances_include_required_entries() -> None:
    states = {row.state for row in SPEC.state_nuances}
    assert {"Florida", "Texas", "All states — vendor dissatisfaction"} <= states


def test_raw_source_hash_is_sha256_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", SPEC.raw_source_hash)


@pytest.mark.asyncio
async def test_run_seed_idempotent(db_session: AsyncSession) -> None:
    first = await run_seed()
    second = await run_seed()
    result = await db_session.execute(text("SELECT COUNT(*) FROM signal_reason_codes"))

    assert first == {"inserted": 17, "skipped": 0}
    assert second == {"inserted": 0, "skipped": 17}
    assert result.scalar_one() == 17
