"""Persistence for the Brand Signals corpus.

All writes go through ``upsert_findings``, which is idempotent on
``content_hash``. Re-running a scan an hour later must not create duplicates and
must not un-report anything already briefed.

Merge rules on conflict, and each one exists because a story genuinely arrives
twice in different shapes:

* ``names_amira`` is OR-ed. A story first seen through a state sweep (where the
  brand was only in the summary) must not lose its brand flag when the vendor
  lane sees it again, or vice versa.
* ``lane`` upgrades to ``vendor`` and never downgrades. Vendor is the stronger
  claim; a later category-lane sighting of the same story does not weaken it.
* ``state`` only ever moves AWAY from ``US``. National means "no state could be
  resolved", so a later run that does resolve one is strictly better
  information; a later run that cannot must not erase it.
* ``themes`` takes the larger set, since a longer text (summary present, or an
  improved matcher) is what produces more matches.

``first_seen_at`` and ``reported_at`` are never overwritten.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.sentiment.models import (
    LANE_CATEGORY,
    LANE_VENDOR,
    BrandSignalFinding,
    content_hash_for,
)

_log = logging.getLogger(__name__)

NATIONAL = "US"


async def upsert_findings(
    session: AsyncSession, findings: Sequence[dict[str, Any]]
) -> tuple[int, int]:
    """Insert or refresh *findings*. Returns ``(inserted, refreshed)``.

    Does NOT commit — the caller owns the transaction, so a failed brief can
    roll the whole run back rather than leaving a half-written corpus.
    """
    if not findings:
        return (0, 0)

    rows: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for finding in findings:
        digest = content_hash_for(finding["title"])
        # Collapse duplicates WITHIN this batch too: ON CONFLICT cannot see a
        # second conflicting row in the same statement and would error.
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        rows.append(
            {
                "content_hash": digest,
                "title": finding["title"],
                "link": finding["link"],
                "lane": finding.get("lane") or "category",
                "state": finding.get("state") or NATIONAL,
                "themes": list(finding.get("themes") or []),
                "names_amira": bool(finding.get("amira")),
                "published_at": finding.get("published"),
            }
        )

    before = await count_all(session)
    table = BrandSignalFinding.__table__
    statement = pg_insert(BrandSignalFinding).values(rows)
    excluded = statement.excluded
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["content_hash"],
            set_={
                "last_seen_at": func.now(),
                "title": excluded.title,
                "link": excluded.link,
                "names_amira": excluded.names_amira | table.c.names_amira,
                # Explicit CASE, not least()/greatest(): those compare
                # alphabetically, and least('category','vendor') is 'category'
                # -- the exact opposite of "vendor wins, never downgrades".
                "lane": case(
                    (
                        (excluded.lane == LANE_VENDOR) | (table.c.lane == LANE_VENDOR),
                        LANE_VENDOR,
                    ),
                    else_=LANE_CATEGORY,
                ),
                "state": case(
                    (excluded.state != NATIONAL, excluded.state),
                    else_=table.c.state,
                ),
                "themes": case(
                    (
                        func.jsonb_array_length(excluded.themes)
                        >= func.jsonb_array_length(table.c.themes),
                        excluded.themes,
                    ),
                    else_=table.c.themes,
                ),
                "published_at": func.coalesce(excluded.published_at, table.c.published_at),
            },
        )
    )
    after = await count_all(session)
    inserted = after - before
    return (inserted, len(rows) - inserted)


async def count_all(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(BrandSignalFinding))
    return int(result.scalar() or 0)


async def unreported(session: AsyncSession, *, limit: int = 25) -> list[BrandSignalFinding]:
    """Stories never included in a brief, newest first."""
    result = await session.execute(
        select(BrandSignalFinding)
        .where(BrandSignalFinding.reported_at.is_(None))
        .order_by(
            BrandSignalFinding.published_at.desc().nullslast(),
            BrandSignalFinding.id.desc(),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def window_findings(
    session: AsyncSession, *, days: int = 120, limit: int = 200
) -> list[BrandSignalFinding]:
    """The retained corpus inside the reporting window, newest first.

    This is what the standing sections of the brief render from -- the TABLE,
    not a live feed, so the same story reads the same way two mornings running.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(BrandSignalFinding)
        .where(BrandSignalFinding.published_at >= cutoff)
        .order_by(
            BrandSignalFinding.published_at.desc().nullslast(),
            BrandSignalFinding.id.desc(),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_unreported(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(BrandSignalFinding)
        .where(BrandSignalFinding.reported_at.is_(None))
    )
    return int(result.scalar() or 0)


async def standing_picture(session: AsyncSession, *, days: int = 120) -> dict[str, Any]:
    """Rollup over the retained corpus: totals, per-state, per-theme.

    Reads the TABLE, not a live feed, so the numbers are stable between runs.
    The drifting counts in the first version were the feed answering slightly
    differently each call, not the world changing.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    window = BrandSignalFinding.published_at >= cutoff

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(BrandSignalFinding).where(window)
            )
        ).scalar()
        or 0
    )
    amira = int(
        (
            await session.execute(
                select(func.count())
                .select_from(BrandSignalFinding)
                .where(window, BrandSignalFinding.names_amira.is_(True))
            )
        ).scalar()
        or 0
    )
    state_rows = (
        await session.execute(
            select(BrandSignalFinding.state, func.count())
            .where(window, BrandSignalFinding.state != NATIONAL)
            .group_by(BrandSignalFinding.state)
            .order_by(func.count().desc())
        )
    ).all()
    theme_rows = (
        await session.execute(
            text(
                "SELECT t.theme, COUNT(*) FROM brand_signal_findings f,"
                " jsonb_array_elements_text(f.themes) AS t(theme)"
                " WHERE f.published_at >= :cutoff"
                " GROUP BY t.theme ORDER BY COUNT(*) DESC"
            ),
            {"cutoff": cutoff},
        )
    ).all()
    corpus = await count_all(session)
    return {
        "total": total,
        "amira": amira,
        "states": [(str(s), int(n)) for s, n in state_rows],
        "themes": [(str(t), int(n)) for t, n in theme_rows],
        "corpus": corpus,
    }


async def mark_reported(session: AsyncSession, ids: Sequence[int]) -> int:
    """Stamp *ids* as briefed. Call ONLY after Slack accepted the post.

    A failed post must leave rows unreported so the next run picks them up;
    marking first would silently drop a story from every future brief.
    """
    if not ids:
        return 0
    result = await session.execute(
        update(BrandSignalFinding)
        .where(BrandSignalFinding.id.in_(list(ids)))
        .values(reported_at=func.now())
    )
    return int(cast("CursorResult[Any]", result).rowcount or 0)


async def mark_all_reported(session: AsyncSession) -> int:
    """Backfill helper: treat the whole corpus as already briefed.

    Used once, when the table is first populated from a window that has already
    been posted by hand — otherwise the next brief would announce 30 stories as
    "new" on its first run.
    """
    result = await session.execute(
        update(BrandSignalFinding)
        .where(BrandSignalFinding.reported_at.is_(None))
        .values(reported_at=func.now())
    )
    return int(cast("CursorResult[Any]", result).rowcount or 0)
