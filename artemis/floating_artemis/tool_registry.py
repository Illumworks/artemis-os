"""Shared Floating Artemis tool-registry construction helpers.

This module exists so both the in-process chat path and the claude-code MCP
subprocess path can reconstruct the same Floating Artemis tool catalog without
importing the full chat orchestration module.
"""

from __future__ import annotations

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.argus_tools import register_argus_tools
from artemis.floating_artemis.tools.builders import register_builders_tools
from artemis.floating_artemis.tools.callie_dm import register_callie_dm_tool
from artemis.floating_artemis.tools.core import register_core_tools
from artemis.floating_artemis.tools.directory_tools import register_directory_tools
from artemis.floating_artemis.tools.granola_tools import register_granola_tools
from artemis.floating_artemis.tools.jira_tools import register_jira_tools
from artemis.floating_artemis.tools.marketing import register_marketing_tools
from artemis.floating_artemis.tools.okr import register_okr_tools
from artemis.floating_artemis.tools.screentime_tools import register_screentime_report_tools
from artemis.floating_artemis.tools.system import register_system_tools
from artemis.floating_artemis.tools.writing_rules import register_writing_rules_tools
from artemis.integrations.gcal.tools import register_gcal_tools
from artemis.integrations.gmail.tools import register_gmail_tools
from artemis.integrations.slack.tools import register_slack_tools


def _build_kai_tool_registry(speaker_id: str | None = None) -> AuthorizedToolRegistry:
    """Build Kai's registry: three read-only enablement tools + two gated writes.

    Kai (Chiron) is enablement-scoped and near-read-only.  He gets NO personal
    scopes, NO agent:artemis, NO creation/agency tools, NO marketing tools,
    NO gcal/gmail/slack/builders/system tools.

    The ONE exception is ``flag_catalog_gap`` (layer 2), which posts a
    structured gap note into #enablement-library.  It exists because Kai was
    already claiming to escalate without any tool behind it (2026-08-10
    "Escalation filed and noted" — nothing was filed).  Its authorization is
    enforced from ``speaker_id``, the Slack user id resolved from the inbound
    event, which is bound into the implementation as a closure variable so tool
    input cannot spoof it.  ``speaker_id=None`` denies (fail-closed), which is
    what the resume-after-confirm path gets.

    A second identity-gated tool, ``update_asset_summary`` (layer 2), lets the
    people who own the catalog correct a summary in conversation and have it
    re-indexed immediately.  Added 2026-08-11 when the bulk-review model was
    dropped: nobody has time to sift 400+ generated summaries, so corrections
    happen live instead.  Its allowlist is Jon, Sara, and Missy.

    SECURITY: This function is the sole caller of register_enablement_tools,
    register_enablement_gap_flag_tool, and register_enablement_summary_edit_tool.
    Do NOT add any other tool registrations here.
    """
    from artemis.enablement.actions import (
        register_enablement_gap_flag_tool,
        register_enablement_summary_edit_tool,
    )
    from artemis.enablement.tools import register_enablement_tools

    registry = AuthorizedToolRegistry()
    register_enablement_tools(registry)
    register_enablement_gap_flag_tool(registry, speaker_id=speaker_id)
    register_enablement_summary_edit_tool(registry, speaker_id=speaker_id)
    return registry


def _build_ares_tool_registry(
    agent_id: str,
    project_path: str | None = None,
) -> AuthorizedToolRegistry:
    """Build Ares's registry: owner-private, read-only conversation tools.

    Brief-1 base: query_memory (scope-gated to his owner-private allowance),
    list_scopes, surface_status, read_file.  NO write/propose/spawn, NO
    marketing, NO enablement, NO OKR, NO gcal/gmail/slack-posting/builders/system.

    Brief-2 extension (chunk 2.1): when ``project_path`` is provided, the four
    Forge coding tools (read_project_file, list_project_dir, git_status,
    git_diff) are also registered, all at layer 1 (read-only).

    Like Kai's registry, this is an early-return that does NOT fall through to
    the general tool registrations.
    """
    from artemis.floating_artemis.tools.core import (
        LIST_SCOPES,
        QUERY_MEMORY,
        READ_FILE,
        SURFACE_STATUS,
        _list_scopes,
        _make_query_memory,
        _read_file,
        _surface_status,
        register_ares_coding_tools,
    )

    registry = AuthorizedToolRegistry()
    # query_memory is gated to Ares's owner-private scope allowance (M3).
    registry.register(QUERY_MEMORY, _make_query_memory(agent_id), layer=1)
    registry.register(LIST_SCOPES, _list_scopes, layer=1)
    registry.register(SURFACE_STATUS, _surface_status, layer=1)
    registry.register(READ_FILE, _read_file, layer=1)

    # Brief-2 chunk 2.1: project-scoped coding tools (read-only).
    if project_path is not None:
        register_ares_coding_tools(registry, project_path)

    return registry


def build_authorized_tool_registry(
    available_surfaces: set[str],
    agent_id: str | None = None,
    project_path: str | None = None,
    speaker_id: str | None = None,
) -> AuthorizedToolRegistry:
    """Build the Floating Artemis tool catalog for the given agent and surfaces.

    ``agent_id`` is threaded into ``register_core_tools`` so that ``query_memory``
    is gated to the calling agent's scope allowance (M3).  Must be supplied for
    any live session; None → fail-closed (empty results for every query).

    ``project_path`` is Ares-only: when set, the four Forge coding tools
    (read_project_file, list_project_dir, git_status, git_diff) are added to
    his registry.  Ignored for all other agents.

    ``speaker_id`` is the Slack user id of whoever is speaking this turn, used
    to authorize side-effecting tools for TWO agents (it was Kai-only before
    CALLIE-1):

      - Kai: flag_catalog_gap and update_asset_summary.
      - Callie: send_guarded_dm, where it is the requester half of the
        allowlist pair.

    It must come from the resolved Slack event, never from model output or tool
    input. Omitting it denies every one of those tools (fail-closed) -- for
    Callie the tool is still registered but refuses at call time, so she can say
    she could not verify who was asking instead of going silent. Ignored for all
    other agents.

    Special case — Kai:
      Kai's registry contains ONLY the three read-only enablement tools plus
      flag_catalog_gap and update_asset_summary. He receives none of the
      unconditional tools (core/builders/system/slack/gcal/gmail). This enforces
      the security-critical scope_policy: enablement-scoped, near-read-only.
    """
    normalized_agent = (agent_id or "").strip().lower()

    # SECURITY: Kai gets only the three read-only enablement retrieval tools
    # plus the two identity-gated writes.
    # Early return — no fallthrough to the general tool registrations.
    if normalized_agent == "kai":
        return _build_kai_tool_registry(speaker_id=speaker_id)

    # SECURITY: Ares gets only owner-private read tools + optional Forge coding tools.
    # Early return — no fallthrough to the general tool registrations.
    if normalized_agent == "ares":
        return _build_ares_tool_registry(agent_id or "ares", project_path=project_path)

    registry = AuthorizedToolRegistry()
    register_core_tools(registry, agent_id=agent_id)
    # Directory resolution (name→email): read-only, harmless, all agents.
    register_directory_tools(registry)
    register_builders_tools(registry)
    register_system_tools(registry)
    if "okr" in available_surfaces:
        register_okr_tools(registry)
    if "writing-rules" in available_surfaces:
        register_writing_rules_tools(registry)
    if "marketing-os" in available_surfaces or "signal-queue" in available_surfaces:
        register_marketing_tools(registry)
    # CALLIE-1: send_slack_dm is a raw, unauthenticated-by-content DM tool
    # gated ONLY by a layer-3 "operator confirmation" — which in the Slack
    # path is answered by whoever is already chatting with the agent in that
    # same channel. For Callie specifically, that channel can hold many
    # people (that is the whole reason her requester allowlist matters —
    # see tools/callie_dm.py's module docstring), so leaving this tool live
    # for her alongside send_guarded_dm would make the new guard decorative:
    # a determined requester (or the model itself) could just ask for
    # send_slack_dm instead and reach anyone with no allowlist check at all.
    # Artemis keeps it: her inbound gate is a small, explicit USER allowlist
    # (a privacy boundary, not a shared channel), so the same proxying risk
    # does not apply there in the same way, and widening that is out of
    # scope for CALLIE-1.
    register_slack_tools(registry, include_dm=normalized_agent != "callie")
    register_gcal_tools(registry)
    register_gmail_tools(registry)
    if "jira-board" in available_surfaces:
        register_jira_tools(registry)
    if "meetings" in available_surfaces:
        register_granola_tools(registry)
    # Argus dispatch tool: available to callie only.
    # Callie is the face for Argus; no other agent should see this tool.
    if normalized_agent == "callie":
        register_argus_tools(registry)
        # On-demand Screen-Time & AI-policy report: Callie only, read-only,
        # fires on request (no scheduler, no auto-push). See
        # artemis/screentime/callie_report.py + tools/screentime_tools.py.
        register_screentime_report_tools(registry)
        # CALLIE-1: her one initiating capability. speaker_id is bound as a
        # closure so tool input can never spoof the requester (see
        # tools/callie_dm.py).
        register_callie_dm_tool(registry, speaker_id=speaker_id)
    return registry
