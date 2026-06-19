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

from artemis.screentime import filters
from artemis.screentime.classifier import classify_signal
from artemis.screentime.scout_fanout import gather_national_findings
from artemis.screentime.stance_config import load_stance_rules

_logger = logging.getLogger(__name__)


@dataclass
class RunReport:
    gathered: int = 0
    normalized: int = 0
    deduped: int = 0
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
    """
    report = RunReport()
    try:
        from artemis.screentime import repository

        rules = await load_stance_rules(session)
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

        # 4. "Real moves" filter.
        real_moves = []
        for c in candidates:
            if filters.is_real_move(c, rules):
                real_moves.append(c)
            else:
                report.dropped_not_real_move += 1
        report.real_moves = len(real_moves)

        # 5. Classify + 6. Store (per-signal failure-safe).
        for c in real_moves:
            try:
                classification = await classify_signal(c, rules, session=session)
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

                    if _settings.screentime_report_channel:
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
            report = await run_screentime_pipeline(session)
            await session.commit()
            _logger.info("screentime: scheduled run complete: %s", report.as_dict())
            return report.as_dict()
    except Exception as exc:  # pragma: no cover - cron guard
        _logger.exception("screentime: run_scheduled failed")
        return {"error": str(exc)}


def register_screentime_schedule(scheduler: Any) -> None:
    """Register the cron job on an APScheduler instance. Idempotent.

    Call from the FastAPI lifespan (Brief 3 / wiring) — not invoked here so this
    module imports cleanly without side effects.
    """
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    from artemis.config import settings

    trigger = CronTrigger.from_crontab(settings.screentime_cron, timezone=settings.screentime_cron_tz)
    scheduler.add_job(
        run_scheduled,
        trigger=trigger,
        id="screentime.watch.sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _logger.info("screentime: registered cron %s (%s)", settings.screentime_cron, settings.screentime_cron_tz)
