# PIPE3 — Per-Type Node Config Forms

**Owner:** Sonnet Worker (form design + interaction work)
**Branch:** `worker/pipe3-node-config-forms`
**LOC budget:** ~850 (estimate; honest overrun OK up to ~1100 — five distinct form types is the bulk)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE2 + PIPE2 polish merged (canvas + config drawer exist, drawer currently shows generic JSON).
**Grounded in:** PIPE1's `PipelineNode` TypedDict (node types: `agent_invocation`, `skill_call`, `trigger_manual`, `trigger_scheduled`, `trigger_webhook`, `trigger_event`, `human_gate`, `conditional`, `sub_pipeline`), PIPE2's `pipeline-config-drawer.js` which currently renders generic JSON.

## Why this brief exists

PIPE2 shipped the canvas + config drawer with a **generic JSON textarea** as placeholder editor for any node's config. That's fine for power users but unfriendly. PIPE3 replaces the JSON view with **typed forms per node type**. After PIPE3:

- Click an `agent_invocation` node → searchable agent picker, cost cap input, mode dropdown
- Click a `trigger_scheduled` node → cron picker + timezone + human-readable preview
- Click a `human_gate` node → approver multi-select + timeout input + approval_kind dropdown
- Click a `conditional` node → small predicate builder
- Click a `sub_pipeline` node → searchable pipeline picker

JSON editor remains as a power-user fallback (toggle inside the drawer: "Form / JSON").

This unblocks the demo path: a non-engineer can build a pipeline visually without ever seeing raw JSON.

## Scope

### In scope — five form types

For each: the form replaces the generic JSON textarea when that node type is selected. Saves write back to `node.config` JSONB; PIPE1's schema validation enforces shape on PATCH.

#### 1. `agent_invocation` form

Fields:
- **Agent** (required) — searchable picker fetching from `/api/agents`. Shows agent name + agent_id slug. Persists to `config.agent_id`.
- **Mode** — dropdown: `scheduled` / `manual` / `backfill`. Default `scheduled`. Persists to `config.mode`.
- **Cost cap (USD)** — number input, optional. Default 1.00. Persists to `config.cost_cap_usd`.
- **Provider override** (optional, collapsed by default) — provider + model pickers. If set, overrides the agent's own provider for this pipeline. Persists to `config.provider_override` + `config.model_override`.

#### 2. `trigger_scheduled` form

Fields:
- **Schedule** — cron expression input with a human-readable preview below ("Every 4 hours" for `0 */4 * * *`). Use a small client-side parser; common patterns recognized. Persists to `config.cron`.
- **Timezone** — dropdown of common timezones (America/Chicago, America/Los_Angeles, America/New_York, UTC, Europe/London, etc.). Persists to `config.timezone`.
- **Active dates** (optional) — start_date + end_date inputs. Persists to `config.start_date` / `config.end_date`.
- **Next run preview** — read-only "Next run: 2026-05-22 04:00 CDT" computed client-side from cron + timezone.

#### 3. `human_gate` form

Fields:
- **Approval kind** — dropdown: `signal_brief` / `content_draft` / `policy_decision` / custom string. Persists to `config.approval_kind`.
- **Approvers** — multi-select pulling from a hardcoded list for v1: `josh@amiralearning.com`, `angela@amiralearning.com`, `jon@amiralearning.com`. Free-text fallback (type email + Enter). Persists to `config.approvers` (array).
- **Timeout (hours)** — number input. Default 72. Persists to `config.timeout_hours`.
- **On timeout** — dropdown: `auto_approve` / `auto_reject` / `escalate`. Default `escalate`. Persists to `config.on_timeout`.

#### 4. `conditional` form

Fields:
- **Predicate** — a small expression composer. v1 keeps it simple: dropdown of operators (`equals`, `not_equals`, `greater_than`, `less_than`, `contains`, `in_list`) + left-hand-side dropdown of available signal/context fields + right-hand-side text input. Persists to `config.predicate` as `{op, left, right}` JSONB.
- **True branch label** — text input describing the "if true" path (e.g., "Hot signal — fast lane"). Persists to `config.true_label`.
- **False branch label** — same for "if false". Persists to `config.false_label`.
- For v1 power users: a "Raw JSONLogic" toggle that lets them write more complex predicates as JSONLogic JSON. Future PIPE-extension can replace with full expression builder.

#### 5. `sub_pipeline` form

Fields:
- **Target pipeline** — searchable picker pulling from `/api/pipelines`. Shows pipeline name + node count. Excludes the current pipeline (no self-reference). Persists to `config.pipeline_id`.
- **Mode** — dropdown: `inline` (runs in same execution context) / `async_fire_and_forget` (kicks off, doesn't wait). Default `inline`. Persists to `config.mode`.
- **Pass-through inputs** — read-only preview showing the parent pipeline's outputs that will be available to the sub-pipeline. (For v1: just a placeholder note "Inputs pass through via signal_queue / shared context." Real data-shape passing comes in PIPE4.)

### Form/JSON toggle in drawer

- Top of the drawer: small toggle `Form / JSON` (default Form).
- JSON view is the existing PIPE2 textarea — completely unchanged.
- Toggle preserves edits both ways: edit in JSON, switch to Form → form shows current state; edit in Form, switch to JSON → textarea shows current state.

### Drawer behavior

- Header (unchanged from PIPE2): node label (editable inline), node type subtitle, delete affordance, close button
- Body: the per-type form OR JSON textarea (per toggle)
- Footer (unchanged): Save / Cancel buttons
- Save validates the form locally (required fields, format), then writes `node.config` back to the canvas state, then PATCHes the pipeline. Same flow PIPE2 ships.
- Cancel discards form changes, closes drawer.
- ESC also closes (drops changes — confirm if unsaved? brief decision: NO confirm for v1, user can undo via canvas Ctrl+Z; defer confirm-on-unsaved to later if it bites).

### Out of scope

- `skill_call` form. The Skill picker has different shape needs; defer to a small PIPE3a brief later.
- `trigger_manual`, `trigger_webhook`, `trigger_event` forms. Manual trigger has no config; webhook needs URL generation; event needs event-type registry. Defer.
- Predicate builder full power (multiple ANDs/ORs, nested expressions). v1 is one-line simple predicates; JSONLogic fallback covers power users.
- Form validation beyond required-field + format-of-input. No cross-field "if agent X then must include tool Y" rules — that's runtime concern.
- Auto-save on every keystroke. Save button only.
- Form theming for dark mode. Use existing tokens; whatever theme system exists picks up automatically.

## Invariants

1. **Form save writes back to `node.config` JSONB.** Schema shapes preserved per PIPE1's contract.
2. **JSON view is canonical.** If form can't represent a field (rare, e.g., user added a custom key), the JSON view shows it and Save preserves it. Form view ignores unknown fields but doesn't strip them.
3. **No new node types.** PIPE1's enum is the canonical list.
4. **Picker components** (agent picker, pipeline picker, etc.) are reusable. Extract into `public/js/components/` if they share interaction patterns (search + select + display).
5. **No new design tokens.** Use existing palette.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/pipeline-config-drawer.js` | ~150 delta (toggle + dispatch to per-type form renderer) |
| `public/js/components/node-config-forms/agent-invocation-form.js` (new) | ~150 |
| `public/js/components/node-config-forms/trigger-scheduled-form.js` (new) | ~120 |
| `public/js/components/node-config-forms/human-gate-form.js` (new) | ~130 |
| `public/js/components/node-config-forms/conditional-form.js` (new) | ~140 |
| `public/js/components/node-config-forms/sub-pipeline-form.js` (new) | ~100 |
| `public/css/features/pipelines.css` | ~100 delta (form styles — inputs, dropdowns, multi-select, picker results) |
| `tests/unit/frontend/test_pipe3_node_config_forms.py` (new) | ~150 |

**Total: ~1040 LOC.** Above the 850 estimate but five distinct forms is the bulk. Cap 1100. If you're heading materially over 1100, STOP and ping Lead with the structural reason.

## Test plan

For each of the 5 forms:
1. Click a node of that type → drawer opens with the form (not JSON textarea by default)
2. Fields populate from existing `node.config` (round-trip from prior save)
3. Edit fields → save → PATCH fires → canvas updates → reload → values persist
4. Required field missing → save disabled OR shows inline error
5. Toggle Form ↔ JSON → state preserved both ways
6. Cancel → no PATCH fires; node.config unchanged

Plus:
7. Smoke: open all 16 marketing pipeline nodes one by one — each renders the right form
8. Empty state: new pipeline, new node → form shows with empty/default values

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (Python tests only)
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set before declaring done
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors; existing PIPE2 + PIPE2-polish canvas behavior unchanged (pan, drag, edge tracking, JSON toggle)

## What "done" looks like

1. Click on each of the 5 supported node types → typed form opens (not generic JSON)
2. Each form's fields populate from `node.config`, save writes back, persistence verified
3. Form/JSON toggle preserves state both ways
4. Marketing pipeline's 16 nodes all open with the right typed form
5. PIPE2 + polish behaviors unchanged
6. Tests pass
7. `check.sh` passes within exempt set

## Report Worker submits

1. `git diff --stat` output
2. Screenshots of each of the 5 forms (or at least 3 representative ones)
3. The shape of `node.config` JSONB for each type (paste a sample for each)
4. Test pass count
5. Branch + worktree path
6. Any visual judgment that wasn't pre-specified — flag for Lead

---

**Lead notes (not for Worker):**
- This is the "build a pipeline without seeing JSON" landing. Big demo value.
- The conditional form's predicate builder is the trickiest UX call — keep v1 simple (single one-line predicate + JSONLogic fallback). A full expression builder is its own brief (PIPE-conditional-v2) if it ever ships.
- `skill_call` is intentionally deferred — the Skills surface is itself underdeveloped (no categories/templates/import yet per the audit). When Skills lifecycle (OP2 — Skills polish) lands, that's when `skill_call` form makes sense.
- After PIPE3 + PIPE2 + PIPE2-polish + PIPE1 + PIPE5 = a credible "build, visualize, edit" pipeline experience. Then PIPE4 (execution) makes Run button do something real.
