"""The scheduled Screen-Time COLLECTION cron must NOT auto-push to Slack, even
when screentime_report_channel is set (owner decision: collect silently; Callie
reports on-demand; a digest is a separate deliberate step).

Regression anchor: report_channel was set (C0BBYM8N26M), so without an explicit
deliver_alerts=False the daily cron would broadcast big-move alerts automatically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from artemis.screentime import runner


async def test_run_scheduled_passes_deliver_alerts_false() -> None:
    """run_scheduled (the cron entry point) calls the pipeline with alerts OFF."""
    fake_report = type("R", (), {"as_dict": lambda self: {"stored_new": 0}})()

    class _CM:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(
            runner, "run_screentime_pipeline", new=AsyncMock(return_value=fake_report)
        ) as m,
        patch("artemis.db.SessionLocal", return_value=_CM()),
    ):
        await runner.run_scheduled()

    assert m.await_count == 1
    _, kwargs = m.call_args
    assert kwargs.get("deliver_alerts") is False, "collection cron must suppress Slack alerts"
