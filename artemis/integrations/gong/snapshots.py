"""Keep a dated record of how each district sounded, so trends become visible.

The brief section computes deviations fresh every run: eight pages of Gong calls,
scored, ranked, printed, discarded. That has two costs.

**It cannot see change.** "Pinellas raised product feedback on 100% of calls" is
a fact about now. "Pinellas went from 40% to 100% over six weeks" is the thing
worth acting on, and no snapshot can say it.

**It re-reads the whole corpus to answer one question.** Roughly 800 calls per
run, on every surface that wants the answer.

So each run's verdict is written as a memory observation. Derived counts only:
tracker names and rates, never a word anyone said, which is what CLAUDE.md rule 4
permits and what the client can produce in the first place.

Written to scope ``workspace:marketing`` under category ``gong_account_signal``
so it lands in the same lossless store as everything else, is readable by Callie
under her existing allowance, and needs no migration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.gong.baseline import AccountDeviation

logger = logging.getLogger(__name__)

SNAPSHOT_CATEGORY = "gong_account_signal"


@dataclass(frozen=True)
class TrendVerdict:
    """How a district's concern level compares to its own recent past."""

    account_name: str
    direction: str  # "worsening" | "improving" | "steady" | "no_history"
    current_rate: float | None = None
    previous_rate: float | None = None
    tracker: str | None = None

    def describe(self) -> str:
        if self.direction == "no_history":
            return (
                f"{self.account_name}: first recorded reading, so there is nothing to "
                "compare it against yet."
            )
        if self.direction == "steady" or self.current_rate is None:
            return f"{self.account_name}: no meaningful change since the last reading."
        arrow = "up from" if self.direction == "worsening" else "down from"
        return (
            f"{self.account_name}: {self.tracker} now {self.current_rate:.0%}, "
            f"{arrow} {self.previous_rate:.0%} at the previous reading."
        )


def _content(dev: AccountDeviation, on: date) -> str:
    """The stored line. Counts and rates; never anything anyone said."""
    concern = ", ".join(f"{t} {r:.0%}" for t, r in sorted(dev.elevated_concern.items()))
    advocacy = ", ".join(f"{t} {r:.0%}" for t, r in sorted(dev.elevated_advocacy.items()))
    return (
        f"[gong|{on.isoformat()}|{dev.account_name}] "
        f"calls={dev.calls_considered} "
        f"concern=({concern or 'none'}) advocacy=({advocacy or 'none'})"
    )


async def write_snapshots(
    session: AsyncSession, deviations: list[AccountDeviation], *, on: date | None = None
) -> int:
    """Persist today's readings. Returns how many were written.

    Only accounts with a signal are stored. Recording "nothing unusual" for 300
    districts daily would bury the readings that matter in their own noise, and
    the absence of a row already means what it should.
    """
    from artemis.memory.schemas import Scope
    from artemis.memory.store import write_observation

    on = on or datetime.now(UTC).date()
    written = 0
    for dev in deviations:
        if not dev.has_signal:
            continue
        try:
            await write_observation(
                session,
                Scope(scope_kind="workspace", scope_id="marketing"),
                content=_content(dev, on),
                category=SNAPSHOT_CATEGORY,
                source_quality=0.8,
                raw_source_kind="gong_tracker_snapshot",
                raw_source_id=f"{dev.account_name}:{on.isoformat()}",
                raw_actor="gong_snapshot",
            )
            written += 1
        except Exception:
            logger.warning(
                "gong snapshot: could not store reading for %s", dev.account_name, exc_info=True
            )
    return written


def compare(current: AccountDeviation, previous_rates: dict[str, float]) -> TrendVerdict:
    """Is this district getting better or worse than when we last looked?

    Compares the district to ITSELF, which is the comparison the portfolio
    baseline cannot make. The portfolio answers "is this unusual"; this answers
    "is this changing", and a district drifting from 40% to 100% matters even
    while both readings sit above the norm.
    """
    if not previous_rates:
        return TrendVerdict(current.account_name, "no_history")
    if not current.elevated_concern:
        return TrendVerdict(current.account_name, "improving" if previous_rates else "steady")

    tracker, now_rate = max(current.elevated_concern.items(), key=lambda kv: kv[1])
    then_rate = previous_rates.get(tracker)
    if then_rate is None:
        return TrendVerdict(current.account_name, "no_history", now_rate, None, tracker)

    # A tenth of the calls is the smallest move worth reporting: below that, one
    # call joining or leaving the window flips the number on a small sample.
    if abs(now_rate - then_rate) < 0.10:
        return TrendVerdict(current.account_name, "steady", now_rate, then_rate, tracker)
    direction = "worsening" if now_rate > then_rate else "improving"
    return TrendVerdict(current.account_name, direction, now_rate, then_rate, tracker)
