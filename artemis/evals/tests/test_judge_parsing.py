"""Judge-output parsing (incl. malformed replies) + grade_case fail-safety.

LLM is always mocked (FakeAdapter); nothing here touches a real model.
"""

from __future__ import annotations

import json

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import TextBlock
from artemis.evals.judge import (
    JudgeParseError,
    build_judge_prompt,
    compute_overall,
    grade_case,
    parse_judge_output,
)
from artemis.evals.rubrics import get_rubric
from artemis.evals.schemas import CriterionScore, EvalCase, TranscriptTurn

ARES = get_rubric("ares")


def _case(agent_id: str = "ares") -> EvalCase:
    return EvalCase(
        case_id="t1",
        agent_id=agent_id,
        input_transcript=[TranscriptTurn(role="user", text="Should we skip staging?")],
        agent_output="No, and here's why...",
        tool_calls=["run_tests"],
    )


def _judge_json(scores: dict[str, int], comment: str = "solid") -> str:
    return json.dumps(
        {
            "criteria": [
                {"id": cid, "score": score, "justification": f"{cid} reasoning"}
                for cid, score in scores.items()
            ],
            "overall_comment": comment,
        }
    )


class TestParseJudgeOutput:
    def test_clean_json(self) -> None:
        raw = _judge_json({"technical_usefulness": 5, "candor": 4})
        scores, comment, missing = parse_judge_output(raw, ARES)
        assert [(s.criterion_id, s.score) for s in scores] == [
            ("technical_usefulness", 5),
            ("candor", 4),
        ]
        assert comment == "solid"
        assert missing == []

    def test_fenced_json(self) -> None:
        raw = "```json\n" + _judge_json({"technical_usefulness": 3, "candor": 3}) + "\n```"
        scores, _, missing = parse_judge_output(raw, ARES)
        assert len(scores) == 2
        assert missing == []

    def test_json_with_surrounding_prose(self) -> None:
        raw = (
            "Here is my evaluation:\n"
            + _judge_json({"technical_usefulness": 2, "candor": 1})
            + "\nLet me know if you need more detail."
        )
        scores, _, _ = parse_judge_output(raw, ARES)
        assert [s.score for s in scores] == [2, 1]

    def test_scores_clamped_to_range(self) -> None:
        raw = _judge_json({"technical_usefulness": 9, "candor": 0})
        scores, _, _ = parse_judge_output(raw, ARES)
        assert [s.score for s in scores] == [5, 1]

    def test_string_and_float_scores_coerced(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "technical_usefulness", "score": "4", "justification": "j"},
                    {"id": "candor", "score": 3.6, "justification": "j"},
                ]
            }
        )
        scores, _, _ = parse_judge_output(raw, ARES)
        assert [s.score for s in scores] == [4, 4]

    def test_unknown_criterion_dropped_and_missing_reported(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "technical_usefulness", "score": 4, "justification": "j"},
                    {"id": "made_up_criterion", "score": 5, "justification": "j"},
                ]
            }
        )
        scores, _, missing = parse_judge_output(raw, ARES)
        assert [s.criterion_id for s in scores] == ["technical_usefulness"]
        assert missing == ["candor"]

    def test_duplicate_criterion_keeps_first(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "candor", "score": 5, "justification": "first"},
                    {"id": "candor", "score": 1, "justification": "second"},
                    {"id": "technical_usefulness", "score": 3, "justification": "j"},
                ]
            }
        )
        scores, _, _ = parse_judge_output(raw, ARES)
        by_id = {s.criterion_id: s for s in scores}
        assert by_id["candor"].score == 5
        assert by_id["candor"].justification == "first"

    def test_unparseable_score_dropped(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "technical_usefulness", "score": "great", "justification": "j"},
                    {"id": "candor", "score": 2, "justification": "j"},
                ]
            }
        )
        scores, _, missing = parse_judge_output(raw, ARES)
        assert [s.criterion_id for s in scores] == ["candor"]
        assert missing == ["technical_usefulness"]

    def test_boolean_score_dropped(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "technical_usefulness", "score": True, "justification": "j"},
                    {"id": "candor", "score": 2, "justification": "j"},
                ]
            }
        )
        scores, _, _ = parse_judge_output(raw, ARES)
        assert [s.criterion_id for s in scores] == ["candor"]

    def test_long_justification_truncated(self) -> None:
        raw = json.dumps(
            {
                "criteria": [
                    {"id": "candor", "score": 3, "justification": "x" * 5000},
                ]
            }
        )
        scores, _, _ = parse_judge_output(raw, ARES)
        assert len(scores[0].justification) == 500

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "The agent did well overall, I'd say 4 out of 5.",
            "{broken json",
            "[]",
            '{"criteria": "not a list"}',
            '{"criteria": []}',
            '{"criteria": [{"id": "unknown_thing", "score": 3}]}',
            '{"no_criteria_key": true}',
        ],
    )
    def test_malformed_replies_raise(self, raw: str) -> None:
        with pytest.raises(JudgeParseError):
            parse_judge_output(raw, ARES)


class TestComputeOverall:
    def test_weighted_mean(self) -> None:
        # ares: technical_usefulness weight 1.0, candor weight 1.5
        scores = [
            CriterionScore(criterion_id="technical_usefulness", score=5),
            CriterionScore(criterion_id="candor", score=1),
        ]
        assert compute_overall(scores, ARES) == round((5 * 1.0 + 1 * 1.5) / 2.5, 3)

    def test_empty_scores_none(self) -> None:
        assert compute_overall([], ARES) is None


class TestBuildJudgePrompt:
    def test_prompt_includes_load_bearing_parts(self) -> None:
        prompt = build_judge_prompt(ARES, _case())
        assert "Should we skip staging?" in prompt
        assert "No, and here's why..." in prompt
        assert "run_tests" in prompt
        for criterion in ARES.criteria:
            assert f'"{criterion.id}"' in prompt
        assert '"criteria"' in prompt  # output-shape instructions present

    def test_empty_tool_calls_rendered_as_none(self) -> None:
        case = _case()
        case = case.model_copy(update={"tool_calls": []})
        assert "(none)" in build_judge_prompt(ARES, case)


class TestGradeCase:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        adapter = FakeAdapter(
            [ScriptedReply(text=_judge_json({"technical_usefulness": 4, "candor": 5}))]
        )
        grade = await grade_case(adapter, ARES, _case(), judge_provider="fake")
        assert grade.ok
        assert grade.overall == round((4 * 1.0 + 5 * 1.5) / 2.5, 3)
        assert grade.judge_comment == "solid"
        assert grade.error is None
        assert grade.judge_provider == "fake"
        assert len(adapter.requests) == 1
        # Judge must receive the model override and system prompt.
        assert adapter.requests[0].system

    @pytest.mark.asyncio
    async def test_retry_recovers_from_malformed_first_reply(self) -> None:
        adapter = FakeAdapter(
            [
                ScriptedReply(text="Sure! The agent seems pretty good to me."),
                ScriptedReply(text=_judge_json({"technical_usefulness": 3, "candor": 3})),
            ]
        )
        grade = await grade_case(adapter, ARES, _case())
        assert grade.ok
        assert len(adapter.requests) == 2
        # Retry prompt carries the strict-format reminder.
        retry_block = adapter.requests[1].messages[0].content[0]
        assert isinstance(retry_block, TextBlock)
        assert "not valid JSON" in retry_block.text

    @pytest.mark.asyncio
    async def test_persistent_malformed_output_fails_safe(self) -> None:
        adapter = FakeAdapter(
            [
                ScriptedReply(text="no json here"),
                ScriptedReply(text="still no json"),
            ]
        )
        grade = await grade_case(adapter, ARES, _case())
        assert not grade.ok
        assert grade.error is not None
        assert grade.scores == []
        assert grade.overall is None

    @pytest.mark.asyncio
    async def test_adapter_exception_fails_safe(self) -> None:
        class ExplodingAdapter:
            async def complete(self, request: CompletionRequest) -> CompletionResponse:
                raise RuntimeError("provider on fire")

        grade = await grade_case(ExplodingAdapter(), ARES, _case())
        assert not grade.ok
        assert "provider on fire" in (grade.error or "")

    @pytest.mark.asyncio
    async def test_partial_scores_recorded_with_missing_criteria(self) -> None:
        adapter = FakeAdapter([ScriptedReply(text=_judge_json({"candor": 4}))])
        grade = await grade_case(adapter, ARES, _case())
        assert grade.ok
        assert grade.missing_criteria == ["technical_usefulness"]
        assert grade.overall == 4.0  # weighted mean over the criteria present
