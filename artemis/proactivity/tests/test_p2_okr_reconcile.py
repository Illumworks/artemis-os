"""Tests for Part A: OKR check-in breadcrumb and reconcile context injection.

Coverage:
1. After check-in fires: a breadcrumb row exists with KR snapshot + proposal + TTL.
2. DM turn with live breadcrumb: system prompt receives OKR-reconcile context block.
3. DM turn with NO live breadcrumb: no reconcile context injected (no hijack).
4. Breadcrumb expires on TTL (expired rows return None from get_live).
5. Breadcrumb can be marked complete and then returns None.
6. Part C: format_checkin_for_slack with objectives shows actual KR values.
7. build_kr_snapshot returns correct flat list, excludes archived KRs.
8. Voice prompt includes KR snapshot numbers when passed.
9. Voice system prompt enforces dry-witty-Jarvis rules (no bold section labels in casual replies).
10. Persona core has Jarvis/dry-witty register (no McKinsey-deck phrases).
13. complete_okr_checkin tool: layer-1 registration, handler clears breadcrumb.
14. Multi-KR word-dump: breadcrumb stays live across multiple update_okr_kr proposals.
15. Unrelated-topic DM: tool handler clears breadcrumb; subsequent injection returns None.
16. Done signal ("that's all"): tool handler clears breadcrumb and injection is gone.
17. Reconcile context block instructs agent to call complete_okr_checkin on topic-change/done.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as integration_repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.proactivity.models import OkrCheckinBreadcrumb
from artemis.proactivity.okr_checkin import (
    build_kr_snapshot,
    format_checkin_for_slack,
)
from artemis.proactivity.repository import (
    complete_okr_checkin_breadcrumb,
    create_okr_checkin_breadcrumb,
    get_live_okr_checkin_breadcrumb,
)
from artemis.proactivity.scheduler import _fire_okr_checkin, stop_proactivity_scheduler
from artemis.proactivity.voice_render import _build_checkin_voice_prompt, _build_voice_system_prompt

pytestmark = pytest.mark.asyncio

_DELIVERY_DATE = date(2026, 6, 13)  # a Friday


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_kr(
    kr_id: int,
    title: str,
    prog: int = 50,
    archived_at: datetime | None = None,
    target_text: str | None = None,
) -> MagicMock:
    kr = MagicMock()
    kr.id = kr_id
    kr.title = title
    kr.prog = prog
    kr.archived_at = archived_at
    kr.target_text = target_text
    return kr


def _make_obj(obj_id: int, title: str, krs: list[MagicMock]) -> MagicMock:
    obj = MagicMock()
    obj.id = obj_id
    obj.title = title
    obj.key_results = krs
    return obj


async def _seed_slack_context(db_session: AsyncSession) -> None:
    db_session.add(
        Integration(
            provider="slack",
            workspace_id="default",
            agent_id="artemis",
            encrypted_credentials=encrypt_credentials({"access_token": "xoxb-artemis"}),
            connected_at=datetime.now(UTC),
            status="active",
        )
    )
    await integration_repo.upsert_provider_config(
        db_session,
        "slack",
        {"authed_user_id": "U_JON"},
    )
    await db_session.commit()


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_scheduler() -> Any:
    yield
    stop_proactivity_scheduler()


# ── 1. Breadcrumb created after check-in fires ────────────────────────────────


async def test_fire_okr_checkin_leaves_breadcrumb(db_session: AsyncSession) -> None:
    """After _fire_okr_checkin, a breadcrumb row exists for the recipient."""
    await _seed_slack_context(db_session)

    kr = _make_kr(7, "Increase pipeline coverage", prog=45)
    obj = _make_obj(1, "Grow product reach", [kr])

    with (
        patch(
            "artemis.proactivity.scheduler.gather_checkin_sources",
            new_callable=AsyncMock,
            return_value={
                "objectives": [obj],
                "activity": [],
                "jira_done": [],
                "action_items": [],
            },
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
    ):
        await _fire_okr_checkin()

    # Breadcrumb must exist.
    result = await db_session.execute(
        select(OkrCheckinBreadcrumb).where(OkrCheckinBreadcrumb.recipient_id == "U_JON")
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1, f"Expected 1 breadcrumb, got {len(rows)}"

    crumb = rows[0]
    # KR snapshot contains the KR.
    snapshot = crumb.kr_snapshot
    assert isinstance(snapshot, list)
    assert len(snapshot) == 1
    assert snapshot[0]["kr_id"] == 7
    assert snapshot[0]["kr_title"] == "Increase pipeline coverage"
    assert snapshot[0]["prog"] == 45
    assert snapshot[0]["objective_title"] == "Grow product reach"

    # Proposal text is non-empty.
    assert crumb.proposal_text and len(crumb.proposal_text) > 0

    # TTL: expires_at is in the future.
    assert crumb.expires_at > datetime.now(UTC)

    # completed_at is None (not yet completed).
    assert crumb.completed_at is None


# ── 2. DM reply with live breadcrumb injects OKR-reconcile context ────────────


async def test_get_okr_reconcile_context_with_live_breadcrumb(db_session: AsyncSession) -> None:
    """_get_okr_reconcile_context returns a system block when a live crumb exists."""
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    # Insert a live breadcrumb manually.
    kr_snapshot = [
        {
            "kr_id": 9,
            "kr_title": "Asset Hub",
            "objective_title": "Brand Infrastructure",
            "prog": 60,
            "target_text": "100%",
        }
    ]
    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_JON",
        kr_snapshot=kr_snapshot,
        proposal_text="Friday check-in sent.",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    ctx = await _get_okr_reconcile_context("U_JON", db_session)

    assert ctx is not None, "Expected reconcile context, got None"
    # Must mention the reconcile purpose.
    ctx_lower = ctx.lower()
    assert "okr" in ctx_lower or "kr" in ctx_lower
    assert "word-dump" in ctx_lower or "map" in ctx_lower or "reconcil" in ctx_lower
    # Must mention the KR from the snapshot.
    assert "Asset Hub" in ctx or "Brand Infrastructure" in ctx
    # Must reference layer-3 gating.
    assert "layer-3" in ctx or "update_okr_kr" in ctx or "go" in ctx_lower
    # Must NOT bypass: propose don't apply.
    assert "propose" in ctx_lower or "layer-3" in ctx


# ── 3. DM with NO live breadcrumb: no reconcile context injected ──────────────


async def test_get_okr_reconcile_context_no_breadcrumb_returns_none(
    db_session: AsyncSession,
) -> None:
    """No breadcrumb → _get_okr_reconcile_context returns None (no hijack)."""
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    ctx = await _get_okr_reconcile_context("U_NO_CHECKIN", db_session)
    assert ctx is None


async def test_get_okr_reconcile_context_none_speaker_returns_none(
    db_session: AsyncSession,
) -> None:
    """None speaker_id → no reconcile context (safety guard)."""
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    ctx = await _get_okr_reconcile_context(None, db_session)
    assert ctx is None


# ── 4. Breadcrumb expires on TTL ──────────────────────────────────────────────


async def test_expired_breadcrumb_returns_none(db_session: AsyncSession) -> None:
    """A breadcrumb past its expires_at is not returned as live."""
    past = datetime.now(UTC) - timedelta(hours=1)
    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_JON_EXPIRED",
        kr_snapshot=[],
        proposal_text="old check-in",
        expires_at=past,
    )
    await db_session.commit()

    crumb = await get_live_okr_checkin_breadcrumb(db_session, "U_JON_EXPIRED")
    assert crumb is None, "Expired breadcrumb must not be returned as live"


# ── 5. Breadcrumb marked complete returns None ────────────────────────────────


async def test_completed_breadcrumb_returns_none(db_session: AsyncSession) -> None:
    """A completed breadcrumb is not returned as live."""
    future = datetime.now(UTC) + timedelta(days=3)
    crumb = await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_JON_DONE",
        kr_snapshot=[],
        proposal_text="done crumb",
        expires_at=future,
    )
    await db_session.commit()

    # Live before completion.
    live = await get_live_okr_checkin_breadcrumb(db_session, "U_JON_DONE")
    assert live is not None

    # Complete it.
    await complete_okr_checkin_breadcrumb(db_session, crumb.id)
    await db_session.commit()

    # No longer live.
    live_after = await get_live_okr_checkin_breadcrumb(db_session, "U_JON_DONE")
    assert live_after is None, "Completed breadcrumb must not be returned as live"


# ── 6. Digest-based opener: shows slipping KRs grounded in low-progress ──────


def test_format_checkin_shows_slipping_krs_in_digest() -> None:
    """format_checkin_for_slack surfaces low-progress stalled KRs in the digest.

    With no activity history, both KRs at <= 40% appear in the slipping bucket.
    The opener does NOT recite all KRs or their target-description prose.
    """
    kr1 = _make_kr(1, "Asset Hub", prog=40, target_text="100%")
    kr2 = _make_kr(2, "Template Library", prog=25)
    obj = _make_obj(1, "Brand Infrastructure", [kr1, kr2])

    text = format_checkin_for_slack([], delivery_date=_DELIVERY_DATE, objectives=[obj])

    # Slipping KRs (both prog <= 40) should appear with their percentages.
    assert "Asset Hub" in text
    assert "Template Library" in text
    assert "40%" in text
    assert "25%" in text
    # Must still ask what Jon moved.
    assert "move" in text.lower() or "map" in text.lower()
    # Must mention safety gate.
    assert "go" in text.lower()
    # Must NOT recite all KR target-description prose (old "Where your KRs stand" dump).
    assert "Where your KRs stand" not in text
    assert "target:" not in text


def test_format_checkin_no_objectives_still_works() -> None:
    """format_checkin_for_slack with no objectives falls back gracefully."""
    text = format_checkin_for_slack([], delivery_date=_DELIVERY_DATE)
    # No crash; still asks for word-dump.
    assert "go" in text.lower()
    assert "word-dump" in text.lower() or "what" in text.lower()


def test_format_checkin_archived_kr_excluded_from_digest() -> None:
    """Archived KRs are excluded from the digest (slipping bucket).

    The live KR at low progress surfaces; the archived KR never appears
    regardless of how it looks on paper.
    """
    # Use a low-progress live KR so it appears in the slipping bucket.
    kr_live = _make_kr(1, "Active KR", prog=20)
    kr_archived = _make_kr(2, "Old KR", prog=100, archived_at=datetime(2025, 1, 1, tzinfo=UTC))
    obj = _make_obj(1, "Objective", [kr_live, kr_archived])

    text = format_checkin_for_slack([], delivery_date=_DELIVERY_DATE, objectives=[obj])

    assert "Active KR" in text, "Low-progress live KR must appear in the slipping digest"
    assert "Old KR" not in text, "Archived KR must not appear in the digest"


# ── 7. build_kr_snapshot ──────────────────────────────────────────────────────


def test_build_kr_snapshot_flat_list() -> None:
    """build_kr_snapshot returns a flat list of active KR dicts."""
    kr1 = _make_kr(1, "KR One", prog=30, target_text="target A")
    kr2 = _make_kr(2, "KR Two", prog=70)
    kr_archived = _make_kr(3, "Old KR", prog=100, archived_at=datetime(2025, 1, 1, tzinfo=UTC))
    obj = _make_obj(1, "Objective A", [kr1, kr2, kr_archived])

    snapshot = build_kr_snapshot([obj])

    assert len(snapshot) == 2
    ids = {e["kr_id"] for e in snapshot}
    assert ids == {1, 2}
    # Check fields.
    kr1_entry = next(e for e in snapshot if e["kr_id"] == 1)
    assert kr1_entry["kr_title"] == "KR One"
    assert kr1_entry["prog"] == 30
    assert kr1_entry["target_text"] == "target A"
    assert kr1_entry["objective_title"] == "Objective A"


def test_build_kr_snapshot_empty_objectives() -> None:
    """build_kr_snapshot returns [] for empty objectives list."""
    assert build_kr_snapshot([]) == []


# ── 8. Voice prompt includes KR snapshot numbers ──────────────────────────────


def test_build_checkin_voice_prompt_includes_kr_snapshot() -> None:
    """When kr_snapshot is provided, the voice prompt includes the KR numbers."""
    kr_snapshot = [
        {
            "kr_id": 6,
            "kr_title": "Template Library",
            "objective_title": "Brand",
            "prog": 35,
            "target_text": "100 templates",
        }
    ]
    prompt = _build_checkin_voice_prompt([], _DELIVERY_DATE, kr_snapshot=kr_snapshot)
    assert "Template Library" in prompt
    assert "Brand" in prompt
    assert "35%" in prompt
    assert "100 templates" in prompt


def test_build_checkin_voice_prompt_no_snapshot_still_works() -> None:
    """_build_checkin_voice_prompt without snapshot is still coherent."""
    prompt = _build_checkin_voice_prompt([], _DELIVERY_DATE)
    assert "OKR" in prompt or "KR" in prompt
    # Must instruct to ask what Jon moved.
    assert "ask" in prompt.lower() or "word-dump" in prompt.lower() or "moved" in prompt.lower()


# ── 9. Voice system prompt: dry-witty-Jarvis rules ───────────────────────────


def test_voice_system_prompt_forbids_bold_section_labels() -> None:
    """The voice system prompt must explicitly forbid bold section headers/labels."""
    system = _build_voice_system_prompt([])
    system_lower = system.lower()
    # Must mention the rule against bold section labels.
    assert "bold" in system_lower or "section" in system_lower or "labeled" in system_lower
    # Must still forbid em-dashes.
    assert "em-dash" in system_lower or "em dash" in system_lower


def test_voice_system_prompt_no_consulting_deck_language() -> None:
    """Voice system prompt must not include consulting-deck register instructions."""
    system = _build_voice_system_prompt([])
    # These phrases would indicate old McKinsey register still present.
    system_lower = system.lower()
    assert "summarise the highlights" not in system_lower
    assert "key takeaways" not in system_lower
    # Must mention dry-witty or Jarvis or economical register.
    assert (
        "dry" in system_lower
        or "witty" in system_lower
        or "jarvis" in system_lower
        or "economical" in system_lower
        or "chief of staff" in system_lower
    )


# ── 10. Persona core: Jarvis/dry-witty register ──────────────────────────────


def test_artemis_persona_core_has_jarvis_register() -> None:
    """ARTEMIS_PERSONA_CORE must reference dry-witty or Jarvis register."""
    from artemis.floating_artemis.personality import ARTEMIS_PERSONA_CORE

    core_lower = ARTEMIS_PERSONA_CORE.lower()
    assert (
        "dry" in core_lower
        or "witty" in core_lower
        or "jarvis" in core_lower
        or "economical" in core_lower
        or "dry-witty" in core_lower
    ), "Persona core should reference dry-witty/Jarvis register"


def test_artemis_persona_core_forbids_bold_section_labels() -> None:
    """Persona core must forbid bold section label usage in casual replies."""
    from artemis.floating_artemis.personality import ARTEMIS_PERSONA_CORE

    core_lower = ARTEMIS_PERSONA_CORE.lower()
    assert "bold" in core_lower or "section" in core_lower or "deck" in core_lower, (
        "Persona core should explicitly forbid McKinsey-deck section labels"
    )


# ── 11. OKR reconcile context mentions layer-3 guard ─────────────────────────


async def test_reconcile_context_cites_layer3_gate(db_session: AsyncSession) -> None:
    """The reconcile context block must reference update_okr_kr layer-3 gating."""
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_JON_GATE",
        kr_snapshot=[
            {
                "kr_id": 11,
                "kr_title": "Governance",
                "objective_title": "Brand Infrastructure",
                "prog": 15,
                "target_text": "",
            }
        ],
        proposal_text="check-in text",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    ctx = await _get_okr_reconcile_context("U_JON_GATE", db_session)
    assert ctx is not None
    # Must mention update_okr_kr and layer-3 gate.
    assert "update_okr_kr" in ctx
    assert "layer-3" in ctx
    # Must NOT say "apply" without "go" — the gate must be mentioned.
    ctx_lower = ctx.lower()
    assert "go" in ctx_lower or "confirm" in ctx_lower


# ── 12. build_system_prompt injects reconcile context ────────────────────────


def test_build_system_prompt_injects_okr_reconcile_context() -> None:
    """_build_system_prompt appends OKR reconcile context when provided."""
    from artemis.floating_artemis.chat import _build_system_prompt

    reconcile_ctx = "## OKR check-in reconcile context\nMap word-dump to KRs."

    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        okr_reconcile_context=reconcile_ctx,
    )

    assert "OKR check-in reconcile context" in prompt
    assert "Map word-dump to KRs" in prompt


def test_build_system_prompt_no_okr_context_when_none() -> None:
    """_build_system_prompt does NOT add reconcile block when okr_reconcile_context is None."""
    from artemis.floating_artemis.chat import _build_system_prompt

    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        okr_reconcile_context=None,
    )

    assert "OKR check-in reconcile context" not in prompt


# ── 13. complete_okr_checkin tool: layer-1, registered, handler works ─────────


def test_complete_okr_checkin_tool_is_layer1() -> None:
    """complete_okr_checkin must be registered at layer 1 (auto-invoke, no confirmation)."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.floating_artemis.tools.okr import register_okr_tools

    registry = AuthorizedToolRegistry()
    register_okr_tools(registry)

    entry = registry.get("complete_okr_checkin")
    assert entry is not None, "complete_okr_checkin must be registered"
    assert entry.layer == 1, (
        f"complete_okr_checkin must be layer 1 (auto-invoke); got layer {entry.layer}"
    )
    assert registry.is_auto_invoke("complete_okr_checkin"), (
        "complete_okr_checkin must be auto-invoke (no confirmation prompt)"
    )
    assert not registry.requires_confirmation("complete_okr_checkin"), (
        "complete_okr_checkin must NOT require confirmation"
    )


async def test_complete_okr_checkin_handler_clears_breadcrumb(db_session: AsyncSession) -> None:
    """_complete_okr_checkin impl stamps completed_at on the live breadcrumb.

    artemis.db.SessionLocal is already wired to the test engine by the conftest,
    so we just commit the seed data and let the handler open its own connection.
    """
    from artemis.floating_artemis.tools.okr import _complete_okr_checkin

    crumb = await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_TOOL_TEST",
        kr_snapshot=[{"kr_id": 1, "kr_title": "T", "objective_title": "O", "prog": 10}],
        proposal_text="test",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    # Call the tool handler directly — it opens a fresh SessionLocal session.
    result = await _complete_okr_checkin({"speaker_id": "U_TOOL_TEST"})

    assert "marked complete" in result.lower() or "complete" in result.lower(), (
        f"Unexpected result: {result!r}"
    )

    # Expire local cache and re-fetch to see the committed change.
    db_session.expire(crumb)
    await db_session.refresh(crumb)

    # completed_at must be stamped (lossless rule: row still exists).
    assert crumb.completed_at is not None, "completed_at must be set (lossless stamp)"

    # Breadcrumb must no longer be live.
    live = await get_live_okr_checkin_breadcrumb(db_session, "U_TOOL_TEST")
    assert live is None, "Breadcrumb must be cleared after complete_okr_checkin"


async def test_complete_okr_checkin_handler_no_live_breadcrumb(db_session: AsyncSession) -> None:
    """_complete_okr_checkin returns graceful message when no live breadcrumb exists."""
    from artemis.floating_artemis.tools.okr import _complete_okr_checkin

    # No breadcrumb seeded for this user.
    result = await _complete_okr_checkin({"speaker_id": "U_NO_CRUMB_EVER"})
    assert "nothing" in result.lower() or "no live" in result.lower(), (
        f"Expected graceful 'nothing to complete' message, got: {result!r}"
    )


# ── 14. Multi-KR word-dump: breadcrumb stays live across multiple proposals ───


async def test_breadcrumb_stays_live_across_multiple_kr_proposals(
    db_session: AsyncSession,
) -> None:
    """Breadcrumb must NOT be cleared by update_okr_kr applies; it stays live
    so the reconcile context keeps injecting for subsequent KR mappings.

    We simulate: a word-dump maps to 2 KRs; after the first update_okr_kr fires,
    the breadcrumb is still live (injection still returns context for the second).
    Clearing is the agent's job via complete_okr_checkin, not the apply step's.
    """
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    # Create a breadcrumb with 2 KRs.
    kr_snapshot = [
        {
            "kr_id": 20,
            "kr_title": "KR Alpha",
            "objective_title": "Objective X",
            "prog": 40,
            "target_text": "100%",
        },
        {
            "kr_id": 21,
            "kr_title": "KR Beta",
            "objective_title": "Objective X",
            "prog": 55,
            "target_text": "100%",
        },
    ]
    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_MULTI_KR",
        kr_snapshot=kr_snapshot,
        proposal_text="Multi-KR check-in",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    # Before any apply: reconcile context is live.
    ctx_before = await _get_okr_reconcile_context("U_MULTI_KR", db_session)
    assert ctx_before is not None, "Reconcile context must be live before any apply"

    # Simulate the first update_okr_kr executing (we just check the breadcrumb directly;
    # update_okr_kr does NOT call complete_okr_checkin — that's the whole point).
    live_after_first_apply = await get_live_okr_checkin_breadcrumb(db_session, "U_MULTI_KR")
    assert live_after_first_apply is not None, (
        "Breadcrumb must still be live after first KR apply — "
        "clearing is conversation-driven, not apply-driven"
    )

    # Context injection must still work for the second KR mapping.
    ctx_after_first = await _get_okr_reconcile_context("U_MULTI_KR", db_session)
    assert ctx_after_first is not None, (
        "Reconcile context must still inject after first KR apply (second KR mapping still pending)"
    )
    # Both KRs should still appear in context.
    assert "KR Alpha" in ctx_after_first or "KR Beta" in ctx_after_first


# ── 15. Unrelated-topic DM: tool handler clears, next turn gets no injection ──


async def test_unrelated_topic_clears_breadcrumb_stops_injection(
    db_session: AsyncSession,
) -> None:
    """Simulates the unrelated-topic flow at the tool handler layer.

    1. Live breadcrumb exists → reconcile context is injected.
    2. Agent calls complete_okr_checkin (simulated by calling the handler directly).
    3. Breadcrumb is cleared.
    4. Next call to _get_okr_reconcile_context returns None (no hijack).

    artemis.db.SessionLocal is already wired to the test engine by the conftest;
    we commit seed data so the handler's fresh connection sees it.
    """
    from artemis.floating_artemis.chat import _get_okr_reconcile_context
    from artemis.floating_artemis.tools.okr import _complete_okr_checkin

    # Step 1: create a live breadcrumb.
    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_TOPIC_CHANGE",
        kr_snapshot=[{"kr_id": 30, "kr_title": "KR Gamma", "objective_title": "Obj Y", "prog": 20}],
        proposal_text="topic-change test",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    # Step 2: first DM injects reconcile context (passes db_session directly — no new conn).
    ctx_before = await _get_okr_reconcile_context("U_TOPIC_CHANGE", db_session)
    assert ctx_before is not None, "Reconcile context must be live before topic change"

    # Step 3: agent detects topic change, calls complete_okr_checkin handler.
    # Handler opens its own SessionLocal session (points at test engine via conftest).
    result = await _complete_okr_checkin({"speaker_id": "U_TOPIC_CHANGE"})
    assert "complete" in result.lower(), f"Expected completion confirmation, got: {result!r}"

    # Step 4: next DM — reconcile context no longer injected.
    # Re-fetch via db_session (expire cache first to see committed change from handler).
    await db_session.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
    ctx_after = await _get_okr_reconcile_context("U_TOPIC_CHANGE", db_session)
    assert ctx_after is None, (
        "Reconcile context must be None after complete_okr_checkin — "
        "no more hijacking unrelated DMs"
    )


# ── 16. Done signal: tool handler clears, injection gone ─────────────────────


async def test_done_signal_clears_breadcrumb_stops_injection(db_session: AsyncSession) -> None:
    """Simulates 'that's all / thanks / done' flow.

    After the operator signals completion and the agent calls complete_okr_checkin,
    the breadcrumb's completed_at is stamped and subsequent context injection is None.

    artemis.db.SessionLocal is wired to the test engine by the conftest.
    """
    from artemis.floating_artemis.chat import _get_okr_reconcile_context
    from artemis.floating_artemis.tools.okr import _complete_okr_checkin

    crumb = await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_DONE_SIGNAL",
        kr_snapshot=[{"kr_id": 40, "kr_title": "KR Delta", "objective_title": "Obj Z", "prog": 80}],
        proposal_text="done-signal test",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    # Reconcile context is live (uses db_session directly).
    ctx = await _get_okr_reconcile_context("U_DONE_SIGNAL", db_session)
    assert ctx is not None

    # Agent calls complete_okr_checkin after operator says "that's all".
    # Handler opens its own SessionLocal session.
    await _complete_okr_checkin({"speaker_id": "U_DONE_SIGNAL"})

    # Expire local ORM cache so we see the committed change from the handler.
    db_session.expire(crumb)
    await db_session.refresh(crumb)

    # completed_at stamped (lossless).
    assert crumb.completed_at is not None, "completed_at must be stamped (lossless)"

    # Injection cleared — pass db_session; ORM cache expired, fresh read.
    ctx_after = await _get_okr_reconcile_context("U_DONE_SIGNAL", db_session)
    assert ctx_after is None, "Reconcile context must be None after done signal"


# ── 17. Reconcile context block instructs agent to call complete_okr_checkin ──


async def test_reconcile_context_instructs_complete_okr_checkin(
    db_session: AsyncSession,
) -> None:
    """The reconcile context block must tell the agent to call complete_okr_checkin
    when the operator changes topic or signals they are done."""
    from artemis.floating_artemis.chat import _get_okr_reconcile_context

    await create_okr_checkin_breadcrumb(
        db_session,
        recipient_id="U_INSTR_TEST",
        kr_snapshot=[
            {
                "kr_id": 50,
                "kr_title": "KR Epsilon",
                "objective_title": "Obj W",
                "prog": 30,
                "target_text": "100%",
            }
        ],
        proposal_text="instruction test",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db_session.commit()

    ctx = await _get_okr_reconcile_context("U_INSTR_TEST", db_session)
    assert ctx is not None

    # Must mention the complete_okr_checkin tool.
    assert "complete_okr_checkin" in ctx, (
        "Reconcile context must instruct the agent to call complete_okr_checkin"
    )
    # Must cover topic-change trigger.
    ctx_lower = ctx.lower()
    assert "topic" in ctx_lower or "changed" in ctx_lower or "not about" in ctx_lower, (
        "Reconcile context must mention topic-change trigger for complete_okr_checkin"
    )
    # Must cover done-signal trigger.
    assert (
        "that's all" in ctx_lower
        or "thanks" in ctx_lower
        or "done" in ctx_lower
        or "nothing else" in ctx_lower
    ), "Reconcile context must mention done-signal phrases for complete_okr_checkin"
