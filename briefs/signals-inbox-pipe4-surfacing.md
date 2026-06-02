# Signals Inbox PIPE4 Surfacing — Pipeline-Generated Signals in Tree View

**Owner:** Codex (paste-ready)
**Branch:** `codex/signals-inbox-pipe4-surfacing`
**LOC budget:** ~180 (cap 240)
**Depends on:** PIPE4 merged; OPS-UI-3 (Signals Inbox tree refresh) merged.

## Why

Signals Inbox shows nothing right now because scouts produce no signals (M5b stubs). Once real scouts ship, signals will flow into `signal_queue` — but the Inbox should also surface pipeline-run-level context (which pipeline_run produced which signal, what state in the M3 state machine, what brief was composed).

Per Jon's call: (c) tree grouping — surface pipeline-generated context as part of the existing tree view, with reason_code / state / geography grouping working over the combined surface.

## Scope

### Inbox shows pipeline_run context per signal

When a signal_queue row has a `pipeline_run_id` field (or equivalent join), the Inbox card shows:
- Pipeline name + Run ID (truncated)
- Run started_at + status (running / awaiting_approval / succeeded)
- "View pipeline run →" link

For signals without pipeline_run linkage (manual signals, legacy data): existing card render unchanged.

### Schema check / addition if needed

Signal_queue may not have a `pipeline_run_id` column yet. If missing:
- Add migration: `signal_queue.pipeline_run_id` UUID NULL FK → pipeline_runs
- Scout adapters write this when they emit signals during pipeline execution
- Backfill is N/A (no real signals exist)

### Approval state surfacing on signal cards

When a signal has been included in a brief that's awaiting Gate 1 approval:
- Card shows badge: "🔒 Awaiting Gate 1" (or similar)
- "View approval →" link

Implementation: query approval rows by metadata.context.signal_ids (per the approval-card-pipe4-context brief's metadata shape).

### Empty state when scouts produce nothing

Inbox currently empty. Add a more informative empty state:
- "No signals yet. Scouts run on the marketing pipeline's schedule."
- Two CTAs:
  - "Trigger marketing pipeline manually →" (deep-link to Pipelines → Marketing Pipeline → Run button)
  - "Configure scout connectors →" (deep-link to Connections panel)
- If there ARE pipeline_runs in succeeded/skipped state but no signals: "Last 3 pipeline runs produced 0 signals. Configure scout connectors (Starbridge, etc.) to start ingesting data."

### Tree grouping with pipeline context

OPS-UI-3 grouping modes (State / Reason Code / Geography / Urgency / Flat) all continue to work. New grouping mode added:
- **By Pipeline Run** — groups signals by which pipeline_run produced them. Useful for "what happened in last night's run?" debugging.

### Out of scope

- Real-time updates when scouts emit signals (relies on Signals Inbox refresh which happens on user action)
- Detailed scout-by-scout breakdown ("Starbridge produced 3, regional_news produced 0...") — bank for run history brief
- Filtering by pipeline_run_id via URL param. Can deep-link if useful later.

## Tests

- Signal with `pipeline_run_id` populated → card shows pipeline name + run badge + "View run" link
- Signal without `pipeline_run_id` → existing card render
- "By Pipeline Run" grouping mode renders correctly
- Empty state when 0 signals + 0 pipeline_runs → standard empty state
- Empty state when 0 signals + N pipeline_runs in succeeded/skipped → contextualized empty state with connector setup CTA

## Files

| File | LOC |
|---|---|
| `alembic/versions/<rev>_signal_queue_pipeline_run_link.py` (only if needed) | ~40 |
| `artemis/marketing/models.py` (add pipeline_run_id field) | ~10 |
| `artemis/marketing/scout_runner.py` (write pipeline_run_id on emit) | ~10 delta |
| `public/js/components/signal-tree.js` (pipeline grouping mode, pipeline_run badge) | ~50 delta |
| `public/js/features/marketing-os.js` (empty state copy + CTAs) | ~50 delta |
| `public/css/features/marketing-os.css` | ~30 delta (pipeline badge styling) |
| Tests | ~30 |

**Total: ~220 LOC.** Cap 240.

## Invariants

- node --check on JS
- conftest hard-fail on non-test DB
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit
- Backward-compatible: signals without pipeline_run_id render unchanged

## Report

git diff --stat, screenshots (empty state with no runs / empty state with runs / signal card with pipeline badge / By Pipeline Run grouping), test pass count, branch.
