"""Tests for stage-and-confirm wrapper in _build_auto_invoke_tool_registry.

Covers (per brief):
  1. Layer-3 tool on auto-invoke registry creates a PendingConfirmation,
     returns the staged message, and does NOT call the impl / write to DB.
  2. Layer-1/2 tool still executes immediately.
  3. Full round-trip: stage update_okr_krs -> resume_after_confirm("run")
     -> KR rows updated + activity logged.
  4. resume_after_confirm("cancel") -> zero writes.
  5. Non-OKR layer-3 tool (propose_fix) also stages.
  6. Intercepting registry still raises _PendingConfirmationError (web path
     not regressed).
  7. route_inbound: staged pending + "go" -> write; + "no" -> cancel;
     + unrelated -> pending intact.

All DB-backed tests use the dedicated artemis_test_p2gated database.
LLM calls are mocked throughout.
"""

from __future__ import annotations

import contextlib
import os as _os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.okr.models  # noqa: F401 — registers OKR models on Base.metadata
import artemis.proactivity.models  # noqa: F401 — registers breadcrumb model
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Module-level DB wiring
# ---------------------------------------------------------------------------

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get(
    "ARTEMIS_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_p2gated",
)

if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database.")

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

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "okr_update_previews, "
    "okr_next_up, "
    "okr_activity, "
    "okr_key_results, "
    "okr_objectives "
    "RESTART IDENTITY CASCADE"
)

_SESSION_ID = "test-p2-gated-session"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with NullPool. Truncates OKR tables before yield."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_kr(session: AsyncSession, count: int = 1) -> list[int]:
    """Create one objective with `count` KRs. Returns list of KR IDs."""
    from artemis.okr import repository as repo

    obj = await repo.create_objective(session, title="Gated Test Obj", cycle="Q2-2026")
    await session.flush()
    ids = []
    for i in range(count):
        kr = await repo.create_key_result(
            session,
            objective_id=obj.id,
            title=f"KR {i + 1}",
            status="ontrack",
            prog=10,
        )
        await session.flush()
        ids.append(kr.id)
    await session.commit()
    return ids


def _make_auto_invoke_registry(session_id: str = _SESSION_ID) -> Any:
    """Build a full auto-invoke registry for the given session."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.floating_artemis.chat import _build_auto_invoke_tool_registry
    from artemis.floating_artemis.tools.okr import register_okr_tools
    from artemis.floating_artemis.tools.system import register_system_tools

    auth_reg = AuthorizedToolRegistry()
    register_okr_tools(auth_reg)
    register_system_tools(auth_reg)
    return _build_auto_invoke_tool_registry(auth_reg, session_id)


# ---------------------------------------------------------------------------
# Test 1: Layer-3 tool on auto-invoke registry stages (no impl call, no write)
# ---------------------------------------------------------------------------


async def test_layer3_auto_invoke_creates_pending_and_returns_staged_message(
    db_session: AsyncSession,
) -> None:
    """update_okr_krs on the auto-invoke registry must:
    - create a PendingConfirmation in confirmation_store
    - return the staged message string
    - NOT call the impl / NOT write to DB
    """
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.chat import _STAGED_TOOL_MESSAGE
    from artemis.okr import repository as repo

    kr_ids = await _make_kr(db_session, count=2)
    kr1, kr2 = kr_ids

    tool_reg = _make_auto_invoke_registry()

    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None, "update_okr_krs must be in auto-invoke registry"

    inp = {
        "updates": [
            {"kr_id": kr1, "progress": 75, "basis": "shipped 3 features"},
            {"kr_id": kr2, "progress": 60, "basis": "reduced churn"},
        ]
    }

    try:
        result = await tool_entry.impl(inp)
    finally:
        confirmation_store.clear_session(_SESSION_ID)

    # Must return the staged message (not raise)
    assert result == _STAGED_TOOL_MESSAGE, f"Expected staged message, got: {result!r}"

    # DB must be UNCHANGED — no write on the staging call
    kr1_row = await repo.get_key_result(db_session, kr1)
    kr2_row = await repo.get_key_result(db_session, kr2)
    assert kr1_row is not None and kr1_row.prog == 10, (
        f"KR {kr1} must not be written during staging; prog={kr1_row and kr1_row.prog}"
    )
    assert kr2_row is not None and kr2_row.prog == 10, (
        f"KR {kr2} must not be written during staging; prog={kr2_row and kr2_row.prog}"
    )

    activity = await repo.list_activity(db_session)
    assert len(activity) == 0, f"No activity should be logged during staging; got {len(activity)}"


async def test_layer3_auto_invoke_stores_pending_with_correct_fields(
    db_session: AsyncSession,
) -> None:
    """Staging a layer-3 tool stores a PendingConfirmation with correct field values."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.context import floating_tool_use_id_var

    await _make_kr(db_session, count=1)

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None

    inp = {"updates": [{"kr_id": 1, "progress": 50, "basis": "test basis"}]}
    fixed_id = "tuid-stage-field-check"
    token = floating_tool_use_id_var.set(fixed_id)
    try:
        await tool_entry.impl(inp)
    finally:
        floating_tool_use_id_var.reset(token)

    pending_list = confirmation_store.list_for_session(_SESSION_ID)
    confirmation_store.clear_session(_SESSION_ID)

    assert len(pending_list) == 1, f"Expected 1 pending, got {len(pending_list)}"
    p = pending_list[0]
    assert p.session_id == _SESSION_ID
    assert p.tool_use_id == fixed_id, f"Expected {fixed_id!r}, got {p.tool_use_id!r}"
    assert p.tool_name == "update_okr_krs"
    assert p.tool_input == inp
    assert p.layer == 3


async def test_layer3_auto_invoke_generates_tool_use_id_when_var_not_set() -> None:
    """When floating_tool_use_id_var is not set, the staging wrapper generates a uuid4 id."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.context import floating_tool_use_id_var

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None

    # Ensure the var is NOT set
    floating_tool_use_id_var.set(None)

    inp = {"updates": [{"kr_id": 999, "progress": 50, "basis": "test"}]}
    try:
        await tool_entry.impl(inp)
    finally:
        pass

    pending_list = confirmation_store.list_for_session(_SESSION_ID)
    confirmation_store.clear_session(_SESSION_ID)

    assert len(pending_list) == 1, "Expected a pending even without tool_use_id_var"
    p = pending_list[0]
    assert p.tool_use_id is not None and len(p.tool_use_id) > 0, (
        "Generated tool_use_id must be non-empty"
    )


# ---------------------------------------------------------------------------
# Test 2: Layer-1/2 tool still executes immediately (unchanged)
# ---------------------------------------------------------------------------


async def test_layer1_tool_executes_immediately() -> None:
    """A layer-1 tool registered in the auto-invoke registry runs immediately."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry, confirmation_store
    from artemis.floating_artemis.chat import _build_auto_invoke_tool_registry
    from artemis.floating_artemis.tools.core import register_core_tools

    impl_called: list[dict[str, Any]] = []

    auth_reg = AuthorizedToolRegistry()
    register_core_tools(auth_reg)

    # Find any layer-1 or layer-2 tool (not query_memory which has the emit wrapper)
    layer12_entry = next(
        (e for e in auth_reg.all_entries() if e.layer <= 2 and e.tool.name != "query_memory"),
        None,
    )
    assert layer12_entry is not None, "Expected at least one layer-1/2 tool (non query_memory)"

    # Wrap to track call
    orig_impl = layer12_entry.impl

    async def _tracking_impl(inp: dict[str, Any]) -> str:
        impl_called.append(inp)
        return await orig_impl(inp)

    # Re-register with tracking (build a minimal registry just for this tool)
    minimal_auth = AuthorizedToolRegistry()
    minimal_auth.register(layer12_entry.tool, _tracking_impl, layer=layer12_entry.layer)

    tool_reg = _build_auto_invoke_tool_registry(minimal_auth, _SESSION_ID)
    entry = tool_reg.get(layer12_entry.tool.name)
    assert entry is not None

    # Should NOT raise, should run directly
    # (pass a minimal valid-looking input; result doesn't matter for this test)
    with contextlib.suppress(Exception):
        await entry.impl({})

    # The key assertion: impl was called (not staged)
    assert len(impl_called) == 1, (
        "Layer-1/2 impl must be called immediately on auto-invoke registry"
    )

    # No pending should have been created
    pending = confirmation_store.list_for_session(_SESSION_ID)
    assert len(pending) == 0, "Layer-1/2 tool must not create a pending confirmation"


# ---------------------------------------------------------------------------
# Test 3: Full round-trip — stage -> resume("run") -> KRs updated + activity
# ---------------------------------------------------------------------------


async def test_full_roundtrip_stage_go_applies_krs(db_session: AsyncSession) -> None:
    """Stage update_okr_krs via auto-invoke, then resume_after_confirm('run')
    -> all KR rows updated + activity entries logged.
    """
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.chat import _STAGED_TOOL_MESSAGE, resume_after_confirm
    from artemis.floating_artemis.context import floating_tool_use_id_var
    from artemis.okr import repository as repo

    kr_ids = await _make_kr(db_session, count=2)
    kr1, kr2 = kr_ids

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None

    inp = {
        "updates": [
            {"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"},
            {"kr_id": kr2, "progress": 62, "basis": "churn dropped"},
        ]
    }
    fixed_id = "tuid-roundtrip-go-001"
    token = floating_tool_use_id_var.set(fixed_id)
    try:
        result = await tool_entry.impl(inp)
    finally:
        floating_tool_use_id_var.reset(token)

    assert result == _STAGED_TOOL_MESSAGE
    pending_list = confirmation_store.list_for_session(_SESSION_ID)
    assert len(pending_list) == 1
    assert pending_list[0].tool_use_id == fixed_id

    # Now mock out the parts of resume_after_confirm that need a real adapter/history
    mock_run_result = MagicMock()
    mock_run_result.messages = []
    mock_run_result.stop_reason = "end_turn"
    mock_run_result.usage = MagicMock(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )

    with (
        patch(
            "artemis.floating_artemis.chat.run_turn",
            new_callable=AsyncMock,
            return_value=mock_run_result,
        ),
        patch(
            "artemis.floating_artemis.chat._load_session_context",
            new_callable=AsyncMock,
            return_value=MagicMock(
                metadata={},
                available_surfaces={"okr"},
                is_personal_slack_dm=True,
                agent_id="artemis",
            ),
        ),
        patch(
            "artemis.floating_artemis.chat._load_message_history",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.floating_artemis.chat._persist_messages",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.chat.ws_manager",
            broadcast=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.floating_artemis.chat._resolve_adapter",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        await resume_after_confirm(
            session_id=_SESSION_ID,
            tool_use_id=fixed_id,
            decision="run",
            db_session=db_session,
        )

    # KR rows must now be updated
    kr1_row = await repo.get_key_result(db_session, kr1)
    kr2_row = await repo.get_key_result(db_session, kr2)
    assert kr1_row is not None and kr1_row.prog == 78, (
        f"KR {kr1} must be 78 after 'run'; got {kr1_row and kr1_row.prog}"
    )
    assert kr2_row is not None and kr2_row.prog == 62, (
        f"KR {kr2} must be 62 after 'run'; got {kr2_row and kr2_row.prog}"
    )

    activity = await repo.list_activity(db_session)
    assert len(activity) == 2, f"Expected 2 activity entries after 'run'; got {len(activity)}"


# ---------------------------------------------------------------------------
# Test 4: resume_after_confirm("cancel") -> zero writes
# ---------------------------------------------------------------------------


async def test_full_roundtrip_stage_cancel_no_writes(db_session: AsyncSession) -> None:
    """Stage update_okr_krs, then resume_after_confirm('cancel') -> DB unchanged."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.chat import resume_after_confirm
    from artemis.floating_artemis.context import floating_tool_use_id_var
    from artemis.okr import repository as repo

    kr_ids = await _make_kr(db_session, count=2)
    kr1, kr2 = kr_ids

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None

    inp = {
        "updates": [
            {"kr_id": kr1, "progress": 99, "basis": "should not apply"},
            {"kr_id": kr2, "progress": 99, "basis": "should not apply"},
        ]
    }
    fixed_id = "tuid-roundtrip-cancel-001"
    token = floating_tool_use_id_var.set(fixed_id)
    try:
        await tool_entry.impl(inp)
    finally:
        floating_tool_use_id_var.reset(token)

    # Verify pending created
    assert len(confirmation_store.list_for_session(_SESSION_ID)) == 1

    mock_run_result = MagicMock()
    mock_run_result.messages = []
    mock_run_result.stop_reason = "end_turn"
    mock_run_result.usage = MagicMock(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )

    with (
        patch(
            "artemis.floating_artemis.chat.run_turn",
            new_callable=AsyncMock,
            return_value=mock_run_result,
        ),
        patch(
            "artemis.floating_artemis.chat._load_session_context",
            new_callable=AsyncMock,
            return_value=MagicMock(
                metadata={},
                available_surfaces={"okr"},
                is_personal_slack_dm=True,
                agent_id="artemis",
            ),
        ),
        patch(
            "artemis.floating_artemis.chat._load_message_history",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.floating_artemis.chat._persist_messages",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.chat.ws_manager",
            broadcast=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.floating_artemis.chat._resolve_adapter",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        await resume_after_confirm(
            session_id=_SESSION_ID,
            tool_use_id=fixed_id,
            decision="cancel",
            db_session=db_session,
        )

    # DB must be unchanged
    kr1_row = await repo.get_key_result(db_session, kr1)
    kr2_row = await repo.get_key_result(db_session, kr2)
    assert kr1_row is not None and kr1_row.prog == 10, (
        f"KR {kr1} must be 10 after cancel; got {kr1_row and kr1_row.prog}"
    )
    assert kr2_row is not None and kr2_row.prog == 10, (
        f"KR {kr2} must be 10 after cancel; got {kr2_row and kr2_row.prog}"
    )

    activity = await repo.list_activity(db_session)
    assert len(activity) == 0, f"No activity should be logged on cancel; got {len(activity)}"


# ---------------------------------------------------------------------------
# Test 5: Non-OKR layer-3 tool also stages (fix is general)
# ---------------------------------------------------------------------------


async def test_non_okr_layer3_tool_stages() -> None:
    """propose_fix (system layer-3 tool) also gets staged via the auto-invoke wrapper."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.chat import _STAGED_TOOL_MESSAGE

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("propose_fix")
    assert tool_entry is not None, (
        "propose_fix (layer-3 system tool) must be in the auto-invoke registry"
    )

    inp = {"issue": "test issue", "fix": "test fix"}
    try:
        result = await tool_entry.impl(inp)
    finally:
        confirmation_store.clear_session(_SESSION_ID)

    assert result == _STAGED_TOOL_MESSAGE, (
        f"Non-OKR layer-3 tool must return staged message; got {result!r}"
    )

    confirmation_store.clear_session(_SESSION_ID)


async def test_non_okr_layer3_creates_pending_in_store() -> None:
    """propose_fix staging wrapper adds a PendingConfirmation to confirmation_store."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.floating_artemis.context import floating_tool_use_id_var

    tool_reg = _make_auto_invoke_registry()
    tool_entry = tool_reg.get("propose_fix")
    assert tool_entry is not None

    fixed_id = "tuid-propose-fix-001"
    token = floating_tool_use_id_var.set(fixed_id)
    try:
        await tool_entry.impl({"issue": "issue text", "fix": "fix text"})
    finally:
        floating_tool_use_id_var.reset(token)

    pending_list = confirmation_store.list_for_session(_SESSION_ID)
    confirmation_store.clear_session(_SESSION_ID)

    assert len(pending_list) == 1, f"Expected 1 pending for propose_fix; got {len(pending_list)}"
    p = pending_list[0]
    assert p.tool_name == "propose_fix"
    assert p.tool_use_id == fixed_id
    assert p.layer == 3


# ---------------------------------------------------------------------------
# Test 6: Intercepting registry still raises _PendingConfirmationError (web path)
# ---------------------------------------------------------------------------


async def test_intercepting_registry_layer3_still_raises(db_session: AsyncSession) -> None:
    """_build_intercepting_tool_registry layer-3 wrapper must still raise
    _PendingConfirmationError (web/Anthropic path not regressed).
    """
    from artemis.floating_artemis.authority import AuthorizedToolRegistry, confirmation_store
    from artemis.floating_artemis.chat import (
        _build_intercepting_tool_registry,
        _PendingConfirmationError,
    )
    from artemis.floating_artemis.context import floating_tool_use_id_var
    from artemis.floating_artemis.tools.okr import register_okr_tools

    auth_reg = AuthorizedToolRegistry()
    register_okr_tools(auth_reg)
    tool_reg = _build_intercepting_tool_registry(auth_reg, _SESSION_ID)

    tool_entry = tool_reg.get("update_okr_krs")
    assert tool_entry is not None

    token = floating_tool_use_id_var.set("tuid-intercept-001")
    try:
        with pytest.raises(_PendingConfirmationError) as exc_info:
            await tool_entry.impl({"updates": [{"kr_id": 1, "progress": 50, "basis": "test"}]})
    finally:
        floating_tool_use_id_var.reset(token)
        confirmation_store.clear_session(_SESSION_ID)

    exc = exc_info.value
    assert exc.tool_name == "update_okr_krs"
    assert exc.tool_use_id == "tuid-intercept-001"


# ---------------------------------------------------------------------------
# Test 7: route_inbound — staged pending + "go"/"no"/"unrelated"
# (reuse of existing confirm test helpers, adapted to the staging path)
# ---------------------------------------------------------------------------

_TEAM_ID = "T_GATED_TEST"
_CHANNEL_ID = "D_GATED_DM"
_USER_ID = "U_JON_GATED"
_AGENT_ID = "artemis"
_ROUTE_SESSION_ID = f"slack-{_AGENT_ID}-{_TEAM_ID}-{_CHANNEL_ID}-_"


def _make_event_data(text: str = "go") -> dict[str, Any]:
    return {
        "team_id": _TEAM_ID,
        "channel": _CHANNEL_ID,
        "user": _USER_ID,
        "text": text,
        "ts": "888.000",
        "thread_ts": None,
    }


def _make_mock_session_local() -> MagicMock:
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def _make_staged_pending(tool_use_id: str = "tuid-route-001") -> Any:
    from artemis.floating_artemis.authority import PendingConfirmation

    return PendingConfirmation(
        session_id=_ROUTE_SESSION_ID,
        tool_use_id=tool_use_id,
        tool_name="update_okr_krs",
        tool_input={"updates": [{"kr_id": 1, "progress": 78, "basis": "route test"}]},
        layer=3,
    )


async def _run_route_inbound(
    text: str,
    *,
    classifier_verdict: str,
    pending: Any = None,
) -> tuple[list[str], MagicMock, MagicMock]:
    """Run route_inbound with mocked deps. Returns (posted_texts, mock_resume, mock_handle)."""
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.routes.integrations_slack_events import route_inbound

    if pending is not None:
        confirmation_store.add(pending)

    posted_texts: list[str] = []
    mock_session_local = _make_mock_session_local()

    mock_agent_cfg = MagicMock()
    mock_agent_cfg.access_token = "xoxb-test-gated"

    async def _fake_post(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted_texts.append(text)

    mock_slack = MagicMock()
    mock_slack.post_message = _fake_post

    mock_handle = AsyncMock()
    mock_handle.return_value = MagicMock(
        response_text="Normal response.",
        pending_tool_use_id=None,
    )

    mock_resume = AsyncMock()
    mock_resume.return_value = MagicMock(response_text="Done — applied.")

    async def _classifier(t: str) -> str:
        return classifier_verdict

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
            patch(
                "artemis.integrations.repository.get_slack_user",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "artemis.floating_artemis.repository.get_session_by_id",
                new_callable=AsyncMock,
                side_effect=ValueError("not found"),
            ),
            patch("artemis.floating_artemis.repository.create_session", new_callable=AsyncMock),
            patch("artemis.floating_artemis.chat.handle_turn", mock_handle),
            patch("artemis.floating_artemis.chat.resume_after_confirm", mock_resume),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack),
        ):
            await route_inbound(
                _make_event_data(text),
                agent_id=_AGENT_ID,
                confirm_classifier=_classifier,
            )
    finally:
        confirmation_store.clear_session(_ROUTE_SESSION_ID)

    return posted_texts, mock_resume, mock_handle


async def test_route_inbound_go_after_staged_pending_calls_resume_run() -> None:
    """Staged pending + 'go' -> route_inbound calls resume_after_confirm(decision='run')."""
    pending = _make_staged_pending("tuid-route-go-001")
    posted, mock_resume, mock_handle = await _run_route_inbound(
        "go",
        classifier_verdict="YES",
        pending=pending,
    )

    mock_resume.assert_awaited_once()
    kwargs = mock_resume.call_args.kwargs
    assert kwargs.get("decision") == "run", f"Expected 'run'; got {kwargs.get('decision')!r}"
    assert kwargs.get("tool_use_id") == "tuid-route-go-001"
    assert kwargs.get("session_id") == _ROUTE_SESSION_ID

    # handle_turn should NOT be called (we resolved the pending)
    mock_handle.assert_not_awaited()
    # A reply text was posted
    assert len(posted) == 1


async def test_route_inbound_no_after_staged_pending_calls_resume_cancel() -> None:
    """Staged pending + 'no' -> route_inbound calls resume_after_confirm(decision='cancel')."""
    pending = _make_staged_pending("tuid-route-no-001")
    posted, mock_resume, mock_handle = await _run_route_inbound(
        "no",
        classifier_verdict="NO",
        pending=pending,
    )

    mock_resume.assert_awaited_once()
    kwargs = mock_resume.call_args.kwargs
    assert kwargs.get("decision") == "cancel", f"Expected 'cancel'; got {kwargs.get('decision')!r}"
    mock_handle.assert_not_awaited()
    assert len(posted) == 1


async def test_route_inbound_unrelated_leaves_pending_intact() -> None:
    """Unrelated reply while a staged pending exists -> handle_turn runs, pending NOT resolved."""
    from artemis.floating_artemis.authority import confirmation_store

    pending = _make_staged_pending("tuid-route-unrelated-001")
    confirmation_store.add(pending)

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = MagicMock()
    mock_agent_cfg.access_token = "xoxb-test-gated"

    async def _fake_post(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack = MagicMock()
    mock_slack.post_message = _fake_post

    mock_handle = AsyncMock()
    mock_handle.return_value = MagicMock(
        response_text="Sure, here's your answer.",
        pending_tool_use_id=None,
    )
    mock_resume = AsyncMock()

    async def _neither_classifier(t: str) -> str:
        return "NEITHER"

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
            patch(
                "artemis.integrations.repository.get_slack_user",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "artemis.floating_artemis.repository.get_session_by_id",
                new_callable=AsyncMock,
                side_effect=ValueError("not found"),
            ),
            patch("artemis.floating_artemis.repository.create_session", new_callable=AsyncMock),
            patch("artemis.floating_artemis.chat.handle_turn", mock_handle),
            patch("artemis.floating_artemis.chat.resume_after_confirm", mock_resume),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack),
        ):
            from artemis.routes.integrations_slack_events import route_inbound

            await route_inbound(
                _make_event_data("What's the budget for Q3?"),
                agent_id=_AGENT_ID,
                confirm_classifier=_neither_classifier,
            )
    finally:
        remaining = confirmation_store.list_for_session(_ROUTE_SESSION_ID)
        confirmation_store.clear_session(_ROUTE_SESSION_ID)

    mock_handle.assert_awaited_once()
    mock_resume.assert_not_awaited()
    assert len(remaining) == 1, f"Pending must survive NEITHER reply; got {len(remaining)}"
    assert remaining[0].tool_use_id == "tuid-route-unrelated-001"


# ---------------------------------------------------------------------------
# Test: resume_after_confirm registry includes integration tools (regression)
# ---------------------------------------------------------------------------
# Guards against the bug where resume_after_confirm rebuilt the tool registry
# by hand, omitting gcal/gmail/slack/jira/granola.  The fix uses
# build_authorized_tool_registry (same as main turn path) so the sets stay in
# sync automatically.
# ---------------------------------------------------------------------------

_INTEGRATION_TOOL_NAMES = [
    # GCal — unconditionally registered
    "create_event",
    "update_event",
    "delete_event",
    "list_calendars",
    "list_events",
    # Slack — unconditionally registered
    "send_slack_message",
    "send_slack_dm",
    "read_slack_channel",
    "list_slack_channels",
    # Gmail — unconditionally registered
    "list_recent_gmail_messages",
    "get_gmail_thread",
]

_INTEGRATION_SESSION = "test-integration-resume-session"


async def test_resume_registry_resolves_gcal_create_event() -> None:
    """Approving a pending create_event via resume_after_confirm must NOT return
    'Tool <x> not found.' — the registry must include gcal tools.

    We stage a fake PendingConfirmation for create_event, then call
    resume_after_confirm('run') with all heavy I/O mocked.  The test asserts that
    the tool impl is invoked (not "not found") and the result is the mocked value.
    """
    from artemis.floating_artemis.authority import PendingConfirmation, confirmation_store
    from artemis.floating_artemis.chat import resume_after_confirm

    tool_use_id = "tuid-gcal-resume-001"
    # Stage a fake PendingConfirmation for create_event
    pending = PendingConfirmation(
        tool_use_id=tool_use_id,
        tool_name="create_event",
        tool_input={
            "summary": "Sprint review",
            "start": "2026-06-20T10:00:00",
            "end": "2026-06-20T11:00:00",
        },
        session_id=_INTEGRATION_SESSION,
        layer=3,
        prior_tool_results=[],
    )
    confirmation_store._pending[tool_use_id] = pending

    mock_run_result = MagicMock()
    mock_run_result.messages = []
    mock_run_result.stop_reason = "end_turn"
    mock_run_result.usage = MagicMock(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )

    gcal_result = "Event created: Sprint review"
    # Track what the tool resolved to
    resolved_result: list[str] = []

    async def _fake_create_event(inp: dict) -> str:  # type: ignore[type-arg]
        resolved_result.append(gcal_result)
        return gcal_result

    with (
        patch(
            "artemis.floating_artemis.chat.get_status",
            new_callable=AsyncMock,
            return_value={"available_surfaces": ["okr", "writing-rules", "marketing-os"]},
        ),
        patch(
            "artemis.floating_artemis.chat._load_session_context",
            new_callable=AsyncMock,
            return_value=MagicMock(
                metadata={},
                available_surfaces={"okr", "writing-rules", "marketing-os"},
                is_personal_slack_dm=False,
                agent_id="artemis",
            ),
        ),
        patch(
            "artemis.integrations.gcal.tools._create_event",
            new=_fake_create_event,
        ),
        patch(
            "artemis.floating_artemis.chat.run_turn",
            new_callable=AsyncMock,
            return_value=mock_run_result,
        ),
        patch(
            "artemis.floating_artemis.chat._load_message_history",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.floating_artemis.chat._persist_messages",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.chat.ws_manager",
            broadcast=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.floating_artemis.chat._resolve_adapter",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        await resume_after_confirm(
            session_id=_INTEGRATION_SESSION,
            tool_use_id=tool_use_id,
            decision="run",
        )

    assert len(resolved_result) == 1, (
        "create_event impl must have been called — not 'Tool not found'"
    )
    assert resolved_result[0] == gcal_result


async def test_resume_registry_resolves_send_slack_dm() -> None:
    """Approving send_slack_dm via resume_after_confirm resolves the tool (not 'not found')."""
    from artemis.floating_artemis.authority import PendingConfirmation, confirmation_store
    from artemis.floating_artemis.chat import resume_after_confirm

    tool_use_id = "tuid-slack-dm-resume-001"
    pending = PendingConfirmation(
        tool_use_id=tool_use_id,
        tool_name="send_slack_dm",
        tool_input={"user_id": "U123", "text": "Hey there"},
        session_id=_INTEGRATION_SESSION,
        layer=3,
        prior_tool_results=[],
    )
    confirmation_store._pending[tool_use_id] = pending

    mock_run_result = MagicMock()
    mock_run_result.messages = []
    mock_run_result.stop_reason = "end_turn"
    mock_run_result.usage = MagicMock(
        input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0
    )

    slack_result = "DM sent"
    resolved_result: list[str] = []

    async def _fake_send_dm(inp: dict) -> str:  # type: ignore[type-arg]
        resolved_result.append(slack_result)
        return slack_result

    with (
        patch(
            "artemis.floating_artemis.chat.get_status",
            new_callable=AsyncMock,
            return_value={"available_surfaces": []},
        ),
        patch(
            "artemis.floating_artemis.chat._load_session_context",
            new_callable=AsyncMock,
            return_value=MagicMock(
                metadata={},
                available_surfaces=set(),
                is_personal_slack_dm=False,
                agent_id="artemis",
            ),
        ),
        patch(
            "artemis.integrations.slack.tools._send_slack_dm",
            new=_fake_send_dm,
        ),
        patch(
            "artemis.floating_artemis.chat.run_turn",
            new_callable=AsyncMock,
            return_value=mock_run_result,
        ),
        patch(
            "artemis.floating_artemis.chat._load_message_history",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "artemis.floating_artemis.chat._persist_messages",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.chat.ws_manager",
            broadcast=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.floating_artemis.chat._resolve_adapter",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    ):
        await resume_after_confirm(
            session_id=_INTEGRATION_SESSION,
            tool_use_id=tool_use_id,
            decision="run",
        )

    assert len(resolved_result) == 1, (
        "send_slack_dm impl must have been called — not 'Tool not found'"
    )
    assert resolved_result[0] == slack_result


async def test_resume_registry_includes_all_integration_tools() -> None:
    """Assert that all integration tool names registered by build_authorized_tool_registry
    are resolvable in a resume-path registry — guards against future drift.
    """
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    # Build the registry with all surfaces enabled (worst-case: maximum tools)
    all_surfaces = {
        "okr",
        "writing-rules",
        "marketing-os",
        "signal-queue",
        "jira-board",
        "meetings",
    }
    registry = build_authorized_tool_registry(all_surfaces, agent_id="artemis")

    missing = [name for name in _INTEGRATION_TOOL_NAMES if registry.get(name) is None]
    assert not missing, (
        f"Integration tools missing from build_authorized_tool_registry (resume registry): {missing}"
    )
