"""Scheduler registration tests for Automations (OP1).

Tests:
- Scheduler registers cron jobs for active scheduled automations
- Scheduler deregisters jobs on archive/pause
- Idempotent: calling reregister twice does not duplicate jobs
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest

from artemis.automations.scheduler import (
    _deregister_job,
    _register_job,
    get_automation_scheduler,
    reregister_automation,
    stop_automation_scheduler,
)


def _make_auto(
    automation_id: str = "test-id",
    status: str = "active",
    trigger_type: str = "scheduled",
    schedule_config: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Create a lightweight automation stub (no DB required)."""
    return SimpleNamespace(
        id=automation_id,
        status=status,
        trigger_type=trigger_type,
        schedule_config=schedule_config
        if schedule_config is not None
        else {"cron": "0 9 * * 1", "timezone": "UTC"},
    )


@pytest.fixture(autouse=True)
async def reset_scheduler() -> AsyncGenerator[None, None]:
    """Ensure scheduler is stopped after each test."""
    yield
    stop_automation_scheduler()


@pytest.mark.asyncio
async def test_register_job_adds_cron_job() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto()
    _register_job(scheduler, auto)  # type: ignore[arg-type]

    job = scheduler.get_job(f"automation_{auto.id}")
    assert job is not None


@pytest.mark.asyncio
async def test_register_job_idempotent() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto(automation_id="idempotent-id")
    _register_job(scheduler, auto)  # type: ignore[arg-type]
    _register_job(scheduler, auto)  # type: ignore[arg-type]

    jobs = [j for j in scheduler.get_jobs() if j.id == f"automation_{auto.id}"]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_deregister_job_removes_job() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto(automation_id="deregister-id")
    _register_job(scheduler, auto)  # type: ignore[arg-type]
    assert scheduler.get_job(f"automation_{auto.id}") is not None

    _deregister_job(scheduler, f"automation_{auto.id}")
    assert scheduler.get_job(f"automation_{auto.id}") is None


@pytest.mark.asyncio
async def test_reregister_removes_job_on_archive() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto(automation_id="archive-id")
    _register_job(scheduler, auto)  # type: ignore[arg-type]

    auto.status = "archived"
    reregister_automation(auto)  # type: ignore[arg-type]

    assert scheduler.get_job(f"automation_{auto.id}") is None


@pytest.mark.asyncio
async def test_reregister_removes_job_on_pause() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto(automation_id="pause-id")
    _register_job(scheduler, auto)  # type: ignore[arg-type]

    auto.status = "paused"
    reregister_automation(auto)  # type: ignore[arg-type]

    assert scheduler.get_job(f"automation_{auto.id}") is None


@pytest.mark.asyncio
async def test_register_job_skips_missing_cron() -> None:
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        scheduler.start()

    auto = _make_auto(automation_id="no-cron-id", schedule_config={})
    _register_job(scheduler, auto)  # type: ignore[arg-type]

    assert scheduler.get_job(f"automation_{auto.id}") is None
