"""Score aggregation + run artifacts.

Aggregates CaseGrades into per-agent, per-criterion summaries and persists
each run as a timestamped JSON + markdown pair so runs are comparable over
time. File-based on purpose (same choice as memory/eval): a DB table can come
later without touching the aggregation API.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from artemis.evals.rubrics import get_rubric
from artemis.evals.schemas import (
    AgentSummary,
    CaseGrade,
    CriterionSummary,
    ReportCardRun,
)

#: Default artifact root. Override with the ARTEMIS_EVALS_DIR env var or the
#: explicit ``output_dir`` argument (tests pass tmp_path).
DEFAULT_EVALS_DIR = Path.home() / ".artemis" / "evals"


def evals_dir() -> Path:
    override = os.environ.get("ARTEMIS_EVALS_DIR", "").strip()
    return Path(override) if override else DEFAULT_EVALS_DIR


# ── Aggregation ──────────────────────────────────────────────────────────────


def summarize_agent(agent_id: str, grades: list[CaseGrade]) -> AgentSummary:
    """Aggregate one agent's grades. Failed grades (parse errors) are counted
    and listed but excluded from the averages."""
    rubric = get_rubric(agent_id)
    graded = [g for g in grades if g.ok]
    failed = [g for g in grades if not g.ok]

    by_criterion: dict[str, list[int]] = defaultdict(list)
    for grade in graded:
        for score in grade.scores:
            by_criterion[score.criterion_id].append(score.score)

    criteria: list[CriterionSummary] = []
    for criterion in rubric.criteria:
        values = by_criterion.get(criterion.id, [])
        if not values:
            continue
        criteria.append(
            CriterionSummary(
                criterion_id=criterion.id,
                name=criterion.name,
                mean=round(sum(values) / len(values), 3),
                min=min(values),
                max=max(values),
                count=len(values),
            )
        )

    overalls = [g.overall for g in graded if g.overall is not None]
    return AgentSummary(
        agent_id=agent_id,
        rubric_id=rubric.rubric_id,
        case_count=len(grades),
        graded_count=len(graded),
        failed_count=len(failed),
        overall_mean=round(sum(overalls) / len(overalls), 3) if overalls else None,
        criteria=criteria,
        failed_case_ids=[g.case_id for g in failed],
    )


def summarize(
    grades: list[CaseGrade],
    *,
    label: str = "report-card",
    judge_provider: str | None = None,
    judge_model: str | None = None,
    notes: list[str] | None = None,
) -> ReportCardRun:
    """Roll a flat grade list up into a ReportCardRun (grouped by agent)."""
    by_agent: dict[str, list[CaseGrade]] = defaultdict(list)
    for grade in grades:
        by_agent[grade.agent_id].append(grade)

    created_at = datetime.now(UTC)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "report-card"
    return ReportCardRun(
        run_id=f"{stamp}-{safe_label}",
        label=safe_label,
        judge_provider=judge_provider,
        judge_model=judge_model,
        created_at=created_at,
        agents=[summarize_agent(agent_id, by_agent[agent_id]) for agent_id in sorted(by_agent)],
        grades=grades,
        notes=notes or [],
    )


# ── Rendering + persistence ──────────────────────────────────────────────────


def render_markdown(run: ReportCardRun) -> str:
    """Human-readable per-agent summary table."""
    lines: list[str] = [
        f"# Agent Report Card — {run.run_id}",
        "",
        f"- Judge: {run.judge_provider or 'unknown'}"
        + (f" / {run.judge_model}" if run.judge_model else ""),
        f"- Graded at: {run.created_at.isoformat()}",
        "",
    ]
    for note in run.notes:
        lines.append(f"> {note}")
    if run.notes:
        lines.append("")

    for agent in run.agents:
        overall = f"{agent.overall_mean:.2f}" if agent.overall_mean is not None else "n/a"
        lines += [
            f"## {agent.agent_id} — overall {overall} / 5",
            "",
            f"Rubric `{agent.rubric_id}` — {agent.graded_count}/{agent.case_count} "
            f"cases graded ({agent.failed_count} failed).",
            "",
            "| Criterion | Mean | Min | Max | n |",
            "|---|---|---|---|---|",
        ]
        for criterion in agent.criteria:
            lines.append(
                f"| {criterion.name} | {criterion.mean:.2f} | {criterion.min} "
                f"| {criterion.max} | {criterion.count} |"
            )
        if agent.failed_case_ids:
            lines += ["", f"Failed cases: {', '.join(agent.failed_case_ids)}"]
        lines.append("")

    lines.append("## Per-case grades")
    lines.append("")
    for grade in run.grades:
        if grade.ok:
            overall = f"{grade.overall:.2f}" if grade.overall is not None else "n/a"
            detail = "; ".join(f"{s.criterion_id}={s.score}" for s in grade.scores)
            lines.append(f"- `{grade.case_id}` ({grade.agent_id}): {overall} — {detail}")
            if grade.judge_comment:
                lines.append(f"  - {grade.judge_comment}")
        else:
            lines.append(f"- `{grade.case_id}` ({grade.agent_id}): FAILED — {grade.error}")
    lines.append("")
    return "\n".join(lines)


def write_run_artifacts(run: ReportCardRun, *, output_dir: Path | None = None) -> tuple[Path, Path]:
    """Persist the run as ``<run_id>.json`` + ``<run_id>.md`` under
    ``<evals_dir>/reports/``. Returns (json_path, md_path)."""
    root = (output_dir or evals_dir()) / "reports"
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{run.run_id}.json"
    md_path = root / f"{run.run_id}.md"
    json_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(run), encoding="utf-8")
    return json_path, md_path
