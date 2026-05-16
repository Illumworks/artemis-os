"""DAG executor — dependency-graph agent pipeline with parallelism.

Nodes execute in topological order; nodes with no unresolved dependencies
run in parallel via asyncio.gather. Each node's output is passed as the
user_message to dependent nodes (concatenated if multiple parents).

IMPORTANT: Parallel nodes each get their own SQLAlchemy session because a
single AsyncSession is not safe for concurrent coroutines. The session passed
in by the caller is used only for loading the DAG definition and (in
run_dag_with_context) for reading parent context. Node agent_runs are written
to independent sessions obtained from artemis.db.SessionLocal and committed
immediately when each node completes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import ModelAdapter
from artemis.builders.executor import run_agent
from artemis.builders.models import AgentRun
from artemis.builders.repository import (
    get_agent_context,
    get_agent_dag,
)

logger = logging.getLogger(__name__)


async def run_dag(
    *,
    session: AsyncSession,
    dag_id: str,
    initial_inputs: dict[str, str] | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
) -> dict[str, AgentRun]:
    """Execute a DAG of agent nodes in dependency order and return results.

    Args:
        session:        SQLAlchemy async session.
        dag_id:         Slug of the DAG definition.
        initial_inputs: Optional seed inputs, keyed by node_id. If a node has
                        no depends_on entries, its input comes from here.
        owner_user_id:  User triggering the run.
        model_adapter:  Override adapter (for tests).

    Returns:
        Dict mapping node_id → AgentRun for every node that was executed.

    Raises:
        ValueError: If the DAG has a cycle.
    """
    dag = await get_agent_dag(session, dag_id)
    nodes_raw: list[dict[str, Any]] = dag.nodes if isinstance(dag.nodes, list) else []

    if not nodes_raw:
        return {}

    initial_inputs = initial_inputs or {}

    # Build adjacency structures
    node_map: dict[str, dict[str, Any]] = {str(n["id"]): n for n in nodes_raw}
    in_degree: dict[str, int] = {str(n["id"]): 0 for n in nodes_raw}
    dependents: dict[str, list[str]] = defaultdict(list)  # node → nodes that depend on it

    for node in nodes_raw:
        node_deps: list[str] = list(node.get("depends_on") or [])
        in_degree[str(node["id"])] += len(node_deps)
        for dep in node_deps:
            dependents[dep].append(str(node["id"]))

    # Topological sort (Kahn's algorithm) to detect cycles
    order: list[list[str]] = []  # list of parallel batches
    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0

    while queue:
        batch = list(queue)
        queue.clear()
        order.append(batch)
        visited += len(batch)
        next_in_degree = dict(in_degree)
        for nid in batch:
            for dep in dependents[nid]:
                next_in_degree[dep] -= 1
                if next_in_degree[dep] == 0:
                    queue.append(dep)
        in_degree = next_in_degree

    if visited != len(node_map):
        raise ValueError(f"DAG '{dag_id}' contains a cycle and cannot be executed")

    # Execute batches in order. Each node gets its own session so that
    # asyncio.gather can run them concurrently without session contention.
    results: dict[str, AgentRun] = {}

    for batch in order:
        batch_tasks = []
        for nid in batch:
            node = node_map[nid]
            user_msg = _resolve_input(nid, node, initial_inputs, results)
            batch_tasks.append(
                _run_node_isolated(
                    node_id=nid,
                    agent_id=str(node["agent_id"]),
                    user_message=user_msg,
                    owner_user_id=owner_user_id,
                    model_adapter=model_adapter,
                )
            )

        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
        for nid, res in zip(batch, batch_results, strict=True):
            if isinstance(res, BaseException):
                raise res
            results[nid] = res

    return results


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_input(
    node_id: str,
    node: dict[str, Any],
    initial_inputs: dict[str, str],
    results: dict[str, AgentRun],
) -> str | None:
    """Determine the user_message for *node_id*.

    Priority:
    1. If the node has no depends_on, use initial_inputs.get(node_id).
    2. Otherwise None — parent output resolution requires async context fetches
       and is done in run_dag_with_context instead.
    """
    node_deps: list[str] = list(node.get("depends_on") or [])
    if not node_deps:
        return initial_inputs.get(node_id)
    return None


async def _run_node_isolated(
    *,
    node_id: str,
    agent_id: str,
    user_message: str | None,
    owner_user_id: int | None,
    model_adapter: ModelAdapter | None,
) -> AgentRun:
    """Execute one DAG node in an independent session and commit on completion.

    Using an isolated session per node allows asyncio.gather to run nodes
    concurrently without hitting SQLAlchemy's 'Session is already flushing'
    error that occurs when a single session is shared across concurrent tasks.
    """
    import artemis.db as _db

    async with _db.SessionLocal() as node_session:
        run = await run_agent(
            session=node_session,
            agent_id=agent_id,
            user_message=user_message,
            owner_user_id=owner_user_id,
            model_adapter=model_adapter,
        )
        await node_session.commit()
        return run


async def run_dag_with_context(
    *,
    session: AsyncSession,
    dag_id: str,
    initial_inputs: dict[str, str] | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
) -> dict[str, AgentRun]:
    """Full DAG execution with proper parent-output chaining.

    This variant resolves depends_on by fetching final_response from each
    parent's agent_context and concatenating them as the child's user_message.
    This is the production entry point; run_dag is kept for compatibility.
    """
    dag = await get_agent_dag(session, dag_id)
    nodes_raw: list[dict[str, Any]] = dag.nodes if isinstance(dag.nodes, list) else []

    if not nodes_raw:
        return {}

    initial_inputs = initial_inputs or {}
    node_map: dict[str, dict[str, Any]] = {str(n["id"]): n for n in nodes_raw}
    in_degree: dict[str, int] = {str(n["id"]): 0 for n in nodes_raw}
    dependents: dict[str, list[str]] = defaultdict(list)

    for node in nodes_raw:
        node_deps: list[str] = list(node.get("depends_on") or [])
        in_degree[str(node["id"])] += len(node_deps)
        for dep in node_deps:
            dependents[dep].append(str(node["id"]))

    order: list[list[str]] = []
    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0

    while queue:
        batch = list(queue)
        queue.clear()
        order.append(batch)
        visited += len(batch)
        next_in_degree = dict(in_degree)
        for nid in batch:
            for dep in dependents[nid]:
                next_in_degree[dep] -= 1
                if next_in_degree[dep] == 0:
                    queue.append(dep)
        in_degree = next_in_degree

    if visited != len(node_map):
        raise ValueError(f"DAG '{dag_id}' contains a cycle")

    results: dict[str, AgentRun] = {}

    for batch in order:
        # Build user_message for each node in this batch; then run them in
        # isolated sessions so asyncio.gather can parallelize safely.
        batch_tasks = []
        for nid in batch:
            node = node_map[nid]
            node_deps_ctx: list[str] = list(node.get("depends_on") or [])

            if not node_deps_ctx:
                msg: str | None = initial_inputs.get(nid)
            else:
                # Collect parent responses using the caller session (read-only)
                parent_texts: list[str] = []
                for dep_id in node_deps_ctx:
                    if dep_id in results:
                        parent_run = results[dep_id]
                        try:
                            ctx = await get_agent_context(
                                session, parent_run.run_id, "final_response"
                            )
                            parent_texts.append(
                                ctx.value if isinstance(ctx.value, str) else str(ctx.value)
                            )
                        except ValueError:
                            pass
                msg = "\n\n".join(parent_texts) if parent_texts else None

            batch_tasks.append(
                _run_node_isolated(
                    node_id=nid,
                    agent_id=str(node["agent_id"]),
                    user_message=msg,
                    owner_user_id=owner_user_id,
                    model_adapter=model_adapter,
                )
            )

        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
        for nid, res in zip(batch, batch_results, strict=True):
            if isinstance(res, BaseException):
                raise res
            results[nid] = res

    return results
