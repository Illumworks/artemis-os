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
        # An "ai_draft" summary was written by a model and NOT reviewed by
        # Enablement. Kai must caveat it rather than state it as catalog fact --
        # otherwise AI-drafted speed just recreates the 2026-08-10 problem of
        # confident unverified claims.
        "summary_status": asset.summary_status,
        "format": asset.format,
        "grade_range": asset.grade_range,
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


def _score_enablement_(assets: list[Any], query: str) -> list[tuple[Any, int]]:
    """Return (asset, relevance_score) pairs in ranked order.

    Sara, 2026-06-19: "Why did you choose option 1 and 2 over option 3?" Kai had
    not ranked at all — it listed in arbitrary order, and the numbering implied a
    preference that did not exist. Exposing the score lets Kai either give a real
    reason for the order or say plainly that the list is unordered.
    """
    ranked = _rerank_enablement_(assets, query)
    scores = _relevance_scores(ranked, query)
    return list(zip(ranked, scores, strict=True))


def _relevance_scores(assets: list[Any], query: str) -> list[int]:
    """Score each asset against the query. Higher is a better match.

    Extracted from _rerank_enablement_ so the same numbers can both order the
    results AND be surfaced to Kai, who has to justify the order he presents.
    Returns all-zero when the query has no usable terms, which is exactly the
    case where the list is genuinely unordered.
    """
    import re as _re

    q = (query or "").lower().strip()
    terms = [t for t in _re.split(r"[^a-z0-9]+", q) if len(t) >= 3]
    if not terms:
        return [0] * len(assets)

    # Format intent: when the asker names a format ("video", "deck"), prefer assets
    # of that actual type over a same-words asset of the wrong type (Sara asked for a
    # video; a "Student Prep Video" teacher_resource should not beat a student_video).
    _format_types: dict[str, tuple[str, ...]] = {
        "video": ("student_video", "video"),
        "deck": ("training_deck",),
        "slides": ("training_deck",),
        "presentation": ("training_deck",),
        "walkthrough": ("walkthrough",),
        "demo": ("walkthrough",),
        "handout": ("handout",),
    }
    wanted_types: set[str] = set()
    for word, types in _format_types.items():
        if word in q:
            wanted_types.update(types)

    def _score(a: Any) -> int:
        title = (getattr(a, "title", "") or "").lower()
        tags = " ".join(getattr(a, "tags", None) or []).lower()
        name = (getattr(a, "asset_name", "") or "").lower()
        hay = f"{title} {tags} {name}"
        phrase = 3 if (q and q in hay) else 0  # whole query appears
        title_hits = sum(1 for t in terms if t in title)  # title matches weighted
        any_hits = sum(1 for t in terms if t in hay)
        type_hit = 3 if (getattr(a, "type", "") or "") in wanted_types else 0
        return phrase * 4 + title_hits * 2 + any_hits + type_hit

    return [_score(a) for a in assets]


def _rerank_enablement_(assets: list[Any], query: str) -> list[Any]:
    """Hybrid re-rank: boost assets whose TITLE / tags / name contain the query's
    terms, so an exact-named asset (e.g. "Instruct-Core Coherent") wins over a
    semantically-close cousin (e.g. "Assess") that the embedding scored marginally
    higher. Stable: ties preserve the incoming order (vector distance / recency).
    Added 2026-06-20 — vector-only ranking surfaced the wrong deck for Sara."""
    scores = _relevance_scores(assets, query)
    if not any(scores):
        return assets
    # sorted() is stable, so equal scores keep vector-distance / recency order.
    return [a for a, _ in sorted(zip(assets, scores, strict=True), key=lambda p: -p[1])]


async def _search_enablement_assets(inp: dict[str, Any]) -> str:
    """Vector + keyword search over enablement_assets with optional facet filters."""
    query = str(inp.get("query", "")).strip()
    if not query:
        return "Error: query is required"
    limit = int(inp.get("limit", 5))
    limit = max(1, min(limit, 20))  # clamp: 1–20
    candidate_k = max(limit * 4, 20)  # wider pool so the hybrid re-rank has room to work

    # Translate "suite + function" phrasing into the product name the asset is filed
    # under (e.g. "Lectura ILP" -> + "Enseñar"), so retrieval connects regardless of
    # how the user phrased it. No-op unless a suite AND a function are both named.
    from artemis.enablement.product_taxonomy import expand_domain_terms, expand_query

    # Two expansions, and they answer different questions. expand_query resolves
    # "suite + function" into a product name. expand_domain_terms bridges the gap
    # between how people ask and how the library is filed -- "rostering" appears
    # in none of the 416 assets, so nothing finds it without this.
    search_text = expand_query(query)
    _domain = expand_domain_terms(query)
    if _domain:
        search_text = f"{search_text} {' '.join(_domain)}"

    # Optional structured filters.
    audience_filter: str | None = inp.get("audience")
    asset_type_filter: str | None = inp.get("asset_type")
    tags_filter: list[str] | None = inp.get("tags")
    # F6.2: Sara asked for a Google Slides deck and got a PDF, because format
    # was never captured. Grade range answers "K-8 or PK-8?".
    format_filter: str | None = inp.get("format")
    grade_range_filter: str | None = inp.get("grade_range")

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
            if format_filter:
                stmt = stmt.where(
                    func.lower(EnablementAsset.format) == format_filter.strip().lower()
                )
            if grade_range_filter:
                stmt = stmt.where(
                    func.upper(EnablementAsset.grade_range) == grade_range_filter.strip().upper()
                )
            return stmt

        # Attempt vector search first; fall back to keyword if embeddings unavailable.
        embedding: list[float] | None = None
        try:
            from artemis.memory.embeddings import MiniLMProvider

            provider = MiniLMProvider()
            embedding = await provider.embed(search_text)
        except Exception as embed_exc:  # noqa: BLE001
            _logger.debug("search_enablement_assets: embedding unavailable (%r)", embed_exc)

        async with _db.SessionLocal() as session:
            # Retrieval is a UNION of vector and keyword, not a fallback chain.
            #
            # Until 2026-09-04 keyword ran ONLY when vector returned nothing, so a
            # document whose title literally contained the asker's word could never
            # enter the pool as long as the vector search returned anything at all.
            # Sara asked for "customer facing tech requirement documents for
            # rostering" and got the Tech Care Family Letter -- a parent PDF --
            # while "Amira Technical Guide", "Tech Prep Guide" and the Clever /
            # ClassLink rostering walkthroughs sat unretrieved.
            #
            # 69% of the library has no summary, so those assets embed on title
            # alone and compete badly on a compound question. Keyword is exactly
            # what rescues them, which is why it now always runs.
            #
            # The re-ranker below is unchanged: this only widens what it can see.
            pool: dict[Any, Any] = {}

            def _absorb(found: list[Any]) -> None:
                for a in found:
                    pool.setdefault(a.id, a)

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
                    .limit(candidate_k)
                )
                stmt = _apply_filters(stmt)
                result = await session.execute(stmt)
                _absorb(list(result.scalars().all()))

            # Keyword pass, ALWAYS. Matches significant TERMS rather than the whole
            # query as one substring -- "%customer facing tech requirement documents
            # for rostering%" matches nothing, ever, which made the old fallback
            # useless for exactly the natural-language questions people ask.
            import re as _re

            terms = [
                t for t in _re.split(r"[^a-z0-9]+", (search_text or "").lower()) if len(t) >= 3
            ][:8]
            if terms:
                term_clauses = []
                for term in terms:
                    like = f"%{term}%"
                    term_clauses.extend(
                        [
                            func.lower(EnablementAsset.title).like(like),
                            func.lower(EnablementAsset.summary).like(like),
                            func.lower(EnablementAsset.asset_name).like(like),
                            func.lower(EnablementAsset.searchable_text).like(like),
                        ]
                    )
                stmt = (
                    select(EnablementAsset)
                    .where(
                        or_(
                            EnablementAsset.source_scope == "enablement",
                            EnablementAsset.source_scope == "shared",
                        )
                    )
                    .where(EnablementAsset.status.is_distinct_from("archived"))
                    .where(or_(*term_clauses))
                    .order_by(EnablementAsset.updated_at.desc())
                    .limit(candidate_k)
                )
                stmt = _apply_filters(stmt)
                result = await session.execute(stmt)
                _absorb(list(result.scalars().all()))

            assets = list(pool.values())

        # Hybrid re-rank (keyword/title boost over the vector pool), then trim.
        # Use the expanded text so the product-name boost (e.g. "Enseñar") applies.
        scored = _score_enablement_(assets, search_text)[:limit]

        if not scored:
            return json.dumps({"results": [], "count": 0, "query": query})

        # Answer-shape honesty (Sara, 2026-06-19): the position of a result must
        # never imply a preference that was not computed. When every candidate
        # scores the same, the list IS arbitrary and Kai has to say so.
        distinct = {score for _, score in scored}
        ordering = "ranked_by_relevance" if len(distinct) > 1 else "unordered_tied"

        results: list[dict[str, Any]] = []
        for position, (asset, score) in enumerate(scored, start=1):
            record = _asset_to_dict(asset)
            record["rank"] = position
            record["relevance"] = score
            results.append(record)

        return json.dumps(
            {
                "results": results,
                "count": len(results),
                "query": query,
                "ordering": ordering,
                "ordering_note": (
                    "Ranked by relevance to the query (title, tags, name, and "
                    "format match). Position 1 is the strongest match, and you "
                    "can say why in one line."
                    if ordering == "ranked_by_relevance"
                    else "These all scored EQUALLY. The order is arbitrary. Do "
                    "not present one as the best match or imply a preference "
                    "you did not compute. Say the list is unordered, or ask one "
                    "narrowing question."
                ),
            },
            default=str,
        )
    except Exception as exc:
        _logger.exception("search_enablement_assets failed")
        return f"search_enablement_assets failed: {exc}"


def _looks_like_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _normalize_url(value: str) -> str:
    """Canonical form for comparing two URLs.

    Lowercases the scheme+host, drops a trailing slash, and strips the query
    string and fragment (Drive and HubSpot links routinely carry ?usp=sharing,
    #page=2, tracking params). Deliberately conservative: it never rewrites the
    path, so two genuinely different documents cannot collapse into one.
    """
    trimmed = value.strip()
    for separator in ("?", "#"):
        if separator in trimmed:
            trimmed = trimmed.split(separator, 1)[0]
    trimmed = trimmed.rstrip("/")
    # Scheme and host are case-insensitive; the path is not.
    if "://" in trimmed:
        scheme, rest = trimmed.split("://", 1)
        host, _, path = rest.partition("/")
        return f"{scheme.lower()}://{host.lower()}" + (f"/{path}" if path else "")
    return trimmed


def _match_link_in_asset(asset: Any, target: str) -> dict[str, Any] | None:
    """Return the asset's own link record matching ``target``, if any."""
    wanted = _normalize_url(target)
    if asset.drive_link and _normalize_url(str(asset.drive_link)) == wanted:
        return {
            "url": asset.drive_link,
            "label": "Default catalog link",
            "role": "drive_link",
            "visibility": "customer",
            "on_request": False,
            "make_copy": bool(asset.requires_copy),
        }
    for link in asset.links or []:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if url and _normalize_url(str(url)) == wanted:
            return dict(link)
    return None


async def _get_enablement_asset(inp: dict[str, Any]) -> str:
    """Single asset lookup by drive_file_id, name, title, or URL.

    The URL path is the "is this customer-facing?" capability Sara asked for on
    2026-07-20 by pasting a Drive link. Matching is against drive_link AND every
    entry in the links JSONB, so a link pasted from any surface resolves. The
    matched link's own ``visibility`` flag answers the safe-to-send question,
    rather than anything inferred from the URL.
    """
    identifier = str(inp.get("drive_file_id_or_name", "")).strip()
    if not identifier:
        return "Error: drive_file_id_or_name is required"

    is_url = _looks_like_url(identifier)

    try:
        from sqlalchemy import or_, select
        from sqlalchemy import text as sa_text

        import artemis.db as _db
        from artemis.enablement.models import EnablementAsset

        scope_filter = or_(
            EnablementAsset.source_scope == "enablement",
            EnablementAsset.source_scope == "shared",
        )

        async with _db.SessionLocal() as session:
            if is_url:
                # Narrow to rows whose drive_link or links JSONB mentions the
                # URL's distinctive path, then confirm in Python with the
                # normalizer (SQL LIKE cannot do query-string-insensitive
                # comparison safely).
                probe = _normalize_url(identifier)
                like_probe = f"%{probe.split('://', 1)[-1]}%"
                stmt = (
                    select(EnablementAsset)
                    .where(scope_filter)
                    .where(
                        or_(
                            EnablementAsset.drive_link.ilike(like_probe),
                            sa_text("CAST(links AS text) ILIKE :probe").bindparams(
                                probe=like_probe
                            ),
                        )
                    )
                    .limit(25)
                )
                candidates = list((await session.execute(stmt)).scalars().all())
                asset = None
                matched_link: dict[str, Any] | None = None
                for candidate in candidates:
                    matched_link = _match_link_in_asset(candidate, identifier)
                    if matched_link is not None:
                        asset = candidate
                        break
            else:
                stmt = (
                    select(EnablementAsset)
                    .where(scope_filter)
                    .where(
                        or_(
                            EnablementAsset.drive_file_id == identifier,
                            EnablementAsset.asset_name == identifier,
                            EnablementAsset.title == identifier,
                        )
                    )
                    .limit(1)
                )
                asset = (await session.execute(stmt)).scalar_one_or_none()
                matched_link = None

        if asset is None:
            payload: dict[str, Any] = {"found": False, "identifier": identifier}
            if is_url:
                payload["lookup_type"] = "url"
                payload["verdict"] = "not_a_catalog_asset"
                payload["guidance"] = (
                    "This URL does not match any link in the catalog. Say exactly "
                    "that and stop. You cannot tell whether it is safe to send, "
                    "because you have no record for it. Do NOT speculate about why "
                    "it is missing, and do not infer anything from the URL itself. "
                    "If they want it added, Sara and Missy own the catalog."
                )
            return json.dumps(payload)

        record = _asset_to_dict(asset)
        if not is_url:
            return json.dumps({"found": True, "asset": record}, default=str)

        visibility = str((matched_link or {}).get("visibility", "")).lower()
        is_archived = str(asset.status or "").lower() == "archived"
        customer_facing = visibility == "customer" and not is_archived

        if is_archived:
            verdict = "archived_do_not_send"
        elif visibility == "customer":
            verdict = "customer_facing"
        elif visibility == "internal":
            verdict = "internal_only"
        else:
            verdict = "unknown_visibility"

        return json.dumps(
            {
                "found": True,
                "lookup_type": "url",
                "verdict": verdict,
                "customer_facing": customer_facing,
                "matched_link": matched_link,
                "status": asset.status,
                "asset": record,
                "guidance": (
                    "Report the verdict from `verdict` and `matched_link.visibility` "
                    "only. 'customer_facing' means this exact link is the "
                    "customer-facing one and is safe to send. 'internal_only' means "
                    "do NOT send it to a customer; point them at the customer link "
                    "on this asset instead. 'archived_do_not_send' means the record "
                    "is archived. 'unknown_visibility' means the record does not say, "
                    "so tell them it needs verification rather than guessing."
                ),
            },
            default=str,
        )
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
        "Each result carries `rank` and `relevance`, and the response carries `ordering`: "
        "'ranked_by_relevance' means position 1 really is the strongest match and you should say "
        "why in one line; 'unordered_tied' means every result scored the same and the order is "
        "ARBITRARY, so you must not imply a preference you did not compute. "
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
            "format": {
                "type": "string",
                "description": (
                    "Filter on the actual file format. One of: google_slides, "
                    "google_doc, pdf, video, google_sheet, web_page, form, "
                    "demo_account, other. USE THIS when someone asks for a "
                    "specific format ('a deck, not a PDF', 'the slides', 'a "
                    "video'). Not every asset has it recorded yet, so an empty "
                    "result may mean 'not captured' rather than 'does not exist' "
                    "- retry without the filter before concluding anything."
                ),
            },
            "grade_range": {
                "type": "string",
                "description": (
                    "Filter on grade band: PK-2, K-2, PK-5, K-5, K-8, PK-8, 3-5, "
                    "6-8, 9-12, K-12. Most assets have no grade range recorded, "
                    "so use this only when the person asks about grades, and "
                    "retry without it if nothing comes back."
                ),
            },
        },
        "required": ["query"],
    },
)

GET_ENABLEMENT_ASSET = Tool(
    name="get_enablement_asset",
    description=(
        "Look up a single enablement asset by its Google Drive file ID, asset name, title, "
        "OR a URL. Returns the full asset record including Drive link, summary, confidence "
        "label, audience, and transcript link. Returns found=false when no match exists. "
        "USE THIS WHENEVER SOMEONE PASTES A LINK AND ASKS WHAT IT IS, WHETHER IT IS CURRENT, "
        "OR WHETHER IT IS SAFE TO SEND A CUSTOMER. Given a URL it matches against the default "
        "drive_link and every entry in the asset's `links`, and adds `verdict` "
        "('customer_facing' = this exact link is safe to send, 'internal_only' = do NOT send "
        "it to a customer, 'archived_do_not_send', 'unknown_visibility' = the record does not "
        "say, so it needs verification, 'not_a_catalog_asset' = no match). Report the verdict "
        "the tool returns. Never infer safety from what the URL looks like, and when there is "
        "no match say exactly that and stop rather than speculating about why. "
        f"{_SURFACE_TAG} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "drive_file_id_or_name": {
                "type": "string",
                "description": (
                    "The Drive file ID, asset_name, title, or a full URL "
                    "(https://...) that someone pasted. Exact match only for "
                    "ids/names — use search_enablement_assets for fuzzy lookup. "
                    "URL matching ignores query strings and trailing slashes."
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
