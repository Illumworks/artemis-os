"""Agent Report Card — LLM-as-judge evaluation harness for named agents.

Grades agent outputs against per-agent rubrics so quality is measured over
time instead of guessed. v1 is fixture-driven (curated transcripts under
``artemis/evals/fixtures/``), but every entry point accepts arbitrary
``EvalCase`` lists so real captured outputs can be plugged in later.

Public API::

    from artemis.evals import run_report_card, get_rubric, load_fixture_cases

    run = await run_report_card(agent_ids=["artemis", "callie", "ares"])
    print(render_markdown(run))

CLI::

    uv run python -m artemis.evals --agents artemis callie ares
"""

from artemis.evals.fixtures import load_fixture_cases
from artemis.evals.harness import run_report_card
from artemis.evals.judge import JudgeParseError, grade_case, parse_judge_output
from artemis.evals.report import render_markdown, summarize, write_run_artifacts
from artemis.evals.rubrics import get_rubric, list_rubrics, register_rubric
from artemis.evals.schemas import (
    AgentSummary,
    CaseGrade,
    CriterionScore,
    CriterionSummary,
    EvalCase,
    ReportCardRun,
    Rubric,
    RubricCriterion,
)

__all__ = [
    "AgentSummary",
    "CaseGrade",
    "CriterionScore",
    "CriterionSummary",
    "EvalCase",
    "JudgeParseError",
    "ReportCardRun",
    "Rubric",
    "RubricCriterion",
    "get_rubric",
    "grade_case",
    "list_rubrics",
    "load_fixture_cases",
    "parse_judge_output",
    "register_rubric",
    "render_markdown",
    "run_report_card",
    "summarize",
    "write_run_artifacts",
]
