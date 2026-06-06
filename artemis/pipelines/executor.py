"""Pipeline execution engine — PIPE4.

Public class: PipelineExecutor

Usage:
    executor = PipelineExecutor(run_id)
    await executor.run(session)
    await session.commit()

Architecture:
    run()
      ├── Load pipeline definition + current node_states from DB
      ├── Topological sort over nodes (Kahn's algorithm)
      ├── For each node in topo order:
      │   ├── Skip if already succeeded/failed/partial_complete
      │   ├── Skip downstream of a suspended gate
      │   ├── Dispatch to per-node executor
      │   ├── Persist node_states after EVERY transition (crash-recoverable)
      │   └── If node returns suspend → set status=awaiting_approval, exit
      ├── On completion → status=succeeded
      └── On failure  → status=failed with error_message

node_states shape per node:
  {
    "status":         "pending"|"running"|"succeeded"|"failed"|"suspended"|
                      "partial_complete"|"waiting_for_upstream"|"skipped",
    "started_at":     "2026-05-22T...",
    "ended_at":       "2026-05-22T..." | null,
    "output_summary": "...",
    "error":          null | "...",
    "cost_usd":       0.05,
    "delivery_log":   [...],        # human_gate Slack DM status
    "decision":       null | "approved"|"rejected"|"auto_approved"|"auto_rejected"
  }
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset(["succeeded", "failed", "partial_complete", "skipped"])
_TRIGGER_TYPES = frozenset(
    ["trigger_scheduled", "trigger_manual", "trigger_webhook", "trigger_event"]
)


def _topological_sort(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Kahn's algorithm topological sort.

    Returns nodes in execution order (triggers first, terminal nodes last).
    Raises ValueError on cycle detection.
    """
    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        src = edge.get("source_node_id", "")
        tgt = edge.get("target_node_id", "")
        if src in node_map and tgt in node_map:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    ordered: list[dict[str, Any]] = []

    while queue:
        nid = queue.popleft()
        ordered.append(node_map[nid])
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered) != len(nodes):
        raise ValueError(
            "Pipeline graph has a cycle — topological sort failed. "
            f"Ordered {len(ordered)} of {len(nodes)} nodes."
        )

    return ordered


def _make_node_state(
    *,
    status: str,
    started_at: str | None = None,
    ended_at: str | None = None,
    output_summary: str = "",
    error: str | None = None,
    cost_usd: float = 0.0,
    **extra: Any,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "output_summary": output_summary,
        "error": error,
        "cost_usd": cost_usd,
    }
    state.update(extra)
    return state


class PipelineExecutor:
    """Walks a pipeline graph, dispatching each node to its type executor.

    Resumable: if a gate suspended the run, re-instantiating with the same
    run_id and calling run() again will continue from where it left off.
    """

    def __init__(
        self,
        pipeline_run_id: str,
        ancestor_run_ids: set[str] | None = None,
    ) -> None:
        self.run_id = pipeline_run_id
        self.ancestor_run_ids: set[str] = ancestor_run_ids or set()

    async def run(self, session: AsyncSession) -> None:
        """Execute the pipeline run identified by self.run_id.

        Raises: ValueError if run not found.
        """
        run = await repo.get_pipeline_run(session, self.run_id)
        pipeline = await repo.get_pipeline(session, run.pipeline_id)

        nodes: list[dict[str, Any]] = list(pipeline.nodes or [])
        edges: list[dict[str, Any]] = list(pipeline.edges or [])
        node_states: dict[str, Any] = dict(run.node_states or {})

        # Mark run as running if queued
        if run.status == "queued":
            run.status = "running"
            run.started_at = datetime.now(UTC)
            await session.flush()

        # Sort nodes
        try:
            ordered = _topological_sort(nodes, edges)
        except ValueError as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(UTC)
            await session.flush()
            return

        # Determine accumulated cost so far
        accumulated_cost = float(getattr(run, "cost_usd", 0.0) or 0.0)

        # Find the set of nodes that are downstream of a suspended gate.
        # We track whether ANY gate on a path to this node is suspended.
        suspended_gate_ids = {
            nid
            for nid, ns in node_states.items()
            if isinstance(ns, dict) and ns.get("status") == "suspended"
        }

        # Adjacency for downstream detection (always built; used both here and
        # when a newly-suspended gate is encountered during the node loop below)
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            adjacency[edge.get("source_node_id", "")].append(edge.get("target_node_id", ""))

        reachable_from_suspended: set[str] = set()
        if suspended_gate_ids:
            # BFS from each already-suspended gate
            for gate_id in suspended_gate_ids:
                q: deque[str] = deque([gate_id])
                while q:
                    cur = q.popleft()
                    for nxt in adjacency.get(cur, []):
                        if nxt not in reachable_from_suspended:
                            reachable_from_suspended.add(nxt)
                            q.append(nxt)

        for node in ordered:
            node_id: str = node["id"]
            node_type: str = node.get("type", "")
            existing = node_states.get(node_id, {})
            existing_status = (
                existing.get("status", "pending") if isinstance(existing, dict) else "pending"
            )

            # Skip nodes that are already terminal
            if existing_status in _TERMINAL_STATUSES:
                accumulated_cost += float(existing.get("cost_usd", 0.0))
                continue

            # Skip nodes downstream of a suspended gate (they should not run yet)
            if node_id in reachable_from_suspended:
                continue

            # Awaiting-approval gate: re-check if now resolved
            if existing_status == "suspended":
                decision = existing.get("decision")
                if decision in ("approved", "auto_approved"):
                    # Gate resolved; mark succeeded and continue downstream
                    existing["status"] = "succeeded"
                    node_states[node_id] = existing
                    run.node_states = node_states
                    await session.flush()
                    # Remove this gate from suspended tracking and unblock its
                    # downstream nodes so they can run in this same pass.
                    suspended_gate_ids.discard(node_id)
                    # BFS-remove all nodes reachable exclusively from this gate
                    # (they were blocked; now unblocked since this gate resolved).
                    # Recompute reachable from remaining suspended gates.
                    reachable_from_suspended.clear()
                    for gid in suspended_gate_ids:
                        q2: deque[str] = deque([gid])
                        while q2:
                            cur2 = q2.popleft()
                            for nxt2 in adjacency.get(cur2, []):
                                if nxt2 not in reachable_from_suspended:
                                    reachable_from_suspended.add(nxt2)
                                    q2.append(nxt2)
                    continue
                elif decision in ("rejected", "auto_rejected"):
                    run.status = "failed"
                    run.error_message = f"Gate '{node_id}' rejected (decision={decision})"
                    run.completed_at = datetime.now(UTC)
                    run.node_states = node_states
                    await session.flush()
                    return
                else:
                    # Still waiting for human decision — stay suspended
                    run.status = "awaiting_approval"
                    await session.flush()
                    return

            # Mark node as running and persist
            started_at = datetime.now(UTC).isoformat()
            node_states[node_id] = _make_node_state(
                status="running",
                started_at=started_at,
            )
            run.node_states = node_states
            await session.flush()

            if node_id == "qualifier_brief_composer":
                qualified_count = await _qualified_signal_count_for_run(session, run)
                if qualified_count == 0:
                    ended_at = datetime.now(UTC).isoformat()
                    node_states[node_id] = _make_node_state(
                        status="succeeded",
                        started_at=started_at,
                        ended_at=ended_at,
                        output_summary="No signals qualified this run",
                    )
                    for downstream_id in _descendants_of(node_id, edges):
                        if downstream_id in node_states:
                            continue
                        node_states[downstream_id] = _make_node_state(
                            status="skipped",
                            ended_at=ended_at,
                            output_summary="Skipped: upstream produced no signals",
                            reason="upstream produced no signals",
                        )
                    metadata = dict(run.metadata_ or {})
                    metadata["summary"] = "No signals this run; downstream skipped"
                    metadata["completion_reason"] = "no_signals"
                    run.status = "succeeded"
                    run.completed_at = datetime.now(UTC)
                    run.error_message = None
                    run.metadata_ = metadata
                    run.node_states = node_states
                    await session.flush()
                    return

            try:
                result = await self._dispatch_node(
                    node=node,
                    node_states=node_states,
                    all_nodes=nodes,
                    all_edges=edges,
                    session=session,
                    pipeline_id=pipeline.id,
                    pipeline_name=pipeline.name,
                    accumulated_cost_usd=accumulated_cost,
                )
            except Exception as exc:
                logger.exception("Node '%s' raised an unexpected exception", node_id)
                result = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "output_summary": "",
                    "cost_usd": 0.0,
                }

            ended_at = datetime.now(UTC).isoformat()
            node_cost = float(result.get("cost_usd", 0.0))
            accumulated_cost += node_cost

            node_state = _make_node_state(
                status=result.get("status", "failed"),
                started_at=started_at,
                ended_at=ended_at,
                output_summary=result.get("output_summary", ""),
                error=result.get("error"),
                cost_usd=node_cost,
                **{
                    k: v
                    for k, v in result.items()
                    if k
                    not in (
                        "status",
                        "output_summary",
                        "error",
                        "cost_usd",
                        "started_at",
                        "ended_at",
                    )
                },
            )
            node_states[node_id] = node_state

            # Update cumulative cost
            run.cost_usd = accumulated_cost
            run.node_states = node_states
            await session.flush()

            node_result_status = result.get("status")

            if node_result_status == "suspended":
                run.status = "awaiting_approval"
                # Add this gate to the suspended set so downstream is blocked
                suspended_gate_ids.add(node_id)
                # Recompute reachable from the new suspended set
                qnew: deque[str] = deque([node_id])
                while qnew:
                    cur = qnew.popleft()
                    for nxt in adjacency.get(cur, []):
                        if nxt not in reachable_from_suspended:
                            reachable_from_suspended.add(nxt)
                            qnew.append(nxt)
                await session.flush()
                # Continue — there may be parallel branches not downstream of this gate

            elif node_result_status == "waiting_for_upstream":
                # Leave as-is; the node will be re-evaluated on next run() call
                continue

            elif node_result_status == "partial_complete":
                run.status = "partial_complete"
                run.error_message = result.get("error") or f"Node '{node_id}' hit cost cap"
                run.completed_at = datetime.now(UTC)
                run.node_states = node_states
                await session.flush()
                return

            elif node_result_status == "failed":
                # Check if this is a connector error — surface clearly
                error_msg = result.get("error", f"Node '{node_id}' failed")
                # Starbridge / connector missing → propagate clearly
                if (
                    "ConnectorNotConfigured" in str(error_msg)
                    or "connector required" in str(error_msg).lower()
                ):
                    error_msg = f"Connector not linked: {error_msg}"

                # Optional nodes (continue_on_failure: true) — log and continue
                node_config = node.get("config") or {}
                if node_config.get("continue_on_failure"):
                    logger.warning(
                        "Optional node '%s' failed (continue_on_failure=true) — "
                        "continuing run. error=%s",
                        node_id,
                        error_msg,
                    )
                    # node_state already written with status=failed above; keep run alive
                    continue

                run.status = "failed"
                run.error_message = error_msg
                run.completed_at = datetime.now(UTC)
                run.node_states = node_states
                await session.flush()
                return

            elif node_result_status == "succeeded":
                # If this gate was previously suspended but is now resolved,
                # remove from suspended tracking
                if node_type == "human_gate":
                    suspended_gate_ids.discard(node_id)
                    reachable_from_suspended = {
                        nid
                        for nid in reachable_from_suspended
                        if any(n2id in suspended_gate_ids for n2id in _ancestors_of(nid, edges))
                    }

            # conditional node: record branch selection for edge routing
            if node_result_status == "succeeded" and node_type == "conditional":
                branch = result.get("branch", "true_branch")
                node_states[node_id]["branch"] = branch
                run.node_states = node_states
                await session.flush()

        # After processing all ordered nodes:
        # If there are still suspended gates, run stays at awaiting_approval
        pending_gates = [
            nid
            for nid, ns in node_states.items()
            if isinstance(ns, dict) and ns.get("status") == "suspended"
        ]
        if pending_gates:
            run.status = "awaiting_approval"
            await session.flush()
            return

        # All nodes processed without failure/suspension
        if run.status not in _TERMINAL_STATUSES:
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            run.node_states = node_states
            await session.flush()

    async def _dispatch_node(
        self,
        *,
        node: dict[str, Any],
        node_states: dict[str, Any],
        all_nodes: list[dict[str, Any]],
        all_edges: list[dict[str, Any]],
        session: AsyncSession,
        pipeline_id: str,
        pipeline_name: str,
        accumulated_cost_usd: float,
    ) -> dict[str, Any]:
        """Route a node to the appropriate executor."""
        from artemis.pipelines.node_executors.agent_executor import execute_agent_node
        from artemis.pipelines.node_executors.conditional_executor import execute_conditional_node
        from artemis.pipelines.node_executors.human_gate_executor import execute_human_gate_node
        from artemis.pipelines.node_executors.sub_pipeline_executor import execute_sub_pipeline_node
        from artemis.pipelines.node_executors.trigger_executor import execute_trigger_node

        node_type: str = node.get("type", "")

        if node_type in _TRIGGER_TYPES:
            return await execute_trigger_node(
                node=node,
                node_states=node_states,
                run_id=self.run_id,
            )

        elif node_type == "agent_invocation":
            result = await execute_agent_node(
                node=node,
                node_states=node_states,
                session=session,
                run_id=self.run_id,
                accumulated_cost_usd=accumulated_cost_usd,
            )
            return result

        elif node_type == "human_gate":
            return await execute_human_gate_node(
                node=node,
                node_states=node_states,
                all_nodes=all_nodes,
                all_edges=all_edges,
                session=session,
                run_id=self.run_id,
                pipeline_name=pipeline_name,
            )

        elif node_type == "conditional":
            # Build context from node_states outputs
            context: dict[str, Any] = {}
            for nid, ns in node_states.items():
                if isinstance(ns, dict):
                    if ns.get("output_summary"):
                        context[nid] = ns["output_summary"]
                    if ns.get("branch"):
                        context[f"{nid}_branch"] = ns["branch"]
            return await execute_conditional_node(
                node=node,
                node_states=node_states,
                context=context,
            )

        elif node_type == "sub_pipeline":
            return await execute_sub_pipeline_node(
                node=node,
                node_states=node_states,
                session=session,
                run_id=self.run_id,
                pipeline_id=pipeline_id,
                ancestor_run_ids=self.ancestor_run_ids,
            )

        else:
            logger.warning(
                "Unknown node type %r for node %r; marking succeeded as no-op",
                node_type,
                node.get("id"),
            )
            return {
                "status": "succeeded",
                "output_summary": f"No-op: unknown node type '{node_type}'",
                "cost_usd": 0.0,
            }


def _ancestors_of(node_id: str, edges: list[dict[str, Any]]) -> set[str]:
    """Return all nodes that have a path TO node_id."""
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        src = edge.get("source_node_id", "")
        tgt = edge.get("target_node_id", "")
        parents[tgt].add(src)

    visited: set[str] = set()
    queue: deque[str] = deque([node_id])
    while queue:
        cur = queue.popleft()
        for parent in parents.get(cur, set()):
            if parent not in visited:
                visited.add(parent)
                queue.append(parent)
    return visited


def _descendants_of(node_id: str, edges: list[dict[str, Any]]) -> set[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.get("source_node_id", "")].append(edge.get("target_node_id", ""))

    visited: set[str] = set()
    queue: deque[str] = deque([node_id])
    while queue:
        cur = queue.popleft()
        for child in adjacency.get(cur, []):
            if child not in visited:
                visited.add(child)
                queue.append(child)
    return visited


async def _qualified_signal_count_for_run(session: AsyncSession, run: Any) -> int:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM signal_queue
            WHERE signal_status = 'qualified'
              AND pipeline_run_id = :pipeline_run_id
            """
        ),
        {"pipeline_run_id": run.id},
    )
    return int(result.scalar_one() or 0)
