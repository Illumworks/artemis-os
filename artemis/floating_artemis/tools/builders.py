"""Builders tools — agents, workflows, skills, chains, DAGs.

Authority layers:
  1 (read-only): list_agents, list_workflows, list_skills, list_chains, list_dags
  2 (idempotent): run_agent, run_workflow
  3 (side-effect): propose_agent, propose_workflow, propose_skill
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

# ── Implementations ──────────────────────────────────────────────────────────


async def _list_agents(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            agents = await repo.list_agents(session, limit=limit)
        if not agents:
            return "No agents defined."
        lines = [f"{a.agent_id}: {a.name} — {a.description or '(no description)'}" for a in agents]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_agents failed: {exc}"


async def _list_workflows(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            workflows = await repo.list_workflows(session, limit=limit)
        if not workflows:
            return "No workflows defined."
        lines = [f"{w.workflow_id}: {w.name}" for w in workflows]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_workflows failed: {exc}"


async def _list_skills(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            skills = await repo.list_skills(session, limit=limit)
        if not skills:
            return "No skills defined."
        lines = [f"{s.slug}: {s.name}" for s in skills]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_skills failed: {exc}"


async def _list_chains(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            chains = await repo.list_agent_chains(session, limit=limit)
        if not chains:
            return "No chains defined."
        lines = [f"{c.chain_id}: {c.name}" for c in chains]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_chains failed: {exc}"


async def _list_dags(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            dags = await repo.list_agent_dags(session, limit=limit)
        if not dags:
            return "No DAGs defined."
        lines = [f"{d.dag_id}: {d.name}" for d in dags]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_dags failed: {exc}"


async def _run_agent(inp: dict[str, Any]) -> str:
    agent_id = inp.get("agent_id", "")
    message = inp.get("message", "")
    if not agent_id or not message:
        return "Error: agent_id and message are required"
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        run_id = str(uuid.uuid4())
        async with _db.SessionLocal() as session:
            await repo.create_agent_run(
                session,
                run_id=run_id,
                agent_id=agent_id,
                user_message=message,
            )
            await session.commit()
        return f"Agent run queued: run_id={run_id} agent_id={agent_id}"
    except Exception as exc:
        return f"run_agent failed: {exc}"


async def _run_workflow(inp: dict[str, Any]) -> str:
    workflow_id = inp.get("workflow_id", "")
    context = inp.get("context", {})
    if not workflow_id:
        return "Error: workflow_id is required"
    try:
        import artemis.db as _db
        from artemis.builders import repository as repo

        async with _db.SessionLocal() as session:
            run = await repo.create_workflow_run(
                session,
                workflow_id=workflow_id,
                shared_context=context,
            )
            await session.commit()
        return f"Workflow run queued: id={run.id} workflow_id={workflow_id}"
    except Exception as exc:
        return f"run_workflow failed: {exc}"


async def _propose_agent(inp: dict[str, Any]) -> str:
    """Propose a new agent definition (layer 3 — requires operator confirmation)."""
    name = inp.get("name", "")
    description = inp.get("description", "")
    goal = inp.get("goal", "")
    system_prompt = inp.get("system_prompt", "")
    if not name:
        return "Error: name is required"

    agent_id = inp.get("agent_id") or name.lower().replace(" ", "-")
    proposal = {
        "type": "agent_proposal",
        "agent_id": agent_id,
        "name": name,
        "description": description,
        "goal": goal,
        "system_prompt": system_prompt,
        "tools": inp.get("tools", []),
        "model": inp.get("model", "claude-sonnet-4-6"),
        "provider": inp.get("provider", "anthropic"),
    }
    return f"Agent proposal ready (pending confirmation):\n{json.dumps(proposal, indent=2)}"


async def _propose_workflow(inp: dict[str, Any]) -> str:
    """Propose a new workflow definition (layer 3)."""
    name = inp.get("name", "")
    if not name:
        return "Error: name is required"
    workflow_id = inp.get("workflow_id") or name.lower().replace(" ", "-")
    proposal = {
        "type": "workflow_proposal",
        "workflow_id": workflow_id,
        "name": name,
        "steps": inp.get("steps", []),
    }
    return f"Workflow proposal ready (pending confirmation):\n{json.dumps(proposal, indent=2)}"


async def _propose_skill(inp: dict[str, Any]) -> str:
    """Propose a new skill definition (layer 3)."""
    name = inp.get("name", "")
    if not name:
        return "Error: name is required"
    skill_id = inp.get("skill_id") or name.lower().replace(" ", "-")
    proposal = {
        "type": "skill_proposal",
        "skill_id": skill_id,
        "name": name,
        "description": inp.get("description", ""),
        "prompt": inp.get("prompt", ""),
    }
    return f"Skill proposal ready (pending confirmation):\n{json.dumps(proposal, indent=2)}"


# ── Tool definitions ──────────────────────────────────────────────────────────


LIST_AGENTS = Tool(
    name="list_agents",
    description="List defined agents in the Artemis builder. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

LIST_WORKFLOWS = Tool(
    name="list_workflows",
    description="List defined workflows in the Artemis builder. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

LIST_SKILLS = Tool(
    name="list_skills",
    description="List defined skills in the Artemis builder. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

LIST_CHAINS = Tool(
    name="list_chains",
    description="List defined agent chains in the Artemis builder. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

LIST_DAGS = Tool(
    name="list_dags",
    description="List defined DAG pipelines in the Artemis builder. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

RUN_AGENT = Tool(
    name="run_agent",
    description="Queue an agent run for a given agent_id with a user message. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "agent_id": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["agent_id", "message"],
    },
)

RUN_WORKFLOW = Tool(
    name="run_workflow",
    description="Queue a workflow run for a given workflow_id. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "context": {"type": "object", "description": "Shared context to inject", "default": {}},
        },
        "required": ["workflow_id"],
    },
)

PROPOSE_AGENT = Tool(
    name="propose_agent",
    description="Propose a new agent definition. Requires operator confirmation before creation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "agent_id": {"type": "string"},
            "description": {"type": "string"},
            "goal": {"type": "string"},
            "system_prompt": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}, "default": []},
            "model": {"type": "string", "default": "claude-sonnet-4-6"},
            "provider": {"type": "string", "default": "anthropic"},
        },
        "required": ["name"],
    },
)

PROPOSE_WORKFLOW = Tool(
    name="propose_workflow",
    description="Propose a new workflow definition. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "workflow_id": {"type": "string"},
            "steps": {"type": "array", "default": []},
        },
        "required": ["name"],
    },
)

PROPOSE_SKILL = Tool(
    name="propose_skill",
    description="Propose a new skill definition. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "skill_id": {"type": "string"},
            "description": {"type": "string"},
            "prompt": {"type": "string"},
        },
        "required": ["name"],
    },
)


def register_builders_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all builder tools into the provided registry."""
    registry.register(LIST_AGENTS, _list_agents, layer=1)
    registry.register(LIST_WORKFLOWS, _list_workflows, layer=1)
    registry.register(LIST_SKILLS, _list_skills, layer=1)
    registry.register(LIST_CHAINS, _list_chains, layer=1)
    registry.register(LIST_DAGS, _list_dags, layer=1)
    registry.register(RUN_AGENT, _run_agent, layer=2)
    registry.register(RUN_WORKFLOW, _run_workflow, layer=2)
    registry.register(PROPOSE_AGENT, _propose_agent, layer=3)
    registry.register(PROPOSE_WORKFLOW, _propose_workflow, layer=3)
    registry.register(PROPOSE_SKILL, _propose_skill, layer=3)
