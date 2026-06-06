"""Tests for scripts/seed_josh_rulesets.py.

Covers:
  (a) build_ruleset_map: correct families, codes, weights, tags, no hard filters
  (b) build_territory_data: correct states per family
  (c) DB seed: inserts 5 rulesets + 5 territory_config rows (one per family)
  (d) Idempotency: re-running produces same state, no duplicates
  (e) Archiving: active non-josh_spec_v1 rulesets get state='archived'
  (f) Qualification: hot code in priority state → fitScore ≈ 0.9, qualified
  (g) Qualification: enrichment weight (0.30) does NOT pass min_fit (0.5)
  (h) Qualification: standard weight in unlisted state (0.60 × 0.85 = 0.51) passes
  (i) weight derivation: faithful to Josh's tier mapping
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.josh_spec import CANONICAL_CAMPAIGN_FAMILIES, parse_spec
from artemis.marketing.models import Ruleset, SignalQueue, TerritoryConfig
from artemis.marketing.qualifier import (
    RulesetInput,
    SignalInput,
    TerritoryEntry,
    qualify_signal,
)
from artemis.marketing.seeds.reason_codes import seed_reason_codes

# Import the functions under test
from scripts.seed_josh_rulesets import (
    VERSION_TAG,
    _derive_weight,
    _upsert_rulesets,
    _upsert_territory_configs,
    build_ruleset_map,
    build_territory_data,
)

# ─────────────────────────────────────────────────────────────────────────────
# (a) build_ruleset_map — structure and content
# ─────────────────────────────────────────────────────────────────────────────


def test_build_ruleset_map_returns_all_canonical_families() -> None:
    rulesets = build_ruleset_map()
    assert set(rulesets.keys()) == set(CANONICAL_CAMPAIGN_FAMILIES)


def test_build_ruleset_map_version_and_state() -> None:
    rulesets = build_ruleset_map()
    for data in rulesets.values():
        assert data["version_tag"] == "josh_spec_v1"
        assert data["state"] == "active"
        assert data["hard_filters"] == []
        assert data["qualitative_rubrics"] == []


def test_build_ruleset_map_weighted_signals_tagged_source() -> None:
    rulesets = build_ruleset_map()
    for data in rulesets.values():
        ws = data["weighted_signals"]
        assert isinstance(ws, list)
        assert len(ws) > 0
        for entry in ws:
            assert entry["source"] == "josh_spec_v1"
            assert "reason_code" in entry
            assert "weight" in entry
            assert entry["weight"] in {0.90, 0.60, 0.30}


def test_build_ruleset_map_obc_has_5_codes() -> None:
    rulesets = build_ruleset_map()
    codes = [ws["reason_code"] for ws in rulesets["obc"]["weighted_signals"]]
    assert len(codes) == 5
    assert "VENDOR_APPROVED_LIST" in codes
    assert "PROCUREMENT_LITERACY_RFP" in codes
    assert "POLICY_LIT_MANDATE" in codes


def test_build_ruleset_map_hit_has_3_codes_with_correct_weights() -> None:
    rulesets = build_ruleset_map()
    ws_by_code = {ws["reason_code"]: ws["weight"] for ws in rulesets["hit"]["weighted_signals"]}
    assert ws_by_code["TX_HB1416_WAIVER"] == 0.90  # "hot"
    assert ws_by_code["FUNDING_LITERACY_GRANT"] == 0.90  # "hot if deadline..."
    assert ws_by_code["DISTRICT_MTSS_STRAIN"] == 0.60  # "standard"


def test_build_ruleset_map_biliteracy_single_code() -> None:
    rulesets = build_ruleset_map()
    codes = [ws["reason_code"] for ws in rulesets["biliteracy"]["weighted_signals"]]
    assert codes == ["DISTRICT_DLL_EXPANSION"]


def test_build_ruleset_map_faithful_to_spec_only() -> None:
    """No codes outside Josh's §3 mappings."""
    spec = parse_spec()
    all_spec_codes = {rc.code for rc in spec.reason_codes}
    rulesets = build_ruleset_map()
    for data in rulesets.values():
        for ws in data["weighted_signals"]:
            assert ws["reason_code"] in all_spec_codes, (
                f"reason_code {ws['reason_code']!r} not in Josh's spec"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (b) build_territory_data — structure and content
# ─────────────────────────────────────────────────────────────────────────────


def test_build_territory_data_returns_all_canonical_families() -> None:
    territory = build_territory_data()
    assert set(territory.keys()) == set(CANONICAL_CAMPAIGN_FAMILIES)


def test_build_territory_data_priority_states() -> None:
    territory = build_territory_data()
    expected_states = sorted(["FL", "IN", "MD", "MO", "IL", "TX"])
    for data in territory.values():
        assert sorted(data["standard_states"]) == expected_states
        assert data["hot_states"] == []
        assert data["unlisted_multiplier"] == 0.85


# ─────────────────────────────────────────────────────────────────────────────
# (i) _derive_weight — faithful to Josh's tier mapping
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "urgency_string, expected_weight",
    [
        ("hot", 0.90),
        ("hot at PASSED_CHAMBER or ENACTED; standard at INTRODUCED", 0.90),
        ("hot if deadline ≤ 30 days; standard if 30–90; enrichment otherwise", 0.90),
        ("hot for 90 days post-hire", 0.90),
        ("standard", 0.60),
        ("standard; hot if board votes non-renewal or RFP follows", 0.90),  # contains "hot"
        ("standard; hot when RFP posts", 0.90),  # contains "hot"
        ("enrichment (context only — not a discrete event)", 0.30),
        ("enrichment", 0.30),
    ],
)
def test_derive_weight_tiers(urgency_string: str, expected_weight: float) -> None:
    assert _derive_weight(urgency_string) == expected_weight


# ─────────────────────────────────────────────────────────────────────────────
# (c) DB seed: inserts correct number of rows
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seed_inserts_rulesets_and_territory(db_session: AsyncSession) -> None:
    rulesets = build_ruleset_map()
    territory = build_territory_data()

    await _upsert_rulesets(db_session, rulesets)
    await _upsert_territory_configs(db_session, territory)
    await db_session.commit()

    rs_result = await db_session.execute(
        select(Ruleset).where(
            Ruleset.version_tag == VERSION_TAG,
            Ruleset.state == "active",
        )
    )
    rows = list(rs_result.scalars().all())
    assert len(rows) == 5

    tc_result = await db_session.execute(select(TerritoryConfig))
    tc_rows = list(tc_result.scalars().all())
    assert len(tc_rows) == 5

    families = {r.family for r in rows}
    assert families == set(CANONICAL_CAMPAIGN_FAMILIES)


# ─────────────────────────────────────────────────────────────────────────────
# (d) Idempotency: second upsert produces no duplicates
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seed_idempotent_no_duplicates(db_session: AsyncSession) -> None:
    rulesets = build_ruleset_map()
    territory = build_territory_data()

    # First pass
    await _upsert_rulesets(db_session, rulesets)
    await _upsert_territory_configs(db_session, territory)
    await db_session.commit()

    # Second pass
    await _upsert_rulesets(db_session, rulesets)
    await _upsert_territory_configs(db_session, territory)
    await db_session.commit()

    rs_result = await db_session.execute(
        select(Ruleset).where(
            Ruleset.version_tag == VERSION_TAG,
            Ruleset.state == "active",
        )
    )
    rows = list(rs_result.scalars().all())
    assert len(rows) == 5  # exactly one per family, no duplicates

    tc_result = await db_session.execute(select(TerritoryConfig))
    tc_rows = list(tc_result.scalars().all())
    assert len(tc_rows) == 5  # exactly one per family


# ─────────────────────────────────────────────────────────────────────────────
# (e) Archiving: stale non-josh_spec_v1 rulesets get archived
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_rulesets_get_archived(db_session: AsyncSession) -> None:
    from scripts.seed_josh_rulesets import _archive_stale_rulesets

    # Insert a stale smoke-1 ruleset
    stale = Ruleset(
        family="obc",
        version_tag="smoke-1",
        state="active",
        hard_filters=[],
        weighted_signals=[],
        qualitative_rubrics=[],
    )
    db_session.add(stale)
    await db_session.flush()
    await db_session.refresh(stale)

    archived = await _archive_stale_rulesets(db_session, {"obc"})
    await db_session.commit()
    await db_session.refresh(stale)

    assert archived == 1
    assert stale.state == "archived"


@pytest.mark.asyncio
async def test_josh_spec_v1_not_archived(db_session: AsyncSession) -> None:
    """josh_spec_v1 rulesets are never touched by archive logic."""
    from scripts.seed_josh_rulesets import _archive_stale_rulesets

    rulesets = build_ruleset_map()
    await _upsert_rulesets(db_session, rulesets)
    await db_session.commit()

    # Archive pass should find nothing to archive
    archived = await _archive_stale_rulesets(db_session, set(rulesets.keys()))
    await db_session.commit()

    assert archived == 0

    # All 5 josh_spec_v1 rulesets should still be active
    result = await db_session.execute(
        select(Ruleset).where(
            Ruleset.version_tag == VERSION_TAG,
            Ruleset.state == "active",
        )
    )
    assert len(list(result.scalars().all())) == 5


# ─────────────────────────────────────────────────────────────────────────────
# (f) Qualification: hot code in priority state → fitScore ≈ 0.9, qualified
# ─────────────────────────────────────────────────────────────────────────────


def test_hot_code_in_priority_state_qualifies() -> None:
    """VENDOR_APPROVED_LIST (weight=0.90) in TX (standard state) → score=0.9, passes."""
    rulesets = build_ruleset_map()
    territory = build_territory_data()

    from artemis.marketing.qualifier import TerritoryEntry

    territory_entries = {
        family: [
            TerritoryEntry(state_code=state, priority_tier="standard")
            for state in territory[family]["standard_states"]
        ]
        for family in rulesets
    }

    signal = SignalInput(
        state_code="TX",
        reason_codes=[{"code": "VENDOR_APPROVED_LIST", "confidence": 1.0}],
        campaign_family="obc",
    )
    ruleset_inputs = [
        RulesetInput(
            campaign_family=family,
            version_number=VERSION_TAG,
            min_fit_score=0.5,
            hard_filters=[],
            weighted_signals=cast(list[dict[str, Any]], data["weighted_signals"]),
        )
        for family, data in rulesets.items()
    ]

    result = qualify_signal(signal, ruleset_inputs, territory_entries)

    # Find the obc score
    obc_score = next(s for s in result.scores if s.campaign_family == "obc")
    assert abs(obc_score.raw_score - 0.90) < 0.001
    assert abs(obc_score.adjusted_score - 0.90) < 0.001
    assert obc_score.passes_min_fit_score is True


def test_tx_hb1416_waiver_qualifies_hit() -> None:
    """TX_HB1416_WAIVER (weight=0.90) in TX → score=0.90, qualified for HIT."""
    rulesets = build_ruleset_map()
    territory = build_territory_data()

    territory_entries = {
        family: [
            TerritoryEntry(state_code=state, priority_tier="standard")
            for state in territory[family]["standard_states"]
        ]
        for family in rulesets
    }

    signal = SignalInput(
        state_code="TX",
        reason_codes=[{"code": "TX_HB1416_WAIVER", "confidence": 1.0}],
        campaign_family="hit",
    )
    ruleset_inputs = [
        RulesetInput(
            campaign_family=family,
            version_number=VERSION_TAG,
            min_fit_score=0.5,
            hard_filters=[],
            weighted_signals=cast(list[dict[str, Any]], data["weighted_signals"]),
        )
        for family, data in rulesets.items()
    ]

    result = qualify_signal(signal, ruleset_inputs, territory_entries)
    hit_score = next(s for s in result.scores if s.campaign_family == "hit")
    assert abs(hit_score.raw_score - 0.90) < 0.001
    assert hit_score.passes_min_fit_score is True


# ─────────────────────────────────────────────────────────────────────────────
# (g) Enrichment-only signal (~0.30) does NOT pass min_fit (0.50)
# ─────────────────────────────────────────────────────────────────────────────


def test_enrichment_weight_does_not_pass_min_fit() -> None:
    """A signal whose only matching code has weight 0.30 must not pass min_fit=0.50."""
    signal = SignalInput(
        state_code="TX",
        reason_codes=[{"code": "ENRICHMENT_ONLY", "confidence": 1.0}],
    )
    ruleset = RulesetInput(
        campaign_family="obc",
        version_number=VERSION_TAG,
        min_fit_score=0.5,
        hard_filters=[],
        weighted_signals=[
            {"reason_code": "ENRICHMENT_ONLY", "weight": 0.30, "source": "josh_spec_v1"}
        ],
    )
    territories: dict[str, list[TerritoryEntry]] = {
        "obc": [TerritoryEntry(state_code="TX", priority_tier="standard")]
    }

    result = qualify_signal(signal, [ruleset], territories)
    score = result.scores[0]

    assert abs(score.adjusted_score - 0.30) < 0.001
    assert score.passes_min_fit_score is False
    assert len(result.recommended_families) == 0


# ─────────────────────────────────────────────────────────────────────────────
# (h) Standard weight in unlisted state: 0.60 × 0.85 = 0.51 — passes min_fit
# ─────────────────────────────────────────────────────────────────────────────


def test_standard_weight_unlisted_state_passes_min_fit() -> None:
    """DISTRICT_DLL_EXPANSION (weight=0.60) in CA (unlisted) → 0.60 × 0.85 = 0.51 — passes."""
    rulesets = build_ruleset_map()
    territory = build_territory_data()

    territory_entries = {
        family: [
            TerritoryEntry(state_code=state, priority_tier="standard")
            for state in territory[family]["standard_states"]
        ]
        for family in rulesets
    }

    signal = SignalInput(
        state_code="CA",  # NOT a priority state
        reason_codes=[{"code": "DISTRICT_DLL_EXPANSION", "confidence": 1.0}],
        campaign_family="biliteracy",
    )
    ruleset_inputs = [
        RulesetInput(
            campaign_family=family,
            version_number=VERSION_TAG,
            min_fit_score=0.5,
            hard_filters=[],
            weighted_signals=cast(list[dict[str, Any]], data["weighted_signals"]),
        )
        for family, data in rulesets.items()
    ]

    result = qualify_signal(signal, ruleset_inputs, territory_entries)
    biliteracy_score = next(s for s in result.scores if s.campaign_family == "biliteracy")

    assert biliteracy_score.territory_tier == "unlisted"
    assert abs(biliteracy_score.territory_multiplier - 0.85) < 0.001
    assert abs(biliteracy_score.raw_score - 0.60) < 0.001
    expected_adjusted = round(0.60 * 0.85, 10)
    assert abs(biliteracy_score.adjusted_score - expected_adjusted) < 0.001
    assert biliteracy_score.passes_min_fit_score is True  # 0.51 >= 0.50


# ─────────────────────────────────────────────────────────────────────────────
# Integration: DB round-trip — seed → qualify → assert non-zero fitScore
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_hot_signal_qualifies_after_seed(db_session: AsyncSession) -> None:
    """End-to-end: seed rulesets + reason codes, insert hot signal, run qualification."""
    await seed_reason_codes(db_session)

    rulesets = build_ruleset_map()
    territory = build_territory_data()
    await _upsert_rulesets(db_session, rulesets)
    await _upsert_territory_configs(db_session, territory)
    await db_session.commit()

    # Insert a hot signal with VENDOR_APPROVED_LIST in priority state TX
    signal = SignalQueue(
        headline="TX DoE adds Amira to approved vendor list",
        campaign_family="obc",
        urgency_tier="hot",
        state="TX",
        reason_codes=[{"code": "VENDOR_APPROVED_LIST", "confidence": 1.0}],
        signal_status="pending_qualification",
    )
    db_session.add(signal)
    await db_session.flush()
    await db_session.refresh(signal)

    from artemis.marketing.qualification import run_and_store_qualification

    qual = await run_and_store_qualification(db_session, signal)
    await db_session.commit()
    await db_session.refresh(signal)

    assert qual is not None
    assert signal.signal_status == "qualified"

    obc_score = next(s for s in qual["scores"] if s["campaignFamily"] == "obc")
    assert abs(obc_score["rawScore"] - 0.90) < 0.001
    assert obc_score["passesMinFitScore"] is True
