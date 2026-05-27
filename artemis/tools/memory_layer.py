"""Tools: memory_layer.upsert_last_seen, memory_layer.get, memory_layer.compute_similarity

Stubs — Memory-M2 table not yet designed (Q3 decision).
Returns placeholder strings and logs WARNING on each call.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF_UPSERT = Tool(
    name="memory_layer.upsert_last_seen",
    description=(
        "Record (district, reason_code, signal_id) for deduplication. "
        "STUB: Memory-M2 table not yet available. Returns 'ok-stub'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "districtId": {"type": "string"},
            "reasonCode": {"type": "string"},
            "signalId": {"type": "string"},
        },
    },
)

_DEF_GET = Tool(
    name="memory_layer.get",
    description=(
        "Retrieve last-seen records for a district/reason_code combination. "
        "STUB: Memory-M2 table not yet available. Returns empty list."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "districtId": {"type": "string"},
            "reasonCode": {"type": "string"},
        },
    },
)

_DEF_SIMILARITY = Tool(
    name="memory_layer.compute_similarity",
    description=(
        "Compute semantic similarity between two signals. "
        "STUB: Memory-M2 embeddings not yet available. Returns 0.0."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "signalIdA": {"type": "string"},
            "signalIdB": {"type": "string"},
        },
    },
)


def _factory_upsert(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "memory_layer.upsert_last_seen called (stub) by agent=%s — Memory-M2 not yet deployed",
            ctx.agent_id,
        )
        return "ok-stub"

    return (_DEF_UPSERT, _impl)


def _factory_get(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "memory_layer.get called (stub) by agent=%s — Memory-M2 not yet deployed",
            ctx.agent_id,
        )
        return "[]"

    return (_DEF_GET, _impl)


def _factory_similarity(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "memory_layer.compute_similarity called (stub) by agent=%s — Memory-M2 not yet deployed",
            ctx.agent_id,
        )
        return "0.0"

    return (_DEF_SIMILARITY, _impl)


register_tool("memory_layer.upsert_last_seen", _factory_upsert)
register_tool("memory_layer.get", _factory_get)
register_tool("memory_layer.compute_similarity", _factory_similarity)
