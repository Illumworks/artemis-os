"""Shared Floating Artemis tool-registry construction helpers.

This module exists so both the in-process chat path and the claude-code MCP
subprocess path can reconstruct the same Floating Artemis tool catalog without
importing the full chat orchestration module.
"""

from __future__ import annotations

from artemis.floating_artemis.authority import AuthorizedToolRegistry
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


def build_authorized_tool_registry(available_surfaces: set[str]) -> AuthorizedToolRegistry:
    """Build the full Floating Artemis tool catalog for the given surfaces."""
    registry = AuthorizedToolRegistry()
    register_core_tools(registry)
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
    return registry
