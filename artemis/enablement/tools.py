"""Enablement retrieval tools for Kai (Chiron) — read-only, Layer 1.

Exposes:
  - search_enablement_assets: semantic + keyword search over enablement_assets
  - get_enablement_asset: single asset lookup by drive_file_id or title
  - list_enablement_facets: facet vocabulary (audiences, types, top tags with counts)

All tools are READ-ONLY and carry no side effects.  They are gated on
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
    """Vector + keyword search over enablement_assets with optional facet filters."""
    query = str(inp.get("query", "")).strip()
    if not query:
        return "Error: query is required"
    limit = int(inp.get("limit", 5))
    limit = max(1, min(limit, 20))  # clamp: 1–20

    # Optional structured filters.
    audience_filter: str | None = inp.get("audience")
    asset_type_filter: str | None = inp.get("asset_type")
    tags_filter: list[str] | None = inp.get("tags")

    try:
        from sqlalchemy import func, or_, select

        import artemis.db as _db
        from artemis.enablement.models import EnablementAsset

        def _apply_filters(stmt: Any) -> Any:
            """AND the optional facet filters onto any SELECT statement."""
            if audience_filter:
                stmt = stmt.where(func.lower(EnablementAsset.audience) == audience_filter.lower())
            if asset_type_filter:
                stmt = stmt.where(EnablementAsset.type == asset_type_filter)
            if tags_filter:
                # Row's tags array must contain ALL provided values (@> in Postgres).
                stmt = stmt.where(EnablementAsset.tags.contains(tags_filter))
            return stmt

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
                stmt = _apply_filters(stmt)
                result = await session.execute(stmt)
                assets = list(result.scalars().all())

                # If vector search returned nothing, fall back to keyword.
                if not assets:
                    embedding = None  # trigger keyword path below

            if embedding is None:
                # Keyword search: case-insensitive substring match on title, summary, tags.
                q_lower = f"%{query.lower()}%"

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
                stmt = _apply_filters(stmt)
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


async def _list_enablement_facets(inp: dict[str, Any]) -> str:
    """Return facet vocabulary: distinct audiences, types, and top tags with counts.

    Excludes archived rows and restricts to enablement/shared source_scope.
    This lets Kai ask precise narrowing questions and answer "what do you have?"
    without guessing at valid filter values.
    """
    tags_limit = int(inp.get("limit", 40))
    tags_limit = max(1, min(tags_limit, 200))  # clamp: 1–200

    try:
        from sqlalchemy import Integer, cast, func, or_, select
        from sqlalchemy import text as sa_text

        import artemis.db as _db
        from artemis.enablement.models import EnablementAsset

        # Common scope + archived filter applied to all three sub-queries.
        _scope_filter = or_(
            EnablementAsset.source_scope == "enablement",
            EnablementAsset.source_scope == "shared",
        )
        _active_filter = EnablementAsset.status.is_distinct_from("archived")

        async with _db.SessionLocal() as session:
            # 1. Distinct audience values with counts.
            aud_stmt = (
                select(
                    EnablementAsset.audience,
                    cast(func.count(), Integer).label("count"),
                )
                .where(_scope_filter)
                .where(_active_filter)
                .where(EnablementAsset.audience.isnot(None))
                .group_by(EnablementAsset.audience)
                .order_by(func.count().desc())
            )
            aud_rows = (await session.execute(aud_stmt)).all()

            # 2. Distinct type (asset_type) values with counts.
            type_stmt = (
                select(
                    EnablementAsset.type,
                    cast(func.count(), Integer).label("count"),
                )
                .where(_scope_filter)
                .where(_active_filter)
                .where(EnablementAsset.type.isnot(None))
                .group_by(EnablementAsset.type)
                .order_by(func.count().desc())
            )
            type_rows = (await session.execute(type_stmt)).all()

            # 3. Top N tags by frequency via unnest(tags) + GROUP BY.
            #    Uses a raw text() fragment for the unnest lateral join; the
            #    scope/archived filter is applied in the WHERE clause via a
            #    correlated subquery approach (unnest in FROM is cleaner).
            tag_stmt = sa_text(
                """
                SELECT tag, CAST(COUNT(*) AS INTEGER) AS cnt
                FROM enablement_assets,
                     LATERAL unnest(tags) AS t(tag)
                WHERE status IS DISTINCT FROM 'archived'
                  AND source_scope IN ('enablement', 'shared')
                  AND tags IS NOT NULL
                GROUP BY tag
                ORDER BY cnt DESC
                LIMIT :lim
                """
            ).bindparams(lim=tags_limit)
            tag_rows = (await session.execute(tag_stmt)).all()

        return json.dumps(
            {
                "audiences": [{"audience": row.audience, "count": row.count} for row in aud_rows],
                "types": [{"asset_type": row.type, "count": row.count} for row in type_rows],
                "tags": [{"tag": row.tag, "count": row.cnt} for row in tag_rows],
            },
            default=str,
        )
    except Exception as exc:
        _logger.exception("list_enablement_facets failed")
        return f"list_enablement_facets failed: {exc}"


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
        "Optional filters: `audience` (e.g. 'Teacher', 'Admin'), `asset_type` "
        "(e.g. 'training_deck', 'student_video', 'walkthrough', 'teacher_resource', 'doc', "
        "'demo_account'), and `tags` (array of values that must ALL be present — product names like "
        "'Assess'/'Instruct'/'Tutor', grade, language, persona, category, and micro-intervention "
        "facets live in tags; use list_enablement_facets to discover valid values). "
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
            "audience": {
                "type": "string",
                "description": (
                    "Case-insensitive filter on the audience column "
                    "(e.g. 'Teacher', 'Admin', 'District Leader'). "
                    "Use list_enablement_facets to see available values."
                ),
            },
            "asset_type": {
                "type": "string",
                "description": (
                    "Exact match filter on the asset type column "
                    "(e.g. 'training_deck', 'student_video', 'walkthrough', "
                    "'teacher_resource', 'doc', 'demo_account'). "
                    "Use list_enablement_facets to see available values."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Require ALL listed tag values to be present on the asset "
                    "(case-sensitive). Product names (Assess/Instruct/Tutor), grade, "
                    "language, persona, category, and micro-intervention facets are "
                    "stored as tags. Use list_enablement_facets to discover valid values."
                ),
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

LIST_ENABLEMENT_FACETS = Tool(
    name="list_enablement_facets",
    description=(
        "Return the facet vocabulary of the enablement asset catalog: distinct audience values, "
        "distinct asset_type values, and the most common tag values — each with counts. "
        "Use this BEFORE searching to discover valid filter values for audience, asset_type, "
        "and tags in search_enablement_assets. Also useful for answering 'what do you have?' "
        "questions without guessing. Excludes archived rows. "
        f"{_SURFACE_TAG} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of tag facets to return (1–200, default 40).",
                "default": 40,
            },
        },
        "required": [],
    },
)


# ── Registration ──────────────────────────────────────────────────────────────


def register_enablement_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Kai's read-only enablement retrieval tools into the provided registry.

    All three tools are Layer 1 (read-only, no confirmation required).
    Call this ONLY for agent_id="kai" — the tool_registry.py gate enforces this.
    """
    registry.register(SEARCH_ENABLEMENT_ASSETS, _search_enablement_assets, layer=1)
    registry.register(GET_ENABLEMENT_ASSET, _get_enablement_asset, layer=1)
    registry.register(LIST_ENABLEMENT_FACETS, _list_enablement_facets, layer=1)
