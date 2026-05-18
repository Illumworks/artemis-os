"""OKR Studio router — /api/okr.

Endpoints:
  GET    /api/okr/overview                     — aggregated overview for the shell

  GET    /api/okr/objectives                   — list objectives (with KRs)
  POST   /api/okr/objectives                   — create objective
  GET    /api/okr/objectives/{id}              — get objective + KRs
  PATCH  /api/okr/objectives/{id}              — update objective
  DELETE /api/okr/objectives/{id}              — delete objective

  GET    /api/okr/objectives/{id}/key-results  — list KRs for objective
  POST   /api/okr/objectives/{id}/key-results  — create KR
  PATCH  /api/okr/key-results/{id}             — update KR
  DELETE /api/okr/key-results/{id}             — delete KR

  GET    /api/okr/activity                     — list activity log
  POST   /api/okr/activity                     — add activity entry

  GET    /api/okr/next-up                      — list next-up items
  POST   /api/okr/next-up                      — create next-up item
  PATCH  /api/okr/next-up/{id}                 — update next-up item
  POST   /api/okr/next-up/{id}/dismiss         — dismiss next-up item
  DELETE /api/okr/next-up/{id}                 — delete next-up item
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.okr import repository as repo
from artemis.okr.models import OkrActivity
from artemis.okr.schemas import (
    OkrActivityCreate,
    OkrActivityRead,
    OkrKeyResultCreate,
    OkrKeyResultRead,
    OkrKeyResultUpdate,
    OkrNextUpCreate,
    OkrNextUpRead,
    OkrNextUpUpdate,
    OkrObjectiveCreate,
    OkrObjectiveRead,
    OkrObjectiveUpdate,
)

router = APIRouter(
    prefix="/api/okr",
    tags=["okr"],
    dependencies=[Depends(require_token)],
)


def _not_found(label: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": f"{label} not found", "code": "not_found"},
    )


def _current_quarter_label() -> str:
    """Return e.g. 'Q2 2026' derived from today's date."""
    now = datetime.now()
    quarter = (now.month - 1) // 3 + 1
    return f"Q{quarter} {now.year}"


# ── Overview ──────────────────────────────────────────────────────────────────


@router.get("/overview")
async def get_overview(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Aggregated overview for the OKR shell UI.

    Returns objectives (with krs), stats chips, recent activity, evidence
    placeholder, next-up items, and the current quarter label.
    """
    # --- 1. Objectives with eager-loaded key results (archived filtered out) ---
    objectives = await repo.list_objectives(session, include_archived=False)

    # --- 2. Evidence counts per KR (single aggregate query, no N+1) -----------
    count_rows = await session.execute(
        select(OkrActivity.kr_id, func.count(OkrActivity.id).label("cnt"))
        .where(OkrActivity.kr_id.is_not(None))
        .group_by(OkrActivity.kr_id)
    )
    evidence_counts: dict[int, int] = {row.kr_id: row.cnt for row in count_rows}

    # --- 3. Build objectives payload ------------------------------------------
    objectives_out: list[dict[str, Any]] = []
    total_krs = 0
    atrisk_count = 0

    for obj in objectives:
        krs_out: list[dict[str, Any]] = []
        for kr in obj.key_results:
            if kr.archived_at is not None:
                continue
            total_krs += 1
            if kr.status == "atrisk":
                atrisk_count += 1
            krs_out.append(
                {
                    "id": kr.id,
                    "title": kr.title,
                    "prog": kr.prog,
                    "status": kr.status,
                    "note": kr.note,
                    "evidence_count": evidence_counts.get(kr.id, 0),
                    "done_bullets": kr.done_bullets if kr.done_bullets else [],
                    "gaps_bullets": kr.gaps_bullets if kr.gaps_bullets else [],
                    "target_text": kr.target_text,
                }
            )
        objectives_out.append(
            {
                "id": obj.id,
                "title": obj.title,
                "progress": obj.progress,
                "tone": obj.tone,
                "cycle": obj.cycle,
                "krs": krs_out,
            }
        )

    # --- 4. Stats chips -------------------------------------------------------
    n_objectives = len(objectives_out)
    stats: list[dict[str, Any]] = [
        {
            "n": n_objectives,
            "suffix": "",
            "tone": "zero" if n_objectives == 0 else "sage",
            "label": "Objectives",
        },
        {
            "n": total_krs,
            "suffix": "",
            "tone": "zero" if total_krs == 0 else "sage",
            "label": "Key Results",
        },
        {
            "n": atrisk_count,
            "suffix": "",
            "tone": "zero" if atrisk_count == 0 else "warn",
            "label": "At risk",
        },
    ]

    # --- 5. Recent activity (newest-first, limit 10) --------------------------
    activity_rows = await repo.list_activity(session, limit=10)
    activity_out: list[dict[str, Any]] = [
        {
            "id": a.id,
            "text": a.text,
            "kr_id": a.kr_id,
            "kr_label": a.kr_label,
            "created_at": a.created_at.isoformat(),
        }
        for a in activity_rows
    ]

    # --- 6. Next-up (undismissed, ordered by sort_order) ----------------------
    next_up_rows = await repo.list_next_up(session, include_dismissed=False)
    next_up_out: list[dict[str, Any]] = [
        {
            "id": n.id,
            "ref": n.ref,
            "text": n.text,
            "prio": n.prio,
        }
        for n in next_up_rows
    ]

    return {
        "status": "ok",
        "objectives": objectives_out,
        "stats": stats,
        "activity": activity_out,
        "evidence": [],
        "nextUp": next_up_out,
        "quarter": {"label": _current_quarter_label()},
    }


# ── Objectives ────────────────────────────────────────────────────────────────


@router.get("/objectives", response_model=list[OkrObjectiveRead])
async def list_objectives(
    cycle: str | None = None,
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OkrObjectiveRead]:
    rows = await repo.list_objectives(session, cycle=cycle, include_archived=include_archived)
    return [OkrObjectiveRead.model_validate(r) for r in rows]


@router.post("/objectives", response_model=OkrObjectiveRead, status_code=201)
async def create_objective(
    body: OkrObjectiveCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrObjectiveRead:
    obj = await repo.create_objective(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(obj)
    return OkrObjectiveRead.model_validate(obj)


@router.get("/objectives/{objective_id}", response_model=OkrObjectiveRead)
async def get_objective(
    objective_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrObjectiveRead:
    obj = await repo.get_objective(session, objective_id)
    if obj is None:
        raise _not_found("Objective")
    return OkrObjectiveRead.model_validate(obj)


@router.patch("/objectives/{objective_id}", response_model=OkrObjectiveRead)
async def update_objective(
    objective_id: int,
    body: OkrObjectiveUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrObjectiveRead:
    obj = await repo.update_objective(
        session,
        objective_id,
        **body.model_dump(exclude_none=True),
    )
    if obj is None:
        raise _not_found("Objective")
    await session.commit()
    await session.refresh(obj)
    return OkrObjectiveRead.model_validate(obj)


@router.delete("/objectives/{objective_id}", status_code=204)
async def delete_objective(
    objective_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_objective(session, objective_id)
    if not deleted:
        raise _not_found("Objective")
    await session.commit()


# ── Key Results ───────────────────────────────────────────────────────────────


@router.get(
    "/objectives/{objective_id}/key-results",
    response_model=list[OkrKeyResultRead],
)
async def list_key_results(
    objective_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OkrKeyResultRead]:
    rows = await repo.list_key_results(session, objective_id)
    return [OkrKeyResultRead.model_validate(r) for r in rows]


@router.post(
    "/objectives/{objective_id}/key-results",
    response_model=OkrKeyResultRead,
    status_code=201,
)
async def create_key_result(
    objective_id: int,
    body: OkrKeyResultCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrKeyResultRead:
    data = body.model_dump(exclude_none=True)
    data["objective_id"] = objective_id
    kr = await repo.create_key_result(session, **data)
    await session.commit()
    await session.refresh(kr)
    return OkrKeyResultRead.model_validate(kr)


@router.patch("/key-results/{kr_id}", response_model=OkrKeyResultRead)
async def update_key_result(
    kr_id: int,
    body: OkrKeyResultUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrKeyResultRead:
    kr = await repo.update_key_result(session, kr_id, **body.model_dump(exclude_none=True))
    if kr is None:
        raise _not_found("Key result")
    await session.commit()
    await session.refresh(kr)
    return OkrKeyResultRead.model_validate(kr)


@router.delete("/key-results/{kr_id}", status_code=204)
async def delete_key_result(
    kr_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_key_result(session, kr_id)
    if not deleted:
        raise _not_found("Key result")
    await session.commit()


# ── Activity ──────────────────────────────────────────────────────────────────


@router.get("/activity", response_model=list[OkrActivityRead])
async def list_activity(
    kr_id: int | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OkrActivityRead]:
    rows = await repo.list_activity(session, kr_id=kr_id, limit=limit)
    return [OkrActivityRead.model_validate(r) for r in rows]


@router.post("/activity", response_model=OkrActivityRead, status_code=201)
async def create_activity(
    body: OkrActivityCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrActivityRead:
    act = await repo.create_activity(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(act)
    return OkrActivityRead.model_validate(act)


# ── Next Up ───────────────────────────────────────────────────────────────────


@router.get("/next-up", response_model=list[OkrNextUpRead])
async def list_next_up(
    include_dismissed: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[OkrNextUpRead]:
    rows = await repo.list_next_up(session, include_dismissed=include_dismissed)
    return [OkrNextUpRead.model_validate(r) for r in rows]


@router.post("/next-up", response_model=OkrNextUpRead, status_code=201)
async def create_next_up(
    body: OkrNextUpCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrNextUpRead:
    item = await repo.create_next_up(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(item)
    return OkrNextUpRead.model_validate(item)


@router.patch("/next-up/{item_id}", response_model=OkrNextUpRead)
async def update_next_up(
    item_id: int,
    body: OkrNextUpUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrNextUpRead:
    item = await repo.update_next_up(session, item_id, **body.model_dump(exclude_none=True))
    if item is None:
        raise _not_found("Next-up item")
    await session.commit()
    await session.refresh(item)
    return OkrNextUpRead.model_validate(item)


@router.post("/next-up/{item_id}/dismiss", response_model=OkrNextUpRead)
async def dismiss_next_up(
    item_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OkrNextUpRead:
    item = await repo.dismiss_next_up(session, item_id)
    if item is None:
        raise _not_found("Next-up item")
    await session.commit()
    await session.refresh(item)
    return OkrNextUpRead.model_validate(item)


@router.delete("/next-up/{item_id}", status_code=204)
async def delete_next_up(
    item_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_next_up(session, item_id)
    if not deleted:
        raise _not_found("Next-up item")
    await session.commit()
