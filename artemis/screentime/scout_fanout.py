"""National, screen-time-tuned scout fan-out — read-only, in-process.

We REUSE the existing scouts (``legislative``, ``state_doe``, ``board_minutes``,
``regional_news``) WITHOUT editing them. Each scout exposes ``_gather_findings()``
returning raw finding dicts; we call that directly (in-process) so nothing is
POSTed to the marketing ``/api/scouts/runs`` ingest path — our findings flow into
the isolated ``screentime_*`` tables instead.

National scope:
  - legislative + state_doe accept ``priority_states`` → we pass all 50 + DC.
  - legislative also accepts ``keywords`` → we pass screen-time terms (instructional
    screen-time limits + evidence-based-tool exemptions; NOT cellphone bans).
  - board_minutes + regional_news are watch-list driven (no national source list
    exists), so they run on their default national-ish watch lists, read-only.

Every scout is wrapped in try/except: a failing source NEVER breaks the sweep
(failure-safe). ``run_once`` is not used (it POSTs); we only call the pure-ish
gather path.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.scouts.base import ScoutConfig

_logger = logging.getLogger(__name__)

# All 50 states + DC — the national sweep set.
US_STATES_AND_DC: list[str] = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# Screen-time-tuned search terms — instructional screen-time + evidence-based
# carve-outs. Deliberately NOT cellphone-ban terms (a different project).
#
# LegiScan-query bug (the first live run's `legislative: ok:0`): the LegiScan
# `getSearch` op runs an ADAS full-text query where SPACE-separated terms are
# AND-ed (a doc must contain them all). The shared LegislativeScout joins its
# keyword list with spaces (`" ".join(keywords)`), so passing these as a flat
# list produced one giant AND query — '"screen time" AND "instructional
# technology" AND "digital learning" AND ...' — which essentially no bill
# matches → 0 results. (The literacy default "works" by luck: its terms are
# individually common, but it is the same latent bug.)
#
# Fix WITHOUT touching the shared scout: hand the scout a SINGLE pre-composed
# ADAS boolean OR expression as its one keyword. Joined-with-spaces that is just
# the OR string, so the search now matches bills mentioning ANY screen-time term.
# Phrases are quoted so multi-word terms match as phrases, not loose tokens.
SCREENTIME_TERMS: list[str] = [
    "screen time",
    "screen-time",
    "device time",
    "screen use in schools",
    "instructional screen time",
    "student screen time",
    "screen time limit",
    "device usage limit",
]


def _legiscan_query(terms: list[str]) -> str:
    """Compose an ADAS boolean OR expression from screen-time terms.

    Each multi-word term is quoted so LegiScan matches it as a phrase; terms are
    OR-ed so a bill matching ANY one is returned (vs. the implicit-AND bug).
    """
    return " OR ".join(f'"{t}"' for t in terms)


# A single-element keyword list: the shared scout's `" ".join(keywords)` yields
# exactly the OR expression below. Kept as a list because the scout's `keywords=`
# parameter expects one.
SCREENTIME_KEYWORDS: list[str] = [_legiscan_query(SCREENTIME_TERMS)]


def _dry_run_config() -> ScoutConfig:
    """ScoutConfig used for gathering — api_url unused on the gather path.

    dry_run=True keeps any scout that branches on it in its safest mode; we never
    call emit_signals, so no HTTP POST happens regardless.
    """
    return ScoutConfig(dry_run=True, enabled=True)


async def _gather_legislative(states: list[str]) -> list[dict[str, Any]]:
    from artemis.scouts.legislative.scout import LegislativeScout

    scout = LegislativeScout(
        _dry_run_config(),
        priority_states=states,
        keywords=SCREENTIME_KEYWORDS,
    )
    return await scout._gather_findings()


async def _gather_state_doe(states: list[str]) -> list[dict[str, Any]]:
    from artemis.scouts.state_doe.scout import StateDoEScout

    # state_doe is source-list driven; pass the national states it has sources for.
    scout = StateDoEScout(_dry_run_config(), priority_states=states)
    return await scout._gather_findings()


async def _gather_board_minutes() -> list[dict[str, Any]]:
    from artemis.scouts.board_minutes.scout import BoardMinutesScout

    scout = BoardMinutesScout(_dry_run_config())
    return await scout._gather_findings()


async def _gather_regional_news() -> list[dict[str, Any]]:
    from artemis.scouts.regional_news.scout import RegionalNewsScout

    scout = RegionalNewsScout(_dry_run_config())
    return await scout._gather_findings()


# scout label → coroutine factory. Kept as a dict so tests can monkeypatch a
# single source or inject fakes.
_SCOUT_GATHERERS: dict[str, Any] = {
    "legislative": lambda states: _gather_legislative(states),
    "state_doe": lambda states: _gather_state_doe(states),
    "board_minutes": lambda _states: _gather_board_minutes(),
    "regional_news": lambda _states: _gather_regional_news(),
}


async def gather_national_findings(
    *,
    states: list[str] | None = None,
    gatherers: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Run every scout nationally (read-only) and return (findings, per_source_status).

    Failure-safe: each source is isolated; an exception in one source logs +
    continues, never aborting the sweep. ``per_source_status`` records
    ``ok:<n>`` or ``error:<msg>`` per source for the run report.
    """
    sweep_states = states or list(US_STATES_AND_DC)
    sources = gatherers or _SCOUT_GATHERERS

    findings: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    for label, factory in sources.items():
        try:
            got = await factory(sweep_states)
            got = list(got or [])
            findings.extend(got)
            status[label] = f"ok:{len(got)}"
            _logger.info("screentime fan-out: %s → %d findings", label, len(got))
        except Exception as exc:  # failure-safe per source
            status[label] = f"error:{exc}"
            _logger.warning("screentime fan-out: %s failed — skipping: %s", label, exc)
    return findings, status
