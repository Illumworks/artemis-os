"""Tests for supporting Floating Artemis tool modules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.floating_artemis.tools.system import _propose_fix
from artemis.floating_artemis.tools.writing_rules import _propose_writing_rule

pytestmark = pytest.mark.asyncio


def _mock_session_cm() -> tuple[AsyncMock, MagicMock]:
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return session, cm


async def test_propose_fix_persists_definition_proposal() -> None:
    mock_session, mock_cm = _mock_session_cm()
    saved_row = type("ProposalRow", (), {"id": 51})()
    issue = "Indexer drift"

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.builder.repository.create_definition_proposal",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_fix(
            {
                "issue": issue,
                "proposed_action": "Rebuild the stale index and add a consistency check.",
                "risk": "medium",
            }
        )

    assert "proposal_id=51" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["kind"] == "automation"
    assert mock_create.await_args.kwargs["proposed_definition"]["issue"] == issue
    assert mock_create.await_args.kwargs["citations"] == {
        "source": "floating_artemis",
        "tool": "propose_fix",
    }
    mock_session.commit.assert_awaited_once()


async def test_propose_writing_rule_persists_training_candidate() -> None:
    mock_session, mock_cm = _mock_session_cm()
    saved_row = type("TrainingCandidateRow", (), {"id": 61})()
    title = "Keep CTA singular"

    with (
        patch("artemis.db.SessionLocal", return_value=mock_cm),
        patch(
            "artemis.writing_rules.repository.create_training_candidate",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
    ):
        result = await _propose_writing_rule(
            {
                "title": title,
                "rule_type": "style",
                "description": "End each email with one clear call to action.",
            }
        )

    assert "candidate_id=61" in result
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["candidate_type"] == "style"
    assert mock_create.await_args.kwargs["proposed_text"] == (
        "End each email with one clear call to action."
    )
    assert mock_create.await_args.kwargs["scope"] == {
        "title": title,
        "rule_type": "style",
    }
    mock_session.commit.assert_awaited_once()


async def test_list_writing_rules_limit_is_honored() -> None:
    with patch("artemis.writing_rules.repository.list_rules") as mock_list_rules:

        class Rule:
            def __init__(self, rid: int) -> None:
                self.id = rid
                self.rule_type = "style"
                self.title = f"Rule {rid}"

        mock_list_rules.return_value = [Rule(1), Rule(2), Rule(3)]

        from artemis.floating_artemis.tools.writing_rules import _list_writing_rules

        result = await _list_writing_rules({"limit": 2})

    assert "Rule 1" in result
    assert "Rule 2" in result
    assert "Rule 3" not in result
