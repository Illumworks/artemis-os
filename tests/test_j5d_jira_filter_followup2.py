"""Tests for worker/jira-filter-followup2 bug fixes.

Coverage:
  Bug 1 (dropdown positioning) — visual/CSS only; no unit test possible.
  Bug 2 (filter list roster):
    - jira_overview route includes teamRoster [{id, name}] in response
      when team_members is configured + project_key is set
    - teamRoster is [] when me_only=True (brief path)
    - teamRoster is [] when no team_members configured
    - teamRoster is [] when no project_key (can't call assignable-users)
    - Frontend roster-union logic (pure-Python port):
        * all team-roster members appear in people list regardless of tickets
        * ticket assignees not in roster still appear
        * de-duplication by account ID
        * currentUser always in list
        * Unassigned always in list
        * empty teamRoster falls back to ticket-assignees + currentUser
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.integrations.jira.client import JiraAPIError, JiraClient


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _mock_response(status: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _myself_response(account_id: str = "ME123", display_name: str = "Jon Fila") -> MagicMock:
    return _mock_response(
        200,
        {
            "accountId": account_id,
            "displayName": display_name,
            "emailAddress": f"{account_id.lower()}@example.com",
        },
    )


def _empty_search() -> MagicMock:
    return _mock_response(200, {"issues": []})


def _user(account_id: str, display_name: str) -> dict[str, Any]:
    return {
        "accountId": account_id,
        "displayName": display_name,
        "emailAddress": f"{account_id.lower()}@example.com",
        "avatarUrls": {"48x48": "", "32x32": ""},
    }


def _make_client() -> JiraClient:
    return JiraClient("https://example.atlassian.net", "jon@example.com", "secret")


# ── Backend: teamRoster in overview response ──────────────────────────────────


@pytest.mark.asyncio
async def test_jira_overview_includes_team_roster_with_team_and_project() -> None:
    """When team_members + project_key are both set, teamRoster must be populated."""
    raw_cfg = {
        "site_url": "https://test.atlassian.net",
        "email": "jon@example.com",
        "api_token": "tok",
        "project_key": "ENG",
        "team_members": ["U1", "U2", "U3"],
    }
    assignable_users = [
        _user("U1", "Alice"),
        _user("U2", "Bob"),
        _user("U3", "Angela Miata"),
    ]
    assignable_resp = _mock_response(200, assignable_users)

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw_cfg,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        # /myself → ME123; /user/assignable/search → team list
        mock_http.get = AsyncMock(side_effect=[_myself_response(), assignable_resp])
        mock_http.post = AsyncMock(return_value=_empty_search())
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_overview

        mock_session = AsyncMock()
        result = await jira_overview(_=None, session=mock_session)

    assert "teamRoster" in result, "teamRoster key must be present in overview response"
    roster = result["teamRoster"]
    assert len(roster) == 3
    ids = {m["id"] for m in roster}
    assert ids == {"U1", "U2", "U3"}
    names = {m["name"] for m in roster}
    assert "Angela Miata" in names, "Display names must be resolved from assignable-users"


@pytest.mark.asyncio
async def test_jira_overview_team_roster_empty_when_no_team_configured() -> None:
    """teamRoster must be [] when team_members is empty/absent."""
    raw_cfg = {
        "site_url": "https://test.atlassian.net",
        "email": "jon@example.com",
        "api_token": "tok",
        "project_key": "ENG",
        # no team_members key
    }

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw_cfg,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search())
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_overview

        mock_session = AsyncMock()
        result = await jira_overview(_=None, session=mock_session)

    assert result.get("teamRoster") == [], "teamRoster must be [] when no team configured"


@pytest.mark.asyncio
async def test_jira_overview_team_roster_empty_when_me_only() -> None:
    """Brief path (me_only=True) must not fetch team roster."""
    raw_cfg = {
        "site_url": "https://test.atlassian.net",
        "email": "jon@example.com",
        "api_token": "tok",
        "project_key": "ENG",
        "team_members": ["U1", "U2"],
    }

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw_cfg,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search())
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_overview

        mock_session = AsyncMock()
        result = await jira_overview(_=None, session=mock_session, me_only=True)

    assert result.get("teamRoster") == [], "Brief path must not populate teamRoster"
    # Confirm we only called /myself once (no extra assignable-users call)
    assert mock_http.get.call_count == 1, "me_only path must not call assignable-users endpoint"


@pytest.mark.asyncio
async def test_jira_overview_team_roster_empty_when_no_project_key() -> None:
    """No project_key → cannot call assignable-users; teamRoster stays []."""
    raw_cfg = {
        "site_url": "https://test.atlassian.net",
        "email": "jon@example.com",
        "api_token": "tok",
        # no project_key
        "team_members": ["U1"],
    }

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw_cfg,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search())
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_overview

        mock_session = AsyncMock()
        result = await jira_overview(_=None, session=mock_session)

    assert result.get("teamRoster") == [], "No project_key → teamRoster must be []"


# ── Frontend roster-union logic (pure-Python port) ─────────────────────────────
#
# The JS buildJiraDedicatedViewModel people-list logic is ported here as a
# pure-Python helper so it can be tested without a browser.  This keeps the
# tests in the existing pytest suite and avoids a Node dependency.
#
# The helper mirrors the exact union-build order in home.js:
#   1. teamRoster (id + name from backend)
#   2. ticket assignees from columns
#   3. currentUser
#   4. Unassigned sentinel appended last
# De-duped by id; people with zero tickets are kept if they are in the
# teamRoster or are the currentUser (pinned).

UNASSIGNED_ID = "__unassigned__"


def _build_people_list(
    *,
    team_roster: list[dict],  # [{id, name}]
    columns: list[dict],  # [{items: [{assigneeId, assignee}]}]
    current_user_id: str = "",
    current_user_name: str = "",
) -> list[dict]:
    """Python port of the roster-union logic from buildJiraDedicatedViewModel."""
    people_map: dict[str, dict] = {}

    # 1. Seed from team roster
    for member in team_roster:
        mid = member.get("id", "")
        if mid and mid not in people_map:
            people_map[mid] = {"id": mid, "name": member.get("name") or mid}

    # 2. Seed from ticket assignees
    for col in columns:
        for item in col.get("items", []):
            aid = item.get("assigneeId", "")
            if aid and aid not in people_map:
                people_map[aid] = {"id": aid, "name": item.get("assignee") or aid}

    # 3. Current user
    if current_user_id and current_user_id not in people_map:
        people_map[current_user_id] = {
            "id": current_user_id,
            "name": current_user_name or current_user_id,
        }

    people = sorted(people_map.values(), key=lambda p: p["name"])
    people.append({"id": UNASSIGNED_ID, "name": "Unassigned"})
    return people


def _build_swimlanes(
    *,
    people: list[dict],
    columns: list[dict],
    team_roster_ids: set[str],
    owner_id: str = "",
) -> list[dict]:
    """Python port of swimlane building — returns people whose lane is shown."""
    pinned = team_roster_ids | ({owner_id} if owner_id else set())
    col_keys = ["todo", "prog", "blocked", "review"]
    col_by_key = {c["key"]: c for c in columns}
    shown = []
    for person in people:
        cells = {}
        for key in col_keys:
            col = col_by_key.get(key, {})
            items = [
                i
                for i in col.get("items", [])
                if (person["id"] == UNASSIGNED_ID and not i.get("assigneeId"))
                or i.get("assigneeId") == person["id"]
            ]
            cells[key] = items
        total = sum(len(cells[k]) for k in col_keys)
        is_pinned = person["id"] == UNASSIGNED_ID or person["id"] in pinned
        if total > 0 or is_pinned:
            shown.append({"person": person, "cells": cells})
    return shown


# ── Roster-union: all team members appear regardless of tickets ────────────────


def test_people_list_includes_all_team_roster_members() -> None:
    """All team roster members must appear in the filter list, even with 0 tickets."""
    roster = [
        {"id": "U1", "name": "Alice"},
        {"id": "U2", "name": "Bob"},
        {"id": "U3", "name": "Angela Miata"},  # no tickets
    ]
    columns = [
        {"key": "todo", "items": [{"assigneeId": "U1", "assignee": "Alice"}]},
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(team_roster=roster, columns=columns, current_user_id="U1")
    ids = {p["id"] for p in people}
    assert "U3" in ids, "Angela Miata (0 tickets) must still be in the filter list"
    assert "U1" in ids
    assert "U2" in ids
    assert UNASSIGNED_ID in ids


def test_people_list_includes_ticket_assignee_not_in_roster() -> None:
    """Assignees on tickets who are NOT in the team roster must still appear."""
    roster = [{"id": "U1", "name": "Alice"}]
    columns = [
        {
            "key": "todo",
            "items": [
                {"assigneeId": "GUEST99", "assignee": "External Guest"},
            ],
        },
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(team_roster=roster, columns=columns, current_user_id="U1")
    ids = {p["id"] for p in people}
    assert "GUEST99" in ids, "Ticket assignee not in roster must be included"


def test_people_list_deduplicates_by_account_id() -> None:
    """A person in both teamRoster and ticket assignees must appear only once."""
    roster = [{"id": "U1", "name": "Alice from roster"}]
    columns = [
        {"key": "todo", "items": [{"assigneeId": "U1", "assignee": "Alice from ticket"}]},
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(team_roster=roster, columns=columns)
    u1_entries = [p for p in people if p["id"] == "U1"]
    assert len(u1_entries) == 1, "U1 must appear exactly once (de-duped)"
    # Roster name wins (roster is seeded first)
    assert u1_entries[0]["name"] == "Alice from roster"


def test_people_list_always_includes_current_user() -> None:
    """currentUser must appear even with no tickets and not in teamRoster."""
    roster: list[dict] = []
    columns = [
        {"key": "todo", "items": []},
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(
        team_roster=roster,
        columns=columns,
        current_user_id="OWNER",
        current_user_name="Jon Fila",
    )
    ids = {p["id"] for p in people}
    assert "OWNER" in ids, "currentUser must always be in the filter list"
    owner = next(p for p in people if p["id"] == "OWNER")
    assert owner["name"] == "Jon Fila"


def test_people_list_always_ends_with_unassigned() -> None:
    """Unassigned sentinel must always be the last entry."""
    roster = [{"id": "U1", "name": "Alice"}]
    columns = [
        {"key": "todo", "items": []},
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(team_roster=roster, columns=columns)
    assert people[-1]["id"] == UNASSIGNED_ID, "Unassigned must be last"


def test_people_list_empty_team_roster_falls_back_to_ticket_assignees() -> None:
    """When teamRoster is empty, only ticket-assignees + currentUser appear."""
    roster: list[dict] = []
    columns = [
        {"key": "todo", "items": [{"assigneeId": "U1", "assignee": "Alice"}]},
        {"key": "prog", "items": [{"assigneeId": "U2", "assignee": "Bob"}]},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(
        team_roster=roster, columns=columns, current_user_id="OWNER", current_user_name="Owner"
    )
    ids = {p["id"] for p in people}
    assert ids == {"U1", "U2", "OWNER", UNASSIGNED_ID}


# ── Swimlane pinning: zero-ticket team members get an (empty) lane ─────────────


def test_swimlane_keeps_zero_ticket_team_member_lane() -> None:
    """A team-roster member with 0 tickets must have a lane (not filtered out)."""
    roster = [
        {"id": "U1", "name": "Alice"},
        {"id": "ANGELA", "name": "Angela Miata"},
    ]
    columns = [
        {
            "key": "todo",
            "items": [
                {
                    "assigneeId": "U1",
                    "assignee": "Alice",
                    "key": "T-1",
                    "title": "Task",
                    "priority": "Medium",
                    "labels": [],
                    "created": "2026-01-01",
                    "assigneeId": "U1",
                    "commentCount": 0,
                    "attachmentCount": 0,
                    "worklogTotal": 0,
                    "sprint": "",
                }
            ],
        },
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    people = _build_people_list(team_roster=roster, columns=columns, current_user_id="U1")
    team_roster_ids = {"U1", "ANGELA"}
    lanes = _build_swimlanes(
        people=people, columns=columns, team_roster_ids=team_roster_ids, owner_id="U1"
    )
    lane_ids = {lane["person"]["id"] for lane in lanes}
    assert "ANGELA" in lane_ids, "Angela Miata (0 tickets) must still have a swim lane"


def test_swimlane_filters_out_ad_hoc_assignee_with_zero_tickets() -> None:
    """An ad-hoc assignee NOT in the roster must be dropped when they have no tickets.

    This preserves old behaviour: once their last ticket closes the empty lane
    disappears rather than persisting forever.
    """
    # GUEST99 appears in the people map because they had a ticket, but after
    # filtering only people with tickets (plus pinned) remain.
    roster = [{"id": "U1", "name": "Alice"}]
    columns = [
        {"key": "todo", "items": []},
        {"key": "prog", "items": []},
        {"key": "blocked", "items": []},
        {"key": "review", "items": []},
    ]
    # Manually add GUEST99 to people (as if they appeared in a previous render)
    people = [
        {"id": "GUEST99", "name": "External Guest"},
        {"id": "U1", "name": "Alice"},
        {"id": UNASSIGNED_ID, "name": "Unassigned"},
    ]
    team_roster_ids = {"U1"}
    lanes = _build_swimlanes(
        people=people, columns=columns, team_roster_ids=team_roster_ids, owner_id="U1"
    )
    lane_ids = {lane["person"]["id"] for lane in lanes}
    assert "GUEST99" not in lane_ids, "Ad-hoc assignee with 0 tickets must be pruned"
    assert "U1" in lane_ids, "Pinned team member must still appear"
