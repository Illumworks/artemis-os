"""Pipeline execution audit log.

Writes structured audit entries for gate decisions, timeout-based auto-decisions,
and escalation events. Audit rows are appended to pipeline_runs.node_states under
a reserved ``_audit`` key as a list of dicts.

Public entry point: ``audit_log()``

Each entry shape:
  {
    "kind":              str,          # gate_auto_decision | gate_human_decision | escalation_sent
    "pipeline_run_id":   str,
    "node_id":           str,
    "decision":          str | None,   # approved | rejected | auto_approved | auto_rejected | escalated
    "reason":            str,          # e.g. "timeout_after_72h"
    "actor":             str | None,   # email of human actor, or None for automated decisions
    "configured_approvers": list[str],
    "elapsed_seconds":   float | None,
    "ts":                str,          # ISO-8601 UTC
  }
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo

logger = logging.getLogger(__name__)

_AUDIT_KEY = "_audit"


async def audit_log(
    session: AsyncSession,
    entry: dict[str, Any],
) -> None:
    """Append *entry* to pipeline_runs.node_states[_audit].

    Non-fatal: logs and swallows errors so audit failures never block execution.
    Callers should still flush/commit after calling this if they want atomicity
    with the surrounding node_states write.
    """
    run_id: str | None = entry.get("pipeline_run_id")
    if not run_id:
        logger.warning("audit_log called without pipeline_run_id; skipping: %r", entry)
        return

    try:
        run = await repo.get_pipeline_run(session, run_id)
        node_states: dict[str, Any] = dict(run.node_states or {})
        audit_trail: list[dict[str, Any]] = list(node_states.get(_AUDIT_KEY) or [])
        audit_trail.append(
            {
                **entry,
                "ts": entry.get("ts") or datetime.now(UTC).isoformat(),
            }
        )
        node_states[_AUDIT_KEY] = audit_trail
        run.node_states = node_states
        await session.flush()
    except Exception:
        logger.exception("audit_log failed for run %s; continuing", run_id)
