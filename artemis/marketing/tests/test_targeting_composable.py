"""Tests for the composable targeting builder (CMP-TARGETING).

Coverage:
A. TargetScope schema: composite shape validates correctly; legacy still validates;
   invalid inputs rejected with 422.
B. Backward compat: legacy {mode:"states", states:["TX"]} resolves to the SAME
   district id list as composite {base:"states", states:["TX"]}.
C. Composite resolution semantics:
   - base=all
   - base=states
   - base=states ∩ tiers (intersection)
   - include_district_ids adds unsupported/out-of-base districts (union)
   - 0-match case
D. _count_districts_for_scope matches resolution cardinality for each shape.
E. POST /api/marketing/initiation/target-scope/preview:
   - returns correct count for composite + legacy shapes
   - 422 on invalid scope
F. GET /api/marketing/districts/search:
   - returns matches by name substring
   - optional state filter
   - capped at limit
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.initiation_schemas import TargetScope
from artemis.marketing.models import CampaignCandidate, District
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.routes.initiation import _count_districts_for_scope
from artemis.marketing.sends import resolve_district_ids_for_candidate

pytestmark = pytest.mark.asyncio


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _make_district(
    session: AsyncSession,
    *,
    name: str = "Test District",
    state: str = "TX",
    tier: str | None = "D1",
    supported: bool = True,
) -> District:
    d = District(name=name, state=state, tier=tier, supported=supported)
    session.add(d)
    await session.flush()
    return d


async def _make_candidate(
    session: AsyncSession,
    *,
    target_scope_json: dict[str, Any] | None = None,
    state: str = "TX",
) -> CampaignCandidate:
    signal = await create_signal(
        session,
        headline="Test signal",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Test",
        discovered_by="test",
        state=state,
        reason_codes=[],
    )
    candidate = await create_campaign_candidate_from_signal(
        session, signal_id=signal.id, ruleset_version_tag="v1"
    )
    if target_scope_json is not None:
        candidate.target_scope_json = target_scope_json
        await session.flush()
    return candidate


# ── A. TargetScope schema ──────────────────────────────────────────────────────


class TestTargetScopeSchema:
    def test_legacy_all_districts(self) -> None:
        scope = TargetScope.model_validate({"mode": "all_districts"})
        assert scope.mode == "all_districts"
        assert scope.base is None

    def test_legacy_states(self) -> None:
        scope = TargetScope.model_validate({"mode": "states", "states": ["MI", "TX"]})
        assert scope.mode == "states"
        assert scope.states == ["MI", "TX"]

    def test_legacy_district_tier(self) -> None:
        scope = TargetScope.model_validate({"mode": "district_tier", "tiers": ["D1", "D2"]})
        assert scope.tiers == ["D1", "D2"]

    def test_legacy_named_districts(self) -> None:
        scope = TargetScope.model_validate({"mode": "named_districts", "district_ids": [1, 2, 3]})
        assert scope.district_ids == [1, 2, 3]

    def test_composite_base_all(self) -> None:
        scope = TargetScope.model_validate({"base": "all"})
        assert scope.base == "all"
        assert scope.mode is None

    def test_composite_base_states(self) -> None:
        scope = TargetScope.model_validate({"base": "states", "states": ["MI"]})
        assert scope.base == "states"
        assert scope.states == ["MI"]

    def test_composite_with_tiers(self) -> None:
        scope = TargetScope.model_validate(
            {"base": "states", "states": ["MI"], "tiers": ["D1", "D2"]}
        )
        assert scope.tiers == ["D1", "D2"]

    def test_composite_with_include_ids(self) -> None:
        scope = TargetScope.model_validate({"base": "all", "include_district_ids": [10, 20]})
        assert scope.include_district_ids == [10, 20]

    def test_composite_states_coerced_upper(self) -> None:
        scope = TargetScope.model_validate({"base": "states", "states": ["mi", "tx"]})
        assert scope.states == ["MI", "TX"]

    def test_composite_tiers_coerced_upper(self) -> None:
        scope = TargetScope.model_validate({"base": "all", "tiers": ["d1", "d2"]})
        assert scope.tiers == ["D1", "D2"]

    def test_composite_invalid_base(self) -> None:
        with pytest.raises(Exception, match="base must be one of"):
            TargetScope.model_validate({"base": "region"})

    def test_composite_states_required_when_base_states(self) -> None:
        with pytest.raises(Exception, match="non-empty states"):
            TargetScope.model_validate({"base": "states"})

    def test_composite_invalid_state_code(self) -> None:
        with pytest.raises(Exception, match="Unknown state"):
            TargetScope.model_validate({"base": "states", "states": ["ZZ"]})

    def test_composite_invalid_tier(self) -> None:
        with pytest.raises(Exception, match="Invalid tier"):
            TargetScope.model_validate({"base": "all", "tiers": ["D9"]})

    def test_missing_mode_and_base_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TargetScope.model_validate({"states": ["MI"]})

    def test_extra_field_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TargetScope.model_validate({"base": "all", "unknown_field": True})


# ── B. Backward compatibility ─────────────────────────────────────────────────


class TestBackwardCompat:
    async def test_legacy_and_composite_states_same_ids(self, db_session: AsyncSession) -> None:
        """Critical: legacy {mode:states,states:[TX]} == composite {base:states,states:[TX]}."""
        d1 = await _make_district(
            db_session, name="Austin ISD", state="TX", tier="D1", supported=True
        )
        d2 = await _make_district(
            db_session, name="Houston ISD", state="TX", tier="D2", supported=True
        )
        _d3 = await _make_district(
            db_session, name="Miami Dade", state="FL", tier="D1", supported=True
        )

        legacy_candidate = await _make_candidate(
            db_session, target_scope_json={"mode": "states", "states": ["TX"]}
        )
        composite_candidate = await _make_candidate(
            db_session, target_scope_json={"base": "states", "states": ["TX"]}
        )

        legacy_ids = await resolve_district_ids_for_candidate(db_session, legacy_candidate)
        composite_ids = await resolve_district_ids_for_candidate(db_session, composite_candidate)

        assert legacy_ids == composite_ids, (
            f"Backward compat broken: legacy={legacy_ids} vs composite={composite_ids}"
        )
        assert set(legacy_ids) == {d1.id, d2.id}

    async def test_legacy_all_districts_same_as_composite_all(
        self, db_session: AsyncSession
    ) -> None:
        d1 = await _make_district(db_session, name="Dist A", state="TX", supported=True)
        d2 = await _make_district(db_session, name="Dist B", state="FL", supported=True)
        _d3 = await _make_district(db_session, name="Dist C", state="MI", supported=False)

        legacy_candidate = await _make_candidate(
            db_session, target_scope_json={"mode": "all_districts"}
        )
        composite_candidate = await _make_candidate(db_session, target_scope_json={"base": "all"})

        legacy_ids = await resolve_district_ids_for_candidate(db_session, legacy_candidate)
        composite_ids = await resolve_district_ids_for_candidate(db_session, composite_candidate)

        assert legacy_ids == composite_ids
        assert set(legacy_ids) == {d1.id, d2.id}  # unsupported excluded


# ── C. Composite resolution semantics ────────────────────────────────────────


class TestCompositeResolution:
    async def test_base_all_returns_all_supported(self, db_session: AsyncSession) -> None:
        d1 = await _make_district(db_session, name="Dist TX", state="TX", supported=True)
        d2 = await _make_district(db_session, name="Dist FL", state="FL", supported=True)
        _unsupported = await _make_district(
            db_session, name="Unsupported", state="CA", supported=False
        )
        candidate = await _make_candidate(db_session, target_scope_json={"base": "all"})
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert set(ids) == {d1.id, d2.id}

    async def test_base_states_filters_by_state(self, db_session: AsyncSession) -> None:
        d_mi = await _make_district(db_session, name="Kalamazoo", state="MI", supported=True)
        _d_tx = await _make_district(db_session, name="Dallas", state="TX", supported=True)
        candidate = await _make_candidate(
            db_session, target_scope_json={"base": "states", "states": ["MI"]}
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert ids == [d_mi.id]

    async def test_base_states_intersect_tiers(self, db_session: AsyncSession) -> None:
        """base=states ∩ tiers: only TX AND D2 districts."""
        d1 = await _make_district(db_session, name="TX D2", state="TX", tier="D2", supported=True)
        _d2 = await _make_district(db_session, name="TX D1", state="TX", tier="D1", supported=True)
        _d3 = await _make_district(db_session, name="FL D2", state="FL", tier="D2", supported=True)
        candidate = await _make_candidate(
            db_session,
            target_scope_json={"base": "states", "states": ["TX"], "tiers": ["D2"]},
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert ids == [d1.id]

    async def test_include_district_ids_union(self, db_session: AsyncSession) -> None:
        """include_district_ids adds districts even if outside base/unsupported."""
        d_in_base = await _make_district(db_session, name="InBase MI", state="MI", supported=True)
        d_out_of_base = await _make_district(
            db_session, name="OutOfBase TX", state="TX", supported=True
        )
        d_unsupported = await _make_district(
            db_session, name="Unsupported CA", state="CA", supported=False
        )
        candidate = await _make_candidate(
            db_session,
            target_scope_json={
                "base": "states",
                "states": ["MI"],
                "include_district_ids": [d_out_of_base.id, d_unsupported.id],
            },
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert set(ids) == {d_in_base.id, d_out_of_base.id, d_unsupported.id}

    async def test_zero_match(self, db_session: AsyncSession) -> None:
        """base=states with no matching districts → empty list."""
        # Add districts for other states, not WY
        await _make_district(db_session, name="Texas ISD", state="TX", supported=True)
        candidate = await _make_candidate(
            db_session, target_scope_json={"base": "states", "states": ["WY"]}
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert ids == []

    async def test_include_ids_deduped(self, db_session: AsyncSession) -> None:
        """District in both base and include_ids is not double-counted."""
        d = await _make_district(db_session, name="Test", state="TX", supported=True)
        candidate = await _make_candidate(
            db_session,
            target_scope_json={
                "base": "states",
                "states": ["TX"],
                "include_district_ids": [d.id],
            },
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert ids == [d.id]  # appears once

    async def test_results_sorted(self, db_session: AsyncSession) -> None:
        """Results should be sorted by id."""
        d1 = await _make_district(db_session, name="B", state="TX", supported=True)
        d2 = await _make_district(db_session, name="A", state="TX", supported=True)
        candidate = await _make_candidate(
            db_session, target_scope_json={"base": "states", "states": ["TX"]}
        )
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        assert ids == sorted(ids)
        assert set(ids) == {d1.id, d2.id}


# ── D. _count_districts_for_scope matches resolution ─────────────────────────


class TestCountMatchesResolution:
    async def test_composite_count_matches_resolution(self, db_session: AsyncSession) -> None:
        await _make_district(db_session, name="MI D1", state="MI", tier="D1", supported=True)
        await _make_district(db_session, name="MI D2", state="MI", tier="D2", supported=True)
        await _make_district(db_session, name="TX D1", state="TX", tier="D1", supported=True)

        scope = {"base": "states", "states": ["MI"], "tiers": ["D1"]}
        candidate = await _make_candidate(db_session, target_scope_json=scope)
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        count = await _count_districts_for_scope(db_session, scope)
        assert count == len(ids)

    async def test_composite_with_include_count_matches_resolution(
        self, db_session: AsyncSession
    ) -> None:
        d_mi = await _make_district(db_session, name="MI D1", state="MI", tier="D1", supported=True)
        d_tx = await _make_district(db_session, name="TX extra", state="TX", supported=True)

        scope: dict[str, Any] = {
            "base": "states",
            "states": ["MI"],
            "include_district_ids": [d_tx.id],
        }
        candidate = await _make_candidate(db_session, target_scope_json=scope)
        ids = await resolve_district_ids_for_candidate(db_session, candidate)
        count = await _count_districts_for_scope(db_session, scope)
        assert count == len(ids)
        assert count == 2
        assert set(ids) == {d_mi.id, d_tx.id}

    async def test_legacy_count_unchanged(self, db_session: AsyncSession) -> None:
        await _make_district(db_session, name="A", state="MI", supported=True)
        await _make_district(db_session, name="B", state="TX", supported=True)

        scope_mi: dict[str, Any] = {"mode": "states", "states": ["MI"]}
        count = await _count_districts_for_scope(db_session, scope_mi)
        assert count == 1


# ── E. POST /api/marketing/initiation/target-scope/preview ───────────────────


class TestPreviewEndpoint:
    async def test_preview_composite_base_all(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(db_session, name="A", state="TX", supported=True)
        await _make_district(db_session, name="B", state="FL", supported=True)
        await db_session.commit()

        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"base": "all"}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    async def test_preview_composite_base_states(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(db_session, name="MI D", state="MI", supported=True)
        await _make_district(db_session, name="TX D", state="TX", supported=True)
        await db_session.commit()

        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"base": "states", "states": ["MI"]}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_preview_composite_with_tiers(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(db_session, name="MI D1", state="MI", tier="D1", supported=True)
        await _make_district(db_session, name="MI D2", state="MI", tier="D2", supported=True)
        await db_session.commit()

        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"base": "states", "states": ["MI"], "tiers": ["D1"]}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_preview_legacy_scope(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(db_session, name="TX Only", state="TX", supported=True)
        await db_session.commit()

        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"mode": "states", "states": ["TX"]}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    async def test_preview_invalid_scope_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"base": "invalid_base"}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 422

    async def test_preview_zero_count(self, db_session: AsyncSession, client: AsyncClient) -> None:
        await _make_district(db_session, name="TX Only", state="TX", supported=True)
        await db_session.commit()

        resp = await client.post(
            "/api/marketing/initiation/target-scope/preview",
            json={"target_scope": {"base": "states", "states": ["WY"]}},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ── F. GET /api/marketing/districts/search ────────────────────────────────────


class TestDistrictSearch:
    async def test_search_by_name_substring(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(
            db_session, name="Kalamazoo Public Schools", state="MI", supported=True
        )
        await _make_district(db_session, name="Grand Rapids", state="MI", supported=True)
        await _make_district(db_session, name="Houston ISD", state="TX", supported=True)
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=kalamazoo",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Kalamazoo Public Schools"

    async def test_search_state_filter(self, db_session: AsyncSession, client: AsyncClient) -> None:
        await _make_district(db_session, name="Grand ISD", state="MI", supported=True)
        await _make_district(db_session, name="Grand Prairie ISD", state="TX", supported=True)
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=grand&state=TX",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["state"] == "TX"

    async def test_search_case_insensitive(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(db_session, name="Ann Arbor Public Schools", state="MI")
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=ANN+ARBOR",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_search_returns_expected_fields(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        await _make_district(
            db_session, name="Lansing School District", state="MI", tier="D2", supported=True
        )
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=lansing",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        row = resp.json()[0]
        assert {"id", "name", "state", "tier", "supported"} == set(row.keys())
        assert row["tier"] == "D2"
        assert row["supported"] is True

    async def test_search_limit(self, db_session: AsyncSession, client: AsyncClient) -> None:
        for i in range(5):
            await _make_district(db_session, name=f"School {i}", state="TX", supported=True)
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=school&limit=3",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_search_empty_query_returns_all_up_to_limit(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        for i in range(5):
            await _make_district(db_session, name=f"Dist {i}", state="TX", supported=True)
        await db_session.commit()

        resp = await client.get(
            "/api/marketing/districts/search?q=&limit=10",
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 5
