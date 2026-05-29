"""CC20 — Builder grounding helpers.

Pure, read-only extractors that surface facts about the codebase and DB so the
Builder LLM can ground proposals against reality instead of inference.

Three helpers (all cached per-process after first call):
  extract_allowed_status_values()  — all valid signal_queue.signal_status values
  extract_db_constraints(tables)   — columns + constraints for requested tables
  extract_tool_registry()          — all registered tool names + schemas

These are called by the three new MCP tools added in CC20:
  builder_read_tool_signatures
  builder_read_db_schema
  builder_read_skill_catalog
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Per-process caches ─────────────────────────────────────────────────────────

_allowed_status_cache: list[str] | None = None
_db_constraints_cache: dict[str, Any] = {}
_tool_registry_cache: list[dict[str, Any]] | None = None


def _reset_caches() -> None:
    """Clear all per-process caches. Useful in tests."""
    global _allowed_status_cache, _db_constraints_cache, _tool_registry_cache
    _allowed_status_cache = None
    _db_constraints_cache = {}
    _tool_registry_cache = None


# ── Static code-scan for status values ─────────────────────────────────────────


def _status_values_from_code() -> list[str]:
    """Extract valid signal_queue.signal_status values from the state-machine module.

    Sources (in priority order):
    1. SignalState StrEnum members (state_machine.py) — the canonical list.
    2. Hard-coded fallback: the six values the brief documents.

    This is intentionally code-only (no DB round-trip) so it can be called at
    startup without a session.  The DB DISTINCT scan in
    ``extract_allowed_status_values`` unions these with live DB values.
    """
    try:
        from artemis.marketing.state_machine import SignalState

        return [member.value for member in SignalState]
    except Exception:
        logger.warning(
            "grounding: could not import SignalState from state_machine; using hard-coded fallback"
        )
        # Fallback — matches brief's authoritative list + state_machine.py members.
        return [
            "pending_qualification",
            "qualified",
            "rejected_hard_filter",
            "suppressed_stale",
            "approved",
            "rejected_at_gate_1",
            "snoozed",
            "archived",
            "held_pending_corroboration",
        ]


def _status_values_from_qualifier() -> list[str]:
    """Scan qualifier_rule_layer.py for any status strings not in SignalState.

    The brief specifically calls out ``held_pending_corroboration`` (line 224 of
    qualifier_rule_layer.py) which is written directly without being in the
    StrEnum. We pull it here via docstring/source scan rather than grep.
    """
    extra: list[str] = []
    try:
        import artemis.marketing.qualifier_rule_layer as qrl

        src = inspect.getsource(qrl)
        # Extract new_status="..." patterns.
        import re

        for m in re.finditer(r'new_status\s*=\s*"([^"]+)"', src):
            val = m.group(1)
            if val not in extra:
                extra.append(val)
    except Exception:
        logger.debug("grounding: qualifier_rule_layer scan failed (non-fatal)", exc_info=True)
    return extra


# ── extract_allowed_status_values ──────────────────────────────────────────────


async def extract_allowed_status_values(session: AsyncSession) -> list[str]:
    """Return all valid signal_queue.signal_status values.

    Combines three sources:
    1. SignalState StrEnum members from state_machine.py.
    2. Source-scan of qualifier_rule_layer.py for ``new_status=`` strings.
    3. DISTINCT query against the live DB (catches rows written before enum was
       formalised).

    Result is cached per-process; the cache is stable after first call.
    """
    global _allowed_status_cache
    if _allowed_status_cache is not None:
        return _allowed_status_cache

    values: set[str] = set()

    # Source 1 + 2: code scan.
    for v in _status_values_from_code():
        values.add(v)
    for v in _status_values_from_qualifier():
        values.add(v)

    # Source 3: live DB DISTINCT.
    try:
        result = await session.execute(
            text("SELECT DISTINCT signal_status FROM signal_queue WHERE signal_status IS NOT NULL")
        )
        for (status,) in result.fetchall():
            if status:
                values.add(status)
    except Exception:
        logger.warning("grounding: DB DISTINCT scan failed (non-fatal)", exc_info=True)

    ordered = sorted(values)
    _allowed_status_cache = ordered
    return ordered


# ── extract_db_constraints ─────────────────────────────────────────────────────


async def extract_db_constraints(
    session: AsyncSession,
    table_names: list[str],
) -> list[dict[str, Any]]:
    """Return columns + constraints for the requested tables.

    Per-table result is cached per-process.  Results include:
    - columns: name, type, nullable, column_default
    - constraints: name, type (CHECK/FOREIGN KEY/UNIQUE/PRIMARY KEY), definition

    Both queries target information_schema and pg_constraint so no schema changes
    are needed.
    """
    results: list[dict[str, Any]] = []
    uncached = [t for t in table_names if t not in _db_constraints_cache]

    if uncached:
        # Column info.
        col_sql = text(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = ANY(CAST(:tables AS TEXT[]))
              AND table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )
        col_rows = await session.execute(col_sql, {"tables": uncached})
        cols_by_table: dict[str, list[dict[str, Any]]] = {}
        for tbl, col, dtype, nullable, default in col_rows.fetchall():
            cols_by_table.setdefault(tbl, []).append(
                {
                    "name": col,
                    "type": dtype,
                    "nullable": nullable == "YES",
                    "default": default,
                }
            )

        # Constraint info (CHECK, FK, UNIQUE, PRIMARY KEY).
        con_sql = text(
            """
            SELECT
                tc.table_name,
                tc.constraint_name,
                tc.constraint_type,
                cc.check_clause
            FROM information_schema.table_constraints tc
            LEFT JOIN information_schema.check_constraints cc
                ON tc.constraint_name = cc.constraint_name
                AND tc.constraint_schema = cc.constraint_schema
            WHERE tc.table_name = ANY(CAST(:tables AS TEXT[]))
              AND tc.table_schema = 'public'
            ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name
            """
        )
        con_rows = await session.execute(con_sql, {"tables": uncached})
        cons_by_table: dict[str, list[dict[str, Any]]] = {}
        for tbl, cname, ctype, clause in con_rows.fetchall():
            cons_by_table.setdefault(tbl, []).append(
                {
                    "name": cname,
                    "type": ctype,
                    "definition": clause,
                }
            )

        for tbl in uncached:
            _db_constraints_cache[tbl] = {
                "table": tbl,
                "columns": cols_by_table.get(tbl, []),
                "constraints": cons_by_table.get(tbl, []),
            }

    for tbl in table_names:
        entry = _db_constraints_cache.get(tbl)
        if entry is not None:
            results.append(entry)
        else:
            results.append({"table": tbl, "columns": [], "constraints": [], "error": "not found"})

    return results


# ── extract_tool_registry ──────────────────────────────────────────────────────


async def extract_tool_registry(session: AsyncSession) -> dict[str, Any]:
    """Return all registered tool names + schemas, plus skills table rows.

    Sources:
    1. artemis.tools.registry.known_tool_names() — in-process registry.
    2. All rows from the ``skills`` table.

    Cached per-process (tool registry never changes within a process lifetime;
    skills table is unlikely to change during a Builder session).
    """
    global _tool_registry_cache
    if _tool_registry_cache is None:
        import artemis.tools  # noqa: F401 — ensure all tool factories are registered
        from artemis.tools.registry import get_factory, known_tool_names

        tool_entries: list[dict[str, Any]] = []
        for name in known_tool_names():
            entry: dict[str, Any] = {"name": name}
            # Get description + schema from a dummy context factory call.
            try:
                factory = get_factory(name)
                if factory is not None:
                    # Build a minimal stub context — we only need the Tool definition,
                    # not the impl (which requires a real session + agent context).
                    from unittest.mock import MagicMock

                    stub_ctx = MagicMock()
                    stub_ctx.session = None
                    stub_ctx.agent_id = "__grounding_stub__"
                    stub_ctx.agent_db_id = 0
                    stub_ctx.agent_run_id = "__grounding_stub__"
                    stub_ctx.pipeline_run_id = None
                    tool_def, _impl = factory(stub_ctx)
                    entry["description"] = tool_def.description
                    entry["input_schema"] = tool_def.input_schema
            except Exception:
                logger.debug(
                    "grounding: could not introspect tool %r (non-fatal)", name, exc_info=True
                )
            tool_entries.append(entry)

        _tool_registry_cache = tool_entries

    # Fetch skills rows fresh each call (cheap; small table; avoids stale cache
    # on the first post-proposal call within the same session).
    skills_rows: list[dict[str, Any]] = []
    try:
        from sqlalchemy import select as sa_select

        from artemis.builders.models import Skill

        result = await session.execute(sa_select(Skill).order_by(Skill.slug))
        for skill in result.scalars().all():
            skills_rows.append(
                {
                    "slug": skill.slug,
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                    "status": skill.status,
                    "kind": skill.kind,
                }
            )
    except Exception:
        logger.warning("grounding: skills table query failed (non-fatal)", exc_info=True)

    return {
        "registered_tools": _tool_registry_cache,
        "skills": skills_rows,
    }
