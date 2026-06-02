"""Trigger node executor.

Handles: trigger_scheduled, trigger_manual, trigger_webhook, trigger_event

Trigger nodes are the entry point of the graph — no upstream nodes.
Execution just records the fire timestamp and mode; no LLM calls made.

Return value:
  {"status": "succeeded", "output_summary": "Trigger fired at <ts> via <mode>"}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


async def execute_trigger_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    run_id: str,
    mode: str = "manual",
) -> dict[str, Any]:
    """Execute a trigger node.

    Args:
        node:        The node dict from pipeline.nodes (id, type, config, label).
        node_states: Current node_states for this run (used for context only; not mutated).
        run_id:      Pipeline run ID (for logging).
        mode:        How the pipeline was triggered: 'manual' | 'scheduled' | 'webhook' | 'event'.

    Returns:
        NodeState-compatible dict with status + output_summary.
    """
    node_type: str = node.get("type", "trigger_manual")
    now = datetime.now(UTC).isoformat()

    type_to_mode: dict[str, str] = {
        "trigger_scheduled": "scheduled",
        "trigger_manual": "manual",
        "trigger_webhook": "webhook",
        "trigger_event": "event",
    }
    effective_mode = type_to_mode.get(node_type, mode)

    return {
        "status": "succeeded",
        "output_summary": f"Trigger fired at {now} via {effective_mode}",
        "cost_usd": 0.0,
    }
