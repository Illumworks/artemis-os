"""A source that runs and produces nothing is broken until proven otherwise.

`starbridge_researcher` ran 48 times over three weeks and emitted nothing on
every single one, because its client pointed at a hostname that does not resolve.
Nothing in the health report noticed: a scout that completes with zero findings
is indistinguishable from a scout with nothing to find. The run is green, the log
says "Scan complete", and zero is a legitimate answer on a quiet day.

It is the Argus failure in a different costume. The tell is not any single zero
-- it is a run count with a zero beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from artemis.ops.health import (
    DEAD_SOURCE_MIN_RUNS,
    Finding,
    Report,
    SourceYield,
    derive_findings,
)


def _src(runs: int, productive: int, name: str = "starbridge_researcher") -> SourceYield:
    return SourceYield(
        scout_type=name, runs=runs, productive_runs=productive, last_run=datetime.now(UTC)
    )


def test_many_runs_and_no_signals_is_flagged() -> None:
    assert _src(39, 0).is_silently_dead


def test_one_signal_is_enough_to_clear_it() -> None:
    """Producing anything proves the path works end to end."""
    assert not _src(39, 1).is_silently_dead


def test_a_source_with_too_few_runs_is_not_judged() -> None:
    """Below the floor, a quiet world is the likelier explanation.

    A new or infrequent scout must not be called broken on its second run.
    """
    assert not _src(DEAD_SOURCE_MIN_RUNS - 1, 0).is_silently_dead


def test_the_threshold_is_low_enough_to_catch_this_early() -> None:
    """Starbridge burned 48 runs. Catching it on the fifth is the point."""
    assert DEAD_SOURCE_MIN_RUNS <= 10


def test_the_finding_says_treat_it_as_broken() -> None:
    """ "Zero signals" reads as good news unless the message says otherwise.

    Whoever reads this at 8am needs to know the number is suspicious, not
    reassuring.
    """
    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.source_yield = [_src(39, 0)]

    findings = derive_findings(report)
    dead = [f for f in findings if "starbridge_researcher" in f.message]

    assert len(dead) == 1
    assert dead[0].severity == "stuck", "a warn is too quiet for a source that emits nothing"
    assert "NOT ONE produced a signal" in dead[0].message
    assert "broken until proven otherwise" in dead[0].message


def test_healthy_sources_produce_no_finding() -> None:
    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.source_yield = [_src(32, 31, "regional_news"), _src(21, 19, "leadership_transition")]

    assert not [f for f in derive_findings(report) if "produced a signal" in f.message]


def test_a_stuck_finding_makes_the_command_exit_nonzero() -> None:
    """So it can gate a cron or a check without anyone reading the output."""
    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.source_yield = [_src(39, 0)]
    findings = derive_findings(report)

    assert any(f.severity == "stuck" for f in findings)
    assert isinstance(findings[0], Finding)


# ── backup freshness ─────────────────────────────────────────────────────────


def test_no_backup_at_all_is_flagged_as_stuck() -> None:
    """There was no database backup of any kind before 2026-09-08.

    The git remote backs up the code and has never held a single row, so every
    signal, memory observation and Argus dossier lived on one disk. The gap was
    invisible because nothing looked for it.
    """
    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.backup = {"state": "none", "dir": "/Users/artemis/artemis-backups"}

    findings = derive_findings(report)
    hit = [f for f in findings if "NO DATABASE BACKUP" in f.message]

    assert len(hit) == 1
    assert hit[0].severity == "stuck"


def test_a_stale_backup_is_flagged() -> None:
    """A backup job that quietly stops is a scout that runs and emits nothing.

    Everything reports fine and the thing being relied on is not happening.
    """
    from artemis.ops.health import BACKUP_STALE_AFTER

    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.backup = {
        "state": "ok",
        "age": "4 days",
        "age_seconds": str(int(BACKUP_STALE_AFTER.total_seconds()) + 3600),
    }

    assert [f for f in derive_findings(report) if "database backup is" in f.message]


def test_a_fresh_backup_produces_no_finding() -> None:
    report = Report(generated_at=datetime.now(UTC), service={"healthz": "200"})
    report.backup = {"state": "ok", "age": "6h", "age_seconds": "21600"}

    assert not [f for f in derive_findings(report) if "backup" in f.message.lower()]


def test_the_window_tolerates_one_missed_night() -> None:
    """Nightly at 03:30, so a laptop asleep overnight must not cry wolf.

    Two consecutive misses is a real problem; one is a closed lid.
    """
    from artemis.ops.health import BACKUP_STALE_AFTER

    assert timedelta(hours=36) <= BACKUP_STALE_AFTER
    assert timedelta(days=4) >= BACKUP_STALE_AFTER
