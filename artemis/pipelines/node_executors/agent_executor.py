"""Agent invocation node executor.

Handles: agent_invocation nodes.

Config shape:
  {
    "agent_id":      str,         # slug of the agent to run
    "mode":          str,         # "scheduled" | "manual" (informational)
    "cost_cap_usd":  float | None # per-node cap (None = no cap)
  }

Behaviour:
1. Resolves credentials via connectors/resolver.py for any tool namespaces
2. Injects reason_codes_emitted from agent blueprint into system context
3. Calls builders/executor.py::run_agent() — do NOT reimplement
4. Accumulates cost; if cumulative > cost_cap_usd, marks partial_complete
5. Returns NodeState-compatible dict

Cost units: estimated via token counts (input_tokens * 3e-6 + output_tokens * 15e-6
for claude-sonnet-4-6 pricing; these are approximate and rounded for display).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Approximate USD/token rates (sonnet-level; haiku is cheaper but we round up)
_INPUT_COST_PER_TOKEN = 3e-6
_OUTPUT_COST_PER_TOKEN = 15e-6


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _INPUT_COST_PER_TOKEN) + (output_tokens * _OUTPUT_COST_PER_TOKEN)


async def execute_agent_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    session: AsyncSession,
    run_id: str,
    accumulated_cost_usd: float = 0.0,
    model_adapter: Any | None = None,
) -> dict[str, Any]:
    """Execute an agent_invocation node.

    Args:
        node:                  Node dict (id, type, config, label).
        node_states:           Current node_states for this run.
        session:               Async DB session (caller owns transaction boundary).
        run_id:                Pipeline run ID (for shared_context injection).
        accumulated_cost_usd:  Running cost total for this run (enforces cost cap).
        model_adapter:         Override adapter for tests (FakeAdapter).

    Returns:
        NodeState-compatible dict with status, output_summary, cost_usd.
        If cost cap is exceeded: status="partial_complete".
    """
    from artemis.builders.executor import run_agent
    from artemis.connectors.resolver import ConnectorNotConfigured, get_credentials_for_tool

    config: dict[str, Any] = node.get("config") or {}
    agent_id: str = config.get("agent_id", "")
    cost_cap: float | None = config.get("cost_cap_usd")

    if not agent_id:
        return {
            "status": "failed",
            "error": "agent_executor: node config missing 'agent_id'",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Build shared context from prior node outputs
    shared_context: dict[str, Any] = {
        "pipeline_run_id": run_id,
        "triggering_node": node.get("id", ""),
    }
    # Inject brief summary from qualifier node outputs if present
    for state_key, state_val in node_states.items():
        if isinstance(state_val, dict) and state_val.get("output_summary"):
            shared_context[f"prior_{state_key}"] = state_val["output_summary"]

    # Resolve connector credentials for any tool namespaces this agent uses
    # We do a best-effort resolution — if an agent has tools with connector
    # namespaces, attempt to load them. Failure raises ConnectorNotConfigured.
    try:
        from artemis.builders.repository import get_agent

        agent = await get_agent(session, agent_id)
    except ValueError as exc:
        return {
            "status": "failed",
            "error": f"Agent '{agent_id}' not found: {exc}",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Check tool namespaces for connector requirements
    tools_list = agent.tools if isinstance(agent.tools, list) else []
    for tool_spec in tools_list:
        if isinstance(tool_spec, dict):
            namespace = tool_spec.get("namespace") or tool_spec.get("connector_kind")
            if namespace and isinstance(namespace, str) and "." in namespace:
                namespace = namespace.split(".")[0]
            if namespace and namespace not in ("", "builtin"):
                try:
                    await get_credentials_for_tool(session, agent.id, namespace)
                except ConnectorNotConfigured:
                    raise ConnectorNotConfigured(agent.id, namespace) from None

    # Inject reason_codes_emitted from blueprint
    reason_codes: list[str] = []
    if isinstance(agent.reason_codes_emitted, list):
        reason_codes = agent.reason_codes_emitted
    if reason_codes:
        shared_context["reason_codes_emitted"] = ", ".join(reason_codes)

    # Resolve the model adapter via the provider cascade unless the caller
    # supplied one (e.g., FakeAdapter in tests).
    resolved_adapter = model_adapter
    if resolved_adapter is None:
        from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

        try:
            resolved_adapter = resolve_adapter(
                getattr(agent, "provider", None),
                getattr(agent, "fallback_provider", None),
            )
        except NoProviderAvailableError as exc:
            return {
                "status": "failed",
                "error": f"No LLM provider available for agent '{agent_id}': {exc}",
                "output_summary": "",
                "cost_usd": 0.0,
            }

    # Imperative instruction that drives immediate tool use (shared with the scout
    # scheduler so autonomous runs behave identically). A per-node override in
    # config["instruction"] wins over the role-based default.
    from artemis.builders.executor import default_agent_instruction

    instruction = default_agent_instruction(agent_id, config.get("instruction"))

    # Run the agent
    agent_run = await run_agent(
        session=session,
        agent_id=agent_id,
        shared_context=shared_context,
        model_adapter=resolved_adapter,
        user_message=instruction,
    )

    # Compute cost
    input_tokens = agent_run.cost_input_tokens or 0
    output_tokens = agent_run.cost_output_tokens or 0
    node_cost = _estimate_cost(input_tokens, output_tokens)
    new_accumulated = accumulated_cost_usd + node_cost

    # Enforce cost cap
    if cost_cap is not None and new_accumulated > cost_cap:
        return {
            "status": "partial_complete",
            "error": f"cost_cap_exceeded: ${new_accumulated:.4f} > cap ${cost_cap:.2f}",
            "output_summary": f"Agent '{agent_id}' stopped: cost cap ${cost_cap:.2f} exceeded",
            "cost_usd": node_cost,
        }

    if agent_run.status == "failed":
        return {
            "status": "failed",
            "error": agent_run.error or f"Agent '{agent_id}' run failed",
            "output_summary": "",
            "cost_usd": node_cost,
        }

    # Extract final response text from agent_context (run_id is the UUID string field)
    output_text = ""
    try:
        from artemis.builders.repository import get_agent_context

        ctx_row = await get_agent_context(session, agent_run.run_id, "final_response")
        raw_val = ctx_row.value
        output_text = str(raw_val or "")[:500]
    except (ValueError, Exception):
        pass

    run_uuid: str = agent_run.run_id or ""
    summary = f"Agent '{agent_id}' completed (run {run_uuid[:8]})"
    if output_text:
        summary += f": {output_text[:120]}…" if len(output_text) > 120 else f": {output_text}"

    return {
        "status": "succeeded",
        "output_summary": summary,
        "cost_usd": node_cost,
        "agent_run_id": run_uuid,
    }
