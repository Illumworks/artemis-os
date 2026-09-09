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
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.gong.baseline import AccountDeviation

logger = logging.getLogger(__name__)

SNAPSHOT_CATEGORY = "gong_account_signal"

#: How old a reading has to be before comparing against it means anything. The
#: section scores a 120-day window, so yesterday's reading shares 119 days of
#: calls with today's -- it can barely move, and comparing to it would report
#: "steady" forever. Three weeks turns over enough of the window that a real
#: shift has somewhere to show up.
TREND_MIN_AGE_DAYS = 21

#: Past this, a district's old level says more about last school year than about
#: now, and "up from 40% in March" is not a fact anyone can act on.
TREND_MAX_AGE_DAYS = 180

#: Ceiling on rows scanned for a comparison. Roughly 40 districts carry a signal
#: on any given day, so this is ~4 months of readings; ordered newest-first, a
#: cut tail only loses readings already too old to compare against.
_MAX_SNAPSHOT_ROWS = 5000


@dataclass(frozen=True)
class TrendVerdict:
    """How a district's concern level compares to its own recent past."""

    account_name: str
    direction: str  # "worsening" | "improving" | "steady" | "no_history"
    current_rate: float | None = None
    previous_rate: float | None = None
    tracker: str | None = None
    since: date | None = None

    def describe(self) -> str:
        if self.direction == "no_history":
            return (
                f"{self.account_name}: first recorded reading, so there is nothing to "
                "compare it against yet."
            )
        if self.direction == "steady" or self.current_rate is None:
            return f"{self.account_name}: no meaningful change since the last reading."
        arrow = "up from" if self.direction == "worsening" else "down from"
        when = f" on {self.since:%d %b}" if self.since else " at the previous reading"
        return (
            f"{self.account_name}: {self.tracker} now {self.current_rate:.0%}, "
            f"{arrow} {self.previous_rate:.0%}{when}."
        )

    def brief_clause(self) -> str:
        """The parenthetical a brief line appends, or "" when there is nothing to add.

        Silent on "steady" and "no_history" by design. A line that reads
        "(no change)" or "(first reading)" spends a reader's attention to tell
        them nothing; absence already says it.
        """
        if self.current_rate is None or self.previous_rate is None:
            return ""
        if self.direction not in ("worsening", "improving"):
            return ""
        arrow = "up" if self.direction == "worsening" else "down"
        when = f" on {self.since:%d %b}" if self.since else ""
        return f" ({arrow} from {self.previous_rate:.0%}{when})"


def _content(dev: AccountDeviation, on: date) -> str:
    """The stored line. Counts and rates; never anything anyone said."""
    concern = ", ".join(f"{t} {r:.0%}" for t, r in sorted(dev.elevated_concern.items()))
    advocacy = ", ".join(f"{t} {r:.0%}" for t, r in sorted(dev.elevated_advocacy.items()))
    return (
        f"[gong|{on.isoformat()}|{dev.account_name}] "
        f"calls={dev.calls_considered} "
        f"concern=({concern or 'none'}) advocacy=({advocacy or 'none'})"
    )


@dataclass(frozen=True)
class Reading:
    """A stored line, read back. Counts and rates -- there is nothing else in it."""

    on: date
    account_name: str
    calls: int
    concern: dict[str, float]
    advocacy: dict[str, float]


_LINE = re.compile(
    r"^\[gong\|(?P<on>\d{4}-\d{2}-\d{2})\|(?P<account>.+?)\] "
    r"calls=(?P<calls>\d+) "
    r"concern=\((?P<concern>.*?)\) advocacy=\((?P<advocacy>.*)\)$"
)

#: Non-greedy up to a percentage followed by a comma or the end, so a tracker
#: carrying brackets in its own name -- "Objections (tracker)", a real one --
#: survives the round trip. Splitting on ")" does not.
_RATE = re.compile(r"\s*(.+?)\s+(\d+)%(?:,|$)")


def _rates(blob: str) -> dict[str, float]:
    if blob.strip() == "none":
        return {}
    return {name: int(pct) / 100 for name, pct in _RATE.findall(blob)}


def parse_snapshot(content: str) -> Reading | None:
    """Read a stored line back, or ``None`` when it is not one.

    ``_content`` became a wire format the moment anything read it back, so these
    two move together and a round-trip test holds them to it. Unparseable rows
    are skipped rather than raised on: a line written by an older version of the
    writer should cost a comparison, not the brief.
    """
    match = _LINE.match(content)
    if match is None:
        return None
    try:
        on = date.fromisoformat(match["on"])
    except ValueError:
        return None
    return Reading(
        on=on,
        account_name=match["account"],
        calls=int(match["calls"]),
        concern=_rates(match["concern"]),
        advocacy=_rates(match["advocacy"]),
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


async def previous_readings(
    session: AsyncSession,
    *,
    before: date | None = None,
    min_age_days: int = TREND_MIN_AGE_DAYS,
) -> dict[str, Reading]:
    """The most recent reading per district that is old enough to compare against.

    Old enough is the whole point -- see ``TREND_MIN_AGE_DAYS``. A district whose
    only readings are recent is absent from the result, and ``compare`` turns
    that absence into ``no_history``: "nothing to compare against yet", which is
    a different claim from "no change".
    """
    from sqlalchemy import select

    from artemis.memory.models import MemoryObservation

    rows = await session.execute(
        select(MemoryObservation.content)
        .where(
            MemoryObservation.category == SNAPSHOT_CATEGORY,
            MemoryObservation.scope_kind == "workspace",
            MemoryObservation.scope_id == "marketing",
            MemoryObservation.superseded_by.is_(None),
            MemoryObservation.created_at >= datetime.now(UTC) - timedelta(days=TREND_MAX_AGE_DAYS),
        )
        # Newest first: the selection below takes the first eligible row per
        # district, and "eligible" is decided on the date inside the line.
        .order_by(MemoryObservation.id.desc())
        .limit(_MAX_SNAPSHOT_ROWS)
    )
    return select_previous(
        (content for (content,) in rows),
        before=before or datetime.now(UTC).date(),
        min_age_days=min_age_days,
    )


def select_previous(
    contents: Iterable[str], *, before: date, min_age_days: int = TREND_MIN_AGE_DAYS
) -> dict[str, Reading]:
    """Which stored line becomes the comparison for each district.

    Split out from the query because this half is where the judgement lives and
    the query half is only reachable against a real database. Takes rows
    newest-first.
    """
    if isinstance(contents, str):
        # `str` satisfies `Iterable[str]`, so a single line passed here type-checks,
        # iterates characters, parses none of them, and returns an empty history --
        # which reads as "this district has no past" and is simply wrong. Loud.
        raise TypeError("select_previous takes lines, not one line")

    newest_usable = before - timedelta(days=min_age_days)
    out: dict[str, Reading] = {}
    for content in contents:
        reading = parse_snapshot(content)
        if reading is None or reading.on > newest_usable:
            continue
        out.setdefault(reading.account_name, reading)
    return out


def trend_for(current: AccountDeviation, previous: Reading | None) -> TrendVerdict:
    """``compare`` against a stored reading, carrying its date through."""
    if previous is None:
        return TrendVerdict(current.account_name, "no_history")
    verdict = compare(current, previous.concern)
    return replace(verdict, since=previous.on)
