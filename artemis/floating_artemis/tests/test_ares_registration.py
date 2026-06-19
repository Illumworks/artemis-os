"""Ares (Brief 1) registration tests — persona, owner-private scope, surface, tools.

Ares is Jon's owner-private build partner living in the Forge surface. This brief
gives him identity + read-only conversation tools only (no coding/agency yet).
Scope is security-critical, so the deny paths are verified adversarially here.
"""

from __future__ import annotations

from artemis.floating_artemis import personality as pm
from artemis.floating_artemis.session_scope import (
    _AGENT_SURFACE_ALLOWLIST,
    resolve_surface_scope,
)
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
from artemis.identity.scope_policy import (
    allowed_scopes_for_agent,
)

# ── Persona ────────────────────────────────────────────────────────────────────


def test_ares_persona_loads() -> None:
    profile = pm.load_agent_profile("ares")
    assert profile.agent_id == "ares"
    assert profile.display_name == "Ares"
    assert profile.persona_core == pm.ARES_PERSONA_CORE
    assert "build partner" in profile.persona_core
    # Full profile text is read from ares-personality-profile.md on disk.
    assert profile.profile_text
    assert "Ares" in profile.profile_text


def test_ares_persona_is_ascii_quotes_only() -> None:
    """Curly quotes break voice-corpus parsing — the inline core must be ASCII."""
    assert "“" not in pm.ARES_PERSONA_CORE  # left double quote
    assert "”" not in pm.ARES_PERSONA_CORE  # right double quote
    # Ares's own style rule: no em/en dashes in his voice.
    assert "—" not in pm.ARES_PERSONA_CORE  # em dash
    assert "–" not in pm.ARES_PERSONA_CORE  # en dash


# ── Scope (owner-private) ───────────────────────────────────────────────────────


def test_ares_scope_is_owner_private() -> None:
    """Ares reads his own scope + Artemis's context, and nothing else."""
    allowance = allowed_scopes_for_agent("ares")
    assert allowance.denied is False
    assert allowance.allow_all is False
    # Reads his own agent scope + Artemis's shared brain.
    assert allowance.permits("agent", "ares")
    assert allowance.permits("agent", "artemis")
    assert allowance.permits("agent", "floating-artemis")


def test_ares_scope_excludes_marketing_enablement_and_others() -> None:
    """Ares must NOT read marketing, enablement, or other agents/personal scopes."""
    allowance = allowed_scopes_for_agent("ares")
    # No marketing-shared scope kinds.
    for kind in ("workspace", "campaign_family", "district", "account", "person", "pipeline"):
        assert not allowance.permits(kind, "anything"), kind
    # No enablement.
    assert not allowance.permits("enablement", "default")
    # No other agents.
    assert not allowance.permits("agent", "callie")
    assert not allowance.permits("agent", "kai")
    # No personal scopes (he has no personal_user_id).
    assert not allowance.permits("personal", "1")
    assert not allowance.permits("personal", "999")


def test_other_agents_cannot_read_ares_scope() -> None:
    """Coworker/marketing/enablement agents must NOT be able to read agent:ares."""
    for agent_id in ("callie", "kai"):
        allowance = allowed_scopes_for_agent(agent_id)
        assert not allowance.permits("agent", "ares"), agent_id


def test_existing_agents_unchanged_by_ares() -> None:
    """Adding Ares does not alter the existing agents' allowances."""
    assert allowed_scopes_for_agent("artemis").allow_all is True
    callie = allowed_scopes_for_agent("callie")
    assert callie.permits("agent", "callie")
    assert not callie.permits("agent", "ares")
    kai = allowed_scopes_for_agent("kai")
    assert kai.permits("enablement", "x")
    assert not kai.permits("agent", "ares")


# ── Surface allowlist ───────────────────────────────────────────────────────────


def test_ares_surface_allowlist_is_forge_only() -> None:
    assert _AGENT_SURFACE_ALLOWLIST["ares"] == frozenset({"dev-projects"})


def test_ares_session_resolves_to_forge_surface_only() -> None:
    all_surfaces = {"dev-projects", "marketing-os", "signal-queue", "okr", "writing-rules"}
    resolved = resolve_surface_scope(
        all_surfaces=all_surfaces,
        session_id="slack-ares-T1-C1-_",
        metadata={"agent_id": "ares", "surface": "slack", "channel_id": "C1"},
    )
    assert resolved == {"dev-projects"}


# ── Tools (read-only, no coding/agency) ─────────────────────────────────────────


def test_ares_tool_registry_is_read_only_conversation_set() -> None:
    registry = build_authorized_tool_registry({"dev-projects"}, agent_id="ares")
    names = {entry.tool.name for entry in registry.all_entries()}
    assert names == {"query_memory", "list_scopes", "surface_status", "read_file"}


def test_ares_has_no_write_agency_or_domain_tools() -> None:
    registry = build_authorized_tool_registry({"dev-projects"}, agent_id="ares")
    names = {entry.tool.name for entry in registry.all_entries()}
    for forbidden in (
        "write_memory",
        "propose_edit",
        "spawn_subagent",
        "set_pref",
        "send_slack_message",
        "search_enablement_assets",
    ):
        assert forbidden not in names, forbidden
    # Everything Ares has is auto-invoke layer 1/2 (no confirmation-gated tools).
    assert all(registry.is_auto_invoke(n) for n in names)
