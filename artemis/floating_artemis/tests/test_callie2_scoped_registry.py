"""Callie's scoped tool registry (CALLIE-2).

Before this, Callie fell through to the general registration path in
``build_authorized_tool_registry`` and held everything the app offers minus
whatever happened not to be plugged in -- protected by accident (missing
Gmail/Granola credentials), not by design. See
``docs/marketing-intelligence-direction.md`` ("What Callie can see", reviewed
2026-08-14) for the finding and ``briefs/callie-2-scoped-registry.md`` for the
keep/drop analysis this suite verifies.

Kai and Ares already get their own early-return registries
(``_build_kai_tool_registry`` / ``_build_ares_tool_registry``); this file is
the equivalent coverage for Callie's ``_build_callie_tool_registry``.
"""

from __future__ import annotations

from typing import Any

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.chat import _build_auto_invoke_tool_registry
from artemis.floating_artemis.tool_registry import (
    _build_callie_tool_registry,
    build_authorized_tool_registry,
)

# ── Explicit expected sets (not re-derivations of the code under test) ────────

# Artemis's general-path registry, worst case (every surface available), as
# it existed before CALLIE-2 and must still exist after. If this list ever
# needs to change, that is a deliberate decision to review on its own merits,
# not a side effect of scoping Callie.
_ALL_SURFACES = {
    "okr",
    "writing-rules",
    "marketing-os",
    "signal-queue",
    "jira-board",
    "meetings",
}

_ARTEMIS_EXPECTED_TOOL_NAMES = {
    # core (register_core_tools)
    "query_memory",
    "write_memory",
    "list_scopes",
    "surface_status",
    "list_routes",
    "read_file",
    "propose_edit",
    "set_pref",
    "set_brief_exclusion",
    "clear_brief_exclusion",
    "spawn_subagent",
    # directory (register_directory_tools)
    "resolve_person",
    # builders (register_builders_tools)
    "list_agents",
    "list_workflows",
    "list_skills",
    "list_chains",
    "list_dags",
    "run_agent",
    "run_workflow",
    "propose_agent",
    "propose_workflow",
    "propose_skill",
    # system (register_system_tools)
    "health_check",
    "recent_failures",
    "propose_fix",
    # okr (register_okr_tools, surface-gated)
    "list_okr_objectives",
    "stage_okr_updates",
    "update_okr_kr",
    "update_okr_krs",
    "complete_okr_checkin",
    # writing rules (register_writing_rules_tools, surface-gated)
    "list_writing_rules",
    "propose_writing_rule",
    # marketing (register_marketing_tools, surface-gated)
    "find_by_keyword",
    "get_message_compass",
    "search_claims_register",
    "get_campaign_performance",
    "list_signals",
    "get_signal",
    "qualify_signal",
    "approve_signal",
    "reject_signal",
    "snooze_signal",
    "list_candidates",
    "assemble_brief",
    "submit_draft_for_review",
    "decide_approval",
    "list_scout_runs",
    "fire_scout",
    "get_active_rulesets",
    "propose_ruleset_change",
    "get_district_contacts",
    "list_content_assets",
    "link_content_asset",
    "post_analyst_message",
    # slack (register_slack_tools — include_dm defaults True for Artemis)
    "send_slack_message",
    "send_slack_dm",
    "read_slack_channel",
    "react_to_slack_message",
    "list_slack_channels",
    # gcal (register_gcal_tools, unconditional)
    "list_calendars",
    "list_events",
    "create_event",
    "update_event",
    "delete_event",
    # gmail (register_gmail_tools, unconditional)
    "list_recent_gmail_messages",
    "get_gmail_thread",
    # jira (register_jira_tools, surface-gated)
    "search_jira",
    "get_jira_issue",
    "list_jira_assignable_users",
    "add_jira_comment",
    "transition_jira_issue",
    "assign_jira_issue",
    "create_jira_issue",
    # granola (register_granola_tools, surface-gated)
    "list_recent_meetings",
    "get_meeting_transcript",
    "get_meeting_summary",
}

# Callie's scoped registry (CALLIE-2), full authorized set (all layers).
_CALLIE_EXPECTED_TOOL_NAMES = {
    "query_memory",
    "write_memory",
    "resolve_person",
    "find_by_keyword",
    "get_message_compass",
    "search_claims_register",
    "get_campaign_performance",
    "list_signals",
    "get_signal",
    "qualify_signal",
    "approve_signal",
    "reject_signal",
    "snooze_signal",
    "list_candidates",
    "assemble_brief",
    "submit_draft_for_review",
    "decide_approval",
    "list_scout_runs",
    "fire_scout",
    "get_active_rulesets",
    "propose_ruleset_change",
    "get_district_contacts",
    "list_content_assets",
    "link_content_asset",
    "post_analyst_message",
    "dispatch_research",
    "list_writing_rules",
    "propose_writing_rule",
    "get_screentime_report",
    "record_screentime_feedback",
    "send_slack_message",
    "read_slack_channel",
    "react_to_slack_message",
    "list_slack_channels",
    "send_guarded_dm",
}

# Tools that must never reach Callie's production (layer<=2, claude-code MCP)
# path -- per the brief's "Tests" checklist, verbatim.
_FORBIDDEN_PRODUCTION_TOOL_NAMES = {
    "run_agent",
    "run_workflow",
    "spawn_subagent",
    "recent_failures",
    "list_scopes",
    "health_check",
    "read_file",
    "propose_edit",
    "propose_fix",
    "list_agents",
    "list_dags",
    "list_routes",
}
_FORBIDDEN_PRODUCTION_PREFIXES = ("gmail", "calendar", "event", "okr", "jira")


def _callie_auto_invoke_names(speaker_id: str | None = "U_TEST") -> set[str]:
    """Callie's production tool NAMES, as exposed on the claude-code MCP path.

    ``_build_auto_invoke_tool_registry`` is Callie's real production path.
    Every tool in her authorized registry gets a name here regardless of
    layer -- layer-1/2 tools run immediately, layer-3/4 tools get a staging
    wrapper instead of their real impl (see that function's docstring) -- so
    a tool is only truly absent from production if it is absent from her
    AUTHORIZED registry in the first place. That is exactly what this helper,
    and the "forbidden" assertions below, check.
    """
    auth_registry = build_authorized_tool_registry(
        set(), agent_id="callie", speaker_id=speaker_id
    )
    auto_registry = _build_auto_invoke_tool_registry(auth_registry, "test-callie-session")
    return {tool.name for tool in auto_registry.specs()}


# ── 1. Production registry excludes internal-operating-info + agency tools ──


def test_callie_production_registry_excludes_forbidden_tools() -> None:
    names = _callie_auto_invoke_names()
    present_forbidden = names & _FORBIDDEN_PRODUCTION_TOOL_NAMES
    assert not present_forbidden, f"Callie's production registry must not expose {present_forbidden}"

    prefixed = [n for n in names if n.startswith(_FORBIDDEN_PRODUCTION_PREFIXES)]
    assert not prefixed, f"Callie's production registry must not expose {prefixed}"


def test_callie_full_authorized_registry_matches_explicit_expected_set() -> None:
    """Callie's whole authorized registry (all layers) is exactly the KEEP list."""
    registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id="U_TEST")
    names = {entry.tool.name for entry in registry.all_entries()}
    assert names == _CALLIE_EXPECTED_TOOL_NAMES


# ── 2. She still has everything she needs ────────────────────────────────────


def test_callie_production_registry_keeps_required_tools() -> None:
    names = _callie_auto_invoke_names()
    for required in (
        "dispatch_research",
        "send_guarded_dm",
        "query_memory",
        "write_memory",
        "get_district_contacts",
        "resolve_person",
        "send_slack_message",  # post
        "read_slack_channel",  # read
    ):
        assert required in names, f"Callie must still have {required}"


# ── 3. Raw send_slack_dm stays removed (CALLIE-1 guarantee, re-verified here) ─


def test_callie_still_lacks_raw_send_slack_dm() -> None:
    registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id="U_TEST")
    assert "send_slack_dm" not in registry


# ── 4. query_memory stays the scope-gated variant for agent_id='callie' ──────


async def test_callie_query_memory_is_still_scope_gated() -> None:
    """A scope outside Callie's allowance (agent:artemis) must return empty --
    proving the registered impl is still _make_query_memory('callie'), not the
    ungated `_query_memory` fallback (which would happily run the DB query).
    """
    registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id="U_TEST")
    entry = registry.get("query_memory")
    assert entry is not None
    result = await entry.impl({"query": "anything", "scope": "agent:artemis"})
    assert result == "No relevant memory found.", (
        "query_memory must deny agent:artemis scope for Callie -- if this "
        "fails, the ungated _query_memory may have been wired in by mistake "
        "(M3 regression, widens rather than narrows her memory reach)"
    )


def test_callie_builder_uses_gated_query_memory_not_bare_fallback() -> None:
    """_build_callie_tool_registry's query_memory impl must not be the bare,
    ungated `_query_memory` module function -- see core.py's own warning that
    it is "NOT registered in any live tool registry"."""
    from artemis.floating_artemis.tools.core import _query_memory

    registry = _build_callie_tool_registry("callie")
    entry = registry.get("query_memory")
    assert entry is not None
    assert entry.impl is not _query_memory, (
        "Callie's query_memory must be _make_query_memory('callie'), never "
        "the ungated bare fallback"
    )


# ── 5. Artemis's registry is byte-for-byte unchanged ─────────────────────────


def test_artemis_registry_unchanged_by_callie2() -> None:
    """Explicit expected list, not a snapshot of new behaviour (per the brief)."""
    registry = build_authorized_tool_registry(_ALL_SURFACES, agent_id="artemis")
    names = {entry.tool.name for entry in registry.all_entries()}
    assert names == _ARTEMIS_EXPECTED_TOOL_NAMES


def test_artemis_still_has_raw_send_slack_dm() -> None:
    """CALLIE-1/CALLIE-2 narrow Callie only; Artemis's send_slack_dm is untouched."""
    registry = build_authorized_tool_registry(set(), agent_id="artemis")
    assert "send_slack_dm" in registry


# ── 6. Kai's and Ares's registries are unchanged ─────────────────────────────


def test_kai_registry_unchanged_by_callie2() -> None:
    registry = build_authorized_tool_registry(set(), agent_id="kai")
    names = {entry.tool.name for entry in registry.all_entries()}
    assert names == {
        "search_enablement_assets",
        "get_enablement_asset",
        "list_enablement_facets",
        "flag_catalog_gap",
        "update_asset_summary",
    }


def test_ares_registry_unchanged_by_callie2() -> None:
    registry = build_authorized_tool_registry({"dev-projects"}, agent_id="ares")
    names = {entry.tool.name for entry in registry.all_entries()}
    assert names == {"query_memory", "list_scopes", "surface_status", "read_file"}


# ── 7. A new general-path registration must not silently reach Callie ───────


def test_sentinel_tool_on_general_path_does_not_reach_callie(monkeypatch: Any) -> None:
    """Simulates a future `register_*` call added to the general fallthrough
    path in build_authorized_tool_registry. Proves Callie's early return holds
    by monkeypatching one of the general-path registration functions to also
    plant a sentinel tool, then asserting it is absent from Callie's registry
    while present for Artemis (who does fall through to the general path).
    """
    import artemis.floating_artemis.tool_registry as tool_registry_mod
    from artemis.agent.types import Tool
    from artemis.floating_artemis.tools.system import (
        register_system_tools as original_register_system_tools,
    )

    sentinel_tool = Tool(
        name="__sentinel_future_tool__",
        description="Sentinel for CALLIE-2's early-return regression test.",
        input_schema={"type": "object", "properties": {}, "required": []},
    )

    async def _sentinel_impl(inp: dict[str, Any]) -> str:  # noqa: ARG001
        return "sentinel"

    def _patched_register_system_tools(registry: AuthorizedToolRegistry) -> None:
        original_register_system_tools(registry)
        registry.register(sentinel_tool, _sentinel_impl, layer=1)

    monkeypatch.setattr(tool_registry_mod, "register_system_tools", _patched_register_system_tools)

    artemis_registry = build_authorized_tool_registry(set(), agent_id="artemis")
    assert "__sentinel_future_tool__" in artemis_registry, (
        "sanity check: the patch must actually land on the general path"
    )

    callie_registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id="U_TEST")
    assert "__sentinel_future_tool__" not in callie_registry, (
        "Callie's early return must not fall through to a newly-added "
        "general-path registration"
    )
