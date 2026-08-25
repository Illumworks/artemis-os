"""Tests for the Slack OAuth scope-regression preflight.

Slack REPLACES a token's scopes on re-authorization instead of merging them, so
an OAuth start list that is not a superset of the live grant silently removes
working capability. The break surfaces much later as a ``missing_scope`` on an
unrelated read path with nothing pointing back at the reconnect that caused it.

These tests pin the two outcomes that must never be conflated (the lesson from
the crisis-content approval bug, CLAUDE.md): a genuine REGRESSION is a hard
refusal that names the agent and the scopes, while an UNREADABLE grant is a
lookup failure that must not masquerade as one.

Note the live grant is read from Slack, not from ``integrations.scopes`` — that
column drifts silently, which is the exact bug that let Callie's stored row read
16 scopes while her live token carried 19 (2026-08-25).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import artemis.routes.integrations as integrations_module
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration


async def _seed(session, *, agent_id: str, token: str, provider: str = "slack") -> None:
    session.add(
        Integration(
            provider=provider,
            workspace_id="T123",
            display_name=f"{agent_id} bot",
            bot_user_id="UBOT",
            encrypted_credentials=encrypt_credentials({"access_token": token}),
            scopes=["stale:value"],  # deliberately wrong — must never be consulted
            status="active",
            agent_id=agent_id,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_passes_when_requested_is_a_superset(db_session, monkeypatch) -> None:
    await _seed(db_session, agent_id="callie", token="xoxb-callie")
    monkeypatch.setattr(
        integrations_module,
        "live_scopes_for_token",
        lambda _t: _async(frozenset({"chat:write", "files:read"})),
    )

    await integrations_module.assert_no_scope_regression(
        db_session,
        provider="slack",
        requested=["chat:write", "files:read", "files:write"],
    )


@pytest.mark.asyncio
async def test_refuses_and_names_the_scope_it_would_strip(db_session, monkeypatch) -> None:
    """The refusal must be actionable: which agent, which scopes."""
    await _seed(db_session, agent_id="callie", token="xoxb-callie")
    monkeypatch.setattr(
        integrations_module,
        "live_scopes_for_token",
        lambda _t: _async(frozenset({"chat:write", "files:read", "im:history"})),
    )

    with pytest.raises(HTTPException) as excinfo:
        await integrations_module.assert_no_scope_regression(
            db_session, provider="slack", requested=["chat:write"]
        )

    detail = str(excinfo.value.detail)
    assert excinfo.value.status_code == 500
    assert "callie" in detail
    assert "files:read" in detail
    assert "im:history" in detail


@pytest.mark.asyncio
async def test_unreadable_grant_is_not_reported_as_a_regression(
    db_session, monkeypatch, caplog
) -> None:
    """A lookup failure must NOT read as "your scope list is unsafe".

    ``live_scopes_for_token`` returns None for "could not read", never an empty
    set — treating None as empty would make a transient Slack outage look like a
    total scope loss and block a legitimate reconnect.
    """
    await _seed(db_session, agent_id="ares", token="xoxb-ares")
    monkeypatch.setattr(integrations_module, "live_scopes_for_token", lambda _t: _async(None))

    with caplog.at_level("WARNING"):
        await integrations_module.assert_no_scope_regression(
            db_session, provider="slack", requested=["chat:write"]
        )

    assert "could not READ" in caplog.text
    assert "ares" in caplog.text


async def _async(value):
    return value
