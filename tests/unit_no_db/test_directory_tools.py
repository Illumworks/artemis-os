"""Unit tests for the resolve_person tool (directory_tools.py) — no DB required.

Covers the tool-layer JSON shape (ambiguous / candidates / note), that
``participants`` is threaded from registration into the resolver call as a
closure (mirroring how speaker_id is bound for Kai/Callie's gated tools), and
that the tool never claims a confident single answer when the underlying
resolver reports an ambiguous pool. Resolver-level scoring (the actual
Josh/Joshua fix) is covered against a real Postgres session in
tests/test_directory.py; here the resolver is mocked so these tests need no DB.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("ARTEMIS_DB_URL", "postgresql+asyncpg://test:test@localhost/test_unit")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-real")
os.environ.setdefault("FERNET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

from artemis.directory.resolver import DirectoryMatch
from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.directory_tools import (
    RESOLVE_PERSON,
    register_directory_tools,
)

pytestmark = pytest.mark.asyncio


def _fake_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _get_impl(participants: list[str] | None = None) -> Any:
    registry = AuthorizedToolRegistry()
    register_directory_tools(registry, participants=participants)
    entry = registry.get(RESOLVE_PERSON.name)
    assert entry is not None
    return entry.impl


async def test_ambiguous_pool_returns_all_candidates_flagged_no_winner() -> None:
    """Two tied 'Josh' candidates: ambiguous=true, both present, no note omitted."""
    matches = [
        DirectoryMatch(
            email="josh.smith@amiralearning.com",
            full_name="Josh Smith",
            confidence=0.40,
            reason="ambiguous",
        ),
        DirectoryMatch(
            email="joshua.mukai@amiralearning.com",
            full_name="Joshua Mukai",
            confidence=0.40,
            reason="ambiguous",
        ),
    ]
    impl = _get_impl()
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch(
            "artemis.directory.resolver.resolve_people",
            new=AsyncMock(return_value=matches),
        ),
    ):
        result = await impl({"name": "Josh"})

    data = json.loads(result)
    assert data["ambiguous"] is True
    assert {c["email"] for c in data["candidates"]} == {
        "josh.smith@amiralearning.com",
        "joshua.mukai@amiralearning.com",
    }
    # No candidate reads as a confident single winner.
    confidences = {c["confidence"] for c in data["candidates"]}
    assert all(c < 0.9 for c in confidences)
    assert "note" in data
    assert "Josh Smith" in data["note"]
    assert "Joshua Mukai" in data["note"]


async def test_confident_match_has_no_ambiguity_note() -> None:
    """A single clean match (e.g. full name/email) reports ambiguous=false, no note."""
    matches = [
        DirectoryMatch(
            email="josh.smith@amiralearning.com",
            full_name="Josh Smith",
            confidence=0.95,
            reason="first + last",
        )
    ]
    impl = _get_impl()
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch(
            "artemis.directory.resolver.resolve_people",
            new=AsyncMock(return_value=matches),
        ),
    ):
        result = await impl({"name": "Josh Smith"})

    data = json.loads(result)
    assert data["ambiguous"] is False
    assert "note" not in data
    assert data["candidates"][0]["email"] == "josh.smith@amiralearning.com"
    assert data["candidates"][0]["confidence"] == 0.95


async def test_participants_bound_at_registration_reach_resolver() -> None:
    """participants passed to register_directory_tools must reach resolve_people —
    the same closure-binding pattern used for speaker_id on Kai/Callie's tools.
    """
    fake_resolve = AsyncMock(return_value=[])
    impl = _get_impl(participants=["Josh Mukai", "Jon Fila"])
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch("artemis.directory.resolver.resolve_people", new=fake_resolve),
    ):
        await impl({"name": "Josh"})

    fake_resolve.assert_awaited_once()
    _, kwargs = fake_resolve.call_args
    assert kwargs.get("participants") == ["Josh Mukai", "Jon Fila"]


async def test_no_participants_still_works() -> None:
    """Omitting participants at registration must not break the tool (default None)."""
    fake_resolve = AsyncMock(return_value=[])
    impl = _get_impl(participants=None)
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch("artemis.directory.resolver.resolve_people", new=fake_resolve),
    ):
        result = await impl({"name": "Nobody"})

    fake_resolve.assert_awaited_once()
    _, kwargs = fake_resolve.call_args
    assert kwargs.get("participants") is None
    data = json.loads(result)
    assert data == {"ambiguous": False, "candidates": []}


async def test_missing_query_is_an_error_not_empty_success() -> None:
    impl = _get_impl()
    result = await impl({})
    assert result.startswith("Error:")


async def test_resolver_exception_reports_failure_not_silence() -> None:
    """A broken resolver call must say so, never come back looking like a
    confident (or confidently empty) answer -- see CLAUDE.md's "an agent
    saying it did something is not evidence it did" / dispatch_research
    lesson: a tool must never let a failure path read like success.
    """
    impl = _get_impl()
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch(
            "artemis.directory.resolver.resolve_people",
            new=AsyncMock(side_effect=RuntimeError("db exploded")),
        ),
    ):
        result = await impl({"name": "Josh"})

    assert "failed" in result.lower()
    assert "db exploded" in result


async def test_participant_resolved_winner_is_not_flagged_ambiguous() -> None:
    """Regression: a participant hint can promote exactly one tied candidate
    to a decisive winner (resolve_people sorts it first). The top-level
    ``ambiguous`` flag must follow the TOP candidate, not "does ANY candidate
    in the list still say ambiguous" -- the runner-up (a same-named stranger
    elsewhere in the company) stays visible in ``candidates`` at low
    confidence, but must not make the overall answer read as unresolved.
    """
    matches = [
        DirectoryMatch(
            email="joshua.mukai@amiralearning.com",
            full_name="Joshua Mukai",
            confidence=0.93,
            reason="resolved via conversation participants",
            in_conversation=True,
        ),
        DirectoryMatch(
            email="josh.smith@amiralearning.com",
            full_name="Josh Smith",
            confidence=0.40,
            reason="ambiguous",
            in_conversation=False,
        ),
    ]
    impl = _get_impl(participants=["Josh Mukai"])
    with (
        patch("artemis.db.SessionLocal", return_value=_fake_session()),
        patch(
            "artemis.directory.resolver.resolve_people",
            new=AsyncMock(return_value=matches),
        ),
    ):
        result = await impl({"name": "Josh"})

    data = json.loads(result)
    assert data["ambiguous"] is False
    assert "note" not in data
    # The runner-up is still visible for context, just not blocking the answer.
    assert len(data["candidates"]) == 2
    assert data["candidates"][0]["email"] == "joshua.mukai@amiralearning.com"
    assert data["candidates"][0]["in_conversation"] is True


async def test_tool_description_warns_against_authorization_use() -> None:
    """Defense in depth: the tool's own description must tell the model this
    is never a valid basis for an authorization decision (see module
    docstring's SECURITY section and callie_dm.py's identity pattern).
    """
    desc = RESOLVE_PERSON.description.lower()
    assert "authoriz" in desc
    assert "never" in desc
