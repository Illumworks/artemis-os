"""Screen-Time Watch — national policy/legislation intelligence pipeline.

A self-contained, isolated namespace that tracks instructional screen-time
legislation and policy across all 50 states. It reuses the existing scout
building blocks (read-only, in-process) but stores everything in its OWN
``screentime_*`` tables — never the marketing ``SignalQueue`` tables.

Brief 1 (this package) is the engine:
  national scout fan-out → dedupe + "real moves" filter → config-driven
  stance + Amira-angle classification (tool-less provider) → store signals
  → recompute per-state stance rollup, run on a cron-safe runner.

Public surface (stable for Briefs 2 & 3):
  - ``models``           — ORM tables (screentime_signals, screentime_state_stance,
                            screentime_stance_config).
  - ``repository``       — store/dedupe/rollup/purge/retention helpers.
  - ``classifier``       — stance + Amira-angle classification (cheap provider).
  - ``filters``          — the "real moves" gate.
  - ``stance_config``    — tunable stance rules (config-driven).
  - ``runner``           — the cron-safe pipeline runner.
  - ``pipeline_seed``    — registers a Pipeline row for pipelines-page visibility.
"""

from __future__ import annotations

PIPELINE_ID = "screentime.watch"

__all__ = ["PIPELINE_ID"]
