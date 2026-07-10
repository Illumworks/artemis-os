"""Report-card harness: cases → judge → aggregated run artifact.

Two entry points:

``grade_cases(adapter, cases)``
    Grade an explicit case list (fixtures today, captured outputs later).

``run_report_card(agent_ids=...)``
    Convenience wrapper: load fixtures for each agent, resolve the judge
    adapter through the provider cascade, grade, summarize, persist.

The judge provider defaults to ``ARTEMIS_EVALS_JUDGE_PROVIDER`` (falling back
to the standard resolver cascade), and tests inject a scripted adapter so
nothing here needs a live model.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from artemis.agent.client import ModelAdapter
from artemis.evals.fixtures import load_fixture_cases
from artemis.evals.judge import grade_case
from artemis.evals.report import summarize, write_run_artifacts
from artemis.evals.rubrics import get_rubric
from artemis.evals.schemas import CaseGrade, EvalCase, ReportCardRun

logger = logging.getLogger(__name__)

#: Agents graded when the caller doesn't say otherwise.
DEFAULT_AGENT_IDS: tuple[str, ...] = ("artemis", "callie", "ares")


def _default_judge_provider() -> str | None:
    return os.environ.get("ARTEMIS_EVALS_JUDGE_PROVIDER", "").strip() or None


async def grade_cases(
    adapter: ModelAdapter,
    cases: list[EvalCase],
    *,
    judge_model: str | None = None,
    judge_provider: str | None = None,
) -> list[CaseGrade]:
    """Grade every case sequentially (fail-safe per case, see grade_case).

    Sequential on purpose: the claude-code adapter times out under concurrent
    runs (see project memory, 2026-06), and eval latency is not a bottleneck.
    """
    grades: list[CaseGrade] = []
    for case in cases:
        rubric = get_rubric(case.agent_id)
        grade = await grade_case(
            adapter,
            rubric,
            case,
            judge_model=judge_model,
            judge_provider=judge_provider,
        )
        grades.append(grade)
        status = f"{grade.overall:.2f}" if grade.overall is not None else f"FAILED ({grade.error})"
        logger.info("Graded %s (%s): %s", case.case_id, case.agent_id, status)
    return grades


async def run_report_card(
    *,
    agent_ids: list[str] | tuple[str, ...] = DEFAULT_AGENT_IDS,
    adapter: ModelAdapter | None = None,
    judge_provider: str | None = None,
    judge_model: str | None = None,
    cases: list[EvalCase] | None = None,
    label: str = "report-card",
    output_dir: Path | None = None,
    write_artifacts: bool = True,
) -> ReportCardRun:
    """Run a full report card and (by default) persist the artifacts.

    Args:
        agent_ids:      Agents whose fixture cases to grade. Ignored when an
                        explicit ``cases`` list is given.
        adapter:        Pre-built judge adapter (tests inject a fake). When
                        None, one is resolved via the provider cascade.
        judge_provider: Preferred provider id for the judge (e.g. "anthropic",
                        "claude-code"). Defaults to ARTEMIS_EVALS_JUDGE_PROVIDER
                        or the resolver's default cascade.
        judge_model:    Optional model override forwarded per judge call.
        cases:          Explicit case list — the plug-in point for captured
                        production outputs. When None, fixtures are loaded.
        label:          Report label stem (becomes part of the run_id).
        output_dir:     Artifact root override (default ~/.artemis/evals or
                        ARTEMIS_EVALS_DIR).
        write_artifacts: Set False to skip disk writes (pure in-memory run).
    """
    provider = judge_provider or _default_judge_provider()

    if cases is None:
        cases = []
        for agent_id in agent_ids:
            get_rubric(agent_id)  # fail fast on unknown agents, before any LLM call
            cases.extend(load_fixture_cases(agent_id))
    else:
        for case in cases:
            get_rubric(case.agent_id)

    if adapter is None:
        from artemis.providers.resolver import resolve_adapter  # noqa: PLC0415

        adapter = resolve_adapter(provider=provider)

    grades = await grade_cases(
        adapter,
        cases,
        judge_model=judge_model,
        judge_provider=provider,
    )
    run = summarize(
        grades,
        label=label,
        judge_provider=provider,
        judge_model=judge_model,
    )
    if write_artifacts:
        json_path, md_path = write_run_artifacts(run, output_dir=output_dir)
        logger.info("Report card written: %s / %s", json_path, md_path)
    return run
