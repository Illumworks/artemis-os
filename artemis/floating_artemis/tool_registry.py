"""Shared Floating Artemis tool-registry construction helpers.

This module exists so both the in-process chat path and the claude-code MCP
subprocess path can reconstruct the same Floating Artemis tool catalog without
importing the full chat orchestration module.
"""

from __future__ import annotations

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.argus_tools import register_argus_tools
from artemis.floating_artemis.tools.builders import register_builders_tools
from artemis.floating_artemis.tools.core import register_core_tools
from artemis.floating_artemis.tools.granola_tools import register_granola_tools
from artemis.floating_artemis.tools.jira_tools import register_jira_tools
from artemis.floating_artemis.tools.marketing import register_marketing_tools
from artemis.floating_artemis.tools.okr import register_okr_tools
from artemis.floating_artemis.tools.system import register_system_tools
from artemis.floating_artemis.tools.writing_rules import register_writing_rules_tools
from artemis.integrations.gcal.tools import register_gcal_tools
from artemis.integrations.gmail.tools import register_gmail_tools
from artemis.integrations.slack.tools import register_slack_tools


def _build_kai_tool_registry() -> AuthorizedToolRegistry:
    """Build Kai's registry: enablement read-only tools ONLY.

    Kai (Chiron) is enablement-scoped and read-only.  He gets NO personal
    scopes, NO agent:artemis, NO creation/agency tools, NO marketing tools,
    NO gcal/gmail/slack/builders/system tools.  Only the two enablement
    retrieval tools are registered.

    SECURITY: This function is the sole caller of register_enablement_tools.
    Do NOT add any other tool registrations here.
    """
    from artemis.enablement.tools import register_enablement_tools

    registry = AuthorizedToolRegistry()
    register_enablement_tools(registry)
    return registry


def build_authorized_tool_registry(
    available_surfaces: set[str],
    agent_id: str | None = None,
) -> AuthorizedToolRegistry:
    """Build the Floating Artemis tool catalog for the given agent and surfaces.

    ``agent_id`` is threaded into ``register_core_tools`` so that ``query_memory``
    is gated to the calling agent's scope allowance (M3).  Must be supplied for
    any live session; None → fail-closed (empty results for every query).

    Special case — Kai:
      Kai's registry contains ONLY search_enablement_assets + get_enablement_asset.
      He receives none of the unconditional tools (core/builders/system/slack/gcal/gmail).
      This enforces the security-critical scope_policy: enablement-scoped, read-only.
    """
    normalized_agent = (agent_id or "").strip().lower()

    # SECURITY: Kai gets only the two read-only enablement retrieval tools.
    # Early return — no fallthrough to the general tool registrations.
    if normalized_agent == "kai":
        return _build_kai_tool_registry()

    registry = AuthorizedToolRegistry()
    register_core_tools(registry, agent_id=agent_id)
    register_builders_tools(registry)
    register_system_tools(registry)
    if "okr" in available_surfaces:
        register_okr_tools(registry)
    if "writing-rules" in available_surfaces:
        register_writing_rules_tools(registry)
    if "marketing-os" in available_surfaces or "signal-queue" in available_surfaces:
        register_marketing_tools(registry)
    register_slack_tools(registry)
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
    return registry
