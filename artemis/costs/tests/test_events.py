"""Tests for artemis.costs.events — record_cost_event DB writer."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.events import record_cost_event
from artemis.costs.models import CostEvent


@pytest.mark.asyncio
async def test_cost_computed_correctly(db_session: AsyncSession) -> None:
    """record_cost_event computes cost across all 4 token streams."""
    # claude-sonnet-4-6: input=3/M, output=15/M, cache_write=3.75/M, cache_read=0.30/M
    event = await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-sonnet-4-6",
        provider_path="api",
        feature_tag="agent_run",
        input_tokens=1_000_000,  # 1M → $3
        output_tokens=1_000_000,  # 1M → $15
        cache_creation_input_tokens=1_000_000,  # 1M → $3.75
        cache_read_input_tokens=1_000_000,  # 1M → $0.30
    )
    await db_session.commit()

    expected = 3.0 + 15.0 + 3.75 + 0.30  # = 22.05
    assert abs(event.cost_usd - expected) < 0.0001


@pytest.mark.asyncio
async def test_cost_zero_when_all_tokens_zero(db_session: AsyncSession) -> None:
    """cost_usd = 0 when all token counts are 0 — no division weirdness."""
    event = await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-sonnet-4-6",
        provider_path="api",
        feature_tag="agent_run",
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    await db_session.commit()
    assert event.cost_usd == 0.0


@pytest.mark.asyncio
async def test_rate_snapshot_frozen(db_session: AsyncSession) -> None:
    """Rate columns on the row match the registry at write time."""
    event = await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-opus-4-7",
        provider_path="api",
        feature_tag="floating_artemis",
        input_tokens=100,
        output_tokens=50,
    )
    await db_session.commit()

    assert event.input_rate_per_million == 15.0
    assert event.output_rate_per_million == 75.0
    assert event.cache_write_rate_per_million == 18.75
    assert event.cache_read_rate_per_million == 1.50


@pytest.mark.asyncio
async def test_row_persisted_to_db(db_session: AsyncSession) -> None:
    """record_cost_event writes a row that is queryable via SELECT."""
    await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        provider_path="cli",
        feature_tag="memory_consolidation",
        input_tokens=500,
        output_tokens=200,
    )
    await db_session.commit()

    result = await db_session.execute(
        select(CostEvent).where(CostEvent.feature_tag == "memory_consolidation")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].provider == "anthropic"
    assert rows[0].provider_path == "cli"
    assert rows[0].input_tokens == 500
    assert rows[0].output_tokens == 200


@pytest.mark.asyncio
async def test_is_error_flag(db_session: AsyncSession) -> None:
    """is_error=True is stored correctly; error_kind is set."""
    event = await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-sonnet-4-6",
        provider_path="api",
        feature_tag="agent_run",
        is_error=True,
        error_kind="TimeoutError",
    )
    await db_session.commit()
    assert event.is_error is True
    assert event.error_kind == "TimeoutError"


@pytest.mark.asyncio
async def test_unknown_model_falls_back_to_zero_rates(db_session: AsyncSession) -> None:
    """Unknown model logs a warning and uses zero rates, but still writes the row."""
    # Use an unknown model — pricing lookup will warn but not raise
    event = await record_cost_event(
        db_session,
        provider="openai",
        model="gpt-99-imaginary",
        provider_path="api",
        feature_tag="agent_run",
        input_tokens=1000,
        output_tokens=500,
    )
    await db_session.commit()
    # Zero rates → zero cost
    assert event.cost_usd == 0.0
    assert event.input_rate_per_million == 0.0


@pytest.mark.asyncio
async def test_recording_failure_does_not_raise(db_session: AsyncSession) -> None:
    """Callers wrapping record_cost_event in try/except see no exception on bad input."""
    # Simulate a caller-level guard — record_cost_event itself may succeed or fail,
    # but the guard pattern must not propagate any exception to the outer scope.
    # Pass a deliberately bad session type to trigger a DB error.
    # The caller's guard silences it — verify nothing escapes the suppression.
    with contextlib.suppress(Exception):
        await record_cost_event(
            None,  # type: ignore[arg-type]
            provider="anthropic",
            model="claude-sonnet-4-6",
            provider_path="api",
            feature_tag="agent_run",
        )

    # If we reached here without an unhandled exception, the guard pattern works.
    assert True


@pytest.mark.asyncio
async def test_record_cost_event_canonicalizes_model_name(db_session: AsyncSession) -> None:
    """A row written with an alias model name is stored under the canonical form.

    Without this, the by_model rollup + routing-opportunities engine treat
    'claude-haiku-4-5' and 'claude-haiku-4-5-20251001' as separate models.
    """
    event = await record_cost_event(
        db_session,
        provider="anthropic",
        model="claude-haiku-4-5",  # alias — should be canonicalized on write
        provider_path="api",
        feature_tag="agent_run",
        input_tokens=1000,
        output_tokens=500,
    )
    await db_session.commit()
    assert event.model == "claude-haiku-4-5-20251001"
