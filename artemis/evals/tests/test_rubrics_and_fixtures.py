"""Rubric registry + fixture loading. No DB, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from artemis.evals.fixtures import FixtureError, load_fixture_cases
from artemis.evals.rubrics import (
    UnknownRubricError,
    get_rubric,
    list_rubrics,
    register_rubric,
)
from artemis.evals.schemas import Rubric, RubricCriterion


class TestRubricRegistry:
    def test_v1_agents_registered(self) -> None:
        assert {r.agent_id for r in list_rubrics()} >= {"artemis", "callie", "ares"}

    @pytest.mark.parametrize(
        ("agent_id", "expected_criteria"),
        [
            ("artemis", ["helpfulness", "tone_naturalness", "acts_vs_narrates", "on_topic"]),
            ("callie", ["signal_relevance", "sourcing_honesty", "actionability"]),
            ("ares", ["technical_usefulness", "candor"]),
        ],
    )
    def test_rubric_criteria(self, agent_id: str, expected_criteria: list[str]) -> None:
        rubric = get_rubric(agent_id)
        assert rubric.agent_id == agent_id
        assert rubric.criterion_ids() == expected_criteria
        assert all(c.guidance for c in rubric.criteria)
        assert all(c.weight > 0 for c in rubric.criteria)

    def test_lookup_is_case_insensitive(self) -> None:
        assert get_rubric("  Artemis ").rubric_id == get_rubric("artemis").rubric_id

    def test_unknown_agent_raises(self) -> None:
        with pytest.raises(UnknownRubricError, match="hestia"):
            get_rubric("hestia")

    def test_register_new_agent_rubric(self) -> None:
        rubric = Rubric(
            rubric_id="kai_retrieval_v1",
            agent_id="kai-test-only",
            description="Retrieval accuracy for Kai.",
            criteria=[
                RubricCriterion(
                    id="retrieval_accuracy",
                    name="Retrieval accuracy",
                    guidance="Did Kai surface the right asset?",
                )
            ],
        )
        register_rubric(rubric)
        assert get_rubric("kai-test-only").rubric_id == "kai_retrieval_v1"

    def test_rubric_rejects_duplicate_criterion_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate criterion ids"):
            Rubric(
                rubric_id="bad",
                agent_id="bad",
                description="dup",
                criteria=[
                    RubricCriterion(id="x", name="X", guidance="g"),
                    RubricCriterion(id="x", name="X again", guidance="g"),
                ],
            )

    def test_rubric_rejects_empty_criteria(self) -> None:
        with pytest.raises(ValidationError, match="at least one criterion"):
            Rubric(rubric_id="bad", agent_id="bad", description="empty", criteria=[])


class TestFixtureLoading:
    @pytest.mark.parametrize("agent_id", ["artemis", "callie", "ares"])
    def test_checked_in_fixtures_load(self, agent_id: str) -> None:
        cases = load_fixture_cases(agent_id)
        assert len(cases) >= 3
        for case in cases:
            assert case.agent_id == agent_id
            assert case.agent_output
            assert case.input_transcript
            assert case.source == "fixture"

    def test_artemis_has_a_narration_case(self) -> None:
        # The acts-vs-narrates criterion needs at least one fixture where the
        # agent claimed action with zero tool calls.
        cases = load_fixture_cases("artemis")
        assert any("acts-vs-narrates" in c.tags and not c.tool_calls for c in cases)

    def test_missing_agent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FixtureError, match="no fixture file"):
            load_fixture_cases("artemis", fixtures_dir=tmp_path)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "artemis.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(FixtureError, match="malformed"):
            load_fixture_cases("artemis", fixtures_dir=tmp_path)

    def test_agent_id_mismatch_raises(self, tmp_path: Path) -> None:
        case = {
            "case_id": "c1",
            "agent_id": "callie",
            "input_transcript": [{"role": "user", "text": "hi"}],
            "agent_output": "hello",
        }
        (tmp_path / "artemis.json").write_text(json.dumps([case]), encoding="utf-8")
        with pytest.raises(FixtureError, match="agent_id"):
            load_fixture_cases("artemis", fixtures_dir=tmp_path)

    def test_duplicate_case_ids_raise(self, tmp_path: Path) -> None:
        case = {
            "case_id": "c1",
            "agent_id": "artemis",
            "input_transcript": [{"role": "user", "text": "hi"}],
            "agent_output": "hello",
        }
        (tmp_path / "artemis.json").write_text(json.dumps([case, case]), encoding="utf-8")
        with pytest.raises(FixtureError, match="duplicate case_ids"):
            load_fixture_cases("artemis", fixtures_dir=tmp_path)

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "artemis.json").write_text("[]", encoding="utf-8")
        with pytest.raises(FixtureError, match="no cases"):
            load_fixture_cases("artemis", fixtures_dir=tmp_path)
