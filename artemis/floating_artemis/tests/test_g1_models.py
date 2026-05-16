"""Tests for Floating Artemis Pydantic schemas — round-trips and validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from artemis.floating_artemis.schemas import (
    ActiveRunRead,
    MessageCreate,
    MessageRead,
    PageContextRead,
    PageContextSet,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    ToolConfirmRequest,
    ToolConfirmResponse,
    TurnRequest,
    VoiceCorpusRead,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


# ── SessionCreate ─────────────────────────────────────────────────────────────


def test_session_create_minimal() -> None:
    sc = SessionCreate(session_id="abc-123")
    assert sc.session_id == "abc-123"
    assert sc.owner_user_id is None
    assert sc.title is None
    assert sc.metadata == {}


def test_session_create_full() -> None:
    sc = SessionCreate(
        session_id="s1",
        owner_user_id=42,
        title="My session",
        metadata={"key": "val"},
    )
    assert sc.owner_user_id == 42
    assert sc.metadata["key"] == "val"


def test_session_create_empty_id_fails() -> None:
    with pytest.raises(ValueError):
        SessionCreate(session_id="")


# ── SessionRead ───────────────────────────────────────────────────────────────


def test_session_read_round_trip() -> None:
    data = {
        "id": 1,
        "session_id": "s1",
        "owner_user_id": None,
        "started_at": _NOW,
        "last_active_at": _NOW,
        "closed_at": None,
        "title": "Test",
        "metadata": {"x": 1},
    }
    sr = SessionRead(**data)
    assert sr.session_id == "s1"
    assert sr.metadata == {"x": 1}
    assert sr.closed_at is None


def test_session_read_dump() -> None:
    sr = SessionRead(
        id=7,
        session_id="s7",
        owner_user_id=None,
        started_at=_NOW,
        last_active_at=_NOW,
        closed_at=None,
        title=None,
        metadata={},
    )
    d = sr.model_dump()
    assert d["id"] == 7
    assert d["session_id"] == "s7"


# ── SessionUpdate ─────────────────────────────────────────────────────────────


def test_session_update_all_optional() -> None:
    su = SessionUpdate()
    assert su.title is None
    assert su.metadata is None


def test_session_update_exclude_none() -> None:
    su = SessionUpdate(title="New title")
    data = su.model_dump(exclude_none=True)
    assert "title" in data
    assert "metadata" not in data


# ── MessageCreate ─────────────────────────────────────────────────────────────


def test_message_create_defaults() -> None:
    mc = MessageCreate(content=[{"type": "text", "text": "hello"}])
    assert mc.role == "user"
    assert mc.cost_input_tokens == 0


def test_message_create_assistant() -> None:
    mc = MessageCreate(
        role="assistant",
        content=[{"type": "text", "text": "hi"}],
        cost_input_tokens=100,
        cost_output_tokens=50,
    )
    assert mc.role == "assistant"
    assert mc.cost_input_tokens == 100


# ── MessageRead ───────────────────────────────────────────────────────────────


def test_message_read_round_trip() -> None:
    mr = MessageRead(
        id=1,
        session_id="s1",
        role="user",
        content=[{"type": "text", "text": "hello"}],
        cost_input_tokens=0,
        cost_output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        created_at=_NOW,
    )
    assert mr.role == "user"
    assert mr.content[0]["text"] == "hello"


# ── PageContext ───────────────────────────────────────────────────────────────


def test_page_context_set_minimal() -> None:
    pcs = PageContextSet(page="home")
    assert pcs.page == "home"
    assert pcs.ref_id is None


def test_page_context_set_empty_page_fails() -> None:
    with pytest.raises(ValueError):
        PageContextSet(page="")


def test_page_context_read() -> None:
    pcr = PageContextRead(id=1, session_id="s1", page="okr", ref_id="obj-42", set_at=_NOW)
    assert pcr.ref_id == "obj-42"


# ── ToolConfirm ───────────────────────────────────────────────────────────────


def test_tool_confirm_run() -> None:
    req = ToolConfirmRequest(tool_use_id="tuid-1", decision="run")
    assert req.decision == "run"


def test_tool_confirm_cancel() -> None:
    req = ToolConfirmRequest(tool_use_id="tuid-2", decision="cancel")
    assert req.decision == "cancel"


def test_tool_confirm_invalid_decision() -> None:
    with pytest.raises(ValueError):
        ToolConfirmRequest(tool_use_id="x", decision="maybe")


def test_tool_confirm_response() -> None:
    resp = ToolConfirmResponse(tool_use_id="t1", decision="run", result="ok", error=False)
    assert not resp.error


# ── TurnRequest ───────────────────────────────────────────────────────────────


def test_turn_request_minimal() -> None:
    tr = TurnRequest(message="hello")
    assert tr.message == "hello"
    assert tr.attachments == []


def test_turn_request_empty_message_fails() -> None:
    with pytest.raises(ValueError):
        TurnRequest(message="")


# ── VoiceCorpusRead ───────────────────────────────────────────────────────────


def test_voice_corpus_read() -> None:
    vcr = VoiceCorpusRead(
        id=1,
        line="Already on it.",
        context_tag=None,
        source="seed",
        use_count=3,
        active=True,
        last_used_at=_NOW,
    )
    assert vcr.source == "seed"
    assert vcr.active


# ── ActiveRunRead ─────────────────────────────────────────────────────────────


def test_active_run_read() -> None:
    run = ActiveRunRead(
        run_id="run-1",
        run_type="agent",
        subject_id="my-agent",
        status="running",
        started_at=_NOW,
        completed_at=None,
        owner_user_id=None,
    )
    assert run.run_type == "agent"
    assert run.completed_at is None
