"""Tests for Phase B3: consolidation, incremental trigger, maintenance, and score channel.

Consolidation tests that need the LLM use a lightweight mock — no real Anthropic calls.
DB tests use the shared db_session fixture from conftest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.consolidator import (
    ConsolidationProposal,
    apply_consolidation,
    consolidate_observations,
    heuristic_filter,
)
from artemis.memory.incremental_consolidator import (
    IncrementalConsolidator,
    _reset_singleton_for_tests,
    get_incremental_consolidator,
)
from artemis.memory.maintenance import _DECAY_FACTORS, run_maintenance
from artemis.memory.retrieval import (
    RetrievalWeights,
    ScoreFeatureWeights,
    _composite_score,
    _compute_final_score,
)
from artemis.memory.schemas import Scope, SourceQualityHint
from artemis.memory.store import write_drawer, write_observation
from artemis.memory.tests.test_b2_embeddings import _SCOPE, _SOURCE, MockProvider

_SCOPE2 = Scope(scope_kind="project", scope_id="proj-b3")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_obs(
    obs_id: int = 1,
    content: str = "The district announced a new curriculum initiative.",
    category: str = "discovery",
    source_quality: float = 0.7,
) -> Any:
    """Return a minimal Observation-like object for unit tests."""
    from artemis.memory.schemas import Observation

    return Observation(
        id=obs_id,
        scope_kind="workspace",
        scope_id="ws-test",
        category=category,
        content=content,
        content_hash="abc",
        score=1.0,
        hit_count=0,
        source_quality=source_quality,
        user_confirmed=False,
        valid_from=None,
        valid_until=None,
        superseded_by=None,
        owner_user_id=None,
        created_at=datetime.now(UTC),
        accessed_at=datetime.now(UTC),
    )


def _make_llm_response(proposals: list[dict[str, Any]], removed_ids: list[int]) -> MagicMock:
    payload = json.dumps({"optimized": proposals, "removed_ids": removed_ids, "summary": "test"})
    content_block = MagicMock()
    content_block.text = payload
    response = MagicMock()
    response.content = [content_block]
    return response


# ── SourceQualityHint ─────────────────────────────────────────────────────────


def test_source_quality_hint_values() -> None:
    assert SourceQualityHint.user == 1.0
    assert SourceQualityHint.consolidation == 0.9
    assert SourceQualityHint.agent == 0.7
    assert SourceQualityHint.extractor == 0.5


# ── Heuristic filter ──────────────────────────────────────────────────────────


def test_heuristic_filter_rejects_too_short() -> None:
    obs = _make_obs(content="short")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_too_long() -> None:
    obs = _make_obs(content="x" * 501)
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_markdown_header() -> None:
    obs = _make_obs(content="## Section Title About Districts")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_bullet_list() -> None:
    obs = _make_obs(content="- First item in the list")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_fenced_code() -> None:
    obs = _make_obs(content="```python\nprint('hello')\n```")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_tool_output_opener() -> None:
    obs = _make_obs(content="Result: the analysis returned 42 matches")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_rejects_high_markdown_density() -> None:
    # >15% markdown chars
    obs = _make_obs(content="**Bold** [link](url) `code` ##head ##head ##head")
    assert heuristic_filter([obs]) == []


def test_heuristic_filter_passes_clean_prose() -> None:
    obs = _make_obs(
        content="The district announced a new phonics curriculum adoption for fall 2025."
    )
    assert heuristic_filter([obs]) == [obs]


def test_heuristic_filter_returns_subset() -> None:
    good1 = _make_obs(1, "Good observation about reading intervention results in third grade.")
    good2 = _make_obs(2, "Federal grant opportunity for literacy programs was announced in March.")
    noise = _make_obs(3, "## Noise Header")
    result = heuristic_filter([good1, noise, good2])
    assert result == [good1, good2]


def test_heuristic_filter_boundary_length() -> None:
    # exactly 15 chars → pass; exactly 500 chars → pass
    obs_15 = _make_obs(content="A" * 15)
    obs_500 = _make_obs(content="A" * 500)
    assert heuristic_filter([obs_15]) == [obs_15]
    assert heuristic_filter([obs_500]) == [obs_500]


# ── consolidate_observations — unit (mock LLM) ───────────────────────────────


async def test_consolidate_returns_empty_when_fewer_than_two_candidates() -> None:
    obs = _make_obs(1, "Only one clean observation available for testing.")
    result = await consolidate_observations([obs])
    assert result == []


async def test_consolidate_returns_empty_when_all_noise() -> None:
    obs1 = _make_obs(1, "## Header")
    obs2 = _make_obs(2, "- bullet item")
    result = await consolidate_observations([obs1, obs2])
    assert result == []


async def test_consolidate_happy_path_returns_proposals() -> None:
    obs1 = _make_obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _make_obs(2, "New Title I supplemental funding available for rural reading programs.")

    llm_response = _make_llm_response(
        proposals=[
            {
                "category": "discovery",
                "content": "Federal funding announced for early literacy and rural reading programs.",
                "evidence_from_ids": [1, 2],
            }
        ],
        removed_ids=[],
    )
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=llm_response)

    result = await consolidate_observations([obs1, obs2], client=mock_client)
    assert len(result) == 1
    assert result[0].source_quality == 0.9
    assert 1 in result[0].evidence_from_ids
    assert 2 in result[0].evidence_from_ids


async def test_consolidate_retries_on_bad_json() -> None:
    obs1 = _make_obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _make_obs(2, "Title I supplemental funding available for rural reading districts.")

    good_response = _make_llm_response(
        proposals=[
            {"category": "discovery", "content": "Merged insight.", "evidence_from_ids": [1, 2]}
        ],
        removed_ids=[],
    )

    call_count = 0

    async def _side_effect(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            bad = MagicMock()
            bad.content = [MagicMock(text="not valid json {{{")]
            return bad
        return good_response

    mock_client = AsyncMock()
    mock_client.messages.create = _side_effect

    result = await consolidate_observations([obs1, obs2], client=mock_client)
    assert call_count == 2
    assert len(result) == 1


async def test_consolidate_returns_empty_after_two_failures() -> None:
    obs1 = _make_obs(1, "Federal grant opportunity for early literacy programs was announced.")
    obs2 = _make_obs(2, "New Title I supplemental funding available for rural reading programs.")

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text="bad json")])
    )

    result = await consolidate_observations([obs1, obs2], client=mock_client)
    assert result == []


# ── apply_consolidation (DB) ──────────────────────────────────────────────────


async def test_apply_consolidation_creates_new_observation(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "Federal grant announced for reading programs.",
            embedding_provider=provider,
        )
        obs2 = await write_observation(
            db_session,
            _SCOPE,
            "Title I funding available in rural districts.",
            embedding_provider=provider,
        )

    proposal = ConsolidationProposal(
        category="discovery",
        content="Federal and Title I funding available for rural reading programs.",
        evidence_from_ids=[obs1.id, obs2.id],
        source_quality=0.9,
    )

    from artemis.memory.store import get_observation

    async with db_session.begin():
        created = await apply_consolidation(
            db_session, _SCOPE, [proposal], {obs1.id: obs1, obs2.id: obs2}
        )

    assert len(created) == 1
    new_obs = await get_observation(db_session, created[0].id)
    assert new_obs is not None
    # source_quality is REAL (float32) — allow precision drift on round-trip.
    assert new_obs.source_quality == pytest.approx(0.9, rel=1e-5)


async def test_apply_consolidation_supersedes_sources(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "District issued an RFP for reading software tools.",
            embedding_provider=provider,
        )
        obs2 = await write_observation(
            db_session,
            _SCOPE,
            "Procurement bid open for elementary phonics curriculum.",
            embedding_provider=provider,
        )

    proposal = ConsolidationProposal(
        category="procurement",
        content="District has open bids for reading software and phonics curriculum tools.",
        evidence_from_ids=[obs1.id, obs2.id],
        source_quality=0.9,
    )

    from artemis.memory.store import get_observation

    async with db_session.begin():
        created = await apply_consolidation(
            db_session, _SCOPE, [proposal], {obs1.id: obs1, obs2.id: obs2}
        )

    new_id = created[0].id
    refreshed1 = await get_observation(db_session, obs1.id)
    refreshed2 = await get_observation(db_session, obs2.id)
    assert refreshed1 is not None and refreshed1.superseded_by == new_id
    assert refreshed2 is not None and refreshed2.superseded_by == new_id


async def test_apply_consolidation_links_evidence(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "Senate bill advances literacy assessment requirements.",
            embedding_provider=provider,
        )
        obs2 = await write_observation(
            db_session,
            _SCOPE,
            "House committee reviews phonics mandate legislation.",
            embedding_provider=provider,
        )

    proposal = ConsolidationProposal(
        category="legislation",
        content="Both chambers advancing literacy legislation including assessments and phonics.",
        evidence_from_ids=[obs1.id, obs2.id],
        source_quality=0.9,
    )

    from artemis.memory.store import list_evidence_for_observation

    async with db_session.begin():
        created = await apply_consolidation(
            db_session, _SCOPE, [proposal], {obs1.id: obs1, obs2.id: obs2}
        )

    evidence = await list_evidence_for_observation(db_session, created[0].id)
    src_ids = {ev.source_id for ev in evidence}
    assert obs1.id in src_ids
    assert obs2.id in src_ids


async def test_apply_consolidation_is_lossless(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "Superintendent announced retirement after long tenure.",
            embedding_provider=provider,
        )
        obs2 = await write_observation(
            db_session,
            _SCOPE,
            "Board begins national search for new superintendent.",
            embedding_provider=provider,
        )

    proposal = ConsolidationProposal(
        category="leadership",
        content="Superintendent retiring; board conducting national search for replacement.",
        evidence_from_ids=[obs1.id, obs2.id],
        source_quality=0.9,
    )

    from artemis.memory.store import get_observation

    async with db_session.begin():
        await apply_consolidation(db_session, _SCOPE, [proposal], {obs1.id: obs1, obs2.id: obs2})

    # Both source observations must still exist in the DB (lossless rule)
    still1 = await get_observation(db_session, obs1.id)
    still2 = await get_observation(db_session, obs2.id)
    assert still1 is not None
    assert still2 is not None


async def test_apply_consolidation_forwards_drawer_evidence(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        drawer = await write_drawer(
            db_session,
            _SCOPE,
            "Source article text about district reading scores.",
            _SOURCE,
            embedding_provider=provider,
        )
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "District reading scores declined in third grade cohort.",
            embedding_provider=provider,
        )

    from artemis.memory.store import link_evidence, list_evidence_for_observation

    async with db_session.begin():
        await link_evidence(db_session, obs1.id, "drawer", drawer.id, weight=1.0)

    proposal = ConsolidationProposal(
        category="discovery",
        content="Third grade reading scores show decline per district reporting.",
        evidence_from_ids=[obs1.id],
        source_quality=0.9,
    )

    async with db_session.begin():
        created = await apply_consolidation(db_session, _SCOPE, [proposal], {obs1.id: obs1})

    evidence = await list_evidence_for_observation(db_session, created[0].id)
    drawer_ev = [e for e in evidence if e.source_kind == "drawer"]
    assert len(drawer_ev) == 1
    assert drawer_ev[0].source_id == drawer.id
    assert drawer_ev[0].weight == pytest.approx(0.9, abs=0.001)


# ── IncrementalConsolidator ───────────────────────────────────────────────────


def test_incremental_consolidator_increments_counter() -> None:
    ic = IncrementalConsolidator(threshold=25)
    ic.notify_drawer_written(_SCOPE)
    assert ic.get_count(_SCOPE) == 1


def test_incremental_consolidator_get_count_default_zero() -> None:
    ic = IncrementalConsolidator(threshold=25)
    assert ic.get_count(_SCOPE2) == 0


def test_incremental_consolidator_no_timer_below_threshold() -> None:
    ic = IncrementalConsolidator(threshold=5)
    for _ in range(4):
        ic.notify_drawer_written(_SCOPE2)
    assert ic.pending_slots() == []


def test_incremental_consolidator_timer_scheduled_at_threshold() -> None:
    import asyncio

    async def _run() -> list[tuple[str, str, str]]:
        ic = IncrementalConsolidator(threshold=3, debounce_seconds=300.0)
        for _ in range(3):
            ic.notify_drawer_written(_SCOPE2)
        pending = ic.pending_slots()
        ic.cancel_pending(_SCOPE2)
        return pending

    slots = asyncio.get_event_loop().run_until_complete(_run())
    assert (_SCOPE2.scope_kind, _SCOPE2.scope_id, "discovery") in slots


def test_incremental_consolidator_cancel_pending_removes_timer() -> None:
    import asyncio

    async def _run() -> list[tuple[str, str, str]]:
        ic = IncrementalConsolidator(threshold=3, debounce_seconds=300.0)
        for _ in range(3):
            ic.notify_drawer_written(_SCOPE2)
        ic.cancel_pending(_SCOPE2)
        return ic.pending_slots()

    slots = asyncio.get_event_loop().run_until_complete(_run())
    assert slots == []


def test_incremental_consolidator_disabled_no_timer() -> None:
    import asyncio

    async def _run() -> list[tuple[str, str, str]]:
        ic = IncrementalConsolidator(threshold=2, enabled=False)
        ic.notify_drawer_written(_SCOPE2)
        ic.notify_drawer_written(_SCOPE2)
        return ic.pending_slots()

    slots = asyncio.get_event_loop().run_until_complete(_run())
    assert slots == []


def test_incremental_consolidator_reset_count() -> None:
    ic = IncrementalConsolidator(threshold=25)
    for _ in range(5):
        ic.notify_drawer_written(_SCOPE2)
    ic.reset_count(_SCOPE2)
    assert ic.get_count(_SCOPE2) == 0


def test_get_incremental_consolidator_returns_singleton() -> None:
    _reset_singleton_for_tests()
    a = get_incremental_consolidator()
    b = get_incremental_consolidator()
    assert a is b


# ── Maintenance ───────────────────────────────────────────────────────────────


async def test_run_maintenance_decays_discovery(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "Observation that should decay during maintenance run.",
            category="discovery",
            embedding_provider=provider,
        )

    from artemis.memory.store import get_observation

    original_score = obs.score
    async with db_session.begin():
        updated = await run_maintenance(db_session)

    refreshed = await get_observation(db_session, obs.id)
    assert refreshed is not None
    expected = original_score * _DECAY_FACTORS["discovery"]
    assert refreshed.score == pytest.approx(expected, rel=1e-4)
    assert updated.get("discovery", 0) >= 1


async def test_run_maintenance_does_not_decay_warning(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "Warning signal that must never decay over time.",
            category="warning",
            embedding_provider=provider,
        )

    from artemis.memory.store import get_observation

    original_score = obs.score
    async with db_session.begin():
        await run_maintenance(db_session)

    refreshed = await get_observation(db_session, obs.id)
    assert refreshed is not None
    assert refreshed.score == pytest.approx(original_score, rel=1e-6)


async def test_run_maintenance_decays_decision(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "Decision made to adopt new phonics curriculum district-wide.",
            category="decision",
            embedding_provider=provider,
        )

    from artemis.memory.store import get_observation

    original_score = obs.score
    async with db_session.begin():
        await run_maintenance(db_session)

    refreshed = await get_observation(db_session, obs.id)
    assert refreshed is not None
    assert refreshed.score < original_score


async def test_run_maintenance_skips_superseded(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        old = await write_observation(
            db_session,
            _SCOPE,
            "Old discovery superseded by a newer consolidated observation.",
            category="discovery",
            embedding_provider=provider,
        )
        new = await write_observation(
            db_session,
            _SCOPE,
            "New consolidated discovery observation replacing the old one.",
            category="discovery",
            embedding_provider=provider,
        )

    from artemis.memory.store import get_observation, supersede_observation

    async with db_session.begin():
        await supersede_observation(db_session, old.id, new.id)
        # Capture old score INSIDE this transaction to avoid autobegin
        # leaking a pending tx into the next `async with begin()`.
        old_score_before = (await get_observation(db_session, old.id)).score  # type: ignore[union-attr]

    async with db_session.begin():
        await run_maintenance(db_session)

    async with db_session.begin():
        old_refreshed = await get_observation(db_session, old.id)
    assert old_refreshed is not None
    # Superseded row score must NOT have decayed
    assert old_refreshed.score == pytest.approx(old_score_before, rel=1e-6)


async def test_run_maintenance_returns_category_counts(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session,
            _SCOPE,
            "Convention: always use structured data formats in exports.",
            category="convention",
            embedding_provider=provider,
        )

    async with db_session.begin():
        result = await run_maintenance(db_session)

    assert isinstance(result, dict)
    assert "convention" in result


# ── Score channel sub-weights ─────────────────────────────────────────────────


def test_composite_score_source_quality_weighted() -> None:
    sf = ScoreFeatureWeights(relevance=0.0, hits=0.0, quality=1.0, confirmed=0.0)
    high = _composite_score(0.0, 0, 1.0, False, sf)
    low = _composite_score(0.0, 0, 0.2, False, sf)
    assert high > low


def test_composite_score_hit_count_normalized() -> None:
    sf = ScoreFeatureWeights(relevance=0.0, hits=1.0, quality=0.0, confirmed=0.0)
    ten_hits = _composite_score(0.0, 10, 0.0, False, sf)
    one_hit = _composite_score(0.0, 1, 0.0, False, sf)
    # 10 hits → min(1, 10/10)=1.0; 1 hit → min(1, 1/10)=0.1
    assert ten_hits == pytest.approx(1.0)
    assert one_hit == pytest.approx(0.1, rel=0.01)


def test_composite_score_hit_count_capped_at_one() -> None:
    sf = ScoreFeatureWeights(relevance=0.0, hits=1.0, quality=0.0, confirmed=0.0)
    capped = _composite_score(0.0, 100, 0.0, False, sf)
    assert capped == pytest.approx(1.0)


def test_composite_score_user_confirmed_boosts() -> None:
    sf = ScoreFeatureWeights(relevance=0.0, hits=0.0, quality=0.0, confirmed=1.0)
    confirmed = _composite_score(0.0, 0, 0.0, True, sf)
    unconfirmed = _composite_score(0.0, 0, 0.0, False, sf)
    assert confirmed > unconfirmed
    assert confirmed == pytest.approx(1.0)


def test_compute_final_score_uses_score_features() -> None:
    weights = RetrievalWeights(fts=0.0, semantic=0.0, recency=0.0, score=1.0)
    sf = ScoreFeatureWeights(relevance=0.0, hits=0.0, quality=1.0, confirmed=0.0)
    score = _compute_final_score(
        0.0,
        0.0,
        0.0,
        0.0,
        weights,
        source_quality=0.8,
        score_features=sf,
    )
    assert score == pytest.approx(0.8, rel=0.01)


def test_compute_final_score_backwards_compat_all_zero() -> None:
    # Old B2 tests passed obs_score=0.0 for all and expected result=0.0
    weights = RetrievalWeights()
    score = _compute_final_score(0.0, 0.0, 0.0, 0.0, weights)
    assert score == pytest.approx(0.0)
