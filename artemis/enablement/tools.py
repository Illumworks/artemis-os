"""Enablement retrieval tools for Kai (Chiron) — read-only, Layer 1.

Exposes:
  - search_enablement_assets: semantic + keyword search over enablement_assets
  - get_enablement_asset: single asset lookup by drive_file_id or title

Both tools are READ-ONLY and carry no side effects.  They are gated on
agent_id == "kai" in tool_registry.py.

source_scope access policy (enforced here):
  "enablement"  — always returned (Kai's primary scope)
  "shared"      — always returned (cross-team shared content Kai is allowed to surface)
  Any other scope — not surfaced by these tools; the enablement_assets table
                    contains only enablement/shared rows by design.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE_TAG = "[surface:enablement]"


# ── Serialization helpers ────────────────────────────────────────────────────


def _asset_to_dict(asset: Any) -> dict[str, Any]:
    """Serialize an EnablementAsset ORM row to a plain dict for tool output.

    ``links`` is the surfacing-critical field: each entry carries
    visibility ("customer"/"internal"), on_request, and make_copy flags that
    Kai's persona rules act on (customer-default, editable/internal on explicit
    request only, make-a-copy reminder). ``drive_link`` is the default
    customer-facing link for backward compatibility.
    """
    return {
        "asset_name": asset.asset_name,
        "title": asset.title,
        "summary": asset.summary,
        "drive_link": asset.drive_link,
        "links": asset.links or [],
        "requires_copy": bool(asset.requires_copy),
        "type": asset.type,
        "confidence_label": asset.confidence_label,
        "audience": asset.audience,
        "tags": asset.tags or [],
        "transcript_link": asset.transcript_link,
        "status": asset.status,
        "source_scope": asset.source_scope,
        "source_sheet": asset.source_sheet,
        "drive_file_id": asset.drive_file_id,
    }


# ── Implementations ───────────────────────────────────────────────────────────


async def _search_enablement_assets(inp: dict[str, Any]) -> str:
    """Vector + keyword search over enablement_assets."""
    query = str(inp.get("query", "")).strip()
    if not query:
        return "Error: query is required"
    limit = int(inp.get("limit", 5))
    limit = max(1, min(limit, 20))  # clamp: 1–20

    try:
        from sqlalchemy import or_, select

        import artemis.db as _db
        from artemis.enablement.models import EnablementAsset

        # Attempt vector search first; fall back to keyword if embeddings unavailable.
        embedding: list[float] | None = None
        try:
            from artemis.memory.embeddings import MiniLMProvider

            provider = MiniLMProvider()
            embedding = await provider.embed(query)
        except Exception as embed_exc:  # noqa: BLE001
            _logger.debug("search_enablement_assets: embedding unavailable (%r)", embed_exc)

        async with _db.SessionLocal() as session:
            if embedding is not None:
                # Cosine similarity via pgvector (<=> operator = cosine distance; order ASC).
                # Filter to enablement + shared scopes only.
                from sqlalchemy import text as sa_text

                stmt = (
                    select(EnablementAsset)
                    .where(
                        or_(
                            EnablementAsset.source_scope == "enablement",
                            EnablementAsset.source_scope == "shared",
                        )
                    )
                    .where(EnablementAsset.status.is_distinct_from("archived"))
                    .where(EnablementAsset.embedding.isnot(None))
                    .order_by(
                        sa_text("embedding <=> CAST(:vec AS vector)").bindparams(vec=str(embedding))
                    )
                    .limit(limit)
                )
                result = await session.execute(stmt)
                assets = list(result.scalars().all())

                # If vector search returned nothing, fall back to keyword.
                if not assets:
                    embedding = None  # trigger keyword path below

            if embedding is None:
                # Keyword search: case-insensitive substring match on title, summary, tags.
                q_lower = f"%{query.lower()}%"
                from sqlalchemy import func

                stmt = (
                    select(EnablementAsset)
                    .where(
                        or_(
                            EnablementAsset.source_scope == "enablement",
                            EnablementAsset.source_scope == "shared",
                        )
                    )
                    .where(EnablementAsset.status.is_distinct_from("archived"))
                    .where(
                        or_(
                            func.lower(EnablementAsset.title).like(q_lower),
                            func.lower(EnablementAsset.summary).like(q_lower),
                            func.lower(EnablementAsset.asset_name).like(q_lower),
                            func.lower(EnablementAsset.searchable_text).like(q_lower),
                        )
                    )
                    .order_by(EnablementAsset.updated_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                assets = list(result.scalars().all())

        if not assets:
            return json.dumps({"results": [], "count": 0, "query": query})

        return json.dumps(
            {
                "results": [_asset_to_dict(a) for a in assets],
                "count": len(assets),
                "query": query,
            },
            default=str,
        )
    except Exception as exc:
        _logger.exception("search_enablement_assets failed")
        return f"search_enablement_assets failed: {exc}"


async def _get_enablement_asset(inp: dict[str, Any]) -> str:
    """Single asset lookup by drive_file_id or title."""
    identifier = str(inp.get("drive_file_id_or_name", "")).strip()
    if not identifier:
        return "Error: drive_file_id_or_name is required"

    try:
        from sqlalchemy import or_, select

        import artemis.db as _db
        from artemis.enablement.models import EnablementAsset

        async with _db.SessionLocal() as session:
            stmt = (
                select(EnablementAsset)
                .where(
                    or_(
                        EnablementAsset.source_scope == "enablement",
                        EnablementAsset.source_scope == "shared",
                    )
                )
                .where(
                    or_(
                        EnablementAsset.drive_file_id == identifier,
                        EnablementAsset.asset_name == identifier,
                        EnablementAsset.title == identifier,
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            asset = result.scalar_one_or_none()

        if asset is None:
            return json.dumps({"found": False, "identifier": identifier})

        return json.dumps({"found": True, "asset": _asset_to_dict(asset)}, default=str)
    except Exception as exc:
        _logger.exception("get_enablement_asset failed")
        return f"get_enablement_asset failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────

SEARCH_ENABLEMENT_ASSETS = Tool(
    name="search_enablement_assets",
    description=(
        "Search the enablement asset catalog by semantic similarity and keyword match. "
        "Returns top matching assets, each with title, summary, type, audience, tags, and a "
        "`links` array. Each link has: role, label, url, `visibility` ('customer' or 'internal'), "
        "`on_request` (only mention if the user explicitly asks for it, e.g. the editable version), "
        "and `make_copy` (view-only; remind the user to make a copy). `requires_copy` flags "
        "copy-first assets. Default to surfacing the customer-visible, non-on_request links. "
        "Use to find decks, handouts, one-pagers, videos, walkthroughs, or any enablement collateral. "
        "Empty store or no match returns an empty results list. "
        f"{_SURFACE_TAG} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language search query describing the asset you need "
                    "(e.g. 'latest one-pager for district CFOs', 'product demo video 2026')."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1–20, default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)

GET_ENABLEMENT_ASSET = Tool(
    name="get_enablement_asset",
    description=(
        "Look up a single enablement asset by its Google Drive file ID, asset name, or title. "
        "Returns the full asset record including Drive link, summary, confidence label, "
        "audience, and transcript link. Returns found=false when no match exists. "
        f"{_SURFACE_TAG} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "drive_file_id_or_name": {
                "type": "string",
                "description": (
                    "The Drive file ID, asset_name, or title of the asset to retrieve. "
                    "Exact match only — use search_enablement_assets for fuzzy lookup."
                ),
            },
        },
        "required": ["drive_file_id_or_name"],
    },
)


# ── Registration ──────────────────────────────────────────────────────────────


def register_enablement_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Kai's read-only enablement retrieval tools into the provided registry.

    Both tools are Layer 1 (read-only, no confirmation required).
    Call this ONLY for agent_id="kai" — the tool_registry.py gate enforces this.
    """
    registry.register(SEARCH_ENABLEMENT_ASSETS, _search_enablement_assets, layer=1)
    registry.register(GET_ENABLEMENT_ASSET, _get_enablement_asset, layer=1)
