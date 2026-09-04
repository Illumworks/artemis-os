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


# ── Anti-fabrication (2026-09-04) ────────────────────────────────────────────


def test_the_scout_is_told_never_to_invent_a_signal() -> None:
    """The state_doe scout invented 149 signals between 2026-08-10 and 2026-09-02.

    signal_queue.write now rejects a source URL that does not resolve, which stops
    fabricated signals landing. This pins the other half: the instruction must
    tell the scout not to invent one in the first place.
    """
    instruction = default_agent_instruction("marketing.scout.state_doe")

    assert "NEVER INVENT A SIGNAL" in instruction
    assert "actually returned to you in THIS run" in instruction
    assert "Do not construct, complete, guess or shorten a URL" in instruction


def test_reporting_zero_is_explicitly_permitted() -> None:
    """The deferred-tools line created pressure against the honest answer.

    It was written for a real bug — scouts giving up when tools looked deferred —
    but a model with empty feeds could resolve the tension by inventing. Both
    instructions have to coexist, so the narrow meaning is spelled out.
    """
    instruction = default_agent_instruction("marketing.scout.state_doe")

    assert "Zero is a real and useful answer" in instruction
    assert "that is success, not failure" in instruction
    # The original guidance must survive — it fixed its own bug.
    assert "Do NOT skip tool calls or report 0 signals" in instruction
    assert "do not report zero because a tool LOOKED unavailable" in instruction


def test_the_anti_fabrication_rule_is_scout_scoped() -> None:
    """Qualifiers read existing signals; they emit none, so the rule is noise there."""
    assert "NEVER INVENT A SIGNAL" not in default_agent_instruction("marketing.qualifier.gate1")
