"""Tests for C3b — marketing Gate cards post via Callie's bot token.

Coverage:
1. DB-backed: _get_slack_token_for_agent() with an artemis row returns Artemis token.
2. DB-backed: _get_slack_token_for_agent() with callie agent_id returns Callie token.
3. DB-backed: _get_slack_token_for_agent("callie") falls back to first active Slack row
   when no Callie row exists (non-marketing environments stay functional).
4. Integration: marketing gate (signal_brief) → channel card uses Callie's token,
   DMs use Artemis token.
5. Integration: non-marketing gate → Artemis token used everywhere (no channel post).
6. Integration: content_draft gate → channel post uses Callie's token (it's marketing).
7. Integration: campaign_initiation gate → channel post uses Callie's token.
8. Integration: owner-DM suppression still fires for marketing gates (QW1 regression guard).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB setup ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text("TRUNCATE integrations RESTART IDENTITY CASCADE")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────────

_ARTEMIS_TOKEN = "xoxb-artemis-token"
_CALLIE_TOKEN = "xoxb-callie-token"
_WORKSPACE_ID = "T_TEST"

_OWNER_EMAIL = "jon.fila@amiralearning.com"
_OWNER_SLACK_ID = "U09F3EPJXSQ"
_NON_OWNER_EMAIL = "teammate@amiralearning.com"
_NON_OWNER_SLACK_ID = "U_TEAMMATE"
_MARKETING_CHANNEL = "C_MARKETING"
_RUN_ID = "run-c3b-001"
_NODE_ID = "gate-c3b"
_PIPELINE = "test-c3b-pipeline"


async def _seed_artemis_integration(session: AsyncSession) -> None:
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    creds = encrypt_credentials({"bot_token": _ARTEMIS_TOKEN})
    await repo.upsert_integration(
        session,
        provider="slack",
        workspace_id=_WORKSPACE_ID,
        agent_id="artemis",
        encrypted_credentials=creds,
        display_name="Artemis Test Workspace",
    )
    await session.commit()


async def _seed_callie_integration(session: AsyncSession) -> None:
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    creds = encrypt_credentials({"bot_token": _CALLIE_TOKEN})
    await repo.upsert_integration(
        session,
        provider="slack",
        workspace_id=_WORKSPACE_ID,
        agent_id="callie",
        encrypted_credentials=creds,
        display_name="Callie Test Workspace",
    )
    await session.commit()


def _node(kind: str, approvers: list[str]) -> dict[str, Any]:
    return {
        "id": _NODE_ID,
        "type": "human_gate",
        "label": "Marketing Review",
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


# ── 1. DB: _get_slack_token_for_agent("artemis") returns Artemis token ─────────


async def test_get_slack_token_for_agent_artemis(db_session: AsyncSession) -> None:
    """agent_id='artemis' returns the Artemis bot token."""
    from artemis.pipelines.node_executors.human_gate_executor import _get_slack_token_for_agent

    await _seed_artemis_integration(db_session)
    token = await _get_slack_token_for_agent(db_session, agent_id="artemis")
    assert token == _ARTEMIS_TOKEN


# ── 2. DB: _get_slack_token_for_agent("callie") returns Callie token ──────────


async def test_get_slack_token_for_agent_callie(db_session: AsyncSession) -> None:
    """agent_id='callie' returns Callie's bot token when the row exists."""
    from artemis.pipelines.node_executors.human_gate_executor import _get_slack_token_for_agent

    await _seed_artemis_integration(db_session)
    await _seed_callie_integration(db_session)
    token = await _get_slack_token_for_agent(db_session, agent_id="callie")
    assert token == _CALLIE_TOKEN


# ── 3. DB: falls back to any active Slack row when callie row absent ───────────


async def test_get_slack_token_for_agent_callie_fallback_to_artemis(
    db_session: AsyncSession,
) -> None:
    """When no callie row exists, falls back to the first active Slack row (Artemis)."""
    from artemis.pipelines.node_executors.human_gate_executor import _get_slack_token_for_agent

    await _seed_artemis_integration(db_session)
    # No Callie row — should not raise; falls back to Artemis.
    token = await _get_slack_token_for_agent(db_session, agent_id="callie")
    assert token == _ARTEMIS_TOKEN


# ── Shared integration-test runner ────────────────────────────────────────────


async def _run_gate(
    *,
    approvers: list[str],
    kind: str,
    fake_settings: MagicMock,
    slack_id_map: dict[str, str],
    artemis_token: str = _ARTEMIS_TOKEN,
    callie_token: str | None = _CALLIE_TOKEN,
    env_authed_user_id: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Execute a gate and return (result_dict, channel_post_tokens).

    ``channel_post_tokens`` collects the ``token`` kwarg passed to each
    ``_post_approval_to_channel`` call so tests can assert Callie vs Artemis.
    """
    from artemis.pipelines.node_executors.human_gate_executor import execute_human_gate_node

    node = _node(kind, approvers)
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()
    session.add = MagicMock()

    approval_instance = MagicMock()
    approval_instance.id = 99

    channel_post_tokens: list[str] = []

    # Token lookup: artemis → artemis_token, callie → callie_token (or fallback)
    async def _fake_token_for_agent(
        _session: Any,
        agent_id: str = "artemis",
    ) -> str | None:
        if agent_id == "callie":
            return callie_token or artemis_token
        return artemis_token

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
        channel_post_tokens.append(kwargs["token"])
        return {
            "target": "channel",
            "channel": _MARKETING_CHANNEL,
            "sent_at": datetime.now(UTC).isoformat(),
            "error": None,
            "fallback": False,
        }

    dummy_stmt = MagicMock()

    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.select",
            return_value=dummy_stmt,
            create=True,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.Approval",
            return_value=approval_instance,
            create=True,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token_for_agent",
            new=_fake_token_for_agent,
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
        patch(
            "artemis.pipelines.node_executors.human_gate_executor.settings",
            fake_settings,
        ),
        patch.dict(
            "os.environ",
            {"SLACK_AUTHED_USER_ID": env_authed_user_id},
            clear=False,
        ),
    ):
        result = await execute_human_gate_node(
            node=node,
            node_states={},
            all_nodes=[node],
            all_edges=[],
            session=session,
            run_id=_RUN_ID,
            pipeline_name=_PIPELINE,
        )

    return result, channel_post_tokens


# ── 4. Integration: marketing signal_brief → channel uses Callie token ─────────


async def test_marketing_gate_channel_post_uses_callie_token() -> None:
    """signal_brief gate channel post uses Callie's token; DMs use Artemis token."""
    result, channel_tokens = await _run_gate(
        approvers=[_NON_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID},
    )

    assert result["status"] == "suspended"
    log: list[dict[str, Any]] = result["delivery_log"]

    # Channel post should have fired once.
    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert len(channel_entries) == 1, "Expected exactly one channel post"

    # The token recorded during channel post must be Callie's.
    assert len(channel_tokens) == 1
    assert channel_tokens[0] == _CALLIE_TOKEN, (
        f"Channel post used {channel_tokens[0]!r} instead of Callie's token {_CALLIE_TOKEN!r}"
    )

    # DM entry for the non-owner goes through (and uses Artemis token, implicit via _fake_dm).
    dm_entries = [e for e in log if e.get("email") == _NON_OWNER_EMAIL]
    assert len(dm_entries) == 1
    assert not dm_entries[0].get("suppressed")


# ── 5. Non-marketing gate → Artemis token, no channel post ────────────────────


async def test_non_marketing_gate_uses_artemis_token_no_channel_post() -> None:
    """Non-marketing gate: no channel post, token fetched for agent='artemis'."""
    result, channel_tokens = await _run_gate(
        approvers=[_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_non_marketing(),
        slack_id_map={_OWNER_EMAIL: _OWNER_SLACK_ID},
    )

    assert result["status"] == "suspended"
    log: list[dict[str, Any]] = result["delivery_log"]

    # No channel post for non-marketing gates.
    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert channel_entries == [], "Non-marketing gate must not post to channel"

    # No Callie token call expected for channel.
    assert channel_tokens == []

    # DM was delivered normally.
    dm_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert len(dm_entries) == 1
    assert not dm_entries[0].get("suppressed")


# ── 6. content_draft gate → channel post uses Callie's token ──────────────────


async def test_content_draft_gate_channel_post_uses_callie_token() -> None:
    """content_draft is in _MARKETING_CHANNEL_KINDS: channel post must use Callie.

    The content_draft gate path also calls ``_content_gate_error`` which requires a
    valid ``candidate_id`` in the pipe4 context. We mock that validator to return None
    (no error) so the gate can proceed to the channel-post step under test.
    """
    with patch(
        "artemis.pipelines.node_executors.human_gate_executor._content_gate_error",
        return_value=None,
    ):
        result, channel_tokens = await _run_gate(
            approvers=[_NON_OWNER_EMAIL],
            kind="content_draft",
            fake_settings=_settings_for_marketing(),
            slack_id_map={_NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID},
        )

    assert result["status"] == "suspended"
    assert len(channel_tokens) == 1
    assert channel_tokens[0] == _CALLIE_TOKEN


# ── 7. campaign_initiation gate → channel post uses Callie's token ─────────────


async def test_campaign_initiation_gate_channel_post_uses_callie_token() -> None:
    """campaign_initiation is in _MARKETING_CHANNEL_KINDS: channel post must use Callie."""
    result, channel_tokens = await _run_gate(
        approvers=[_NON_OWNER_EMAIL],
        kind="campaign_initiation",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID},
    )

    assert result["status"] == "suspended"
    assert len(channel_tokens) == 1
    assert channel_tokens[0] == _CALLIE_TOKEN


# ── 8. QW1 regression: owner-DM still suppressed for marketing gates ──────────


async def test_marketing_gate_owner_dm_still_suppressed() -> None:
    """Owner DM suppression (QW1) must remain intact after the C3b token change."""
    result, channel_tokens = await _run_gate(
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

    # Owner entry must be suppressed.
    owner_entries = [e for e in log if e.get("email") == _OWNER_EMAIL]
    assert len(owner_entries) == 1
    assert owner_entries[0]["suppressed"] is True
    assert owner_entries[0]["suppression_reason"] == "marketing_gate_owner_dm_suppressed"

    # Non-owner DM still goes through.
    non_owner_entries = [e for e in log if e.get("email") == _NON_OWNER_EMAIL]
    assert len(non_owner_entries) == 1
    assert not non_owner_entries[0].get("suppressed")

    # Channel post fires exactly once and uses Callie's token.
    channel_entries = [e for e in log if e.get("target") == "channel"]
    assert len(channel_entries) == 1
    assert len(channel_tokens) == 1
    assert channel_tokens[0] == _CALLIE_TOKEN


# ── 9. Callie absent: channel post falls back to Artemis token ────────────────


async def test_marketing_gate_channel_post_fallback_to_artemis_when_callie_absent() -> None:
    """When Callie's integration row is absent the channel post falls back to Artemis token."""
    result, channel_tokens = await _run_gate(
        approvers=[_NON_OWNER_EMAIL],
        kind="signal_brief",
        fake_settings=_settings_for_marketing(),
        slack_id_map={_NON_OWNER_EMAIL: _NON_OWNER_SLACK_ID},
        callie_token=None,  # signals "no callie row" — fallback path
    )

    assert result["status"] == "suspended"
    assert len(channel_tokens) == 1
    # Fallback: uses Artemis token.
    assert channel_tokens[0] == _ARTEMIS_TOKEN
