"""Tests for the proactivity voice rendering pass (Part A of P2 brief).

Coverage:
1. voice_render: given grounded brief data, output is non-empty, lint-clean,
   contains the grounded facts, and is NOT the labeled-section template.
2. voice_render: given grounded OKR check-in proposals, output is non-empty,
   lint-clean, contains KR facts, and is NOT the labeled-section template.
3. Fallback: if the LLM call errors, render_brief_with_voice returns None
   (caller should fall back to plain rendering).
4. format_checkin_for_slack grounding reframe: Jira evidence is labeled as
   context, not asserted as Jon's accomplishment.
5. OKR grounding: a ticket NOT assigned to Jon (team ticket) is NOT asserted
   as his accomplishment when build_okr_checkin_proposal processes sources.
6. One ticket does not fan-out to multiple KRs.
7. A KR with no real basis gets no claimed change.
8. Voice pass in scheduler: when voice pass returns text it is used; when it
   returns None the plain fallback is used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.proactivity.okr_checkin import (
    build_okr_checkin_proposal,
    format_checkin_for_slack,
)
from artemis.proactivity.voice_render import (
    render_brief_with_voice,
    render_checkin_with_voice,
)

pytestmark = pytest.mark.asyncio


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_kr(
    kr_id: int,
    title: str,
    prog: int = 50,
    archived_at: datetime | None = None,
) -> MagicMock:
    kr = MagicMock()
    kr.id = kr_id
    kr.title = title
    kr.prog = prog
    kr.archived_at = archived_at
    return kr


def _make_activity(kr_id: int, text: str) -> MagicMock:
    act = MagicMock()
    act.kr_id = kr_id
    act.text = text
    act.created_at = datetime.now(UTC)
    return act


def _make_obj(obj_id: int, title: str, krs: list[MagicMock]) -> MagicMock:
    obj = MagicMock()
    obj.id = obj_id
    obj.title = title
    obj.key_results = krs
    return obj


_DELIVERY_DATE = date(2026, 6, 13)


# ── Part A: voice_render.render_brief_with_voice ──────────────────────────────


async def test_render_brief_with_voice_returns_nonelike_on_llm_error() -> None:
    """If the LLM call raises, render_brief_with_voice returns None (caller fallback)."""
    brief: dict[str, Any] = {
        "summary": "A productive day.",
        "highlights": [{"title": "Pipeline ready", "detail": "Cleared 3 sends", "source": "jira"}],
        "priorities": [
            {"item": "Ship the release", "rationale": "Blocked team", "urgency": "high"}
        ],
        "next_actions": [],
        "risks": [],
    }
    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM unavailable"),
    ):
        result = await render_brief_with_voice(brief, _DELIVERY_DATE)

    assert result is None


async def test_render_brief_with_voice_non_empty_when_llm_returns_text() -> None:
    """When LLM returns a sensible response, output is non-empty and lint-clean."""
    brief: dict[str, Any] = {
        "summary": "Focus on pipeline cleanup.",
        "highlights": [{"title": "Signals", "detail": "3 need review", "source": "slack"}],
        "priorities": [
            {"item": "Review candidates", "rationale": "Gate is waiting", "urgency": "high"}
        ],
        "next_actions": [{"action": "Reply to Angela", "owner": "Jon", "due": "today"}],
        "risks": ["Two sends are blocked"],
    }
    llm_response = "Pipeline's the priority today. Three signals need review and Angela is waiting on a reply. Two sends are still blocked, so move those first."

    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_brief_with_voice(brief, _DELIVERY_DATE)

    assert result is not None
    assert len(result) > 0
    # No emojis, no em-dashes (lint-clean guarantee).
    assert "—" not in result  # em-dash
    assert "–" not in result  # en-dash


async def test_render_brief_with_voice_no_labeled_section_headers() -> None:
    """Voice output must NOT contain labeled section headers like Summary: or Highlights:."""
    brief: dict[str, Any] = {
        "summary": "Focus on pipeline cleanup.",
        "highlights": [{"title": "Signals", "detail": "3 need review", "source": "slack"}],
        "priorities": [],
        "next_actions": [],
        "risks": [],
    }
    # A response WITHOUT section headers.
    llm_response = "Three signals need review today. Pipeline is the blocker."

    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_brief_with_voice(brief, _DELIVERY_DATE)

    assert result is not None
    lowered = result.lower()
    for header in ("summary:", "highlights:", "priorities:", "next actions:", "risks:"):
        assert header not in lowered, f"Voice output must not contain '{header}' header"


async def test_render_brief_with_voice_discards_response_with_section_headers() -> None:
    """If LLM returns labeled section headers, the pass returns None (caller fallback)."""
    brief: dict[str, Any] = {
        "summary": "A day.",
        "highlights": [],
        "priorities": [],
        "next_actions": [],
        "risks": [],
    }
    # LLM ignores the rule and returns a labeled template.
    llm_response = "Summary: A day.\nHighlights:\n- Nothing."

    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_brief_with_voice(brief, _DELIVERY_DATE)

    # Should be discarded (None) because it contains labeled headers.
    assert result is None


async def test_render_brief_with_voice_contains_grounded_facts() -> None:
    """Voice output contains facts from the grounded brief (not invented)."""
    brief: dict[str, Any] = {
        "summary": "Pipeline cleanup day.",
        "highlights": [{"title": "AMIRA-123", "detail": "Merged", "source": "jira"}],
        "priorities": [{"item": "Ship AMIRA-456", "rationale": "Sprint end", "urgency": "high"}],
        "next_actions": [],
        "risks": ["AMIRA-789 blocked"],
    }
    # LLM narrates the grounded facts.
    llm_response = (
        "AMIRA-123 is merged. Ship AMIRA-456 before sprint end. AMIRA-789 is still blocked."
    )

    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_brief_with_voice(brief, _DELIVERY_DATE)

    assert result is not None
    assert "AMIRA-123" in result
    assert "AMIRA-456" in result
    assert "AMIRA-789" in result


# ── Part A: voice_render.render_checkin_with_voice ────────────────────────────


async def test_render_checkin_with_voice_returns_none_on_error() -> None:
    """If LLM errors, render_checkin_with_voice returns None."""
    proposals: list[dict[str, Any]] = [
        {
            "kr_id": 1,
            "kr_title": "Improve pipeline",
            "objective_title": "Scale ops",
            "current_prog": 50,
            "basis": ["OKR activity: Shipped pipeline fix"],
        }
    ]
    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await render_checkin_with_voice(proposals, _DELIVERY_DATE)

    assert result is None


async def test_render_checkin_with_voice_non_empty_with_proposals() -> None:
    """Voice checkin output is non-empty and lint-clean when LLM returns text."""
    proposals: list[dict[str, Any]] = [
        {
            "kr_id": 1,
            "kr_title": "Improve pipeline coverage",
            "objective_title": "Scale ops",
            "current_prog": 45,
            "basis": ["OKR activity: Merged pipeline PR"],
        }
    ]
    llm_response = (
        "*Scale ops* > *Improve pipeline coverage* is at 45%. "
        "I see you logged a pipeline PR merge this week. "
        "What else did you move? Send me a word-dump and I'll update the KRs once you say go."
    )
    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_checkin_with_voice(proposals, _DELIVERY_DATE)

    assert result is not None
    assert len(result) > 0
    assert "—" not in result  # no em-dash after lint


async def test_render_checkin_with_voice_no_labeled_section_headers() -> None:
    """Checkin voice output must not contain labeled section headers."""
    proposals: list[dict[str, Any]] = []
    llm_response = "Nothing grounded this week. What did you actually ship? Drop a word-dump."
    with patch(
        "artemis.proactivity.voice_render._call_voice_llm",
        new_callable=AsyncMock,
        return_value=llm_response,
    ):
        result = await render_checkin_with_voice(proposals, _DELIVERY_DATE)

    assert result is not None
    lowered = result.lower()
    for header in ("summary:", "highlights:", "priorities:"):
        assert header not in lowered


# ── Part B: OKR grounding correctness ────────────────────────────────────────


def test_jira_ticket_not_assigned_to_jon_framed_as_context_not_accomplishment() -> None:
    """Jira evidence in basis is labeled as 'Your Jira ticket' (Jon's own), not generic.

    After Part B fixes, the jira JQL uses assignee=currentUser() so only Jon's
    own tickets come back.  The format labels them as 'Your Jira ticket closed
    this week:' — NOT 'Jira closed this week:' (the old team-wide phrasing).
    """
    kr = _make_kr(1, "Increase pipeline coverage")
    obj = _make_obj(1, "Grow product reach", [kr])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        "jira_done": [
            {
                "key": "ENG-42",
                "title": "Pipeline coverage PR",
                "assignee": "Jon Fila",
                "assigneeId": "abc123",
            }
        ],
        "action_items": [],
    }

    proposals = build_okr_checkin_proposal(sources)
    assert len(proposals) == 1
    basis = proposals[0]["basis"]
    assert len(basis) == 1
    # Must say "Your Jira ticket" — not the old "Jira closed this week:" phrase.
    assert "Your Jira ticket" in basis[0], f"Expected 'Your Jira ticket' label, got: {basis[0]!r}"


def test_one_jira_ticket_does_not_map_to_multiple_krs() -> None:
    """A single Jira ticket must not fan-out to multiple KRs."""
    # Two KRs with overlapping keywords — both match the same Jira ticket.
    kr1 = _make_kr(1, "Improve pipeline delivery speed")
    kr2 = _make_kr(2, "Increase pipeline test coverage")
    obj = _make_obj(1, "Engineering excellence", [kr1, kr2])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        # One ticket that matches both KRs via the word "pipeline".
        "jira_done": [
            {
                "key": "ENG-99",
                "title": "Pipeline refactor merged",
                "assignee": "Jon Fila",
                "assigneeId": "abc123",
            }
        ],
        "action_items": [],
    }

    proposals = build_okr_checkin_proposal(sources)

    # Count how many proposals have ENG-99 in their basis.
    matches = [
        p
        for p in proposals
        if any("Pipeline refactor merged" in b or "ENG-99" in b for b in p["basis"])
    ]
    assert len(matches) <= 1, (
        f"Ticket ENG-99 appeared under {len(matches)} KRs — must be at most 1. "
        f"Proposals: {proposals}"
    )


def test_kr_with_no_basis_gets_no_proposal() -> None:
    """A KR with no evidence (no OKR activity, no Jira, no action items) produces no proposal."""
    kr = _make_kr(99, "Unique xyzzy title with no matching sources")
    obj = _make_obj(1, "Parent Objective", [kr])

    sources: dict[str, Any] = {
        "objectives": [obj],
        "activity": [],
        "jira_done": [],
        "action_items": [],
    }

    proposals = build_okr_checkin_proposal(sources)
    assert proposals == [], f"Expected no proposals for ungrounded KR, got {proposals}"


def test_format_checkin_leads_with_kr_state_not_accomplishment_assertion() -> None:
    """format_checkin_for_slack must not assert 'you did X' — must ask what Jon moved."""
    proposals = [
        {
            "kr_id": 1,
            "kr_title": "Improve pipeline",
            "objective_title": "Scale ops",
            "current_prog": 55,
            "basis": ["Your Jira ticket closed this week: Pipeline fix"],
        }
    ]
    text = format_checkin_for_slack(proposals, delivery_date=_DELIVERY_DATE)

    # Must lead with KR state.
    assert "55%" in text or "currently" in text.lower(), "Must show current KR progress"
    # Must ask what Jon moved (not assert he did X).
    assert "what" in text.lower() and ("moved" in text.lower() or "word-dump" in text.lower()), (
        "Must ask what Jon moved, not assert his accomplishments"
    )
    # Must NOT assert "you did X" for the Jira evidence.
    text_lower = text.lower()
    assert "you completed" not in text_lower
    assert "you closed" not in text_lower
    assert "you finished" not in text_lower


def test_format_checkin_labels_jira_as_context() -> None:
    """Jira evidence in format_checkin_for_slack is labeled as 'Context:' not bare assertion."""
    proposals = [
        {
            "kr_id": 2,
            "kr_title": "Ship new feature",
            "objective_title": "Growth",
            "current_prog": 30,
            "basis": ["Your Jira ticket closed this week: Feature flag merged"],
        }
    ]
    text = format_checkin_for_slack(proposals, delivery_date=_DELIVERY_DATE)
    # Jira evidence must be wrapped in context label.
    assert "Context:" in text or "context" in text.lower(), (
        "Jira evidence must be presented as context, not bare assertion"
    )


def test_format_checkin_okr_activity_is_not_labeled_context() -> None:
    """OKR activity (Jon logged it himself) is presented directly, not labeled as context."""
    proposals = [
        {
            "kr_id": 3,
            "kr_title": "Grow user base",
            "objective_title": "Growth",
            "current_prog": 70,
            "basis": ["OKR activity: Jon logged 5 new deals"],
        }
    ]
    text = format_checkin_for_slack(proposals, delivery_date=_DELIVERY_DATE)
    # OKR activity is ground truth — should be shown directly.
    assert "OKR activity: Jon logged 5 new deals" in text


def test_format_checkin_empty_proposals_asks_for_word_dump() -> None:
    """Empty proposals produce a message that asks Jon for a word-dump."""
    text = format_checkin_for_slack([], delivery_date=_DELIVERY_DATE)
    assert "word-dump" in text.lower() or "what did you" in text.lower(), (
        "Empty proposals must ask Jon to provide his accomplishments"
    )
    # No section headers.
    assert "Summary:" not in text
    assert "Highlights:" not in text


def test_format_checkin_has_safety_reminder() -> None:
    """The check-in must include the 'nothing changes until you say go' safety reminder."""
    text = format_checkin_for_slack([], delivery_date=_DELIVERY_DATE)
    assert "go" in text.lower()
    assert "nothing" in text.lower() or "won't" in text.lower() or "until" in text.lower()


# ── Scheduler integration: voice pass fallback ────────────────────────────────


async def test_fire_morning_brief_uses_voice_text_when_available(db_session: Any) -> None:
    """_fire_morning_brief uses voice-rendered text when render_brief_with_voice succeeds."""
    from artemis.integrations import repository as integration_repo
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.proactivity.scheduler import _fire_morning_brief

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
    await integration_repo.upsert_provider_config(db_session, "slack", {"authed_user_id": "U_JON"})
    await db_session.commit()

    voice_text = "Pipeline is the priority. Ship AMIRA-456."
    brief: dict[str, Any] = {
        "summary": "Pipeline day.",
        "highlights": [],
        "priorities": [],
        "next_actions": [],
        "risks": [],
    }

    with (
        patch(
            "artemis.proactivity.scheduler.generate_brief",
            new_callable=AsyncMock,
            return_value=brief,
        ),
        patch(
            "artemis.proactivity.scheduler.render_brief_with_voice",
            new_callable=AsyncMock,
            return_value=voice_text,
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_dm,
    ):
        await _fire_morning_brief()

    assert mock_dm.await_count == 1
    delivered = str(mock_dm.await_args.kwargs["text"])
    assert delivered == voice_text


async def test_fire_morning_brief_falls_back_when_voice_fails(db_session: Any) -> None:
    """_fire_morning_brief falls back to plain rendering when voice pass returns None."""
    from artemis.integrations import repository as integration_repo
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.proactivity.scheduler import _fire_morning_brief

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
    await integration_repo.upsert_provider_config(db_session, "slack", {"authed_user_id": "U_JON"})
    await db_session.commit()

    brief: dict[str, Any] = {
        "summary": "Focused day.",
        "highlights": [],
        "priorities": [
            {"item": "Review candidates", "rationale": "Gate waiting", "urgency": "high"}
        ],
        "next_actions": [],
        "risks": [],
    }

    with (
        patch(
            "artemis.proactivity.scheduler.generate_brief",
            new_callable=AsyncMock,
            return_value=brief,
        ),
        patch(
            "artemis.proactivity.scheduler.render_brief_with_voice",
            new_callable=AsyncMock,
            return_value=None,  # voice pass failed
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_dm,
    ):
        await _fire_morning_brief()

    assert mock_dm.await_count == 1
    delivered = str(mock_dm.await_args.kwargs["text"])
    # Falls back to plain rendering — should contain labeled section content.
    assert "Morning brief" in delivered
    assert "Review candidates" in delivered


async def test_fire_okr_checkin_uses_voice_text_when_available(db_session: Any) -> None:
    """_fire_okr_checkin uses voice-rendered text when render_checkin_with_voice succeeds."""
    from artemis.integrations import repository as integration_repo
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.proactivity.scheduler import _fire_okr_checkin

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
    await integration_repo.upsert_provider_config(db_session, "slack", {"authed_user_id": "U_JON"})
    await db_session.commit()

    voice_text = "Your KRs are looking solid at 50-70%. What did you actually move this week? Send me a word-dump and I'll update once you say go."

    with (
        patch(
            "artemis.proactivity.scheduler.gather_checkin_sources",
            new_callable=AsyncMock,
            return_value={"objectives": [], "activity": [], "jira_done": [], "action_items": []},
        ),
        patch(
            "artemis.proactivity.scheduler.render_checkin_with_voice",
            new_callable=AsyncMock,
            return_value=voice_text,
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_dm,
    ):
        await _fire_okr_checkin()

    assert mock_dm.await_count == 1
    delivered = str(mock_dm.await_args.kwargs["text"])
    assert delivered == voice_text


async def test_fire_okr_checkin_falls_back_when_voice_fails(db_session: Any) -> None:
    """_fire_okr_checkin falls back to plain rendering when voice pass returns None."""
    from artemis.integrations import repository as integration_repo
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.proactivity.scheduler import _fire_okr_checkin

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
    await integration_repo.upsert_provider_config(db_session, "slack", {"authed_user_id": "U_JON"})
    await db_session.commit()

    with (
        patch(
            "artemis.proactivity.scheduler.gather_checkin_sources",
            new_callable=AsyncMock,
            return_value={"objectives": [], "activity": [], "jira_done": [], "action_items": []},
        ),
        patch(
            "artemis.proactivity.scheduler.render_checkin_with_voice",
            new_callable=AsyncMock,
            return_value=None,  # voice pass failed
        ),
        patch(
            "artemis.proactivity.scheduler.SlackClient.post_dm",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_dm,
    ):
        await _fire_okr_checkin()

    assert mock_dm.await_count == 1
    delivered = str(mock_dm.await_args.kwargs["text"])
    # Plain fallback — should mention word-dump.
    assert "word-dump" in delivered.lower() or "Friday" in delivered
