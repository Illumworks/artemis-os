"""Tests for dispatch_research and the ARGUS-1 claimer (v4 claimed dispatch).

Most tests here are UNIT tests -- no DB, no network -- but this file now ALSO
contains real-Postgres tests for the atomic claim SQL (SELECT ... FOR UPDATE
SKIP LOCKED). That concurrency guarantee cannot be meaningfully asserted
against a mock: two mocked sessions never actually contend for a row the way
two real transactions do, so a test that mocked it would assert the SQL text
was written correctly, not that the database enforces the property. This
module points ``artemis.db.SessionLocal``/``engine`` at
``ARTEMIS_TEST_DB_URL`` (falling back to ``ARTEMIS_DB_URL``) at import time
and refuses to load unless that URL contains "artemis_test" -- see the guard
below and CLAUDE.md's account of this exact file writing real rows into
production before that existed.

Coverage
--------
T1 -- dispatch_research returns a 'queued' payload immediately; does NOT
      create a task and does NOT run research_district in the same turn.
T2 -- dispatch_research never schedules or invokes the research pipeline
      (ARGUS-1: there is no background task anymore to observe -- the
      absence of a call IS the assertion).
T3 -- The claimed-row pipeline (_research_and_post) calls SlackClient.post_message
      with Callie-voiced, md_to_mrkdwn-processed text that credits Argus,
      to the captured channel_id.
T4 -- A failure inside _research_and_post is swallowed by _safe_research_and_post
      (no raise propagates).
T5 -- When no channel_id can be resolved, no Slack post is attempted and
      nothing is queued.
T6 -- Thin/no findings: graceful fallback note is still posted (not silently
      dropped).
T8 -- The MCP subprocess sets floating_session_id_var itself (root cause of
      the five-week outage).

ARGUS-1 (real DB, artemis_test_b):
C1 -- _claim_next_request atomically claims a pending row.
C2 -- _claim_next_request returns None when nothing is claimable.
C3 -- Concurrent claimers never take the same row (driven with asyncio.gather
      against real, separate DB sessions -- not asserted via SQL text).
C4 -- A 'running' row older than the stale window is re-claimed, with
      attempts incremented exactly once.
C5 -- A fresh 'running' row (not stale) is never reclaimed.
C6 -- A row reclaimed at/past the attempts cap is finalized 'failed' with a
      reason recorded, WITHOUT another research attempt, and posts fallback.
C7 -- run_claim_tick claims and completes a pending row end to end (research
      -> Callie summary -> Slack post -> status='done').
C8 -- recover_pending_requests cannot double-run a row the scheduled claimer
      already holds (same in-process lock, same atomic claim).
C9 -- A failure processing one claimed row does not stop the same tick from
      processing the next claimable row.

Signal auto-resolution (coordinator addition to ARGUS-1):
S1 -- Neither signal nor signal_id supplied -> the newest qualified signal
      for district_key is looked up and persisted on the row.
S2 -- An explicit signal is used unchanged; the lookup is never called.
S3 -- An explicit signal_id is used unchanged; the lookup is never called.
S4 -- No qualified signal found -> still enqueues, with the absence recorded
      by signal staying null on the persisted row.
S5 -- _resolve_latest_qualified_signal itself, against a real DB: picks the
      newest QUALIFIED signal for the right district, never an older one, a
      different district's, or a non-qualified one.
P1 -- A DB persist failure is reported as 'failed', never 'queued'.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.argus.models  # noqa: F401 -- registers ArgusResearchRequest on Base.metadata
import artemis.db as _db_module
import artemis.marketing.models  # noqa: F401 -- registers SignalQueue on Base.metadata
from artemis.argus.models import ArgusResearchRequest
from artemis.db import attach_pgvector_codec
from artemis.marketing.models import SignalQueue

# ── DB safety guard + engine override (mirrors artemis/pipelines/tests/conftest.py) ──
# This file used to run entirely against mocks while ALSO, on several paths,
# quietly falling through to a real DB connection resolved from whatever
# ARTEMIS_DB_URL happened to be -- which, unset, is production (see
# CLAUDE.md's account of rows landing in the live argus_research_requests).
# Pinning artemis.db.SessionLocal/engine to a verified test database at import
# time, unconditionally, removes that failure mode for every test in this
# file rather than relying on each test to remember its own guard.
_DB_URL = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_DB_URL!r} is not a test database. "
        "This file's ARGUS-1 tests TRUNCATE argus_research_requests and signal_queue "
        "and exercise real Postgres row-locking. Set ARTEMIS_TEST_DB_URL=...artemis_test_b."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
_db_module.engine = _test_engine
_db_module.SessionLocal = async_sessionmaker(
    bind=_test_engine, expire_on_commit=False, class_=AsyncSession
)

_TRUNCATE_SQL = text("TRUNCATE argus_research_requests, signal_queue RESTART IDENTITY CASCADE")


@pytest.fixture(autouse=True)
async def _clean_argus_tables() -> None:
    """Truncate before every test in this file -- cheap, and every test in
    here that touches the DB at all wants a clean slate rather than state
    left over from a previous test."""
    async with _db_module.SessionLocal() as session:
        await session.execute(_TRUNCATE_SQL)
        await session.commit()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_summary(
    *,
    new_findings: int = 3,
    gap_dimensions: list[str] | None = None,
    existing_dimensions: list[str] | None = None,
    recommended_angle: str | None = "Lead with the RFP timeline",
) -> dict[str, Any]:
    return {
        "new_findings": new_findings,
        "gap_dimensions": gap_dimensions
        or ["current_vendor", "procurement_timing", "decision_makers"],
        "existing_dimensions": existing_dimensions or [],
        "recommended_angle": recommended_angle,
        "written_obs_ids": list(range(new_findings)),
    }


def _make_db_mock() -> tuple[MagicMock, AsyncMock]:
    """Return (mock_db_module, mock_session) where mock_db_module.SessionLocal()
    is an async context manager yielding mock_session."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_db = MagicMock()
    mock_db.SessionLocal.return_value = mock_ctx
    return mock_db, mock_session


async def _insert_row(
    *,
    district_key: str = "TX-001",
    channel_id: str = "C_TEST",
    team_id: str = "T_TEST",
    status: str = "pending",
    attempts: int = 0,
    claimed_at: datetime | None = None,
    signal: dict[str, Any] | None = None,
    triggering_signal_id: str | None = None,
) -> int:
    """Insert a row directly via the ORM (bypasses _insert_pending_request /
    _dispatch_research entirely) and return its id. Used to seed real-DB
    tests of the claimer, which must not depend on the tool-call path also
    being correct."""
    async with _db_module.SessionLocal() as session:
        row = ArgusResearchRequest(
            district_key=district_key,
            channel_id=channel_id,
            team_id=team_id,
            status=status,
            attempts=attempts,
            claimed_at=claimed_at,
            signal=signal,
            triggering_signal_id=triggering_signal_id,
        )
        session.add(row)
        await session.flush()
        row_id: int = row.id
        await session.commit()
    return row_id


async def _fetch_row(row_id: int) -> ArgusResearchRequest:
    async with _db_module.SessionLocal() as session:
        row = await session.get(ArgusResearchRequest, row_id)
        assert row is not None
        return row


# ── Autouse mock guard against a real INSERT via the tool-call path ───────────


@pytest.fixture(autouse=True)
def _never_insert_into_a_real_db(request: pytest.FixtureRequest) -> Any:
    """Stop ``_insert_pending_request`` reaching a real database.

    These tests mock ``floating_session_id_var`` to ``slack-callie-TABC-CABC-_``,
    which resolves to channel ``CABC``, so ``_dispatch_research`` runs its full
    happy path -- including the insert. Several of them never patched
    ``artemis.db.SessionLocal``, and that module reads ``ARTEMIS_DB_URL``, not
    ``ARTEMIS_TEST_DB_URL``. So every run wrote real rows into the PRODUCTION
    ``argus_research_requests``.

    Found 2026-08-12: three such rows were sitting in production, and because
    ``CABC`` does not exist, ``recover_pending_requests`` re-fired them on every
    app start and each attempt failed with ``channel_not_found`` -- a permanent
    retry loop seeded by a test suite.

    Autouse so a new test cannot reintroduce it by forgetting the patch. Tests
    that want to assert on the insert can request ``insert_spy``.

    (ARGUS-1 note: this module-level import-time DB override above makes this
    fixture belt-and-suspenders rather than the only thing standing between
    this file and production -- kept anyway, unchanged, per the brief's
    explicit "do not remove it".)
    """
    from unittest.mock import AsyncMock as _AsyncMock

    with patch(
        "artemis.floating_artemis.tools.argus_tools._insert_pending_request",
        new_callable=_AsyncMock,
    ) as spy:
        spy.return_value = 1
        yield spy


# ── T1: dispatch_research returns a queued payload immediately ───────────────


@pytest.mark.asyncio
async def test_dispatch_research_returns_queued_payload_immediately() -> None:
    """dispatch_research returns {"status":"queued","district":...} without
    calling research_district in the same turn. Never "dispatched" or
    "running" -- ARGUS-1 (this tool no longer starts anything)."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    research_was_called = False

    async def fake_safe_post(**kwargs: Any) -> None:
        nonlocal research_was_called
        research_was_called = True

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            new_callable=AsyncMock,
        ) as mock_safe,
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = "slack-callie-TABC-CABC-_"
        mock_safe.return_value = None

        result_str = await _dispatch_research({"district_key": "TX-001"})

    result = json.loads(result_str)
    assert result["status"] == "queued"
    assert result["district"] == "TX-001"
    assert "detail" in result
    assert not research_was_called
    mock_safe.assert_not_called()


# ── T2: dispatch_research never schedules or invokes research ─────────────────


@pytest.mark.asyncio
async def test_dispatch_research_never_runs_research_synchronously_or_in_background() -> None:
    """No call to the research pipeline happens as a side effect of
    dispatch_research -- not in-turn, not scheduled. ARGUS-1 removed the
    ``loop.create_task`` this test used to have to drain with ``asyncio.sleep(0)``
    to observe; there is nothing to drain anymore, so the absence of a call
    right after ``await`` returns is itself the proof."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            new_callable=AsyncMock,
        ) as mock_safe,
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = "slack-callie-TABC-CABC-_"

        result_str = await _dispatch_research({"district_key": "TX-010"})

    result = json.loads(result_str)
    assert result["status"] == "queued"
    # The entire point of ARGUS-1: nothing runs as a result of this call.
    mock_safe.assert_not_called()
    # Give the loop a tick anyway -- if something HAD been scheduled via
    # create_task, this would let it fire. It must still not have.
    await asyncio.sleep(0)
    mock_safe.assert_not_called()


# ── T3: background task posts Callie-voiced, Argus-credited, md_to_mrkdwn'd text ──


@pytest.mark.asyncio
async def test_background_task_posts_callie_voiced_argus_credited_message() -> None:
    """_research_and_post posts a Callie-voiced, md_to_mrkdwn-processed message
    that credits Argus, to the given channel_id.

    channel_id/team_id are resolved in-turn by _dispatch_research (before the
    row is persisted) and passed straight through here -- this function does
    not do session_id resolution itself."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []

    # Fake SlackClient
    async def fake_post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
        posted_calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1234567890.123456"}

    fake_client = MagicMock()
    fake_client.post_message = fake_post_message

    fake_agent_cfg = MagicMock()
    fake_agent_cfg.access_token = "xoxb-callie-fake-token"

    callie_text = (
        "Argus is back with findings on TX-001. "
        "The district is mid-RFP -- strongest angle is the timeline. "
        "No competitive commitments on record yet."
    )

    mock_db, mock_session = _make_db_mock()

    mock_research_district = AsyncMock(return_value=_make_summary())

    with (
        patch("artemis.db.SessionLocal", mock_db.SessionLocal),
        patch("artemis.argus.flow.research_district", mock_research_district),
        patch(
            "artemis.floating_artemis.tools.argus_tools._callie_summarize",
            new_callable=AsyncMock,
            return_value=callie_text,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=fake_agent_cfg,
        ),
    ):
        await _research_and_post(
            request_id=None,
            channel_id="C9999",
            team_id="TABC",
            district_key="TX-001",
            triggering_signal_id=None,
            signal=None,
        )

    # One post must have happened
    assert len(posted_calls) == 1, f"Expected 1 post call, got {len(posted_calls)}"
    posted = posted_calls[0]

    # Posted to the correct channel
    assert posted["channel"] == "C9999", f"Wrong channel: {posted['channel']!r}"

    # Text must be non-empty and credit Argus
    assert posted["text"].strip()
    assert "Argus" in posted["text"], f"'Argus' not found in posted text: {posted['text']!r}"


# ── T4: background task failure is swallowed ──────────────────────────────────


@pytest.mark.asyncio
async def test_background_task_swallows_failure() -> None:
    """An exception inside _research_and_post never propagates out of
    _safe_research_and_post."""
    from artemis.floating_artemis.tools.argus_tools import _safe_research_and_post

    with patch(
        "artemis.floating_artemis.tools.argus_tools._research_and_post",
        side_effect=RuntimeError("simulated research failure"),
    ):
        # Must NOT raise
        await _safe_research_and_post(
            request_id=None,
            channel_id="C9999",
            team_id="TABC",
            district_key="TX-003",
            triggering_signal_id=None,
            signal=None,
        )


# ── T5: no channel_id → no Slack post, no queue ────────────────────────────────


@pytest.mark.asyncio
async def test_no_channel_id_skips_slack_post() -> None:
    """When channel_id cannot be resolved, dispatch_research returns a failed
    payload and never persists a row or schedules research.

    channel_id/team_id resolution happens in-turn inside _dispatch_research
    (via _resolve_channel_and_team) before persistence would happen, rather
    than inside _research_and_post itself -- so this is exercised at the
    _dispatch_research level, not by passing a missing channel_id into
    _research_and_post (whose channel_id param is non-optional)."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    safe_post_was_called = False

    async def unexpected_safe_post(**kwargs: Any) -> None:
        nonlocal safe_post_was_called
        safe_post_was_called = True

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=(None, ""),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=unexpected_safe_post,
        ),
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = None

        result_str = await _dispatch_research({"district_key": "TX-004"})

    result = json.loads(result_str)
    # This assertion used to read status == "dispatched", and that passing test
    # is why the bug shipped. Nothing is persisted and nothing is started on this
    # path, so reporting success made Callie tell Jon and Josh for five weeks
    # that Argus was running while argus_research_requests stayed empty. A tool
    # must not claim work it did not do -- the agent has no way to know better.
    assert result["status"] == "failed"
    assert result["error"] == "no_channel_resolved"
    assert "NOT queued" in result["detail"]
    assert not safe_post_was_called, "Should not post when channel_id is unknown"


# ── T6: thin findings → graceful fallback note is still posted ────────────────


@pytest.mark.asyncio
async def test_thin_findings_posts_graceful_note() -> None:
    """When Argus finds nothing new, _research_and_post still posts a graceful
    note (not silent)."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []
    thin_summary = _make_summary(new_findings=0, gap_dimensions=[], recommended_angle=None)

    thin_note = (
        "Argus came back light on TX-005 -- no new material surfaced this pass. "
        "We can revisit when a stronger signal comes through."
    )

    async def fake_post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
        posted_calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1234567890.123456"}

    fake_client = MagicMock()
    fake_client.post_message = fake_post_message

    fake_agent_cfg = MagicMock()
    fake_agent_cfg.access_token = "xoxb-callie-fake-token"

    mock_db, _ = _make_db_mock()
    mock_research_district = AsyncMock(return_value=thin_summary)

    with (
        patch("artemis.db.SessionLocal", mock_db.SessionLocal),
        patch("artemis.argus.flow.research_district", mock_research_district),
        patch(
            "artemis.floating_artemis.tools.argus_tools._callie_summarize",
            new_callable=AsyncMock,
            return_value=thin_note,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=fake_agent_cfg,
        ),
    ):
        await _research_and_post(
            request_id=None,
            channel_id="C5555",
            team_id="TABC",
            district_key="TX-005",
            triggering_signal_id=None,
            signal=None,
        )

    # A post MUST happen even for thin results
    assert len(posted_calls) == 1, "Expected a graceful fallback post even for thin findings"
    assert posted_calls[0]["channel"] == "C5555"
    assert posted_calls[0]["text"].strip()


# ── T8: the MCP subprocess must set the session contextvar it cannot inherit ──


def test_mcp_subprocess_sets_floating_session_contextvar() -> None:
    """``_serve_floating_artemis`` must set ``floating_session_id_var`` itself.

    The root cause of the five-week Argus outage (2026-08-12). The parent turn
    handler sets that contextvar in ITS process; ``_serve_floating_artemis`` runs
    in a subprocess, and contextvars do not cross a process boundary. So every
    tool reading it got None regardless of what the parent did --
    ``dispatch_research`` resolved no channel, took its early return, and
    persisted nothing while reporting "dispatched".

    Asserted against the source rather than by booting a subprocess: the failure
    mode is the ABSENCE of a call, and a mocked-out subprocess would not have
    caught it either (the original had full test coverage and a test that
    asserted the wrong contract). If this is refactored, keep an assertion that
    the value reaches a tool, not merely that this line exists.
    """
    import inspect

    from artemis.tools import mcp_server

    source = inspect.getsource(mcp_server._serve_floating_artemis)
    assert "floating_session_id_var.set(floating_session_id)" in source, (
        "the MCP subprocess must set floating_session_id_var -- it cannot "
        "inherit it from the parent process"
    )
    assert "floating_trusted_agent_id_var.set(trusted_agent_id)" in source


# ═══════════════════════════════════════════════════════════════════════════
# ARGUS-1: the claimer, against a real DB (artemis_test_b)
# ═══════════════════════════════════════════════════════════════════════════


# ── C1/C2: basic claim ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_next_request_claims_pending_row() -> None:
    from artemis.floating_artemis.tools.argus_tools import _claim_next_request

    row_id = await _insert_row(district_key="TX-100", signal={"headline": "h"})

    claimed = await _claim_next_request()

    assert claimed is not None
    assert claimed.id == row_id
    assert claimed.district_key == "TX-100"
    assert claimed.attempts == 0  # a fresh pending claim never bumps attempts

    row = await _fetch_row(row_id)
    assert row.status == "running"
    assert row.claimed_at is not None


@pytest.mark.asyncio
async def test_claim_next_request_returns_none_when_nothing_claimable() -> None:
    from artemis.floating_artemis.tools.argus_tools import _claim_next_request

    assert await _claim_next_request() is None


# ── C3: concurrent claimers never take the same row ───────────────────────────


@pytest.mark.asyncio
async def test_concurrent_claims_never_take_the_same_row() -> None:
    """Fire more concurrent claim attempts than there are pending rows, over
    real, separate DB sessions/transactions. SKIP LOCKED is a Postgres row
    lock, not an in-process one -- this is what actually proves the
    guarantee; asserting the SQL text would not."""
    from artemis.floating_artemis.tools.argus_tools import _claim_next_request

    n_rows = 6
    row_ids = [await _insert_row(district_key=f"TX-{i:03d}") for i in range(n_rows)]

    n_claimers = n_rows + 3
    results = await asyncio.gather(*[_claim_next_request() for _ in range(n_claimers)])

    claimed_ids = [r.id for r in results if r is not None]
    none_count = sum(1 for r in results if r is None)

    assert sorted(claimed_ids) == sorted(row_ids), "every row should be claimed exactly once"
    assert len(claimed_ids) == len(set(claimed_ids)), "no row was claimed twice"
    assert none_count == n_claimers - n_rows


# ── C4/C5: stale reclaim ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_running_row_is_reclaimed_with_attempts_incremented() -> None:
    from artemis.floating_artemis.tools.argus_tools import _claim_next_request

    stale_claimed_at = datetime.now(UTC) - timedelta(minutes=999)
    row_id = await _insert_row(
        district_key="TX-200", status="running", claimed_at=stale_claimed_at, attempts=0
    )

    reclaimed = await _claim_next_request()

    assert reclaimed is not None
    assert reclaimed.id == row_id
    assert reclaimed.attempts == 1, "a stale-running reclaim must bump attempts exactly once"

    row = await _fetch_row(row_id)
    assert row.status == "running"
    assert row.claimed_at is not None
    assert row.claimed_at > stale_claimed_at.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_fresh_running_row_is_not_reclaimed() -> None:
    from artemis.floating_artemis.tools.argus_tools import _claim_next_request

    fresh_claimed_at = datetime.now(UTC) - timedelta(seconds=5)
    await _insert_row(district_key="TX-201", status="running", claimed_at=fresh_claimed_at)

    assert await _claim_next_request() is None


# ── C6: attempts cap on reclaim -> failed, no re-research, fallback posted ────


@pytest.mark.asyncio
async def test_reclaimed_row_at_attempts_cap_is_finalized_failed_with_reason() -> None:
    """A row reclaimed at/past _MAX_ATTEMPTS is finalized 'failed' with a
    reason recorded -- and never gets another research attempt."""
    from artemis.floating_artemis.tools.argus_tools import _MAX_ATTEMPTS, _run_claimed_request

    research_calls: list[str] = []
    fallback_calls: list[str] = []

    async def fake_safe_research_and_post(**kwargs: Any) -> None:
        research_calls.append(kwargs["district_key"])

    async def fake_post_fallback(*, channel_id: str, team_id: str, district_key: str) -> None:
        fallback_calls.append(district_key)

    row_id = await _insert_row(district_key="TX-300", status="running", attempts=_MAX_ATTEMPTS)
    from artemis.floating_artemis.tools.argus_tools import _ClaimedRequest

    claimed = _ClaimedRequest(
        id=row_id,
        district_key="TX-300",
        channel_id="C_TEST",
        team_id="T_TEST",
        signal=None,
        triggering_signal_id=None,
        attempts=_MAX_ATTEMPTS,
    )

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=fake_safe_research_and_post,
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._post_fallback",
            side_effect=fake_post_fallback,
        ),
    ):
        await _run_claimed_request(claimed)

    assert research_calls == [], "must not research again once at/past the cap"
    assert fallback_calls == ["TX-300"]

    row = await _fetch_row(row_id)
    assert row.status == "failed"
    assert row.error
    assert "attempt" in row.error.lower()


# ── C7: run_claim_tick claims and completes a pending row end to end ──────────


@pytest.mark.asyncio
async def test_run_claim_tick_processes_pending_row_end_to_end() -> None:
    """The claimer -- not the tool call -- picks up a pending row and drives
    it all the way to 'done', including the Slack post."""
    from artemis.floating_artemis.tools.argus_tools import run_claim_tick

    row_id = await _insert_row(district_key="TX-400", channel_id="C_END2END", team_id="T_TEST")

    posted_calls: list[dict[str, Any]] = []

    async def fake_post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
        posted_calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "111.222"}

    fake_client = MagicMock()
    fake_client.post_message = fake_post_message
    fake_agent_cfg = MagicMock()
    fake_agent_cfg.access_token = "xoxb-fake"

    with (
        patch(
            "artemis.argus.flow.research_district",
            new_callable=AsyncMock,
            return_value=_make_summary(),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._callie_summarize",
            new_callable=AsyncMock,
            return_value="Argus found the district mid-RFP.",
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=fake_agent_cfg,
        ),
    ):
        await run_claim_tick()

    assert len(posted_calls) == 1
    assert posted_calls[0]["channel"] == "C_END2END"

    row = await _fetch_row(row_id)
    assert row.status == "done"
    assert row.completed_at is not None


# ── C8: recover_pending_requests cannot double-run a row the claimer holds ────


@pytest.mark.asyncio
async def test_recover_cannot_double_run_a_row_the_scheduled_tick_already_holds() -> None:
    """While a scheduled run_claim_tick is mid-flight holding the in-process
    claim lock on a row it just claimed, a concurrent recover_pending_requests
    call must not process that same row a second time."""
    from artemis.floating_artemis.tools.argus_tools import recover_pending_requests, run_claim_tick

    row_id = await _insert_row(district_key="TX-500")

    started = asyncio.Event()
    may_finish = asyncio.Event()
    call_count = 0

    async def hanging_research_and_post(**kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        started.set()
        await may_finish.wait()
        # The real _research_and_post's own last step marks the row done;
        # this fake replaces the whole function (research/Slack posting are
        # not what this test is about -- test_run_claim_tick_processes_
        # pending_row_end_to_end already proves that pipeline), so it must
        # replicate that last step to reach a meaningful terminal state.
        from artemis.floating_artemis.tools.argus_tools import _mark_request_done

        await _mark_request_done(kwargs["request_id"])

    with patch(
        "artemis.floating_artemis.tools.argus_tools._research_and_post",
        side_effect=hanging_research_and_post,
    ):
        tick_task = asyncio.create_task(run_claim_tick())
        await started.wait()

        # The row is now genuinely 'running' (claimed, mid-research). A
        # concurrent recovery pass must see the in-process lock held and
        # return without touching the table -- not re-claim/re-run.
        await recover_pending_requests()
        assert call_count == 1, "recover must not have started a second run"

        row_mid_flight = await _fetch_row(row_id)
        assert row_mid_flight.status == "running"

        may_finish.set()
        await tick_task

    assert call_count == 1, "the row must never have been processed twice"
    row_final = await _fetch_row(row_id)
    assert row_final.status == "done"


# ── C9: a failure on one row does not stop the claimer processing the next ────


@pytest.mark.asyncio
async def test_failure_on_one_row_does_not_stop_processing_the_next() -> None:
    from artemis.floating_artemis.tools.argus_tools import run_claim_tick

    boom_id = await _insert_row(district_key="TX-BOOM")
    ok_id = await _insert_row(district_key="TX-OK")

    attempted: list[int] = []

    async def fake_run_claimed_request(claimed: Any) -> None:
        attempted.append(claimed.id)
        if claimed.id == boom_id:
            raise RuntimeError("totally unexpected bug, not one of the swallowed paths")
        # Mark the other row done directly, mirroring what a real run would do.
        async with _db_module.SessionLocal() as session:
            row = await session.get(ArgusResearchRequest, claimed.id)
            assert row is not None
            row.status = "done"
            row.completed_at = datetime.now(UTC)
            await session.commit()

    with patch(
        "artemis.floating_artemis.tools.argus_tools._run_claimed_request",
        side_effect=fake_run_claimed_request,
    ):
        await run_claim_tick()

    assert set(attempted) == {boom_id, ok_id}, "both claimable rows must have been attempted"

    ok_row = await _fetch_row(ok_id)
    assert ok_row.status == "done"


# ═══════════════════════════════════════════════════════════════════════════
# Signal auto-resolution (coordinator addition to ARGUS-1)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dispatch_resolves_latest_qualified_signal_when_none_supplied(
    _never_insert_into_a_real_db: AsyncMock,
) -> None:
    """Neither signal nor signal_id supplied -> the resolver is called and its
    result is persisted on the row."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    resolved_signal = {"headline": "h", "state": "IL", "district_id": "IL-U46", "source_url": ""}

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
            return_value=(resolved_signal, "77"),
        ) as mock_resolve,
    ):
        result_str = await _dispatch_research({"district_key": "IL-U46"})

    result = json.loads(result_str)
    assert result["status"] == "queued"
    mock_resolve.assert_awaited_once_with("IL-U46")

    _never_insert_into_a_real_db.assert_awaited_once()
    _, kwargs = _never_insert_into_a_real_db.call_args
    assert kwargs["signal"] == resolved_signal
    assert kwargs["triggering_signal_id"] == "77"


@pytest.mark.asyncio
async def test_dispatch_uses_explicit_signal_unchanged(
    _never_insert_into_a_real_db: AsyncMock,
) -> None:
    """A caller-supplied signal is used unchanged; the auto-resolver is never
    called and never overwrites it."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    explicit_signal = {"headline": "caller supplied", "state": "TX"}

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
        ) as mock_resolve,
    ):
        result_str = await _dispatch_research(
            {"district_key": "TX-777", "signal": explicit_signal}
        )

    result = json.loads(result_str)
    assert result["status"] == "queued"
    mock_resolve.assert_not_called()

    _, kwargs = _never_insert_into_a_real_db.call_args
    assert kwargs["signal"] == explicit_signal
    assert kwargs["triggering_signal_id"] is None


@pytest.mark.asyncio
async def test_dispatch_with_explicit_signal_id_skips_auto_resolution(
    _never_insert_into_a_real_db: AsyncMock,
) -> None:
    """A caller-supplied signal_id alone (no signal dict) also short-circuits
    the auto-resolver -- signal_id is enough evidence the caller already has
    context."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
        ) as mock_resolve,
    ):
        result_str = await _dispatch_research({"district_key": "TX-888", "signal_id": 999})

    result = json.loads(result_str)
    assert result["status"] == "queued"
    mock_resolve.assert_not_called()

    _, kwargs = _never_insert_into_a_real_db.call_args
    assert kwargs["triggering_signal_id"] == "999"


@pytest.mark.asyncio
async def test_dispatch_enqueues_even_when_no_qualified_signal_found(
    _never_insert_into_a_real_db: AsyncMock,
) -> None:
    """No qualified signal exists for the district -> dispatch still enqueues.
    The absence is recorded by signal staying null on the persisted row
    rather than by a separate column (see _resolve_latest_qualified_signal's
    docstring for why no new column was added)."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result_str = await _dispatch_research({"district_key": "TX-999"})

    result = json.loads(result_str)
    assert result["status"] == "queued"

    _never_insert_into_a_real_db.assert_awaited_once()
    _, kwargs = _never_insert_into_a_real_db.call_args
    assert kwargs["signal"] is None
    assert kwargs["triggering_signal_id"] is None


@pytest.mark.asyncio
async def test_resolve_latest_qualified_signal_picks_newest_qualified_for_district() -> None:
    """Real-DB test of the lookup itself: picks the newest QUALIFIED signal
    for the right district -- never an older one, a different district's, or
    a non-qualified one."""
    from artemis.floating_artemis.tools.argus_tools import _resolve_latest_qualified_signal

    def _signal(**overrides: Any) -> SignalQueue:
        defaults: dict[str, Any] = {
            "headline": "h",
            "campaign_family": "obc",
            "urgency_tier": "standard",
            "source_type": "manual",
            "summary": "",
            "discovered_by": "manual",
            "district_id": "TX-777",
            "state": "TX",
            "reason_codes": [],
            "signal_status": "qualified",
        }
        defaults.update(overrides)
        return SignalQueue(**defaults)

    async with _db_module.SessionLocal() as session:
        older = _signal(headline="older qualified")
        session.add(older)
        await session.flush()

        newer = _signal(headline="newer qualified", source_url="https://example.com/newer")
        session.add(newer)
        await session.flush()
        newer_id = newer.id

        # Same district, but not qualified -- must be ignored.
        session.add(_signal(headline="pending, ignore me", signal_status="pending_qualification"))

        # Qualified, but a DIFFERENT district -- must be ignored.
        session.add(_signal(headline="wrong district, ignore me", district_id="TX-888"))

        await session.commit()

    result = await _resolve_latest_qualified_signal("TX-777")

    assert result is not None
    signal, signal_id = result
    assert signal_id == str(newer_id)
    assert signal["headline"] == "newer qualified"
    assert signal["state"] == "TX"
    assert signal["district_id"] == "TX-777"
    assert signal["source_url"] == "https://example.com/newer"


@pytest.mark.asyncio
async def test_resolve_latest_qualified_signal_returns_none_when_none_qualify() -> None:
    from artemis.floating_artemis.tools.argus_tools import _resolve_latest_qualified_signal

    assert await _resolve_latest_qualified_signal("TX-NOPE") is None


# ═══════════════════════════════════════════════════════════════════════════
# Persist failure -> honest 'failed', never 'queued'
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_persist_failure_reports_failed_not_queued(
    _never_insert_into_a_real_db: AsyncMock,
) -> None:
    """If _insert_pending_request fails (returns None), dispatch_research must
    say so plainly -- ARGUS-1 removed the in-process fallback that used to
    make a persist failure survivable; now a persist failure means the work
    is dropped entirely, and the return value must reflect that."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    _never_insert_into_a_real_db.return_value = None

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result_str = await _dispatch_research({"district_key": "TX-PERSISTFAIL"})

    result = json.loads(result_str)
    assert result["status"] == "failed"
    assert result["error"] == "persist_failed"
    assert "NOT queued" in result["detail"]


# Sanity: the module-level engine override above actually points at a
# distinguishable test database, not somewhere unexpected.
def test_module_db_override_points_at_a_test_database() -> None:
    assert "artemis_test" in str(_db_module.engine.url)
