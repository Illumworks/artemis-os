"""Builder-Engine — reusable primitives for any Builder surface (O1).

This module provides the tool-callable primitives the Agent-Builder uses
during a conversation:

  read_existing(kind, session)        — list what's already in the catalog
  read_capabilities(session)          — provider models, available tools
  read_recent_runs(agent_id, session) — for self-improvement context
  propose(kind, definition, session)  — stage a draft DefinitionProposal
  commit(proposal_id, session)        — graduate proposal to real definition tables
  test_run(definition, prompt, ...)   — sandboxed trial run (Option A whitelist)

Decision 2 (test-run sandbox safety) resolved — Lead chose Option A:
  - A static whitelist (_TEST_RUN_SAFE_TOOLS) lists allowed read-only tools.
  - Deny-by-default: new tools must be explicitly added to the whitelist.
  - Rate cap: max 20 tool calls per test_run regardless of whitelist.
  - Result metadata includes tools_skipped: [...] for user visibility.
  - Optional allow_writes flag (double-confirm) for final pre-deployment testing.

NOTE: this module is intentionally kept thin. The Agent-Builder is the only
caller in v1; the engine's job is to expose clean, testable primitives that
the builder's tool list can bind to. Coupling discussion is Decision 1 —
see CONSULT_1 note in the brief.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Test-run sandbox (Decision 2 — Option A whitelist) ───────────────────────

# Single source of truth for tools permitted during test_run.
# Deny-by-default: any tool NOT in this set is blocked.
# Keys are canonical tool names matching the definition's tool_specs name field.
_TEST_RUN_SAFE_TOOLS: frozenset[str] = frozenset(
    [
        # Jira — read-only
        "jira.list_issues",
        "jira.get_issue",
        "jira.search_issues",
        "jira.list_projects",
        # Slack — read-only
        "slack.search_messages",
        "slack.list_channels",
        "slack.get_channel_history",
        # Google Calendar — read-only
        "gcal.list_events",
        "gcal.get_event",
        # Memory — read-only
        "memory.search",
        "memory.get",
        # Google Drive — read-only
        "gdrive.list_files",
        "gdrive.get_file",
        # Confluence / docs — read-only
        "confluence.search",
        "confluence.get_page",
    ]
)

# Max tool calls allowed in a single test_run regardless of whitelist.
_TEST_RUN_MAX_TOOL_CALLS = 20


async def read_existing(
    kind: str,
    *,
    db_session: Any,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return a summary list of existing definitions of the given kind.

    kind: 'agent' | 'skill' | 'workflow' | 'automation'
    Returns a list of dicts with id, name, description (truncated).
    """
    from sqlalchemy import select as sa_select

    if kind == "agent":
        from artemis.builders.models import Agent

        result = await db_session.execute(
            sa_select(Agent.id, Agent.agent_id, Agent.name, Agent.description)
            .order_by(Agent.name)
            .limit(limit)
        )
        return [
            {"id": r.id, "agent_id": r.agent_id, "name": r.name, "description": r.description}
            for r in result.all()
        ]

    if kind == "skill":
        from artemis.builders.models import Skill

        result = await db_session.execute(
            sa_select(Skill.id, Skill.slug, Skill.name, Skill.description, Skill.status)
            .where(Skill.status != "archived")
            .order_by(Skill.name)
            .limit(limit)
        )
        return [
            {
                "id": r.id,
                "slug": r.slug,
                "name": r.name,
                "description": r.description,
                "status": r.status,
            }
            for r in result.all()
        ]

    if kind == "workflow":
        from artemis.builders.models import Workflow

        result = await db_session.execute(
            sa_select(Workflow.id, Workflow.workflow_id, Workflow.name, Workflow.description)
            .order_by(Workflow.name)
            .limit(limit)
        )
        return [
            {"id": r.id, "workflow_id": r.workflow_id, "name": r.name, "description": r.description}
            for r in result.all()
        ]

    # automation kind not yet in schema — return empty
    logger.debug("read_existing: kind=%r not implemented", kind)
    return []


async def read_capabilities(*, db_session: Any) -> dict[str, Any]:
    """Return provider catalog and available tool/integration names.

    The Agent-Builder calls this to learn what models and tools exist
    before proposing a definition.
    """
    # Provider catalog — static for now; Phase G wires this dynamically
    providers = [
        {"id": "anthropic", "models": ["claude-opus-4-5", "claude-sonnet-4-6", "claude-haiku-4-5"]},
        {"id": "openai", "models": ["gpt-4o", "gpt-4o-mini"]},
    ]

    # Available integrations — read from integrations table
    available_integrations: list[str] = []
    try:
        from sqlalchemy import select as sa_select

        from artemis.integrations.models import STATUS_ACTIVE, Integration

        result = await db_session.execute(
            sa_select(Integration.provider).where(Integration.status == STATUS_ACTIVE)
        )
        available_integrations = [r[0] for r in result.all()]
    except Exception:
        logger.debug("read_capabilities: could not load integrations", exc_info=True)

    return {
        "providers": providers,
        "available_integrations": available_integrations,
    }


async def read_recent_runs(
    agent_id: str,
    *,
    db_session: Any,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return recent run records + trajectory summaries for an agent.

    Used by the Agent-Builder in edit-session mode to surface self-improvement context.
    """
    from sqlalchemy import select as sa_select

    from artemis.builders.models import AgentRun, AgentRunTrajectorySummary

    result = await db_session.execute(
        sa_select(AgentRun)
        .where(AgentRun.agent_id == agent_id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()

    output: list[dict[str, Any]] = []
    for run in runs:
        traj_result = await db_session.execute(
            sa_select(AgentRunTrajectorySummary)
            .where(AgentRunTrajectorySummary.run_id == run.id)
            .limit(1)
        )
        traj = traj_result.scalar_one_or_none()
        entry: dict[str, Any] = {
            "id": run.id,
            "run_id": run.run_id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "error": run.error,
        }
        if traj:
            entry["trajectory"] = {
                "what_worked": traj.what_worked,
                "what_stalled": traj.what_stalled,
                "what_was_missing": traj.what_was_missing,
            }
        output.append(entry)

    return output


async def propose(
    kind: str,
    definition: dict[str, Any],
    *,
    db_session: Any,
    builder_session_id: int | None = None,
    target_id: int | None = None,
    proposed_by: str = "builder",
    citations: dict[str, Any] | None = None,
) -> int:
    """Stage a DefinitionProposal and return its id.

    Does NOT commit the DB session — callers own commit.
    """
    from artemis.builder.repository import create_definition_proposal

    proposal = await create_definition_proposal(
        db_session,
        builder_session_id=builder_session_id,
        kind=kind,
        target_id=target_id,
        proposed_by=proposed_by,
        proposed_definition=definition,
        citations=citations,
    )
    return proposal.id


async def commit(
    proposal_id: int,
    *,
    db_session: Any,
) -> dict[str, Any]:
    """Graduate an approved DefinitionProposal into the real definition tables.

    For kind='agent': creates or updates the agents row.
    For kind='skill': creates or updates the skills row.
    Other kinds are stubs returning a not_implemented marker.

    Does NOT commit the DB session — callers own commit.
    Returns a dict describing what was committed.
    """
    from artemis.builder.repository import approve_proposal, get_definition_proposal

    proposal = await get_definition_proposal(db_session, proposal_id)
    if proposal.status == "approved":
        # Already committed — idempotent return
        return {"status": "already_approved", "proposal_id": proposal_id}
    if proposal.status != "pending":
        raise ValueError(f"Cannot commit proposal {proposal_id}: status={proposal.status!r}")

    # Approve the record first
    await approve_proposal(db_session, proposal_id)

    defn = proposal.proposed_definition
    kind = proposal.kind

    if kind == "agent":
        return await _commit_agent(defn, proposal.target_id, db_session, proposal_id)
    if kind == "skill":
        return await _commit_skill(defn, proposal.target_id, db_session, proposal_id)

    logger.warning("commit: kind=%r not yet implemented", kind)
    return {"status": "not_implemented", "kind": kind, "proposal_id": proposal_id}


async def sandbox_run(
    definition: dict[str, Any],
    prompt: str,
    *,
    adapter: Any,
    allow_writes: bool = False,
) -> dict[str, Any]:
    """Run a sandboxed trial of a draft definition.

    Decision 2 Option A: tools are filtered against _TEST_RUN_SAFE_TOOLS before
    the agent loop runs. Write-capable tools are skipped and listed in
    result["tools_skipped"]. Total tool calls are capped at _TEST_RUN_MAX_TOOL_CALLS.

    Parameters
    ----------
    definition:
        The draft agent definition (same shape as proposed_definition in DefinitionProposal).
    prompt:
        The user-supplied test prompt to run against the draft agent.
    adapter:
        A ModelAdapter to use for the run (caller resolves provider).
    allow_writes:
        If True (double-confirmed by the user), bypass the whitelist and allow
        all tools. Default False. Should only be used for final pre-deployment
        validation.

    Returns
    -------
    dict with:
        output: str           — the agent's final text response
        tool_calls: int       — total tool calls that actually ran
        tools_skipped: list   — tool names filtered out by the whitelist
        stop_reason: str      — why the loop stopped
        test_mode: bool       — always True so callers can detect test context
    """
    from artemis.agent.loop import run_turn
    from artemis.agent.loop import user_message as make_user_message
    from artemis.agent.tools import ToolRegistry
    from artemis.agent.types import TextBlock, Tool

    tool_specs: list[dict[str, Any]] = (
        definition.get("tools", []) if isinstance(definition.get("tools"), list) else []
    )
    tools_requested = [t if isinstance(t, str) else t.get("name", "") for t in tool_specs]

    # Determine which tools are allowed
    if allow_writes:
        tools_allowed = tools_requested
        tools_skipped: list[str] = []
    else:
        tools_allowed = [t for t in tools_requested if t in _TEST_RUN_SAFE_TOOLS]
        tools_skipped = [t for t in tools_requested if t not in _TEST_RUN_SAFE_TOOLS]

    # Build a counting tool registry with cap enforcement
    tool_call_count = 0

    registry = ToolRegistry()
    for tool_name in tools_allowed:
        # Stub implementation: returns a realistic placeholder response
        async def _stub_tool(inp: dict[str, Any], _name: str = tool_name) -> str:
            nonlocal tool_call_count
            if tool_call_count >= _TEST_RUN_MAX_TOOL_CALLS:
                return f"[TEST RUN — rate limit reached after {_TEST_RUN_MAX_TOOL_CALLS} calls]"
            tool_call_count += 1
            return (
                f"[TEST RUN] {_name} called with {list(inp.keys())}. "
                f"Stub response — no real data returned in test mode."
            )

        tool = Tool(
            name=tool_name,
            description=f"Test-mode stub for {tool_name} (read-only, sandboxed).",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        registry.register(tool, _stub_tool)

    system_prompt = definition.get("system_prompt", "You are a helpful assistant.")
    if not allow_writes:
        system_prompt = "[TEST RUN MODE — no real external writes will occur]\n\n" + system_prompt

    result = await run_turn(
        adapter=adapter,
        messages=[make_user_message(prompt)],
        tools=registry if tools_allowed else None,
        system=system_prompt,
        max_tokens=1024,
        max_iterations=min(5, _TEST_RUN_MAX_TOOL_CALLS),
        cache_system=False,
        cache_tools=False,
    )

    # Extract assistant text
    output = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    output += block.text
            break

    return {
        "output": output,
        "tool_calls": tool_call_count,
        "tools_skipped": tools_skipped,
        "stop_reason": result.stop_reason,
        "test_mode": True,
        "allow_writes": allow_writes,
    }


async def _commit_agent(
    defn: dict[str, Any],
    target_id: int | None,
    db_session: Any,
    proposal_id: int,
) -> dict[str, Any]:
    """Create or update an agent from a proposal definition."""
    import uuid

    from sqlalchemy import select as sa_select

    from artemis.builders.models import Agent

    if target_id is not None:
        # Update existing
        result = await db_session.execute(sa_select(Agent).where(Agent.id == target_id).limit(1))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent with id={target_id} not found")
        for key in (
            "name",
            "description",
            "goal",
            "system_prompt",
            "tools",
            "model",
            "provider",
            "max_iterations",
        ):
            if key in defn:
                setattr(agent, key, defn[key])
        await db_session.flush()
        return {"status": "updated", "kind": "agent", "id": agent.id, "proposal_id": proposal_id}

    # Create new
    agent_id = defn.get("agent_id") or f"agent-{uuid.uuid4().hex[:8]}"
    agent = Agent(
        agent_id=agent_id,
        name=defn.get("name", agent_id),
        description=defn.get("description"),
        goal=defn.get("goal"),
        system_prompt=defn.get("system_prompt"),
        tools=defn.get("tools", []),
        model=defn.get("model", "claude-sonnet-4-6"),
        provider=defn.get("provider", "anthropic"),
        max_iterations=defn.get("max_iterations", 10),
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)
    return {
        "status": "created",
        "kind": "agent",
        "id": agent.id,
        "agent_id": agent.agent_id,
        "proposal_id": proposal_id,
    }


async def _commit_skill(
    defn: dict[str, Any],
    target_id: int | None,
    db_session: Any,
    proposal_id: int,
) -> dict[str, Any]:
    """Create or update a skill from a proposal definition."""
    import uuid

    from sqlalchemy import select as sa_select

    from artemis.builders.models import Skill

    if target_id is not None:
        result = await db_session.execute(sa_select(Skill).where(Skill.id == target_id).limit(1))
        skill = result.scalar_one_or_none()
        if skill is None:
            raise ValueError(f"Skill with id={target_id} not found")
        for key in ("name", "description", "category", "instructions", "tools", "kind"):
            if key in defn:
                setattr(skill, key, defn[key])
        await db_session.flush()
        return {"status": "updated", "kind": "skill", "id": skill.id, "proposal_id": proposal_id}

    slug = defn.get("slug") or f"skill-{uuid.uuid4().hex[:8]}"
    skill = Skill(
        slug=slug,
        name=defn.get("name", slug),
        description=defn.get("description"),
        category=defn.get("category"),
        status="proposed",
        instructions=defn.get("instructions"),
        tools=defn.get("tools", []),
        kind=defn.get("kind", "user"),
    )
    db_session.add(skill)
    await db_session.flush()
    await db_session.refresh(skill)
    return {
        "status": "created",
        "kind": "skill",
        "id": skill.id,
        "slug": skill.slug,
        "proposal_id": proposal_id,
    }
