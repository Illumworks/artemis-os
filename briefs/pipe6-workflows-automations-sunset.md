# PIPE6 — Workflows + Automations sunset + auto-migrate to Pipelines (execute D6 lock)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`) OR Codex (Codex-friendly: well-specified backend + light frontend)
**Target branch:** `worker/pipe6-sunset`
**Browser smoke owner:** Lead, post-merge — open Operations sidebar, verify Automations + Workflows tabs are gone; verify any migrated row exists in `pipelines`/`pipeline_runs`; verify existing /api/automations and /api/workflows routes return 410 Gone (or are removed cleanly).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400 (migration + route removal + UI nav update + auto-migration logic + tests).
**Priority:** HIGH — executes D6 lock from the original master plan. Workflows + Automations have been marked "Sunsetting in PIPE6" in SITE-MAP.md since 2026-05-22. Per master plan priority order: Phase BH ✅ → Signal Playbook ✅ → **PIPE6** → CC12 → Marketing flow audit.

---

## Why this exists — the D6 architectural lock

From `docs/ARTEMIS-OS-MASTER-PLAN.md`:

> **D6 — Pipeline is the unified orchestration primitive.**
> Workflows, Chains, DAGs, and Automations all reduce to directed graphs of operations with optional triggers. We ship a single `Pipeline` concept that subsumes all four. Existing primitives either get auto-migrated to Pipeline rows (PIPE6) or sunset entirely.

**D6 was locked weeks ago.** PIPE6 is the implementation that delivers it. Today (2026-05-30):

- `automations` table: **0 rows**, `automation_runs` table: **0 rows**. Zero production usage.
- `workflows` table: **1 row** ("Codex Smoke Workflow"), `workflow_runs` table: **3 rows**. Light usage, no real business reliance.
- Both tabs exist in the Operations sidebar — UI clutter that's been "sunsetting in PIPE6" for 8 days per SITE-MAP.md.

PIPE6 removes both tabs from the UI + retires the route handlers + auto-migrates the 1 existing workflow to a Pipeline equivalent.

---

## Scope

### Part A — Auto-migrate the 1 existing workflow to a Pipeline

The single workflow (`Codex Smoke Workflow`) should be auto-migrated to a `pipelines` row with equivalent semantics:

- Read the workflow's node graph + trigger config from `workflows` + `workflow_runs`
- Construct equivalent `pipelines` row + `pipeline_runs` (if any are worth preserving) + node states JSONB
- Write a memory observation noting the migration (per MC carryover pattern, scope `workspace:platform`, category `pipe6_migration`)
- Keep the original `workflows` row + 3 `workflow_runs` rows in DB (lossless invariant — never delete)

**Migration script in `alembic/versions/0053_pipe6_workflows_automations_migrate.py`:**

```python
def upgrade():
    # 1. Auto-migrate workflows to pipelines (1 row in current prod DB)
    op.execute("""
        INSERT INTO pipelines (id, name, definition_json, created_at, updated_at)
        SELECT 
            'migrated-from-workflow-' || w.id::text,
            w.name || ' (migrated from workflow)',
            w.definition_json,  -- verify schema compatibility
            w.created_at,
            now()
        FROM workflows w
        WHERE NOT EXISTS (
            SELECT 1 FROM pipelines p 
            WHERE p.id = 'migrated-from-workflow-' || w.id::text
        )
    """)
    
    # 2. Mark workflows table as deprecated (don't drop — lossless)
    op.execute("""
        COMMENT ON TABLE workflows IS 
            'DEPRECATED 2026-05-30 PIPE6 — superseded by pipelines table (D6 lock). 
            Rows preserved per lossless invariant. Do not write new rows.'
    """)
    op.execute("""
        COMMENT ON TABLE workflow_runs IS 
            'DEPRECATED 2026-05-30 PIPE6 — superseded by pipeline_runs. 
            Rows preserved. Do not write new rows.'
    """)
    op.execute("""
        COMMENT ON TABLE automations IS 
            'DEPRECATED 2026-05-30 PIPE6 — never had production usage. 
            Empty table preserved. Schema may be dropped in a future migration after grace period.'
    """)
    op.execute("""
        COMMENT ON TABLE automation_runs IS 
            'DEPRECATED 2026-05-30 PIPE6 — never had production usage. 
            Empty table preserved.'
    """)

def downgrade():
    # Remove migrated pipelines + uncomment tables. Migrated pipeline_runs (if any
    # were created during the migration window) stay (lossless on downgrade).
    op.execute("""
        DELETE FROM pipelines WHERE id LIKE 'migrated-from-workflow-%'
    """)
    op.execute("COMMENT ON TABLE workflows IS NULL")
    op.execute("COMMENT ON TABLE workflow_runs IS NULL")
    op.execute("COMMENT ON TABLE automations IS NULL")
    op.execute("COMMENT ON TABLE automation_runs IS NULL")
```

**Note on schema compatibility:** verify that `workflows.definition_json` shape maps cleanly to `pipelines.definition_json`. If they differ structurally, the migration may need a transform step. Worker investigates before writing the migration body. If shapes are incompatible, document in the report + propose either: (a) richer transform logic, or (b) skip auto-migration since the 1 workflow is just a smoke test.

### Part B — Route deprecation (return 410 Gone)

`artemis/automations/routes.py` and `artemis/routes/builders/workflows.py`:

Option 1 (preferred): replace the route bodies with 410 Gone responses:

```python
@router.get("/api/automations")
async def list_automations() -> dict:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "automations_deprecated",
            "message": "The Automations surface was sunset in PIPE6 (2026-05-30). "
                       "Use Pipelines with trigger nodes instead. "
                       "See docs/ARTEMIS-OS-MASTER-PLAN.md D6 lock for rationale.",
            "redirect_to": "/api/pipelines",
        },
    )
```

Apply the same pattern to all `/api/automations/*` and `/api/workflows/*` endpoints. Any UI that still calls them sees a clear 410 with redirect guidance — no silent failures, no mysterious 404s.

Option 2 (alternative): physically delete the route files. Cleaner long-term but risks breakage if some path still references them. Worker decides — recommendation: 410 for ~3 months, then physical removal in a follow-up brief once grace period confirms no breakage.

### Part C — Remove tabs from UI

In `public/js/features/operations-shell.js`:

Find the Operations sidebar nav rendering. Remove the `Automations` and `Workflows` entries. Verify the sidebar still renders cleanly with the remaining entries (Skills, Pipelines, Agents, Memory).

Also verify there are no other entry points to Automations / Workflows screens (deep links, breadcrumbs, dashboard widgets). Grep for `/#/automations`, `/#/workflows`, `Automations`, `Workflows` and remove all surface entries.

The route handlers stay (returning 410) for any deep-links that might still exist externally; the UI just no longer offers entry points.

### Part D — Update SITE-MAP.md

Per the existing entries:

> **Automations** | Legacy registry of scheduled triggers. **Sunsetting in PIPE6** — automations become Pipelines with trigger nodes.
> **Workflows** | Legacy sequential recipes. **Sunsetting in PIPE6** — workflows become Pipelines with sequential edges.

Remove these rows from the Operations table. Add a `## Deprecated surfaces` section at the bottom noting:

```
## Deprecated surfaces (sunset in PIPE6, 2026-05-30)

- **Automations** — never had production usage. Routes return 410. Empty tables 
  preserved per lossless invariant. Use Pipelines with trigger nodes instead.
- **Workflows** — light usage (1 workflow + 3 runs migrated to Pipelines via 
  migration 0053). Routes return 410. Rows preserved per lossless invariant. 
  Use Pipelines with sequential edges instead.
```

### Part E — MC-style memory observation for the migration event

Per the carryover pattern (MC1-MC5): write a memory observation noting the migration happened. Scope `workspace:platform`, category `pipe6_migration`. Content includes: D6 lock reference, count of migrated workflows, list of deprecated routes, timestamp.

This becomes part of the platform's durable history — future operators (or Builder) can search for "why are there no Automations / Workflows tabs?" and find the answer in memory.

Hooked from the migration's upgrade() function, or as a separate startup-side helper that fires once after the migration applies. Failure-isolated per existing MC patterns.

### Part F — Tests

`artemis/marketing/tests/test_pipe6_sunset.py` (or wherever fits the codebase pattern):

1. **Migration 0053 applies cleanly.** Verify `pipelines` row count grew by exactly 1 (the migrated workflow).
2. **Backfill verification.** Query the migrated pipeline; verify it has the expected `name` (with " (migrated from workflow)" suffix) + the workflow's original `definition_json`.
3. **Original workflows row preserved.** Lossless: original `workflows` row still in DB after migration.
4. **Routes return 410.** Mock client GET `/api/automations` → 410 with the expected error message + redirect_to hint. Same for `/api/workflows` and a sample sub-route each.
5. **Memory observation lands.** After migration, query `memory_observations WHERE category='pipe6_migration'` — verify exactly 1 row in scope `workspace:platform`.
6. **UI nav rendering excludes Automations + Workflows.** JS smoke test OR Python integration that renders the sidebar HTML — verify the strings "Automations" and "Workflows" do not appear (where they shouldn't).
7. **Pipelines unchanged.** Regression: existing pipeline tests still pass, pipeline runs still work.

---

## Files owned

- NEW: `alembic/versions/0053_pipe6_workflows_automations_migrate.py`
- EDIT: `artemis/automations/routes.py` (replace bodies with 410)
- EDIT: `artemis/routes/builders/workflows.py` (replace bodies with 410)
- EDIT: `public/js/features/operations-shell.js` (remove Automations + Workflows nav entries)
- EDIT: `public/js/core/api.js` (remove or deprecate automation/workflow API wrappers — may safely stay if hooked off the now-410 endpoints)
- EDIT: `public/js/core/navigation.js` (if explicit route handlers reference automations/workflows screens)
- EDIT: `docs/SITE-MAP.md` (update per Part D)
- POSSIBLE EDIT: `artemis/builder/memory_carryover.py` (add MC-style write_pipe6_migration_observation helper)
- NEW: `artemis/marketing/tests/test_pipe6_sunset.py` (or `artemis/automations/tests/...` — match codebase pattern)

---

## Acceptance criteria

1. `uv run alembic upgrade head` shows `0053_pipe6_workflows_automations_migrate`. **Paste.**
2. **Migrated pipeline exists:** `SELECT id, name FROM pipelines WHERE id LIKE 'migrated-from-workflow-%';` returns exactly 1 row. **Paste.**
3. **Lossless verification:** `SELECT COUNT(*) FROM workflows;` still returns 1 (original row preserved). Same for `workflow_runs` (still 3). **Paste.**
4. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_pipe6_sunset.py -v` — all 7 tests pass. **Paste.**
5. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation). **Paste.**
6. **Routes return 410:** `curl -i http://localhost:8000/api/automations` returns 410 with redirect_to hint. Same for `/api/workflows`. **Paste curl output.**
7. **Manual UI smoke (Lead does this post-merge):**
   - Open Operations sidebar in browser
   - Verify Automations + Workflows tabs are GONE
   - Verify other tabs (Skills, Pipelines, Agents, Memory) still render
   - **Paste screenshot or DOM snippet.**
8. **Memory observation landed:** `SELECT id, content FROM memory_observations WHERE category='pipe6_migration';` returns 1 row. **Paste.**
9. `git diff --stat` + `git log --oneline -1` on `worker/pipe6-sunset`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** Existing `workflows` + `workflow_runs` rows NEVER deleted. Migration is additive: creates new `pipelines` row mirroring the workflow.
- **No physical table drops in this brief.** The 4 deprecated tables (automations / automation_runs / workflows / workflow_runs) stay in schema with COMMENT marking them deprecated. Physical drop is a separate future migration after grace period (~3 months).
- **Routes return 410 not 404.** 410 = "Gone, intentionally" with explanatory error body. 404 = "no idea, never existed." We want 410 for clarity to any UI/script still calling.
- **No regression on Pipelines.** The existing Pipelines surface (marketing pipeline lives there) must work exactly as before.
- **Schema-compat investigation first.** Worker investigates `workflows.definition_json` shape vs `pipelines.definition_json` before writing the migration body. If shapes are incompatible, propose options in the report rather than forcing a broken transform.
- **MC-style memory carryover.** Same pattern as MC1-MC5. Failure-isolated. Migration runs even if memory write fails.
- **Local-only git.** Worker commits on `worker/pipe6-sunset`; merge after Lead approves.

---

## What this brief explicitly DOES NOT do

- Does not drop physical tables (deferred to grace-period-end follow-up)
- Does not touch the `chains` / `dags` substrate (if any exists separately — per D6 the unification is conceptual; these may have already been subsumed)
- Does not add new Pipelines features (trigger nodes, sequential edges) — the master plan implies Pipelines already supports both
- Does not migrate the Automations rows (there are 0 of them)
- Does not retroactively migrate historical `workflow_runs` (3 rows) into `pipeline_runs` — those are historical run records, not active state

---

## Report-back format

```
PIPE6 — Workflows + Automations sunset report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Migration apply confirmation + backfill verification (paste new pipeline row)
4. Lossless verification (paste workflows/workflow_runs counts unchanged)
5. Test pass count (7 new + regressions on pipelines)
6. Route 410 verification (paste curl output)
7. UI verification — sidebar no longer shows Automations + Workflows
8. Memory observation verification (paste row)
9. check.sh summary
10. Anything surprising — especially around workflows.definition_json vs pipelines.definition_json shape compatibility, OR any external script/cron that still calls the deprecated routes
```

---

**Worker: PIPE6 executes the D6 architectural lock that has been pending since the original master plan. After this lands, the Operations sidebar is cleaner; the codebase has fewer competing orchestration concepts; and the platform's single-Pipeline-primitive architecture is structurally enforced. Banked operating discipline: future LLM picking up the codebase sees Pipelines as the only orchestration primitive — no confusion about whether to use Pipelines vs Workflows vs Automations.**
