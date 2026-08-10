"""Screen-Time Watch runner — the cron-safe national sweep.

One pass:
  national scout fan-out (read-only) → normalize → dedupe (content_hash) →
  "real moves" filter → config-driven stance + Amira-angle classify (cheap
  provider) → store → recompute per-state stance → expire by retention window.

Cron-safety: ``run_screentime_pipeline`` NEVER raises out — every stage is
guarded; a failing source / classification / store leaves partial results intact
and the run reports what it managed. Designed to be invoked from APScheduler
(see ``register_screentime_schedule``) or one-shot from the CLI.

Why a dedicated runner (not pipeline nodes): the shared PipelineExecutor invokes
scouts via ``agent_invocation`` nodes that write to the marketing SignalQueue —
exactly what Brief 1 forbids. A dedicated runner keeps us fully isolated; a
seeded Pipeline row (see ``pipeline_seed``) still gives pipelines-page visibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from artemis.screentime import filters
from artemis.screentime.classifier import classify_signal
from artemis.screentime.scout_fanout import gather_national_findings
from artemis.screentime.stance_config import load_stance_rules
from artemis.screentime.topic_config import load_topic_rules

_logger = logging.getLogger(__name__)


@dataclass
class RunReport:
    gathered: int = 0
    normalized: int = 0
    deduped: int = 0
    topic_relevant: int = 0
    dropped_off_topic: int = 0
    real_moves: int = 0
    dropped_not_real_move: int = 0
    stored_new: int = 0
    duplicates: int = 0
    states_rolled_up: int = 0
    expired: int = 0
    source_status: dict[str, str] = field(default_factory=dict)
    providers_used: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gathered": self.gathered,
            "normalized": self.normalized,
            "deduped": self.deduped,
            "topic_relevant": self.topic_relevant,
            "dropped_off_topic": self.dropped_off_topic,
            "real_moves": self.real_moves,
            "dropped_not_real_move": self.dropped_not_real_move,
            "stored_new": self.stored_new,
            "duplicates": self.duplicates,
            "states_rolled_up": self.states_rolled_up,
            "expired": self.expired,
            "source_status": self.source_status,
            "providers_used": self.providers_used,
            "error": self.error,
        }


def _resolve_states() -> list[str] | None:
    """Configured state scope: empty setting → None (= national default)."""
    from artemis.config import settings

    raw = (settings.screentime_states or "").strip()
    if not raw:
        return None
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


async def run_screentime_pipeline(
    session: Any,
    *,
    findings: list[dict[str, Any]] | None = None,
    states: list[str] | None = None,
    deliver_alerts: bool = True,
) -> RunReport:
    """Execute one Screen-Time Watch sweep against *session*. Never raises.

    Parameters
    ----------
    session:
        An AsyncSession. The caller owns commit/rollback (the runner flushes
        progressively and the caller commits at the end) — except the cron entry
        point ``run_scheduled`` which manages its own session + commit.
    findings:
        Inject pre-gathered findings to skip the live scout fan-out (tests /
        fixtured runs). When None, the national fan-out runs.
    states:
        Override the configured state scope (tests). None → settings/national.
    deliver_alerts:
        When False, the per-signal big-move Slack alert hook is SUPPRESSED even
        if ``screentime_report_channel`` is set. The scheduled COLLECTION cron
        (``run_scheduled``) passes False — owner decision: collect silently, no
        auto-push; Slack delivery is a separate, deliberate step. Defaults True
        so explicit/manual callers keep the existing behavior.
    """
    report = RunReport()
    try:
        from artemis.screentime import repository

        rules = await load_stance_rules(session)
        topic_rules = await load_topic_rules(session)
        sweep_states = states if states is not None else _resolve_states()

        # 1. Fan-out (or use injected findings).
        if findings is None:
            raw_findings, source_status = await gather_national_findings(states=sweep_states)
        else:
            raw_findings, source_status = list(findings), {"injected": f"ok:{len(findings)}"}
        report.source_status = source_status
        report.gathered = len(raw_findings)

        # 2. Normalize.
        candidates = []
        for f in raw_findings:
            try:
                c = filters.normalize_finding(f)
            except Exception:
                _logger.warning("screentime: normalize failed for a finding", exc_info=True)
                c = None
            if c is not None:
                candidates.append(c)
        report.normalized = len(candidates)

        # 3. Dedupe within batch.
        candidates = filters.dedupe(candidates)
        report.deduped = len(candidates)

        # 3b. Screen-time TOPIC-relevance gate (the core data-quality fix).
        # Runs BEFORE the real-moves filter / classify / store so generic
        # ed-policy noise (literacy, reading retention, curriculum approval,
        # test scores) never reaches the classifier — which also kills the
        # "exempt" false-favorable at the source (an off-topic reading-retention
        # exemption is dropped here, never classified). Failure-safe: a gate
        # error keeps the item (recall over a crash); the LLM tie-break (if
        # enabled) only fires for keyword-ambiguous items.
        topical: list[Any] = []
        for c in candidates:
            try:
                keep = await filters.passes_topic_gate_async(c, topic_rules, session=session)
            except Exception:
                _logger.warning(
                    "screentime: topic gate failed for %r — keeping (failsafe)",
                    c.title[:60],
                    exc_info=True,
                )
                keep = True
            if keep:
                topical.append(c)
            else:
                report.dropped_off_topic += 1
        report.topic_relevant = len(topical)

        # 4. "Real moves" filter.
        real_moves = []
        for c in topical:
            if filters.is_real_move(c, rules):
                real_moves.append(c)
            else:
                report.dropped_not_real_move += 1
        report.real_moves = len(real_moves)

        # 5. Classify + 6. Store (per-signal failure-safe).
        for c in real_moves:
            try:
                classification = await classify_signal(
                    c, rules, session=session, topic_rules=topic_rules
                )
            except Exception:
                _logger.warning("screentime: classify failed for %r", c.title[:60], exc_info=True)
                continue
            if classification.served_by:
                report.providers_used[classification.served_by] = (
                    report.providers_used.get(classification.served_by, 0) + 1
                )
            try:
                inserted = await repository.store_signal(session, c, classification)
            except Exception:
                _logger.warning("screentime: store failed for %r", c.title[:60], exc_info=True)
                continue
            if inserted:
                report.stored_new += 1
                # Brief 2: immediate big-move alert to #policy-watch. Gated on
                # the report channel being set (dormant otherwise) and fully
                # failure-safe (never raises out of the sweep). Only fires for
                # NEWLY stored signals so a duplicate re-run can't re-alert.
                try:
                    from artemis.config import settings as _settings

                    if deliver_alerts and _settings.screentime_report_channel:
                        from artemis.screentime.reporting import (
                            maybe_alert_big_move_by_hash,
                        )

                        await maybe_alert_big_move_by_hash(session, c.content_hash)
                except Exception:
                    _logger.warning(
                        "screentime: big-move alert hook failed for %r",
                        c.title[:60],
                        exc_info=True,
                    )
            else:
                report.duplicates += 1

        await session.flush()

        # 7. Recompute per-state rollup.
        try:
            report.states_rolled_up = await repository.recompute_state_stance(session, rules)
        except Exception:
            _logger.warning("screentime: state rollup failed", exc_info=True)

        # 8. Retention window.
        try:
            from artemis.config import settings

            report.expired = await repository.expire_old_signals(
                session, settings.screentime_retention_days
            )
            if report.expired:
                # Trimmed the set → recompute rollup so it stays consistent.
                report.states_rolled_up = await repository.recompute_state_stance(session, rules)
        except Exception:
            _logger.warning("screentime: retention expiry failed", exc_info=True)

    except Exception as exc:  # absolute outer guard — cron must never see a raise
        report.error = str(exc)
        _logger.exception("screentime: run_screentime_pipeline outer failure")
    return report


async def run_scheduled() -> dict[str, Any]:
    """Cron entry point — opens its own session, runs a sweep, commits. Never raises."""
    try:
        from artemis.db import SessionLocal

        async with SessionLocal() as session:
            # COLLECTION ONLY — deliver_alerts=False suppresses any Slack push even
            # though screentime_report_channel is set. Owner decision: no auto-push;
            # Callie reports on-demand + a deliberate digest can be wired separately.
            report = await run_screentime_pipeline(session, deliver_alerts=False)
            await session.commit()
            _logger.info("screentime: scheduled run complete: %s", report.as_dict())
            return report.as_dict()
    except Exception as exc:  # pragma: no cover - cron guard
        _logger.exception("screentime: run_scheduled failed")
        return {"error": str(exc)}


async def run_board_sweep() -> dict[str, Any]:
    """Weekly, SEPARATE board-peer-validation sweep — bounded concurrency.

    2026-07-11 decoupling: the board scout (BoardDocs fetch + an LLM
    classification call per district) is too slow to share the daily 10-minute
    fast sweep (``run_scheduled`` — legislative + national_news + regional_news
    only). This runs ``_gather_board_peer_validation_concurrent`` (one scout
    instance per district, gathered under an ``asyncio.Semaphore`` capped at
    ~5 concurrent districts — see ``scout_fanout.py``) and feeds the result
    through the SAME normalize → dedupe → topic-gate → real-moves → classify →
    store pipeline as the daily sweep (``run_screentime_pipeline``), into the
    SAME ``screentime_signals`` table. Silent — ``deliver_alerts=False`` always
    (no Slack push from this path; matches the daily collection-only cron).
    Cron-safe: never raises, opens/commits its own session.
    """
    try:
        from artemis.db import SessionLocal
        from artemis.screentime.scout_fanout import (
            _gather_board_peer_validation_concurrent,
        )

        board_findings = await _gather_board_peer_validation_concurrent()

        async with SessionLocal() as session:
            report = await run_screentime_pipeline(
                session, findings=board_findings, deliver_alerts=False
            )
            # run_screentime_pipeline labels injected findings "injected" —
            # relabel for an accurate run report/log line.
            report.source_status = {"board_peer_validation": f"ok:{len(board_findings)}"}
            await session.commit()
            _logger.info("screentime: board sweep complete: %s", report.as_dict())
            return report.as_dict()
    except Exception as exc:  # pragma: no cover - cron guard
        _logger.exception("screentime: run_board_sweep failed")
        return {"error": str(exc)}


def register_screentime_schedule(scheduler: Any) -> None:
    """Register the cron job on an APScheduler instance. Idempotent.

    Registers ONLY the collection sweep (``run_scheduled`` — gather →
    normalize → topic-gate → classify → store). This is the data-collection
    path; it does NOT wire any digest/alerting delivery. The only thing that
    could ever push a message to Slack from a sweep is the per-signal
    big-move-alert hook inside ``run_screentime_pipeline`` itself, and that
    stays dormant unless ``settings.screentime_report_channel`` is explicitly
    set (empty by default — owner decision, see artemis/config.py). Wiring
    that channel is a separate, deliberate step, not part of this schedule.

    Call from the FastAPI lifespan (Brief 3 / wiring) — not invoked here so this
    module imports cleanly without side effects.
    """
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    from artemis.config import settings

    trigger = CronTrigger.from_crontab(
        settings.screentime_cron, timezone=settings.screentime_cron_tz
    )
    scheduler.add_job(
        run_scheduled,
        trigger=trigger,
        id="screentime.watch.sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _logger.info(
        "screentime: registered cron %s (%s)", settings.screentime_cron, settings.screentime_cron_tz
    )


def register_board_sweep_schedule(scheduler: Any) -> None:
    """Register the SEPARATE, weekly board-peer-validation sweep. Idempotent.

    2026-07-11 decoupling: deliberately its OWN job (id
    ``screentime.watch.board_sweep``), OWN weekly cron
    (``settings.screentime_board_sweep_cron``, default Sunday noon UTC —
    day-of-week given by NAME, never numeric, sidestepping this repo's
    APScheduler cron day-of-week gotcha), and its OWN entry point
    (``run_board_sweep``) — completely decoupled from the daily
    ``register_screentime_schedule``/``run_scheduled`` fast path. Silent (no
    Slack; ``run_board_sweep`` always passes ``deliver_alerts=False``).

    Call alongside ``register_screentime_schedule`` from the FastAPI lifespan
    — not invoked here so this module imports cleanly without side effects.
    """
    from apscheduler.triggers.cron import CronTrigger

    from artemis.config import settings

    trigger = CronTrigger.from_crontab(
        settings.screentime_board_sweep_cron, timezone=settings.screentime_cron_tz
    )
    scheduler.add_job(
        run_board_sweep,
        trigger=trigger,
        id="screentime.watch.board_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _logger.info(
        "screentime: registered board sweep cron %s (%s)",
        settings.screentime_board_sweep_cron,
        settings.screentime_cron_tz,
    )


_scheduler: AsyncIOScheduler | None = None


def get_screentime_scheduler() -> AsyncIOScheduler:
    """Return the singleton Screen-Time Watch scheduler, creating it if needed.

    Dedicated scheduler instance (mirrors artemis/memory/scheduler.py,
    artemis/meetings/scheduler.py, etc.) — deliberately NOT the shared
    pipelines scheduler (artemis/pipelines/scheduler.py), which drives
    DB-defined Pipeline rows via the PipelineExecutor. The Screen-Time Watch
    sweep is a dedicated in-process runner (see module docstring for why), so
    it gets its own APScheduler instance instead.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_screentime_scheduler() -> None:
    """Start the Screen-Time Watch scheduler and register both jobs: the daily
    collection sweep AND the separate weekly board-peer-validation sweep. Call
    from the FastAPI lifespan startup, alongside the other schedulers.
    Idempotent — safe to call more than once.
    """
    scheduler = get_screentime_scheduler()
    register_screentime_schedule(scheduler)
    register_board_sweep_schedule(scheduler)
    if not scheduler.running:
        scheduler.start()
        _logger.info("screentime: scheduler started")


def stop_screentime_scheduler() -> None:
    """Stop the scheduler. Call from the FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _logger.info("screentime: scheduler stopped")
    _scheduler = None
