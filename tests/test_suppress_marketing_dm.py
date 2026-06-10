"""Tests for marketing-gate owner DM suppression.

Validates the interim fix that prevents marketing approval cards from
landing in the integration owner's personal Artemis DM when the gate
already posts to the shared marketing channel.

Coverage:
1. Marketing gate (posts to channel) — owner DM suppressed, channel post sent,
   non-owner approver DM still sent.
2. Non-marketing gate (no channel configured) — DM is sent normally; no channel
   post.
3. Marketing gate — owner identity from SLACK_AUTHED_USER_ID env var suppresses
   DM when it matches the approver's resolved Slack ID.
4. Marketing gate — non-matching Slack ID does not suppress a non-owner DM.
5. Marketing gate with only-owner approver — channel post still fires.
6. Pure-unit: _marketing_owner_slack_id() fallback constant and env-var
   precedence.

All tests are pure unit tests — no real database or Slack token required.
The integration tests exercise execute_human_gate_node() by mocking all DB and
Slack I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── constants used across tests ───────────────────────────────────────────────

_OWNER_EMAIL = "jon.fila@amiralearning.com"
_OWNER_SLACK_ID = "U09F3EPJXSQ"
_NON_OWNER_EMAIL = "callie@amiralearning.com"
_NON_OWNER_SLACK_ID = "U_CALLIE"
_MARKETING_CHANNEL = "C_MARKETING"
_RUN_ID = "run-test-001"
_NODE_ID = "gate-marketing"
_PIPELINE = "test-pipeline"
_NODE_LABEL = "Marketing Review"


# ── helpers ───────────────────────────────────────────────────────────────────


def _node(kind: str, approvers: list[str]) -> dict[str, Any]:
    return {
        "id": _NODE_ID,
        "type": "human_gate",
        "label": _NODE_LABEL,
        "config": {
            "approval_kind": kind,
            "approvers": approvers,
            "timeout_hours": 72,
            "on_timeout": "auto_approve",
        },
    }


def _settings_for_marketing(*, override: str = "") -> MagicMock:
    s = MagicMock()
    s.app_base_url = "https://artemis.example.com"
    s.approval_notify_override = override
    s.marketing_campaigns_slack_channel = _MARKETING_CHANNEL
    return s


def _settings_for_non_marketing() -> MagicMock:
    s = MagicMock()
    s.app_base_url = "https://artemis.example.com"
    s.approval_notify_override = ""
    s.marketing_campaigns_slack_channel = ""
    return s


def _make_session_mock() -> AsyncMock:
    """Minimal async SQLAlchemy session mock.

    execute() returns a result whose scalar_one_or_none() returns None
    (simulating no pre-existing approval row).
    """
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


async def _run_gate(
    *,
    approvers: list[str],
    kind: str,
    fake_settings: MagicMock,
    slack_id_map: dict[str, str],
    env_authed_user_id: str = "",
) -> dict[str, Any]:
    """Call execute_human_gate_node with heavy mocking; return result dict.

    All DB and Slack I/O is mocked; no real connection needed.

    ``kind`` should be ``signal_brief`` or ``campaign_initiation`` for simple
    cases — the ``content_draft`` path has extra validation (candidate_id) that
    would require additional mocking.
    """
    from artemis.pipelines.node_executors.human_gate_executor import execute_human_gate_node

    node = _node(kind, approvers)
    session = _make_session_mock()

    # A minimal stand-in for an Approval ORM instance.
    approval_instance = MagicMock()
    approval_instance.id = 42

    # ---------- stub fakes -----------------------------------------------

    async def _fake_token(_session: Any) -> str:
        return "xoxb-fake-token"

    async def _fake_lookup(email: str, token: str) -> str | None:
        return slack_id_map.get(email)

    async def _fake_dm(**kwargs: Any) -> dict[str, Any]:
        return {
            "email": kwargs["email"],
            "sent_at": datetime.now(UTC).isoformat(),
            "channel": f"DM_{kwargs['email']}",
            "error": None,
            "fallback": False,
        }

    async def _fake_post_channel(**kwargs: Any) -> dict[str, Any]:
        return {
            "target": "channel",
            "channel": _MARKETING_CHANNEL,
            "sent_at": datetime.now(UTC).isoformat(),
            "error": None,
            "fallback": False,
        }

    # sqlalchemy.select() validates that its argument is an ORM-mapped class,
    # which breaks when we pass a MagicMock.  We patch select() so it returns
    # a dummy value that session.execute() (also mocked) will accept silently.
    dummy_stmt = MagicMock()

    with (
        # SQLAlchemy: short-circuit select() validation
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.select",
            return_value=dummy_stmt,
            create=True,
        ),
        # SQLAlchemy: Approval class used inside the function (lazy import)
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.Approval",
            return_value=approval_instance,
            create=True,
        ),
        # Slack / token helpers
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=_fake_token,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._lookup_slack_user_id",
            new=_fake_lookup,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._send_approval_dm",
            new=_fake_dm,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._post_approval_to_channel",
            new=_fake_post_channel,
        ),
        # Gate infrastructure
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._build_pipe4_context",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._check_fan_in",
            return_value=True,
        ),
        # Settings
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.settings",
            fake_settings,
        ),
        # Env var for owner identity
        patch.dict(
            "os.environ",
            {"SLACK_AUTHED_USER_ID": env_authed_user_id},
            clear=False,
        ),
    ):
        return await execute_human_gate_node(
            node=node,
            node_states={},
            all_nodes=[node],
            all_edges=[],
            session=session,
            run_id=_RUN_ID,
            pipeline_name=_PIPELINE,
        )


# ── 1. Marketing gate: owner DM suppressed, channel posted, non-owner DMed ───


@pytest.mark.asyncio
async def test_marketing_gate_owner_dm_suppressed_channel_posted() -> None:
    """The owner's DM is suppressed; channel post fires; non-owner approver gets DM."""
    result = await _run_gate(
        approvers=[_OWNER_EMAIL, _NON_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={
            _OWNER_EMAIL: _OWNER_SLACK_ID,
            _NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID,
        },
    )

    assert result["status"] == "suspended"
    log: list[dict[str, Any]] = result["delivery_log"]

    # Owner entry: suppressed, no DM channel
    owner_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert len(owner_entries) == 1
    assert owner_entries[0]["suppressed"] is True
    assert owner_entries[0]["suppression_reason"] == "marketing_gate_owner_dm_suppressed"
    assert owner_entries[0]["channel"] is None

    # Non-owner entry: got a real DM
    non_owner_entries = [e for e in log if e.get("email") == _NON_OWNER_EMAIL]
    assert len(non_owner_entries) == 1
    assert not non_owner_entries[0].get("suppressed")
    assert non_owner_entries[0]["channel"] is not None

    # Channel post present exactly once
    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert len(channel_entries) == 1
    assert channel_entries[0]["channel"] == _MARKETING_CHANNEL


# ── 2. Non-marketing gate: DM sent normally, no channel post ─────────────────


@pytest.mark.asyncio
async def test_non_marketing_gate_dm_sent_unchanged() -> None:
    """No marketing channel configured → owner DM goes through, no channel post."""
    result = await _run_gate(
        approvers=[_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_non_marketing(),
        slack_id_map={_OWNER_EMAIL: _OWNER_SLACK_ID},
    )

    assert result["status"] == "suspended"
    log: list[dict[str, Any]] = result["delivery_log"]

    # DM was sent, no suppression
    dm_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert len(dm_entries) == 1
    assert not dm_entries[0].get("suppressed")
    assert dm_entries[0]["channel"] is not None

    # No channel post
    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert channel_entries == []


# ── 3. Owner identity from SLACK_AUTHED_USER_ID env var suppresses DM ────────


@pytest.mark.asyncio
async def test_marketing_gate_owner_id_from_env_suppresses_dm() -> None:
    """SLACK_AUTHED_USER_ID that matches the approver's Slack ID suppresses the DM."""
    custom_owner_id = "U_ENV_OWNER"

    result = await _run_gate(
        approvers=[_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_OWNER_EMAIL: custom_owner_id},
        env_authed_user_id=custom_owner_id,
    )

    log: list[dict[str, Any]] = result["delivery_log"]
    owner_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert len(owner_entries) == 1
    assert owner_entries[0]["suppressed"] is True, "DM should be suppressed via env owner ID"


# ── 4. Non-matching Slack ID does not suppress ───────────────────────────────


@pytest.mark.asyncio
async def test_marketing_gate_non_matching_id_does_not_suppress() -> None:
    """A non-owner approver whose Slack ID differs from the owner is not suppressed."""
    result = await _run_gate(
        approvers=[_NON_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID},
        env_authed_user_id="",  # falls back to U09F3EPJXSQ
    )

    log: list[dict[str, Any]] = result["delivery_log"]
    entries = [e for e in log if e.get("email") == _NON_OWNER_EMAIL]
    assert len(entries) == 1
    assert not entries[0].get("suppressed"), "Non-owner DM must not be suppressed"
    assert entries[0]["channel"] is not None


# ── 5. Only-owner approver: channel still posts ───────────────────────────────


@pytest.mark.asyncio
async def test_marketing_gate_only_owner_approver_channel_still_posts() -> None:
    """When only the owner is listed, the DM is suppressed but the channel post fires."""
    result = await _run_gate(
        approvers=[_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_OWNER_EMAIL: _OWNER_SLACK_ID},
    )

    assert result["status"] == "suspended"
    log: list[dict[str, Any]] = result["delivery_log"]

    owner_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert owner_entries[0]["suppressed"] is True

    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert len(channel_entries) == 1
    assert channel_entries[0]["channel"] == _MARKETING_CHANNEL


# ── 6. Pure-unit: _marketing_owner_slack_id() ────────────────────────────────


def test_marketing_owner_slack_id_fallback() -> None:
    """Returns the hard-coded fallback when SLACK_AUTHED_USER_ID is absent."""
    from artemis.pipelines.node_executors.human_gate_executor import (
        _MARKETING_OWNER_SLACK_ID_FALLBACK,
        _marketing_owner_slack_id,
    )

    with patch.dict("os.environ", {"SLACK_AUTHED_USER_ID": ""}, clear=False):
        result = _marketing_owner_slack_id()

    assert result == _MARKETING_OWNER_SLACK_ID_FALLBACK
    assert result == "U09F3EPJXSQ"


def test_marketing_owner_slack_id_env_wins() -> None:
    """Returns the env var value when SLACK_AUTHED_USER_ID is set."""
    from artemis.pipelines.node_executors.human_gate_executor import _marketing_owner_slack_id

    with patch.dict("os.environ", {"SLACK_AUTHED_USER_ID": "U_FROM_ENV"}, clear=False):
        result = _marketing_owner_slack_id()

    assert result == "U_FROM_ENV"
