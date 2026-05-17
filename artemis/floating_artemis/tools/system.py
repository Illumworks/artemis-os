"""System health tools for Floating Artemis.

Authority layers:
  1: health_check, recent_failures
  3: propose_fix
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry


async def _health_check(inp: dict[str, Any]) -> str:  # noqa: ARG001
    """Return a basic health snapshot: DB connectivity, active runs count."""
    result: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "db": "unknown",
        "active_runs": None,
    }
    try:
        from sqlalchemy import text

        import artemis.db as _db

        async with _db.SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            result["db"] = "ok"

            # Count active runs from view
            run_count = await session.execute(
                text("SELECT COUNT(*) FROM v_floating_artemis_active_runs")
            )
            result["active_runs"] = run_count.scalar_one()
    except Exception as exc:
        result["db"] = f"error: {exc}"

    return json.dumps(result)


async def _recent_failures(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 10))
    try:
        from sqlalchemy import text

        import artemis.db as _db

        async with _db.SessionLocal() as session:
            # Query failed agent runs
            rows = await session.execute(
                text("""
                    SELECT run_id, agent_id, error, completed_at
                    FROM agent_runs
                    WHERE status = 'failed'
                    ORDER BY completed_at DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            failures = rows.mappings().all()

        if not failures:
            return "No recent failures."
        lines = [f"{r['run_id']} agent={r['agent_id']} error={r['error']!r}" for r in failures]
        return "\n".join(lines)
    except Exception as exc:
        return f"recent_failures query failed: {exc}"


async def _propose_fix(inp: dict[str, Any]) -> str:
    """Propose a fix for a detected system issue (layer 3 — operator confirms)."""
    issue = inp.get("issue", "")
    proposed_action = inp.get("proposed_action", "")
    if not issue or not proposed_action:
        return "Error: issue and proposed_action are required"
    proposal = {
        "type": "system_fix_proposal",
        "issue": issue,
        "proposed_action": proposed_action,
        "risk": inp.get("risk", "low"),
    }
    return f"Fix proposal (pending confirmation):\n{json.dumps(proposal, indent=2)}"


HEALTH_CHECK = Tool(
    name="health_check",
    description="Return a health snapshot: DB connectivity, active run count. [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

RECENT_FAILURES = Tool(
    name="recent_failures",
    description="Return the most recently failed agent runs. [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 10}},
        "required": [],
    },
)

PROPOSE_FIX = Tool(
    name="propose_fix",
    description="Propose a remediation action for a detected system issue. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "issue": {"type": "string"},
            "proposed_action": {"type": "string"},
            "risk": {"type": "string", "enum": ["low", "medium", "high"], "default": "low"},
        },
        "required": ["issue", "proposed_action"],
    },
)


def register_system_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(HEALTH_CHECK, _health_check, layer=1)
    registry.register(RECENT_FAILURES, _recent_failures, layer=1)
    registry.register(PROPOSE_FIX, _propose_fix, layer=3)
