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

from datetime import UTC, datetime

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
