"""Daily brief router — /api/daily-brief.

Endpoints:
  GET  /api/daily-brief          — latest persisted snapshot (instant, no LLM)
  POST /api/daily-brief/generate — trigger new generation
  GET  /api/daily-brief/history  — last 7 snapshot metadata rows
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.brief import repository
from artemis.brief.generator import BriefGenerationError, generate_brief
from artemis.marketing.routes._auth import require_owner, require_token

router = APIRouter(
    prefix="/api/daily-brief",
    tags=["daily-brief"],
    dependencies=[Depends(require_token), Depends(require_owner)],
)


def _hydrate_brief_from_snapshot(snapshot: Any) -> dict[str, Any]:
    brief = dict(snapshot.brief_json) if isinstance(snapshot.brief_json, dict) else {}
    brief["_snapshotId"] = snapshot.id
    brief["_generatedAt"] = snapshot.generated_at.isoformat()
    brief["_tokensInput"] = snapshot.tokens_input
    brief["_tokensOutput"] = snapshot.tokens_output
    return brief


@router.get("")
async def get_brief(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return latest persisted snapshot. Instant, no LLM call."""
    snapshot = await repository.get_latest_brief_snapshot(session)
    if snapshot is None:
        return {"brief": None, "exists": False}
    return {"brief": _hydrate_brief_from_snapshot(snapshot), "exists": True}


@router.post("/generate")
async def generate_brief_endpoint(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Trigger a new generation. Returns the new brief."""
    try:
        brief = await generate_brief(session)
        return {"brief": brief, "generated": True}
    except BriefGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "brief_generation_failed", "detail": str(exc)},
        ) from exc


@router.get("/history")
async def get_history(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Metadata for last 7 snapshots — no full content."""
    rows = await repository.list_brief_snapshots(session, limit=7)
    return {
        "history": [
            {
                "id": r.id,
                "generated_at": r.generated_at.isoformat(),
                "model": r.model,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
            }
            for r in rows
        ]
    }
