"""Read-side helper: retrieve recent gate-decision observations for an agent.

Used by execute_agent_node (C3) to inject prior rejection context into an
agent's shared_context before each run, so agents can silently learn from
operator feedback without repeating mistakes.

Design:
  - Pure read-only — never writes observations.
  - Failure-isolated — any exception is caught, logged as WARNING, returns [].
  - Deterministic filtering: uses list_observations_for_scope + in-memory
    filter (not FTS) so the result is predictable regardless of tsvector indexing.

Heuristic for "rejected" content:
  The C-1+2 write path composes content strings that include the word " rejected "
  (surrounded by spaces) for rejection events: e.g.
      "Operator rejected signal #101 at Gate 1 on ..."
      "... rejected pipeline marketing-ci2 gate at node ..."
  We match that literal substring to identify rejection observations.
  Approval events use " approved " and never contain " rejected ".
  The filter is applied after category scoping; false positives are impossible
  given the writer's fixed templates.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Regex to extract the reason clause from observation content.
# Matches: "Reason: <text>" up to an optional "Citations:" clause, a period+space,
# or end-of-string — whichever comes first.
_REASON_RE = re.compile(r"Reason:\s*(.+?)(?:\.\s*Citations:|\.\s*$|$)", re.DOTALL)

# Literal content substring that signals a rejection decision (see docstring).
_REJECTED_MARKER = " rejected "

# Gate-decision categories written by MC2 / MC4 in memory_carryover.py.
_DEFAULT_CATEGORIES: tuple[str, ...] = ("signal_gate1_decision", "pipeline_gate_decision")


def _parse_reason(content: str) -> str | None:
    """Extract the reason clause from observation content, or None."""
    m = _REASON_RE.search(content)
    if m is None:
        return None
    reason = m.group(1).strip()
    return reason if reason else None


async def fetch_agent_rejection_context(
    session: AsyncSession,
    agent_id: str,
    *,
    limit: int = 5,
    categories: tuple[str, ...] = _DEFAULT_CATEGORIES,
) -> list[dict[str, Any]]:
    """Return up to `limit` recent gate-decision rejection observations for agent_id.

    Looks up observations attached to the `agent:<agent_id>` scope (primary OR
    secondary, since C-1+2 writes the agent scope as a secondary scope) via the
    join table. Filters in memory to the requested categories and to rows whose
    content contains " rejected " (the literal marker embedded by memory_carryover).

    Each returned entry is a dict:
        {
            "observation_id": int,
            "category":       str,
            "content":        str,     # full observation content
            "reason":         str | None,   # parsed from "Reason: ..." clause
            "created_at":     str,     # ISO-8601
        }

    The list is sorted newest-first and sliced to `limit`.

    Heuristic note: the " rejected " marker is reliable because:
      - MC2 writes "... rejected signal #N at Gate 1 ..." for rejections.
      - MC4 writes "... rejected pipeline <id> gate at node ..." for rejections.
      - Approval content uses "approved" and never contains " rejected ".
    This avoids FTS query noise (searching "rejected" via tsvector would also
    match approvals that mention the word in the surrounding prose).

    Failure isolation: any exception is caught, logged as WARNING, and [] is
    returned so agent execution always proceeds.
    """
    try:
        from artemis.memory.store import list_observations_for_scope

        # Fetch all observations where agent_id appears as ANY scope
        # (is_primary=None means both primary and secondary join rows).
        obs_list = await list_observations_for_scope(
            session,
            scope_kind="agent",
            scope_id=agent_id,
            is_primary=None,
        )

        # In-memory filter: category must be in requested set, content must
        # contain the rejection marker, and the observation must not be superseded.
        filtered = [
            obs
            for obs in obs_list
            if obs.category in categories
            and _REJECTED_MARKER in obs.content
            and obs.superseded_by is None
        ]

        # Sort newest-first by created_at, then slice to limit.
        filtered.sort(key=lambda o: o.created_at, reverse=True)
        filtered = filtered[:limit]

        return [
            {
                "observation_id": obs.id,
                "category": obs.category,
                "content": obs.content,
                "reason": _parse_reason(obs.content),
                "created_at": obs.created_at.isoformat(),
            }
            for obs in filtered
        ]

    except Exception as exc:
        logger.warning(
            "fetch_agent_rejection_context failed for agent_id=%r: %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return []
