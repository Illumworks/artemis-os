"""Tests for Floating Artemis builders tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.builders import (
    _list_agents,
    _list_chains,
    _list_dags,
    _list_skills,
    _list_workflows,
    _propose_agent,
    _propose_skill,
    _propose_workflow,
    _run_agent,
    _run_workflow,
    register_builders_tools,
)

pytestmark = pytest.mark.asyncio


def _mock_session_cm() -> tuple[AsyncMock, MagicMock]:
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return session, cm


# ── register_builders_tools ───────────────────────────────────────────────────


def test_register_builders_tools() -> None:
    reg = AuthorizedToolRegistry()
    register_builders_tools(reg)
    expected_tools = {
        "list_agents",
        "list_workflows",
        "list_skills",
        "list_chains",
        "list_dags",
        "run_agent",
        "run_workflow",
        "propose_agent",
        "propose_workflow",
        "propose_skill",
    }
    registered = {e.tool.name for e in reg.all_entries()}
    assert expected_tools == registered


def test_builders_tool_layers() -> None:
    reg = AuthorizedToolRegistry()
    register_builders_tools(reg)
    # Layer 1: list operations
    for name in ["list_agents", "list_workflows", "list_skills", "list_chains", "list_dags"]:
        e = reg.get(name)
        assert e is not None and e.layer == 1, f"{name} should be layer 1"
    # Layer 2: run operations
    for name in ["run_agent", "run_workflow"]:
        e = reg.get(name)
        assert e is not None and e.layer == 2, f"{name} should be layer 2"
    # Layer 3: propose operations
    for name in ["propose_agent", "propose_workflow", "propose_skill"]:
        e = reg.get(name)
        assert e is not None and e.layer == 3, f"{name} should be layer 3"


# ── propose_agent ─────────────────────────────────────────────────────────────


async def test_propose_agent_generates_valid_proposal() -> None:
    session, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 11})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_agent",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_agent(
            {
                "name": "Scout Monitor",
                "description": "Monitors scout runs",
                "goal": "Alert on scout failures",
                "system_prompt": "You watch scout runs and alert on failures.",
            }
        )

    data = json.loads(result.split("\n", 1)[1])  # skip the "Agent proposal ready..." line
    assert data["type"] == "agent_proposal"
    assert data["name"] == "Scout Monitor"
    assert data["agent_id"] == "scout-monitor"
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "agent"
    assert mock_create.await_args.kwargs["target_id"] is None
    assert mock_create.await_args.kwargs["proposed_definition"]["agent_id"] == "scout-monitor"
    session.commit.assert_awaited_once()


async def test_propose_agent_custom_agent_id() -> None:
    _, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 12})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_agent",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ),
    ):
        result = await _propose_agent(
            {
                "name": "My Agent",
                "agent_id": "custom-id",
            }
        )

    data = json.loads(result.split("\n", 1)[1])
    assert data["agent_id"] == "custom-id"


async def test_propose_agent_missing_name() -> None:
    result = await _propose_agent({})
    assert "Error" in result or "required" in result.lower()


async def test_propose_agent_default_model() -> None:
    _, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 13})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_agent",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ),
    ):
        result = await _propose_agent({"name": "Test"})

    data = json.loads(result.split("\n", 1)[1])
    assert data["model"] == "claude-sonnet-4-6"


async def test_propose_agent_custom_tools() -> None:
    _, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 14})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_agent",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ),
    ):
        result = await _propose_agent(
            {
                "name": "Tool User",
                "tools": ["tool_a", "tool_b"],
            }
        )

    data = json.loads(result.split("\n", 1)[1])
    assert data["tools"] == ["tool_a", "tool_b"]


# ── propose_workflow ──────────────────────────────────────────────────────────


async def test_propose_workflow_generates_proposal() -> None:
    _, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 21})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_workflow",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_workflow(
            {
                "name": "Daily Report",
                "steps": [{"agent_id": "reporter", "prompt": "Generate daily report"}],
            }
        )

    data = json.loads(result.split("\n", 1)[1])
    assert data["type"] == "workflow_proposal"
    assert data["name"] == "Daily Report"
    assert data["workflow_id"] == "daily-report"
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "workflow"
    assert (
        mock_create.await_args.kwargs["proposed_definition"]["steps"][0]["agent_id"] == "reporter"
    )


async def test_propose_workflow_missing_name() -> None:
    result = await _propose_workflow({})
    assert "Error" in result or "required" in result.lower()


# ── propose_skill ─────────────────────────────────────────────────────────────


async def test_propose_skill_generates_proposal() -> None:
    _, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 31})()

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_skill",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_skill(
            {
                "name": "Summarize Page",
                "description": "Summarizes the current page",
                "prompt": "Summarize the content on this page in 3 sentences.",
            }
        )

    data = json.loads(result.split("\n", 1)[1])
    assert data["type"] == "skill_proposal"
    assert data["skill_id"] == "summarize-page"
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "skill"
    assert mock_create.await_args.kwargs["proposed_definition"]["name"] == "Summarize Page"


async def test_propose_workflow_persists_definition_proposal() -> None:
    session, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 41})()
    workflow_name = "FA Persisted Workflow"

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_workflow",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_workflow({"name": workflow_name, "steps": [{"name": "step-1"}]})

    assert "proposal_id=41" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "workflow"
    assert mock_create.await_args.kwargs["proposed_by"] == "user"
    assert mock_create.await_args.kwargs["target_id"] is None
    assert mock_create.await_args.kwargs["proposed_definition"]["name"] == workflow_name
    assert mock_create.await_args.kwargs["citations"] == {
        "source": "floating_artemis",
        "tool": "propose_workflow",
    }
    session.commit.assert_awaited_once()


async def test_propose_skill_persists_definition_proposal() -> None:
    session, cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 42})()
    skill_name = "FA Persisted Skill"

    with (
        patch("artemis.db.SessionLocal", return_value=cm),
        patch(
            "artemis.builders.repository.get_skill",
            new=AsyncMock(side_effect=ValueError("not found")),
        ),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_skill({"name": skill_name, "prompt": "Summarize this page."})

    assert "proposal_id=42" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "skill"
    assert mock_create.await_args.kwargs["proposed_by"] == "user"
    assert mock_create.await_args.kwargs["proposed_definition"]["name"] == skill_name
    assert mock_create.await_args.kwargs["proposed_definition"]["prompt"] == "Summarize this page."
    assert mock_create.await_args.kwargs["citations"] == {
        "source": "floating_artemis",
        "tool": "propose_skill",
    }
    session.commit.assert_awaited_once()


# ── list_agents (with mocked DB) ──────────────────────────────────────────────


async def test_list_agents_no_db() -> None:
    """list_agents should return graceful message on DB failure."""
    with patch("artemis.builders.repository.list_agents", side_effect=Exception("no db")):
        result = await _list_agents({})
    assert "failed" in result.lower() or "No agents" in result


async def test_list_workflows_no_db() -> None:
    with patch("artemis.db.SessionLocal") as mock_db:
        mock_db.SessionLocal.return_value.__aenter__ = AsyncMock(side_effect=Exception("no db"))
        result = await _list_workflows({})
    assert "failed" in result.lower() or "No workflows" in result


async def test_list_skills_no_db() -> None:
    with patch("artemis.db.SessionLocal") as mock_db:
        mock_db.SessionLocal.return_value.__aenter__ = AsyncMock(side_effect=Exception("no db"))
        result = await _list_skills({})
    assert "failed" in result.lower() or "No skills" in result


async def test_list_chains_no_db() -> None:
    with patch("artemis.db.SessionLocal") as mock_db:
        mock_db.SessionLocal.return_value.__aenter__ = AsyncMock(side_effect=Exception("no db"))
        result = await _list_chains({})
    assert "failed" in result.lower() or "No chains" in result


async def test_list_dags_no_db() -> None:
    with patch("artemis.db.SessionLocal") as mock_db:
        mock_db.SessionLocal.return_value.__aenter__ = AsyncMock(side_effect=Exception("no db"))
        result = await _list_dags({})
    assert "failed" in result.lower() or "No DAGs" in result


async def test_run_agent_missing_args() -> None:
    result = await _run_agent({})
    assert "required" in result.lower() or "Error" in result


async def test_run_workflow_missing_workflow_id() -> None:
    result = await _run_workflow({})
    assert "required" in result.lower() or "Error" in result
