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


def _build_callie_tool_registry(
    agent_id: str,
    speaker_id: str | None = None,
    participants: list[str] | None = None,
) -> AuthorizedToolRegistry:
    """Build Callie's registry: marketing/signal surfaces, explicitly listed (CALLIE-2).

    Before this, Callie fell through to the general registration path below and
    held everything the app offers minus whatever happened not to be plugged
    in -- protected by accident (missing Gmail/Granola credentials), not by
    design. See ``docs/marketing-intelligence-direction.md`` ("What Callie can
    see", reviewed 2026-08-14) for the finding and
    ``briefs/callie-2-scoped-registry.md`` for the keep/drop analysis this
    function executes verbatim.

    KEEP -- everything she demonstrably needs for marketing work:
      - register_directory_tools: name -> person resolution, with the
        ambiguity fix from 2026-08-12 (participants breaks naming ties).
      - register_marketing_tools: signals, candidates, get_district_contacts.
        Her job.
      - register_argus_tools: dispatch_research (Callie-only; Josh asks for
        this constantly).
      - register_writing_rules_tools: she drafts copy and needs the house
        rules.
      - register_screentime_report_tools: she reports the screentime section
        on request.
      - register_slack_tools(include_dm=False): read/post channels. NOT
        send_slack_dm -- that raw DM tool has no allowlist of its own and is
        gated only by a layer-3 "operator confirmation" that, in a shared
        Slack channel, is answered by whoever replies next. Leaving it live
        alongside send_guarded_dm would make CALLIE-1's guard decorative: a
        determined requester (or the model itself) could just ask for
        send_slack_dm instead and reach anyone with no allowlist check at all.
      - register_callie_dm_tool: her guarded, allowlisted DM (CALLIE-1).
        speaker_id is bound as a closure so tool input can never spoof the
        requester (see tools/callie_dm.py).
      - register_salesforce_tools (SFDC-1): check_salesforce_activity,
        layer 1, read-only in both directions (never writes to Salesforce --
        structurally impossible, see
        artemis.integrations.salesforce.client -- and never writes to
        district_contacts either, since it calls check_suppression with
        enrich=False). Lets her answer "is this district already in play?"
        before drafting, per Jon's "so we are not stepping on people's
        toes" framing in the SFDC-1 brief.
      - query_memory + write_memory (registered directly from core, NOT via
        register_core_tools): her own continuity. query_memory MUST stay the
        scope-gated variant built by ``_make_query_memory(agent_id)`` -- that
        is the M3 security control. Swapping it for the ungated
        ``_query_memory`` would WIDEN her memory reach, not narrow it; that
        is the one line in this function most worth re-checking on review.

    DROP -- everything else, deliberately not registered here:
      - register_builders_tools (run_agent, run_workflow, spawn_subagent,
        list_agents/workflows/skills/chains/dags, propose_agent/workflow/skill)
      - register_system_tools (health_check, recent_failures, propose_fix)
      - register_gcal_tools, register_gmail_tools, register_granola_tools --
        personal data. These are registered unconditionally on the general
        path for every agent, so before this fix, connecting either
        integration for any unrelated reason would have silently given
        Callie Jon's inbox and meeting transcripts.
      - register_okr_tools -- CLAUDE.md flags OKR Studio rows as an
        owner-judgment surface.
      - register_jira_tools.
      - from core: read_file, propose_edit, list_scopes, surface_status,
        list_routes, set_pref, set_brief_exclusion, clear_brief_exclusion,
        spawn_subagent, and the Forge/git tools (already absent for her --
        kept absent).

    Like Kai's and Ares's registries, this is an early return that does NOT
    fall through to the general tool registrations below. A future
    register_* added to that general path must not silently reach Callie --
    it has to be added here, explicitly, or she never sees it.
    """
    from artemis.floating_artemis.tools.core import (
        QUERY_MEMORY,
        WRITE_MEMORY,
        _make_query_memory,
        _write_memory,
    )

    registry = AuthorizedToolRegistry()
    # M3: query_memory is gated to Callie's own scope allowance -- see the
    # docstring above. Do not swap this for the ungated `_query_memory`.
    registry.register(QUERY_MEMORY, _make_query_memory(agent_id), layer=1)
    registry.register(WRITE_MEMORY, _write_memory, layer=2)

    register_directory_tools(registry, participants=participants)
    register_marketing_tools(registry)
    register_argus_tools(registry)
    # Writing rules: the READ tool only. propose_writing_rule is layer 3, and a
    # staged layer-3 confirmation in a shared channel is answered by whoever
    # replies next -- so Josh could confirm a change to the house style. CLAUDE.md
    # names Writing Studio rules an owner-judgment surface, so it must not be
    # reachable from #demand-gen-callie at all.
    #
    # Also worth recording: these tools were NEVER actually reachable by Callie
    # before CALLIE-2 -- her surface allowlist carries 'writing-studio' but the
    # general path gated on 'writing-rules', two distinct strings. So registering
    # even the read tool is a small widening, taken deliberately because she
    # drafts copy and needs the house rules.
    from artemis.floating_artemis.tools.writing_rules import (
        LIST_WRITING_RULES,
        _list_writing_rules,
    )

    registry.register(LIST_WRITING_RULES, _list_writing_rules, layer=1)
    register_screentime_report_tools(registry)
    # include_dm=False is load-bearing -- see "KEEP" above.
    register_slack_tools(registry, include_dm=False)
    register_callie_dm_tool(registry, speaker_id=speaker_id)

    # SFDC-1: check_salesforce_activity, layer 1, read-only. Imported locally
    # (like LIST_WRITING_RULES above) because this tool is Callie-exclusive --
    # it is not used by the general path and should not appear in this
    # module's top-of-file import list as if it were.
    from artemis.floating_artemis.tools.salesforce_tools import register_salesforce_tools

    register_salesforce_tools(registry)

    return registry


def build_authorized_tool_registry(
    available_surfaces: set[str],
    agent_id: str | None = None,
    project_path: str | None = None,
    speaker_id: str | None = None,
    participants: list[str] | None = None,
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

    ``participants`` is the display-name roster of who else is in the current
    conversation (see ``artemis.routes.integrations_slack_events.route_inbound``).
    It is bound into ``resolve_person`` (directory_tools.py) as a ranking
    tiebreaker ONLY -- ambiguous first-name matches prefer a candidate who is
    actually present. It is never an authorization input (resolve_person is
    read-only and Layer 1 for every agent) and is safe to omit; omitting it
    just means resolve_person can't use conversation context to break a tie.

    Special case — Kai:
      Kai's registry contains ONLY the three read-only enablement tools plus
      flag_catalog_gap and update_asset_summary. He receives none of the
      unconditional tools (core/builders/system/slack/gcal/gmail). This enforces
      the security-critical scope_policy: enablement-scoped, near-read-only.

    Special case — Callie (CALLIE-2):
      Callie's registry is built entirely by ``_build_callie_tool_registry``:
      marketing/signal tools, dispatch_research, her guarded DM, Slack
      read/post (no raw DM), writing rules, screentime reporting, directory
      resolution, and scope-gated query_memory/write_memory. She receives
      NONE of the unconditional tools registered below for the general path
      (no builders/system/gcal/gmail/granola/okr/jira, no core read_file /
      propose_edit / list_scopes / surface_status / list_routes /
      spawn_subagent). Before this she fell through to the path below and
      held everything not gated off by a missing surface or credential; see
      ``docs/marketing-intelligence-direction.md`` ("What Callie can see").
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

    # SECURITY: Callie gets a hand-picked marketing/signal registry (CALLIE-2).
    # Early return — no fallthrough to the general tool registrations.
    if normalized_agent == "callie":
        return _build_callie_tool_registry(
            agent_id or "callie", speaker_id=speaker_id, participants=participants
        )

    registry = AuthorizedToolRegistry()
    register_core_tools(registry, agent_id=agent_id)
    # Directory resolution (name→email): read-only, harmless, all agents.
    # participants lets an ambiguous first-name match prefer whoever is
    # actually in this conversation -- see register_directory_tools' docstring.
    register_directory_tools(registry, participants=participants)
    register_builders_tools(registry)
    register_system_tools(registry)
    if "okr" in available_surfaces:
        register_okr_tools(registry)
    if "writing-rules" in available_surfaces:
        register_writing_rules_tools(registry)
    if "marketing-os" in available_surfaces or "signal-queue" in available_surfaces:
        register_marketing_tools(registry)
    # send_slack_dm: Callie no longer reaches this path at all (see
    # _build_callie_tool_registry's early return above), so include_dm is
    # unconditionally True here now -- every remaining caller of this general
    # path (Artemis, and any future non-special-cased agent) keeps the raw
    # send_slack_dm tool, same as before CALLIE-1.
    register_slack_tools(registry)
    register_gcal_tools(registry)
    register_gmail_tools(registry)
    if "jira-board" in available_surfaces:
        register_jira_tools(registry)
    if "meetings" in available_surfaces:
        register_granola_tools(registry)
    return registry
