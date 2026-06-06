# Cost Phase 6 — Soft alerts + per-feature budgets

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-6-alerts-budgets`
**Browser smoke owner:** Lead, post-merge — configure a budget, simulate breach, verify alert fires in-app + persists across reloads.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~280 + 1 alembic migration (budgets table + endpoints + tab + alert hook + tests).
**Priority:** MEDIUM-LOW — final phase. Ships only after Phases 1-5 land + at least 2 weeks of `cost_events` history exists to set thresholds against.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** Phase 5 merged + at least 2 weeks of post-Phase-1 data so thresholds aren't set blindly.

---

## Why this exists

The audit found that the Node prototype had no budgets or alerts — costs could spike without any warning. Phase 6 adds:

- **Per-scope soft budgets** (app-wide, per-source-bucket, per-model)
- **Threshold-based alerts** when daily/weekly/monthly spend crosses configured ceilings
- **In-app banner notifications** when alerts fire (with optional Slack/email hook if external notifications are already wired)

"Soft" means advisory — never hard caps on actual API calls. The system warns; it doesn't block.

---

## Scope

### Part A — Schema migration

NEW alembic migration. ONE new table:

```python
class CostBudget(Base):
    __tablename__ = "cost_budgets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Scope of the budget
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)    # 'app' | 'feature_tag' | 'model' | 'provider'
    scope_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. 'agent_run' or 'claude-sonnet-4-6'; null for scope_kind='app'

    # Threshold
    period: Mapped[str] = mapped_column(Text, nullable=False)        # 'daily' | 'weekly' | 'monthly'
    threshold_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # State
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_value", "period", name="uq_cost_budgets_scope_period"),
    )
```

One budget per (scope_kind, scope_value, period). Re-creating the same scope+period updates the threshold rather than creating duplicates.

### Part B — Backend endpoints

NEW: `artemis/routes/costs_budgets.py`

```
GET    /api/costs/budgets                  — list all budgets
POST   /api/costs/budgets                  — create or update (upsert by scope+period)
DELETE /api/costs/budgets/{id}             — deactivate (sets active=false, doesn't hard-delete)
GET    /api/costs/budgets/status           — current state: which budgets are within / approaching / breached
```

`/status` response shape:

```json
[
  {
    "id": 1,
    "scope_kind": "app",
    "scope_value": null,
    "period": "monthly",
    "threshold_usd": 100.0,
    "current_usd": 87.40,
    "share": 0.874,
    "state": "approaching",  // 'within' (<80%) | 'approaching' (80-100%) | 'breached' (>100%)
    "last_alerted_at": null
  },
  ...
]
```

States:
- `within` — current usage < 80% of threshold
- `approaching` — 80% to 100%
- `breached` — >100%

### Part C — Alert hook

NEW: `artemis/costs/alerts.py`

A small function `check_budgets_and_alert(session)` that:
1. Computes current usage for every active budget.
2. For any budget in `approaching` or `breached` state where `last_alerted_at IS NULL OR last_alerted_at < {period_start_of_now}`:
   - Inserts an `app_notification` row (existing notifications table if present; else a new lightweight log table — Worker confirms which).
   - Updates `last_alerted_at = now()`.
3. Returns the list of alerts fired.

Trigger: this function runs on a schedule. Two options:
- **APScheduler** — add a job running every 30 minutes (uses existing scheduler in `artemis/memory/scheduler.py` or a new top-level scheduler if separation is cleaner).
- **On-demand** — fire after every `record_cost_event` write. Cheaper to wire but possibly chatty.

Worker chooses based on app scheduler infrastructure; Phase 6 recommends the APScheduler path for predictability. Documents the choice in the PR.

Optional Slack/email path: if `ARTEMIS_SLACK_WEBHOOK` (or similar env var) is set, also POST the alert to Slack. Otherwise, in-app banner only. Worker checks `artemis/integrations/` for an existing Slack hook to reuse; if it exists, plug in; if not, in-app only and skip the Slack wiring.

### Part D — Budgets tab UI

Replace the placeholder in `cost-shell.js` for the Budgets tab.

UI:

```
Budgets

Status:
  App monthly                Threshold: $100.00   Current: $87.40 (87%)   Approaching
  ──────────────────────────────────────────────────────────────────────────────────
  Agents monthly             Threshold: $50.00    Current: $42.00 (84%)   Approaching
  ──────────────────────────────────────────────────────────────────────────────────
  Sonnet 4.6 weekly          Threshold: $20.00    Current: $14.00 (70%)   Within

[+ New budget]

Configured budgets:
  App · monthly · $100.00       [Edit] [Remove]
  Feature: Agents · monthly · $50.00      [Edit] [Remove]
  Model: claude-sonnet-4-6 · weekly · $20.00   [Edit] [Remove]

When budgets fire alerts:
  Last 7 days:
    2026-06-04  App monthly approached $100 (was at $82)
    2026-05-29  Agents monthly approached $50 (was at $42)
```

"New budget" opens a small modal/form:

```
Scope:    [App ▼] [Feature ▼ Agents] [Model ▼ Sonnet 4.6] [Provider ▼ Anthropic]
Period:   [Monthly ▼] [Weekly] [Daily]
Threshold: $[100.00]

[Cancel] [Save]
```

In-app notification surface: when an alert fires, an existing banner/notification component shows:

> "Cost alert: App monthly budget approaching ($87.40 of $100.00)"

With a "Dismiss" + "Open Cost page" action. Use existing notification infrastructure (search for `notifications.py` / `notification-card.js`) — don't build a new banner from scratch.

### Part E — Tests

`artemis/routes/tests/test_budgets.py` (new):

1. **Create budget via POST.** Verify row inserts.
2. **Upsert by (scope, period).** POST same scope+period twice → row count = 1, threshold updates.
3. **DELETE deactivates.** POST → DELETE → GET shows `active=false`, no hard delete.
4. **Status reports correct state.** Seed events that produce 50% / 85% / 110% usage. Verify three budgets return `within`/`approaching`/`breached`.

`artemis/costs/tests/test_alerts.py` (new):

5. **Alert fires on breach.** Seed budget + events crossing threshold. Run `check_budgets_and_alert`. Verify one notification row.
6. **Alert doesn't re-fire within same period.** Run check_budgets_and_alert twice in a row. Second call produces zero new alerts.
7. **Within state doesn't fire.** Seed budget at 50% usage. Verify no alert.

---

## Files owned

- NEW: `alembic/versions/00XX_add_cost_budgets_table.py`
- EDIT: `artemis/costs/models.py` (add `CostBudget` ORM)
- NEW: `artemis/costs/alerts.py`
- NEW: `artemis/routes/costs_budgets.py`
- EDIT: `artemis/main.py` (register router)
- EDIT: `artemis/memory/scheduler.py` (or wherever the central scheduler lives) — add the budget-check job
- EDIT: `public/js/features/cost-shell.js` (replace Budgets placeholder)
- EDIT: `public/css/panels/cost.css` (budget rows, new-budget form, alert banner integration)
- NEW: `artemis/routes/tests/test_budgets.py`
- NEW: `artemis/costs/tests/test_alerts.py`

---

## Acceptance criteria

1. **Migration applies cleanly.** `\d cost_budgets` shows the table. **Paste.**
2. **Backend tests pass.** **Paste.**
3. `./scripts/check.sh` passes. **Paste.**
4. **Live smoke (Lead does post-merge with a seeded DB):**
   - Open Cost page → Budgets tab.
   - Create a monthly app budget of $1 (low threshold to force a breach).
   - Verify Budget status updates to "breached" (current month spend > $1 assumed).
   - Run alert check: `uv run python -c "import asyncio; from artemis.db import get_session; from artemis.costs.alerts import check_budgets_and_alert; asyncio.run(... ))"` — verify one alert fires.
   - Verify in-app banner appears (reload page if needed).
   - Verify a second run produces no new alert (within-period suppression).
   - Edit budget to $1000, verify status flips to "within".
   - Delete budget, verify it disappears from Configured list but the row still has `active=false` in DB.
   - **Paste screenshots of the populated Budgets tab + alert banner.**
5. `git diff --stat`. **Paste.**

---

## Hard constraints

- **Soft alerts only.** No code path blocks or throttles LLM calls based on budget state. Hard caps would be a separate, more dangerous design.
- **Within-period suppression.** A budget fires at most once per period (daily / weekly / monthly). `last_alerted_at` is checked against the current period start.
- **No hard delete on budgets.** `active=false` is the retirement state. Same lossless discipline as memory.
- **Use existing notification infrastructure** — don't reinvent banners. Search `artemis/routes/notifications.py` + `public/js/components/notification-card.js` and plug in.
- **Slack/email is optional.** If wiring doesn't exist, skip it cleanly. In-app banner is mandatory.
- **Thresholds are user-set, not auto-suggested.** Phase 6 doesn't try to suggest budgets — Jon sets them based on what the Spend tab shows him.
- **Local-only git.** Worker on `worker/cost-phase-6-alerts-budgets`; Lead merges after the smoke checklist clears.
