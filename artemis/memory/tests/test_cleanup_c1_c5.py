"""Tests for retrieval bug fixes C1 (multi-scope) and C5 (empty query guard).

C1: search_observations was filtering on the legacy scope_kind/scope_id columns
    on memory_observations, so secondary scopes (written to memory_observation_scopes
    with is_primary=FALSE) could never retrieve their observations.

C5: the semantic branch lacked a query.strip() guard, causing it to embed an
    empty string and return up to top_k nearest neighbors for a blank query.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider

# Two distinct scopes for C1 tests.
_SCOPE_A = Scope(scope_kind="workspace", scope_id="c1-scope-a")
_SCOPE_B = Scope(scope_kind="workspace", scope_id="c1-scope-b")


@pytest.fixture(autouse=True)
async def _drain_usage_tasks() -> AsyncGenerator[None, None]:
    import artemis.memory.retrieval as retrieval_mod

    pending_before = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    if pending_before:
        await asyncio.gather(*pending_before, return_exceptions=True)

    yield

    pending_after = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    if pending_after:
        await asyncio.gather(*pending_after, return_exceptions=True)


# ── C1 positive: secondary scope retrieval ───────────────────────────────────


async def test_c1_secondary_scope_is_retrievable(db_session: AsyncSession) -> None:
    """An observation written with additional_scopes=[B] must be findable by scope_set=[B].

    Before the fix, scope filtering used the legacy scope_kind/scope_id columns on
    memory_observations (always the primary scope), so searching by a secondary scope
    returned nothing.
    """
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE_A,
            "multi-scope observation content",
            additional_scopes=[_SCOPE_B],
            embedding_provider=provider,
        )

    # Search by the secondary scope only.
    results = await search_observations(
        db_session,
        [_SCOPE_B],
        "multi-scope observation",
        modes=["recency"],
        provider=provider,
    )
    ids = {r.id for r in results}
    assert obs.id in ids, (
        f"Observation {obs.id} not found when searching secondary scope {_SCOPE_B}. Got: {ids}"
    )


# ── C1 dedup: no duplicate when scope_set covers both primary and secondary ───


async def test_c1_no_duplicate_when_both_scopes_requested(db_session: AsyncSession) -> None:
    """An observation matching both the primary scope A and secondary scope B should
    appear exactly once in results when scope_set=[A, B].
    """
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE_A,
            "dedup test observation content",
            additional_scopes=[_SCOPE_B],
            embedding_provider=provider,
        )

    results = await search_observations(
        db_session,
        [_SCOPE_A, _SCOPE_B],
        "dedup test observation",
        modes=["recency"],
        provider=provider,
    )
    obs_ids = [r.id for r in results if r.id == obs.id]
    assert len(obs_ids) == 1, (
        f"Expected observation {obs.id} exactly once but found {len(obs_ids)} times. "
        f"All result IDs: {[r.id for r in results]}"
    )


# ── C1 regression guard: primary-only scope still works ──────────────────────


async def test_c1_primary_only_scope_still_retrievable(db_session: AsyncSession) -> None:
    """An observation written with no additional_scopes must still be findable via
    its primary scope. Regression guard: the new JOIN path must not break the base case.
    """
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE_A,
            "primary scope only observation",
            embedding_provider=provider,
        )

    results = await search_observations(
        db_session,
        [_SCOPE_A],
        "primary scope only",
        modes=["recency"],
        provider=provider,
    )
    ids = {r.id for r in results}
    assert obs.id in ids, f"Observation {obs.id} not found in primary scope {_SCOPE_A}. Got: {ids}"


# ── C5: empty query returns no results ───────────────────────────────────────


async def test_c5_empty_query_returns_empty(db_session: AsyncSession) -> None:
    """search_observations with an empty query string must return [] for the semantic
    branch. Before the fix the semantic branch would embed "" and return nearest
    neighbors even though no meaningful query was provided.
    """
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session,
            _SCOPE_A,
            "some content that should not surface on empty query",
            embedding_provider=provider,
        )

    # Empty string.
    results = await search_observations(
        db_session,
        [_SCOPE_A],
        "",
        modes=["semantic"],
        provider=provider,
    )
    assert results == [], f"Expected [] for empty query, got {results}"


async def test_c5_whitespace_only_query_returns_empty(db_session: AsyncSession) -> None:
    """Whitespace-only queries (e.g. '   ') must also return [] from the semantic branch."""
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session,
            _SCOPE_A,
            "content that must not surface on whitespace query",
            embedding_provider=provider,
        )

    results = await search_observations(
        db_session,
        [_SCOPE_A],
        "   ",
        modes=["semantic"],
        provider=provider,
    )
    assert results == [], f"Expected [] for whitespace query, got {results}"
