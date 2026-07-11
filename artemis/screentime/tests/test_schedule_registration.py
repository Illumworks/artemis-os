"""Unit tests for the Screen-Time Watch daily collection cron wiring.

Pure — mocks the APScheduler instance so no scheduler actually starts and no
network/DB/provider calls happen. Confirms:
  * register_screentime_schedule registers exactly one daily job for
    run_scheduled (the collection sweep — gather/normalize/topic-gate/
    classify/store), not any digest/reporting entry point.
  * the configured schedule is a plain daily 11:00 UTC cron with NO numeric
    day-of-week field (this repo's known APScheduler day-of-week gotcha:
    numeric dow is 0=Mon, not 0=Sun — see
    artemis/screentime/tests/test_schedule_registration.py and the OP1 bug
    history). A bare '11 * * *' with '*' day-of-week sidesteps it entirely.
  * start/stop wrap register + the scheduler lifecycle idempotently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from artemis.screentime import runner


def test_register_screentime_schedule_adds_one_daily_collection_job():
    scheduler = MagicMock()
    runner.register_screentime_schedule(scheduler)

    assert scheduler.add_job.call_count == 1
    _, kwargs = scheduler.add_job.call_args
    args = scheduler.add_job.call_args.args

    # The collection entry point — the sweep path, not a digest/report call.
    assert args[0] is runner.run_scheduled

    assert kwargs["id"] == "screentime.watch.sweep"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1

    trigger = kwargs["trigger"]
    # CronTrigger fields expose FieldsList; str() of each field gives its expr.
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "11"
    assert fields["minute"] == "0"
    # Day-of-week must be unconstrained ('*') — never a numeric weekday given
    # this repo's from_crontab 0=Mon (not 0=Sun) gotcha.
    assert fields["day_of_week"] == "*"


def test_register_screentime_schedule_is_idempotent_replace_existing():
    scheduler = MagicMock()
    runner.register_screentime_schedule(scheduler)
    runner.register_screentime_schedule(scheduler)
    assert scheduler.add_job.call_count == 2
    for call in scheduler.add_job.call_args_list:
        assert call.kwargs["replace_existing"] is True
        assert call.kwargs["id"] == "screentime.watch.sweep"


async def test_start_stop_screentime_scheduler_registers_and_tears_down():
    runner.stop_screentime_scheduler()  # clean slate regardless of prior test order
    try:
        runner.start_screentime_scheduler()
        scheduler = runner.get_screentime_scheduler()
        assert scheduler.running is True
        job = scheduler.get_job("screentime.watch.sweep")
        assert job is not None
    finally:
        runner.stop_screentime_scheduler()
