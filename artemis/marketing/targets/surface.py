"""What Josh actually sees: his targets, plus statewide news where he sells.

Owner decision 2026-08-25 (option 2). A signal reaches Josh's view when either:

  * it belongs to a **target account** — the thing he asked for; or
  * it is **state-level** (names no district) and lands in a state where he has
    at least one target — a literacy-screening bill in Illinois matters to him
    because he has 48 Illinois targets, even though it belongs to no one
    district.

Everything else is held back: a district genuinely absent from his list, and
state-level news from states he does not sell into.

**Held back, never deleted.** A signal we could not classify is `unresolved`,
not `excluded`, and the caller is expected to show that count. Roughly a fifth
of target accounts do not resolve to an NCES district, so silently discarding
what we failed to match would bury exactly the opportunities this exists to
find. That is the gazetteer error -- confidently wrong because the table was
incomplete -- and the reason UNKNOWN is a first-class verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.targets.matching import (
    MatchOutcome,
    TargetIndex,
    Verdict,
    load_target_index,
)

logger = logging.getLogger(__name__)


@dataclass
class SurfacedSignal:
    """One signal that reached Josh's view, and why."""

    signal_id: int
    headline: str
    state: str
    district_name: str
    summary: str
    source_url: str
    urgency_tier: str
    created_at: datetime
    reason: str
    account_name: str = ""
    marketing_tier: str = ""
    is_state_level: bool = False


@dataclass
class SurfaceResult:
    """The partitioned view, including what was held back and why."""

    targets: list[SurfacedSignal] = field(default_factory=list)
    state_level: list[SurfacedSignal] = field(default_factory=list)
    unresolved: list[SurfacedSignal] = field(default_factory=list)
    excluded_not_target: int = 0
    excluded_other_state: int = 0
    considered: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.targets)} target-account signal(s), "
            f"{len(self.state_level)} statewide, "
            f"{len(self.unresolved)} unresolved, "
            f"{self.excluded_not_target + self.excluded_other_state} held back "
            f"(of {self.considered} considered)"
        )


def _to_signal(row: Any, outcome: MatchOutcome, *, state_level: bool = False) -> SurfacedSignal:
    return SurfacedSignal(
        signal_id=row.id,
        headline=row.headline or "",
        state=(row.state or "").upper(),
        district_name=row.district_id or "",
        summary=row.summary or "",
        source_url=row.source_url or "",
        urgency_tier=row.urgency_tier or "",
        created_at=row.created_at,
        reason=outcome.reason,
        account_name=outcome.account_name,
        marketing_tier=outcome.marketing_tier,
        is_state_level=state_level,
    )


async def select_for_targets(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    days: int = 7,
    statuses: tuple[str, ...] = ("qualified", "approved"),
    limit: int = 500,
    index: TargetIndex | None = None,
) -> SurfaceResult:
    """Partition recent signals into Josh's view.

    Loads the target index ONCE and classifies in a single pass. Doing it per
    signal would issue a query each time and, worse, tempt a second copy of the
    matching rules into whatever SQL the caller writes.
    """
    cutoff = since or (datetime.now(UTC) - timedelta(days=days))
    target_index = index or await load_target_index(session)

    rows = list(
        await session.execute(
            text(
                """
                SELECT id, headline, summary, source_url, urgency_tier,
                       district_id, state, resolved_district_id, created_at
                FROM signal_queue
                WHERE created_at >= :cutoff
                  AND signal_status = ANY(:statuses)
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"cutoff": cutoff, "statuses": list(statuses), "limit": limit},
        )
    )

    result = SurfaceResult(considered=len(rows))
    for row in rows:
        outcome = target_index.classify(
            district_name=row.district_id,
            state=row.state,
            district_id=row.resolved_district_id,
        )

        if outcome.verdict is Verdict.TARGET:
            result.targets.append(_to_signal(row, outcome))
            continue

        state_code = (row.state or "").strip().upper()
        names_no_district = not (row.district_id or "").strip()

        if names_no_district:
            # Statewide news. Relevant iff Josh sells into that state at all.
            if state_code and state_code in target_index.states:
                result.state_level.append(_to_signal(row, outcome, state_level=True))
            else:
                result.excluded_other_state += 1
            continue

        if outcome.verdict is Verdict.NOT_TARGET:
            result.excluded_not_target += 1
            continue

        # UNKNOWN with a district named: we failed to match it, which is not the
        # same as it not being a target. Surfaced separately so nothing real is
        # lost to a naming mismatch.
        result.unresolved.append(_to_signal(row, outcome))

    logger.info("target surface: %s", result.summary())
    return result
