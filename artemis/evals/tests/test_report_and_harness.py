"""Score aggregation, markdown/JSON artifacts, and the full harness with a
mocked judge. No DB, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.evals.fixtures import load_fixture_cases
from artemis.evals.harness import grade_cases, run_report_card
from artemis.evals.report import render_markdown, summarize, write_run_artifacts
from artemis.evals.rubrics import get_rubric
from artemis.evals.schemas import CaseGrade, CriterionScore, ReportCardRun


def _grade(
    case_id: str,
    agent_id: str,
    scores: dict[str, int],
    *,
    error: str | None = None,
) -> CaseGrade:
    rubric = get_rubric(agent_id)
    if error is not None:
        return CaseGrade(
            case_id=case_id, agent_id=agent_id, rubric_id=rubric.rubric_id, error=error
        )
    score_list = [CriterionScore(criterion_id=cid, score=s) for cid, s in scores.items()]
    weights = {c.id: c.weight for c in rubric.criteria}
    total_weight = sum(weights[cid] for cid in scores)
    overall = round(sum(s * weights[cid] for cid, s in scores.items()) / total_weight, 3)
    return CaseGrade(
        case_id=case_id,
        agent_id=agent_id,
        rubric_id=rubric.rubric_id,
        scores=score_list,
        overall=overall,
    )


class TestSummarize:
    def test_per_criterion_averages(self) -> None:
        grades = [
            _grade("a1", "ares", {"technical_usefulness": 5, "candor": 3}),
            _grade("a2", "ares", {"technical_usefulness": 3, "candor": 5}),
        ]
        run = summarize(grades, judge_provider="fake")
        assert len(run.agents) == 1
        agent = run.agents[0]
        assert agent.agent_id == "ares"
        assert agent.case_count == 2
        assert agent.graded_count == 2
        assert agent.failed_count == 0
        by_id = {c.criterion_id: c for c in agent.criteria}
        assert by_id["technical_usefulness"].mean == 4.0
        assert by_id["technical_usefulness"].min == 3
        assert by_id["technical_usefulness"].max == 5
        assert by_id["candor"].count == 2
        # overall_mean = mean of case-level weighted overalls
        overalls = [g.overall for g in grades]
        assert None not in overalls
        expected = round(sum(o for o in overalls if o is not None) / 2, 3)
        assert agent.overall_mean == expected

    def test_failed_grades_excluded_from_averages(self) -> None:
        grades = [
            _grade("a1", "ares", {"technical_usefulness": 4, "candor": 4}),
            _grade("a2", "ares", {}, error="judge exploded"),
        ]
        agent = summarize(grades).agents[0]
        assert agent.case_count == 2
        assert agent.graded_count == 1
        assert agent.failed_count == 1
        assert agent.failed_case_ids == ["a2"]
        assert agent.overall_mean == 4.0

    def test_multiple_agents_grouped_and_sorted(self) -> None:
        grades = [
            _grade(
                "c1", "callie", {"signal_relevance": 5, "sourcing_honesty": 5, "actionability": 5}
            ),
            _grade("a1", "ares", {"technical_usefulness": 2, "candor": 2}),
        ]
        run = summarize(grades)
        assert [a.agent_id for a in run.agents] == ["ares", "callie"]

    def test_all_failed_yields_none_overall(self) -> None:
        grades = [_grade("a1", "ares", {}, error="boom")]
        agent = summarize(grades).agents[0]
        assert agent.overall_mean is None
        assert agent.criteria == []


class TestArtifacts:
    def test_markdown_contains_summary_rows(self) -> None:
        grades = [
            _grade("a1", "ares", {"technical_usefulness": 5, "candor": 3}),
            _grade("a2", "ares", {}, error="parse failed"),
        ]
        run = summarize(grades, label="md-test", judge_provider="fake")
        md = render_markdown(run)
        assert "## ares" in md
        assert "| Technical usefulness | 5.00 | 5 | 5 | 1 |" in md
        assert "FAILED — parse failed" in md
        assert "Failed cases: a2" in md

    def test_write_run_artifacts_roundtrip(self, tmp_path: Path) -> None:
        grades = [_grade("a1", "ares", {"technical_usefulness": 4, "candor": 4})]
        run = summarize(grades, label="round trip!", judge_provider="fake")
        assert run.label == "round-trip"  # sanitized
        json_path, md_path = write_run_artifacts(run, output_dir=tmp_path)
        assert json_path.parent == tmp_path / "reports"
        reloaded = ReportCardRun.model_validate_json(json_path.read_text())
        assert reloaded.run_id == run.run_id
        assert reloaded.agents[0].overall_mean == 4.0
        assert md_path.read_text().startswith("# Agent Report Card")

    def test_evals_dir_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from artemis.evals.report import evals_dir

        monkeypatch.setenv("ARTEMIS_EVALS_DIR", str(tmp_path / "custom"))
        assert evals_dir() == tmp_path / "custom"


def _judge_reply(rubric_agent: str, score: int) -> str:
    rubric = get_rubric(rubric_agent)
    return json.dumps(
        {
            "criteria": [
                {"id": c.id, "score": score, "justification": "scripted"} for c in rubric.criteria
            ],
            "overall_comment": "scripted comment",
        }
    )


class TestHarness:
    async def test_grade_cases_walks_all_cases(self) -> None:
        cases = load_fixture_cases("ares")
        adapter = FakeAdapter([ScriptedReply(text=_judge_reply("ares", 4)) for _ in cases])
        grades = await grade_cases(adapter, cases, judge_provider="fake")
        assert len(grades) == len(cases)
        assert all(g.ok for g in grades)
        assert {g.case_id for g in grades} == {c.case_id for c in cases}

    async def test_run_report_card_end_to_end_with_mock_judge(self, tmp_path: Path) -> None:
        agent_ids = ["artemis", "callie", "ares"]
        total_cases = sum(len(load_fixture_cases(a)) for a in agent_ids)
        # Fixtures load in agent_ids order; script one reply per case.
        replies = []
        for agent_id in agent_ids:
            replies += [
                ScriptedReply(text=_judge_reply(agent_id, 5)) for _ in load_fixture_cases(agent_id)
            ]
        adapter = FakeAdapter(replies)

        run = await run_report_card(
            agent_ids=agent_ids,
            adapter=adapter,
            judge_provider="fake",
            label="e2e",
            output_dir=tmp_path,
        )

        assert len(run.grades) == total_cases
        assert {a.agent_id for a in run.agents} == set(agent_ids)
        for agent in run.agents:
            assert agent.failed_count == 0
            assert agent.overall_mean == 5.0
        # Artifacts persisted and reloadable.
        reports = list((tmp_path / "reports").glob("*-e2e.json"))
        assert len(reports) == 1
        reloaded = ReportCardRun.model_validate_json(reports[0].read_text())
        assert reloaded.judge_provider == "fake"
        assert (tmp_path / "reports" / f"{run.run_id}.md").exists()

    async def test_run_survives_one_bad_case(self, tmp_path: Path) -> None:
        cases = load_fixture_cases("ares")
        # First case: two malformed replies (initial + retry) -> failed grade.
        replies = [
            ScriptedReply(text="not json"),
            ScriptedReply(text="still not json"),
        ]
        replies += [ScriptedReply(text=_judge_reply("ares", 3)) for _ in cases[1:]]
        adapter = FakeAdapter(replies)

        run = await run_report_card(
            agent_ids=["ares"],
            adapter=adapter,
            judge_provider="fake",
            output_dir=tmp_path,
        )
        agent = run.agents[0]
        assert agent.case_count == len(cases)
        assert agent.failed_count == 1
        assert agent.graded_count == len(cases) - 1
        assert agent.overall_mean == 3.0

    async def test_explicit_cases_plug_in(self, tmp_path: Path) -> None:
        # The captured-output path: caller passes its own cases.
        from artemis.evals.schemas import EvalCase, TranscriptTurn

        captured = EvalCase(
            case_id="captured-1",
            agent_id="artemis",
            input_transcript=[TranscriptTurn(role="user", text="ping")],
            agent_output="pong",
            source="captured",
        )
        adapter = FakeAdapter([ScriptedReply(text=_judge_reply("artemis", 4))])
        run = await run_report_card(
            cases=[captured],
            adapter=adapter,
            judge_provider="fake",
            write_artifacts=False,
        )
        assert run.grades[0].case_id == "captured-1"
        assert run.agents[0].case_count == 1

    async def test_unknown_agent_fails_fast_before_llm_calls(self) -> None:
        from artemis.evals.rubrics import UnknownRubricError

        adapter = FakeAdapter([])
        with pytest.raises(UnknownRubricError):
            await run_report_card(
                agent_ids=["nonexistent-agent"], adapter=adapter, write_artifacts=False
            )
        assert adapter.requests == []
