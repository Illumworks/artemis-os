"""Tests for GET /api/okr/overview — J3a.

Six tests covering:
  1. empty-DB response shape
  2. objectives + KRs appear in correct shape
  3. archived objectives are filtered out
  4. stats chips reflect correct counts (including at-risk)
  5. activity is sorted newest-first, limited to 10
  6. only undismissed next-up items are returned
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.okr import repository as repo

# ── helpers ───────────────────────────────────────────────────────────────────


async def _make_objective(
    session: AsyncSession,
    title: str = "Test Objective",
    *,
    archived: bool = False,
    cycle: str | None = "Q2-2026",
) -> int:
    kwargs: dict[str, object] = {"title": title, "cycle": cycle}
    if archived:
        kwargs["archived_at"] = datetime.now(UTC)
    obj = await repo.create_objective(session, **kwargs)
    await session.commit()
    return obj.id


async def _make_kr(
    session: AsyncSession,
    objective_id: int,
    title: str = "Test KR",
    *,
    status: str = "ontrack",
    archived: bool = False,
) -> int:
    kwargs: dict[str, object] = {
        "objective_id": objective_id,
        "title": title,
        "status": status,
    }
    if archived:
        kwargs["archived_at"] = datetime.now(UTC)
    kr = await repo.create_key_result(session, **kwargs)
    await session.commit()
    return kr.id


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_okr_overview_empty_db(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/okr/overview with no data returns 200 and zero-valued shape."""
    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "ok"
    assert body["objectives"] == []
    assert body["activity"] == []
    assert body["evidence"] == []
    assert body["nextUp"] == []
    assert "quarter" in body
    assert body["quarter"]["label"].startswith("Q")

    stats = body["stats"]
    assert len(stats) == 3
    for chip in stats:
        assert chip["n"] == 0
        assert chip["tone"] == "zero"


async def test_okr_overview_with_objectives(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Objective + KR created via repo appear in overview with correct shape."""
    obj_id = await _make_objective(db_session, "Grow Revenue", cycle="Q2-2026")
    kr_id = await _make_kr(db_session, obj_id, "Land 3 pilots", status="ontrack")

    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    body = resp.json()
    assert len(body["objectives"]) == 1

    obj = body["objectives"][0]
    assert obj["id"] == obj_id
    assert obj["title"] == "Grow Revenue"
    assert "progress" in obj
    assert "tone" in obj
    assert "cycle" in obj

    assert "krs" in obj, "key-results array must be keyed 'krs'"
    assert len(obj["krs"]) == 1

    kr = obj["krs"][0]
    assert kr["id"] == kr_id
    assert kr["title"] == "Land 3 pilots"
    assert kr["status"] == "ontrack"
    assert "prog" in kr
    assert "evidence_count" in kr
    assert "done_bullets" in kr
    assert "gaps_bullets" in kr
    assert "target_text" in kr
    assert "note" in kr


async def test_okr_overview_archived_filtered(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Archived objectives do not appear in the overview response."""
    live_id = await _make_objective(db_session, "Active Obj")
    await _make_objective(db_session, "Archived Obj", archived=True)

    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    body = resp.json()
    ids = [o["id"] for o in body["objectives"]]
    assert live_id in ids
    assert len(ids) == 1, "archived objective must not appear"


async def test_okr_overview_stats_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Stats chips reflect correct counts: 2 objectives, 3 KRs, 1 at-risk."""
    obj1_id = await _make_objective(db_session, "Obj 1")
    obj2_id = await _make_objective(db_session, "Obj 2")

    await _make_kr(db_session, obj1_id, "KR 1a", status="ontrack")
    await _make_kr(db_session, obj1_id, "KR 1b", status="atrisk")
    await _make_kr(db_session, obj2_id, "KR 2a", status="ontrack")

    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    stats = {chip["label"]: chip for chip in resp.json()["stats"]}

    assert stats["Objectives"]["n"] == 2
    assert stats["Objectives"]["tone"] == "sage"

    assert stats["Key Results"]["n"] == 3
    assert stats["Key Results"]["tone"] == "sage"

    assert stats["At risk"]["n"] == 1
    assert stats["At risk"]["tone"] == "warn"


async def test_okr_overview_activity_sorted(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Activity entries are returned newest-first (most recent 10)."""
    now = datetime.now(UTC)

    # Insert three activity entries with explicit timestamps so ordering is deterministic
    from artemis.okr.models import OkrActivity

    for i in range(3):
        act = OkrActivity(
            text=f"Activity {i}",
            created_at=now + timedelta(seconds=i),
        )
        db_session.add(act)
    await db_session.commit()

    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    activity = resp.json()["activity"]
    assert len(activity) == 3

    # Newest first: "Activity 2" then "Activity 1" then "Activity 0"
    texts = [a["text"] for a in activity]
    assert texts[0] == "Activity 2"
    assert texts[-1] == "Activity 0"


async def test_okr_overview_next_up(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Only undismissed next-up items appear in the overview."""
    undismissed = await repo.create_next_up(db_session, text="Do the thing", ref="OKR", prio="high")
    await db_session.commit()

    dismissed = await repo.create_next_up(db_session, text="Old item", ref="OKR", prio="med")
    await db_session.commit()
    await repo.dismiss_next_up(db_session, dismissed.id)
    await db_session.commit()

    resp = await client.get("/api/okr/overview")
    assert resp.status_code == 200

    next_up = resp.json()["nextUp"]
    ids = [n["id"] for n in next_up]

    assert undismissed.id in ids
    assert dismissed.id not in ids
    assert len(next_up) == 1

    item = next_up[0]
    assert item["ref"] == "OKR"
    assert item["text"] == "Do the thing"
    assert item["prio"] == "high"
