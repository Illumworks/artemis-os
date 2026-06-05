"""C6: category validation — write-time warning for unknown categories.

Tests that write_observation logs a WARNING when given an unrecognised category,
and emits no warning for known categories. The write must succeed in all cases
(lossless rule preserved).
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.schemas import Scope
from artemis.memory.store import get_observation, write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider

_SCOPE = Scope(scope_kind="workspace", scope_id="c6-test")
_UNKNOWN_CATEGORY_MSG = "Observation written with unknown category"


def _mock_provider() -> MockProvider:
    return MockProvider(dims=384)


# ── C6 unknown category logs WARNING ─────────────────────────────────────────


async def test_c6_unknown_category_logs_warning(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Writing category='brand_voice' (unknown) must log exactly one WARNING
    on the artemis.memory.store logger containing the category name, and the
    observation must still be persisted to the DB."""
    caplog.set_level(logging.WARNING, logger="artemis.memory.store")

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "Some brand voice observation",
            category="brand_voice",
            embedding_provider=_mock_provider(),
        )

    # Warning was emitted
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "artemis.memory.store"
        and _UNKNOWN_CATEGORY_MSG in r.message
    ]
    assert len(warning_records) == 1, (
        f"Expected exactly 1 unknown-category warning, got {len(warning_records)}: "
        f"{[r.message for r in warning_records]}"
    )
    assert "brand_voice" in warning_records[0].message

    # Observation was written
    fetched = await get_observation(db_session, obs.id)
    assert fetched is not None
    assert fetched.category == "brand_voice"


# ── C6 known category emits NO warning ───────────────────────────────────────


async def test_c6_known_category_no_warning(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Writing category='discovery' (known) must not log the unknown-category
    warning on the artemis.memory.store logger."""
    caplog.set_level(logging.WARNING, logger="artemis.memory.store")

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "A standard discovery observation",
            category="discovery",
            embedding_provider=_mock_provider(),
        )

    # No unknown-category warning fired
    unknown_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "artemis.memory.store"
        and _UNKNOWN_CATEGORY_MSG in r.message
    ]
    assert unknown_warnings == [], (
        f"Unexpected unknown-category warning(s) for known category: "
        f"{[r.message for r in unknown_warnings]}"
    )

    # Observation was written
    fetched = await get_observation(db_session, obs.id)
    assert fetched is not None
    assert fetched.category == "discovery"


# ── C6 typo case ─────────────────────────────────────────────────────────────


async def test_c6_typo_category_logs_warning(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Writing category='discvoery' (typo of 'discovery') must log a WARNING
    and still persist the observation."""
    caplog.set_level(logging.WARNING, logger="artemis.memory.store")

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "Typo category observation",
            category="discvoery",
            embedding_provider=_mock_provider(),
        )

    # Warning was emitted
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "artemis.memory.store"
        and _UNKNOWN_CATEGORY_MSG in r.message
    ]
    assert len(warning_records) == 1, (
        f"Expected exactly 1 unknown-category warning, got {len(warning_records)}: "
        f"{[r.message for r in warning_records]}"
    )
    assert "discvoery" in warning_records[0].message

    # Observation was written with the original (typo'd) category
    fetched = await get_observation(db_session, obs.id)
    assert fetched is not None
    assert fetched.category == "discvoery"
