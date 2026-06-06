"""Pure-function tests for scout MCP-deferral mitigations (scout-mcp-deferral).

Tests ``default_agent_instruction`` from ``artemis.builders.executor`` — no DB
required, no async, so these run without the builders/tests conftest DB guard.
"""

from __future__ import annotations

from artemis.builders.executor import default_agent_instruction


def test_scout_instruction_contains_tool_deferral_note() -> None:
    """Scout instruction must tell the LLM that MCP tools are pre-connected.

    This is the prompt-level fallback for the MCP_CONNECTION_NONBLOCKING=false
    env fix: even if the LLM observes a deferred-tool hint in its session context
    it should call tools anyway rather than reporting 0 signals.
    """
    instr = default_agent_instruction("marketing.scout.legislative")
    assert "deferred" in instr.lower(), (
        "Scout instruction must mention 'deferred' so the LLM knows to call "
        "tools even when they appear deferred"
    )
    assert "mcp__artemis__" in instr, (
        "Scout instruction should name the mcp__artemis__ prefix so the LLM "
        "knows which tool namespace is pre-connected"
    )
    # Instruction must still contain the core scout directive.
    assert "signal_queue" in instr


def test_scout_instruction_deferral_note_present_for_all_scout_slugs() -> None:
    """All known scout agent IDs receive the deferral note."""
    scout_ids = [
        "marketing.scout.legislative",
        "marketing.scout.regional_news",
        "marketing.scout.federal_funding",
        "marketing.scout.state_doe",
        "marketing.scout.procurement",
        "marketing.scout.board_minutes",
        "marketing.scout.leadership_transition",
        "marketing.scout.linkedin_observer",
        "marketing.scout.starbridge_researcher",
    ]
    for aid in scout_ids:
        instr = default_agent_instruction(aid)
        assert "deferred" in instr.lower(), f"Missing deferral note for {aid}"
        assert "mcp__artemis__" in instr, f"Missing mcp__artemis__ prefix mention for {aid}"


def test_scout_instruction_override_wins() -> None:
    """A caller-supplied override must replace the default scout instruction entirely."""
    override = "Do something completely custom."
    instr = default_agent_instruction("marketing.scout.regional_news", override=override)
    assert instr == override


def test_qualifier_instruction_does_not_mention_deferred() -> None:
    """Qualifier agent instruction is unaffected by the scout deferral note."""
    instr = default_agent_instruction("marketing.qualifier.cross_reference")
    assert "deferred" not in instr.lower()


def test_generic_instruction_does_not_mention_deferred() -> None:
    """Generic agent instruction is unaffected by the scout deferral note."""
    instr = default_agent_instruction("some.other.agent")
    assert "deferred" not in instr.lower()


def test_scout_instruction_still_directs_tool_use() -> None:
    """Deferral note must not crowd out the core 'execute now' directive."""
    instr = default_agent_instruction("marketing.scout.federal_funding")
    assert "Execute your scan NOW" in instr
    assert "signal_queue.write" in instr
