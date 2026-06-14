"""Memory M1b — unit tests for near_duplicate.py.

Coverage:
  1. normalize_body: strips ISO-timestamp prefix, role tag, collapses whitespace,
     casefolds. Two messages differing only by those markers normalize equal.
  2. plan_clone_clusters (pure): grouping, scope isolation, superseded-skip,
     empty-body skip, singleton skip.
  3. PRECISION / time-series guard: different numeric values never cluster.
  4. _choose_canonical: deterministic preference ordering.
  5. apply_clone_consolidation + consolidate_near_duplicates (DB): lossless
     invariants, idempotency, scope-filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation
from artemis.memory.near_duplicate import (
    _choose_canonical,
    apply_clone_consolidation,
    consolidate_near_duplicates,
    normalize_body,
    plan_clone_clusters,
)
from artemis.memory.schemas import Observation, Scope
from artemis.memory.store import list_evidence_for_observation, write_observation

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_SCOPE = Scope(scope_kind="agent", scope_id="callie-neardup-test")
_SCOPE_B = Scope(scope_kind="agent", scope_id="callie-neardup-test-b")


# ── Observation factory for pure-function tests ───────────────────────────────


def _obs(
    obs_id: int,
    content: str,
    scope_kind: str = "agent",
    scope_id: str = "callie-neardup-test",
    source_quality: float = 0.7,
    evidence_count: int = 1,
    created_at: datetime | None = None,
    superseded_by: int | None = None,
) -> Observation:
    ts = created_at or _NOW
    return Observation(
        id=obs_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        category="discovery",
        content=content,
        content_hash=f"hash-{obs_id}",
        score=1.0,
        hit_count=0,
        source_quality=source_quality,
        user_confirmed=False,
        valid_from=None,
        valid_until=None,
        superseded_by=superseded_by,
        owner_user_id=None,
        created_at=ts,
        accessed_at=ts,
        confidence=0.5,
        supersedes=None,
        evidence_count=evidence_count,
    )


# ── 1. normalize_body ─────────────────────────────────────────────────────────


def test_normalize_body_strips_iso_timestamp() -> None:
    """Leading ISO timestamp bracket is removed."""
    raw = "[2026-06-06T16:11:24+00:00] [USER] Still an echo."
    assert normalize_body(raw) == "still an echo."


def test_normalize_body_strips_role_tag_only() -> None:
    """[ROLE] tag at start (no timestamp) is stripped."""
    assert normalize_body("[USER] Still an echo.") == "still an echo."
    assert normalize_body("[ASSISTANT] Still an echo.") == "still an echo."


def test_normalize_body_casefolds() -> None:
    """Output is casefolded."""
    assert normalize_body("[USER] HELLO WORLD") == "hello world"


def test_normalize_body_collapses_whitespace() -> None:
    """Multiple internal spaces are collapsed."""
    assert normalize_body("[USER] hello   world") == "hello world"


def test_normalize_body_strips_leading_trailing_whitespace() -> None:
    assert normalize_body("  [USER] hello  ") == "hello"


def test_normalize_body_two_messages_differing_by_timestamp_normalize_equal() -> None:
    """Same text at different times normalizes equal."""
    a = "[2026-06-06T16:11:24+00:00] [USER] Holding."
    b = "[2026-06-07T09:00:00+00:00] [USER] Holding."
    assert normalize_body(a) == normalize_body(b)


def test_normalize_body_two_messages_differing_by_role_normalize_equal() -> None:
    """USER echo of ASSISTANT's words is considered equal after normalization."""
    a = "[USER] Still an echo."
    b = "[ASSISTANT] Still an echo."
    assert normalize_body(a) == normalize_body(b)


def test_normalize_body_two_messages_differing_by_case_normalize_equal() -> None:
    assert normalize_body("[USER] HOLDING.") == normalize_body("[USER] holding.")


def test_normalize_body_empty_after_strip_returns_empty() -> None:
    assert normalize_body("[USER]  ") == ""


def test_normalize_body_no_role_tag_passthrough() -> None:
    """Content without a role tag is returned unchanged (minus whitespace/case)."""
    assert normalize_body("campaign angle is strong") == "campaign angle is strong"


# ── 2. plan_clone_clusters (pure) ────────────────────────────────────────────


def test_plan_clone_clusters_groups_byte_identical_after_normalization() -> None:
    """Three messages with the same normalized body → one cluster of 3."""
    obs = [
        _obs(1, "[2026-06-06T16:11:24+00:00] [USER] Holding."),
        _obs(2, "[2026-06-07T09:00:00+00:00] [USER] Holding."),
        _obs(3, "[2026-06-08T10:00:00+00:00] [USER] Holding."),
    ]
    clusters = plan_clone_clusters(obs)
    assert len(clusters) == 1
    assert clusters[0].size == 3


def test_plan_clone_clusters_user_and_assistant_echo_cluster() -> None:
    """USER and ASSISTANT echoing identical text cluster together."""
    obs = [
        _obs(1, "[USER] Not responding."),
        _obs(2, "[ASSISTANT] Not responding."),
    ]
    clusters = plan_clone_clusters(obs)
    assert len(clusters) == 1
    assert clusters[0].size == 2


def test_plan_clone_clusters_singletons_not_returned() -> None:
    """A group of size 1 is not returned."""
    obs = [
        _obs(1, "[USER] Unique message A."),
        _obs(2, "[USER] Unique message B."),
    ]
    clusters = plan_clone_clusters(obs)
    assert clusters == []


def test_plan_clone_clusters_empty_normalized_body_skipped() -> None:
    """Observations whose content normalizes to empty string are excluded."""
    obs = [
        _obs(1, "[USER]  "),
        _obs(2, "[USER]  "),
        _obs(3, "[USER] Real content."),
        _obs(4, "[USER] Real content."),
    ]
    clusters = plan_clone_clusters(obs)
    assert len(clusters) == 1
    assert clusters[0].normalized == "real content."


def test_plan_clone_clusters_superseded_obs_ignored() -> None:
    """Superseded observations are excluded from clustering."""
    obs = [
        _obs(1, "[USER] Holding.", superseded_by=None),
        _obs(2, "[USER] Holding.", superseded_by=99),  # already retired
        _obs(3, "[USER] Holding.", superseded_by=None),
    ]
    clusters = plan_clone_clusters(obs)
    # obs 2 is excluded; only 1 and 3 remain → cluster of 2
    assert len(clusters) == 1
    assert clusters[0].size == 2
    assert 2 not in clusters[0].duplicate_ids
    assert clusters[0].canonical_id != 2


def test_plan_clone_clusters_different_scopes_never_cluster() -> None:
    """Observations in different scopes are never clustered together."""
    obs = [
        _obs(1, "[USER] Holding.", scope_id="callie-neardup-test"),
        _obs(2, "[USER] Holding.", scope_id="callie-neardup-test-b"),
        _obs(3, "[USER] Holding.", scope_id="callie-neardup-test"),
    ]
    clusters = plan_clone_clusters(obs)
    # Only obs 1 + 3 share a scope and normalize equal
    assert len(clusters) == 1
    assert clusters[0].scope_id == "callie-neardup-test"
    assert clusters[0].size == 2


# ── 3. PRECISION — time-series and distinct bodies never cluster ──────────────


def test_plan_clone_clusters_momentum_snapshots_never_cluster() -> None:
    """Momentum snapshot observations are excluded by the time-series guard."""
    obs = [
        _obs(1, "Momentum snapshot for general_growth/MI: current=6, target=10"),
        _obs(2, "Momentum snapshot for general_growth/MI: current=6, target=10"),
    ]
    clusters = plan_clone_clusters(obs)
    assert clusters == []


def test_plan_clone_clusters_different_numeric_values_do_not_cluster() -> None:
    """Different numeric values in body → different normalized bodies → no cluster."""
    obs = [
        _obs(1, "[USER] Signal score is current=6"),
        _obs(2, "[USER] Signal score is current=16"),
    ]
    clusters = plan_clone_clusters(obs)
    assert clusters == []


def test_plan_clone_clusters_genuinely_different_messages_do_not_cluster() -> None:
    """Clearly distinct messages are not clustered."""
    obs = [
        _obs(1, "[USER] Campaign A is live."),
        _obs(2, "[USER] Campaign B is paused."),
    ]
    clusters = plan_clone_clusters(obs)
    assert clusters == []


# ── 4. _choose_canonical ─────────────────────────────────────────────────────


def test_choose_canonical_prefers_highest_source_quality() -> None:
    """Highest source_quality wins, all else equal."""
    members = [
        _obs(1, "x", source_quality=0.5, evidence_count=1),
        _obs(2, "x", source_quality=0.9, evidence_count=1),
        _obs(3, "x", source_quality=0.7, evidence_count=1),
    ]
    canonical = _choose_canonical(members)
    assert canonical.id == 2


def test_choose_canonical_tie_break_by_evidence_count() -> None:
    """Tie on source_quality → highest evidence_count wins."""
    members = [
        _obs(1, "x", source_quality=0.7, evidence_count=3),
        _obs(2, "x", source_quality=0.7, evidence_count=5),
    ]
    canonical = _choose_canonical(members)
    assert canonical.id == 2


def test_choose_canonical_tie_break_by_created_at_earliest() -> None:
    """Tie on quality + evidence → earliest created_at wins."""
    earlier = _NOW - timedelta(hours=1)
    members = [
        _obs(1, "x", source_quality=0.7, evidence_count=1, created_at=_NOW),
        _obs(2, "x", source_quality=0.7, evidence_count=1, created_at=earlier),
    ]
    canonical = _choose_canonical(members)
    assert canonical.id == 2


def test_choose_canonical_final_tie_break_by_id_lowest() -> None:
    """Final tie-break: lowest id wins."""
    members = [
        _obs(5, "x", source_quality=0.7, evidence_count=1, created_at=_NOW),
        _obs(3, "x", source_quality=0.7, evidence_count=1, created_at=_NOW),
    ]
    canonical = _choose_canonical(members)
    assert canonical.id == 3


# ── 5. apply_clone_consolidation + consolidate_near_duplicates (DB) ───────────


async def _write_obs(
    session: AsyncSession,
    content: str,
    scope: Scope = _SCOPE,
    source_quality: float = 0.7,
) -> Observation:
    """Helper: write an observation inside its own transaction."""
    async with session.begin():
        return await write_observation(
            session,
            scope=scope,
            content=content,
            category="discovery",
            source_quality=source_quality,
        )


async def test_apply_clone_consolidation_lossless_row_count(
    db_session: AsyncSession,
) -> None:
    """After consolidation, total observation count is UNCHANGED (lossless)."""
    obs1 = await _write_obs(db_session, "[USER] Holding.")
    obs2 = await _write_obs(db_session, "[2026-06-07T09:00:00+00:00] [USER] Holding.")
    obs3 = await _write_obs(db_session, "[2026-06-08T10:00:00+00:00] [USER] Holding.")

    # Fetch count and build clusters in a single read transaction
    async with db_session.begin():
        all_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
        count_before = len(all_rows)
        all_obs = [
            Observation.model_validate(r)
            for r in all_rows
            if r.scope_kind == _SCOPE.scope_kind and r.scope_id == _SCOPE.scope_id
        ]
        clusters = plan_clone_clusters(all_obs)
        assert len(clusters) == 1
        stats = await apply_clone_consolidation(db_session, clusters)

    assert stats.clusters == 1
    assert stats.observations_superseded == 2  # 3 members → 2 duplicates superseded

    async with db_session.begin():
        count_after = len((await db_session.execute(select(MemoryObservation))).scalars().all())
    # Nothing deleted — same total count
    assert count_after == count_before

    _ = obs1, obs2, obs3  # used for setup


async def test_apply_clone_consolidation_canonical_stays_active(
    db_session: AsyncSession,
) -> None:
    """The canonical observation remains active (superseded_by IS NULL)."""
    obs_high = await _write_obs(db_session, "[USER] Holding.", source_quality=0.9)
    obs_low = await _write_obs(
        db_session, "[2026-06-07T09:00:00+00:00] [USER] Holding.", source_quality=0.5
    )

    async with db_session.begin():
        all_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
        all_obs = [
            Observation.model_validate(r)
            for r in all_rows
            if r.scope_kind == _SCOPE.scope_kind and r.scope_id == _SCOPE.scope_id
        ]
        clusters = plan_clone_clusters(all_obs)
        await apply_clone_consolidation(db_session, clusters)

    async with db_session.begin():
        # The canonical is the obs with higher source_quality
        canonical_row = (
            await db_session.execute(
                select(MemoryObservation).where(MemoryObservation.id == obs_high.id)
            )
        ).scalar_one()
        assert canonical_row.superseded_by is None

        # The duplicate is superseded pointing to canonical
        dup_row = (
            await db_session.execute(
                select(MemoryObservation).where(MemoryObservation.id == obs_low.id)
            )
        ).scalar_one()
        assert dup_row.superseded_by == obs_high.id


async def test_apply_clone_consolidation_evidence_link_created(
    db_session: AsyncSession,
) -> None:
    """Each duplicate gets linked as 'observation' evidence on the canonical."""
    obs1 = await _write_obs(db_session, "[USER] Holding.", source_quality=0.9)
    obs2 = await _write_obs(
        db_session, "[2026-06-07T09:00:00+00:00] [USER] Holding.", source_quality=0.5
    )

    async with db_session.begin():
        all_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
        all_obs = [
            Observation.model_validate(r)
            for r in all_rows
            if r.scope_kind == _SCOPE.scope_kind and r.scope_id == _SCOPE.scope_id
        ]
        clusters = plan_clone_clusters(all_obs)
        stats = await apply_clone_consolidation(db_session, clusters)

    assert stats.evidence_links_created >= 1

    # Evidence row links duplicate → canonical
    async with db_session.begin():
        evidence = await list_evidence_for_observation(db_session, obs1.id)
    source_ids = [e.source_id for e in evidence]
    assert str(obs2.id) in source_ids


async def test_apply_clone_consolidation_idempotent(
    db_session: AsyncSession,
) -> None:
    """Running consolidation twice produces the same outcome (no-op second pass)."""
    await _write_obs(db_session, "[USER] Holding.")
    await _write_obs(db_session, "[2026-06-07T09:00:00+00:00] [USER] Holding.")

    async with db_session.begin():
        stats1 = await consolidate_near_duplicates(
            db_session,
            scope_kind=_SCOPE.scope_kind,
            scope_id=_SCOPE.scope_id,
        )

    async with db_session.begin():
        stats2 = await consolidate_near_duplicates(
            db_session,
            scope_kind=_SCOPE.scope_kind,
            scope_id=_SCOPE.scope_id,
        )

    assert stats1.observations_superseded == 1
    # Second pass: no active duplicates remain → nothing to do
    assert stats2.observations_superseded == 0
    assert stats2.clusters == 0


async def test_consolidate_near_duplicates_scope_filter(
    db_session: AsyncSession,
) -> None:
    """consolidate_near_duplicates with scope_kind/scope_id only touches that scope."""
    # Clone pair in scope A
    await _write_obs(db_session, "[USER] Echo.", scope=_SCOPE)
    await _write_obs(db_session, "[2026-06-07T09:00:00+00:00] [USER] Echo.", scope=_SCOPE)
    # Clone pair in scope B (should be untouched when we filter to scope A)
    await _write_obs(db_session, "[USER] Echo.", scope=_SCOPE_B)
    await _write_obs(db_session, "[2026-06-07T09:00:00+00:00] [USER] Echo.", scope=_SCOPE_B)

    async with db_session.begin():
        stats = await consolidate_near_duplicates(
            db_session,
            scope_kind=_SCOPE.scope_kind,
            scope_id=_SCOPE.scope_id,
        )

    assert stats.observations_superseded == 1  # only scope A touched

    # Scope B observations remain both active
    scope_b_rows = (
        (
            await db_session.execute(
                select(MemoryObservation).where(
                    MemoryObservation.scope_kind == _SCOPE_B.scope_kind,
                    MemoryObservation.scope_id == _SCOPE_B.scope_id,
                    MemoryObservation.superseded_by.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(scope_b_rows) == 2
