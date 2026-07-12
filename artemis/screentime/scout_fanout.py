"""National, screen-time-tuned scout fan-out — read-only, in-process.

We REUSE the existing scouts (``legislative``, ``regional_news``, and — on a
SEPARATE weekly sweep, not the daily one — ``board_peer_validation``) WITHOUT
editing their core behavior. Each scout exposes ``_gather_findings()``
returning raw finding dicts; we call that directly (in-process) so nothing is
POSTed to the marketing ``/api/scouts/runs`` ingest path — our findings flow
into the isolated ``screentime_*`` tables instead.

2026-07-11 source tuning (live PoC sweep findings — see
``docs/screentime-watch-plan.md`` / the tuning brief):
  - ``state_doe`` REMOVED from the daily fan-out. It is the shared
    literacy-scoped marketing scout (``artemis/scouts/state_doe/``) —
    pointing its RSS queries at 50 states floods ~2,185 off-topic items into
    this pipeline. ``national_news`` (below) is the screentime-owned
    replacement for state-level news coverage; the ``state_doe`` package
    itself is UNTOUCHED (still serves marketing). ``_gather_state_doe`` is
    kept (dead code, harmless) only as a reference for what the old wiring
    looked like — it is no longer registered in ``_SCOUT_GATHERERS``.
  - ``board_minutes`` and ``board_peer_validation`` REMOVED from the daily
    fan-out (``_SCOUT_GATHERERS``). The board scout is slow (27 districts ×
    BoardDocs fetch + an LLM call each) and was blowing the 10-minute daily
    sweep timeout. It now runs on its OWN weekly schedule with bounded
    concurrency — see ``run_board_sweep`` in ``artemis.screentime.runner``,
    which calls ``_gather_board_peer_validation_concurrent`` (below) directly;
    it is NOT part of ``gather_national_findings``/``_SCOUT_GATHERERS`` at all
    (kept fully separate so the daily path can never accidentally invoke it).
  - The daily fast set is now exactly: ``legislative`` (national bill search),
    ``national_news`` (per-state Google News RSS), ``regional_news``
    (watch-list newsapi). No BoardDocs, no LLM calls, no state_doe — fast and
    clean.

Daily-sweep sources:
  - legislative accepts ``priority_states`` (all 50 + DC) and ``keywords`` →
    the SCREENTIME_TERMS query (instructional screen-time limits +
    evidence-based-tool exemptions +, as of 2026-07-10, AI-in-schools POLICY
    terms — adoption/guidance/moratoria on AI use in the classroom, scoped to
    schools, NOT general-purpose AI news).
  - regional_news is watch-list driven (no national district list exists) but
    also accepts ``query_topics``/``news_domains`` (2026-07-10) — we pass the
    broadened screen-time/AI-in-schools keyword set + the major ed-policy
    outlet list so its per-district newsapi queries surface this beat, not
    just literacy.
  - national_news (2026-07-10, broadened 2026-07-11) is a NEW, screentime-owned
    gatherer — per-state Google News RSS coverage of screen-time +
    AI-in-schools policy (see ``artemis.screentime.national_news``). This
    fills the NEWS gap: legislative is national but bill-only; national_news
    surfaces agency guidance, board actions, and AI-adoption stories that
    never became a bill, one query per state (all 50 + DC by default every
    run — Google News RSS is lightweight). Deliberately NOT added to
    scouts/state_doe (that package is shared with the literacy-scoped
    marketing scout).

Weekly (separate) sweep — see ``artemis.screentime.runner.run_board_sweep``:
  - board_peer_validation classifies screentime AND ai_in_schools mentions
    (see ``board_minutes.classifier.TOPICS``) across a starter district seed
    list. The topic gate (topic_config.py, ``DEFAULT_TOPIC_RULES`` v3,
    2026-07-10) carries explicit AI-in-schools-policy anchors alongside the
    screen/device-time anchors — the owner decided screen-time and
    AI-in-schools policy are one "rein in the technology" story (per the exec
    report "Board Meetings on Screen Time & the Use of AI") and should be
    tracked together. STANCE tuning for AI-policy items is deliberately NOT
    done here — pending a review with Angela (see topic_config.py) — AI
    findings land with the existing best-effort stance for now.

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
# carve-outs, PLUS (2026-07-10) AI-in-schools POLICY terms. Deliberately NOT
# cellphone-ban terms and NOT general-purpose AI news — every AI term here is
# scoped to schools/students/classrooms so the LegiScan query stays on the
# "AI in K-12 policy" beat, not general AI legislation.
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
# the OR string, so the search now matches bills mentioning ANY screen-time OR
# AI-in-schools term. Phrases are quoted so multi-word terms match as phrases,
# not loose tokens.
SCREENTIME_TERMS: list[str] = [
    # -- screen/device-time terms --
    "screen time",
    "screen-time",
    "device time",
    "screen use in schools",
    "instructional screen time",
    "student screen time",
    "screen time limit",
    "device usage limit",
    # -- AI-in-schools POLICY terms (2026-07-10 broadening; school-scoped) --
    "artificial intelligence in schools",
    "ai in the classroom",
    "ai use in schools",
    "student use of artificial intelligence",
    "generative ai in education",
    "ai policy for schools",
    "school ai guidance",
    "ai literacy in schools",
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


async def _gather_regional_news() -> list[dict[str, Any]]:
    from artemis.scouts.regional_news.client import NEWS_OUTLET_DOMAINS, TOPIC_KEYWORDS
    from artemis.scouts.regional_news.scout import RegionalNewsScout

    scout = RegionalNewsScout(
        _dry_run_config(),
        query_topics=TOPIC_KEYWORDS,
        news_domains=NEWS_OUTLET_DOMAINS,
    )
    return await scout._gather_findings()


async def _gather_board_peer_validation() -> list[dict[str, Any]]:
    """Serial board-peer-validation gather (one scout instance, its own default
    watch list) — NOT part of the daily fan-out (too slow: BoardDocs + an LLM
    call per district, serially). Kept for tests / ad-hoc single-scout use;
    the weekly board sweep uses ``_gather_board_peer_validation_concurrent``
    instead (see ``artemis.screentime.runner.run_board_sweep``).
    """
    from artemis.scouts.board_minutes.peer_scout import BoardPeerValidationScout

    scout = BoardPeerValidationScout(_dry_run_config())
    return await scout._gather_findings()


_BOARD_SWEEP_CONCURRENCY = 5


async def _gather_board_peer_validation_concurrent(
    *,
    concurrency: int = _BOARD_SWEEP_CONCURRENCY,
    watch_list: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Bounded-concurrency board-peer-validation sweep — the weekly path.

    The default ``BoardPeerValidationScout._gather_findings()`` walks its
    watch list one district at a time (BoardDocs fetch + an LLM classify call
    per relevant item) — with ~27 districts that easily blows the 10-minute
    daily-sweep timeout. This runs ONE scout instance per district and
    gathers them under an ``asyncio.Semaphore(concurrency)`` (default 5
    concurrent districts) instead — same per-district watch list, same
    per-district/per-meeting caps inside ``fetch_boarddocs`` (untouched), just
    parallelized. ``BoardPeerValidationScout`` itself is NOT modified.

    A single district's failure (BoardDocs down, LLM error, etc.) is caught
    and logged — it never aborts the rest of the sweep.
    """
    import asyncio

    from artemis.scouts.board_minutes.peer_scout import (
        _DEFAULT_PEER_WATCH_LIST,
        BoardPeerValidationScout,
    )

    districts = watch_list if watch_list is not None else list(_DEFAULT_PEER_WATCH_LIST)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(district: dict[str, Any]) -> list[dict[str, Any]]:
        async with semaphore:
            scout = BoardPeerValidationScout(
                _dry_run_config(), watch_list=[district], max_districts_per_run=1
            )
            try:
                return await scout._gather_findings()
            except Exception as exc:
                _logger.warning(
                    "board sweep: district %s failed — skipping: %s",
                    district.get("district_id", "unknown"),
                    exc,
                )
                return []

    results = await asyncio.gather(*(_one(d) for d in districts))
    findings: list[dict[str, Any]] = []
    for r in results:
        findings.extend(r)
    return findings


async def _gather_national_news(states: list[str]) -> list[dict[str, Any]]:
    """Per-state Google News RSS coverage of screen-time + AI-in-schools policy.

    Screentime-owned (artemis.screentime.national_news) — NOT the shared
    scouts/state_doe package. Fills the news gap LegiScan (bill tracking) can't:
    state agency guidance, board actions, and AI-adoption stories that never
    became a bill. Default (states_per_run=None): sweeps every state passed in
    every run — Google News RSS is lightweight enough not to need throttling by
    default; see national_news.py for the optional rotation/cursor mode.
    """
    from artemis.screentime.national_news import gather_national_policy_news

    findings, _next_cursor = await gather_national_policy_news(states)
    return findings


# scout label → coroutine factory. Kept as a dict so tests can monkeypatch a
# single source or inject fakes.
#
# DAILY FAST SET ONLY (2026-07-11 tuning): legislative + national_news +
# regional_news. Deliberately EXCLUDES state_doe (shared marketing scout,
# floods ~2,185 off-topic items when pointed at 50 states) and board_minutes /
# board_peer_validation (too slow for the daily 10-minute window — BoardDocs +
# an LLM call per district). The board scout runs on its OWN weekly schedule
# instead — see ``artemis.screentime.runner.run_board_sweep``, which calls
# ``_gather_board_peer_validation_concurrent`` directly (not through this
# dict, so it can never be pulled into the daily path by accident).
_SCOUT_GATHERERS: dict[str, Any] = {
    "legislative": lambda states: _gather_legislative(states),
    "regional_news": lambda _states: _gather_regional_news(),
    "national_news": lambda states: _gather_national_news(states),
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
