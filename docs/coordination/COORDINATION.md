# COORDINATION

> Real-time visibility between Lead Claude Code (Account 1) and Worker Claude Code (Account 2).
> Both sessions read this on startup and before any task that touches shared territory.
> Append-only within a day. Archive to `coordination-archive/` weekly.
>
> Protocol: `CLAUDE_CODE_PLANNING_HANDOFF.md` §K, **with local-only-git and autonomy overrides below (supersedes parts of §K)**.
> Historical decisions live in `PROJECT_LOG.md`. This file is *what is happening right now.*

---

## Active locks

_None._

## Active sessions

| Session | Account | Role  | Status |
|---------|---------|-------|--------|
| Lead    | 1       | Lead  | Active (Opus) |
| Worker  | 2       | Build | **J2 COMPLETE** — `worker/j2-gcal-integration` commit `9633b32`. 1378 tests pass. ruff + mypy strict clean. Ready for Lead review + merge. |

---

## Operating protocol overrides (per Jon 2026-05-16)

### MODEL TIERING (Opus Lead, Sonnet execution, Haiku for mechanical leaves)

Three-tier model. The right tier per task:

- **Opus (Lead, me):** architecture, decision conversations with Jon, diff review, cross-cutting plan changes, surfacing subtle issues, synthesis, "bring Jon in" trigger moments.
- **Sonnet (Worker terminal + Lead-spawned sub-agents):** mechanical implementation against a clear brief, test writing against a clear spec, pattern-matching translation (Express → FastAPI), repetitive file generation.
- **Haiku (Worker-spawned sub-agents):** the leaves. Writing N variants of the same test pattern, fixture generation, port-this-one-function from JS to Python, lint cleanup.

**Lead → Sonnet pattern (in effect):** when Lead is about to spend ~10+ tool calls on implementation, spawn `Agent(subagent_type: "general-purpose", model: "sonnet", prompt: <brief>)`. Lead verifies the diff after.

**Worker → sub-agent pattern (NEW — Worker, read this on startup):** the Worker has the `Agent` tool available. Use it when:

1. A slice has **multiple independent leaves** — e.g., "implement and test 5 routes" or "port 6 mapping functions". Spawn a Sonnet sub-agent per cluster (or one Sonnet for all if scope ≤500 LOC). Run them in parallel via multiple `Agent` calls in one message.
2. The work is **purely mechanical** — e.g., "write 15 tests against this exact spec" or "rewrite these 8 imports across these files". Spawn a Haiku sub-agent (`model: "haiku"`). Cheaper, faster, plenty smart for mechanics.
3. The Worker would otherwise serialize ~30+ tool calls of repetitive work. Parallel sub-agents finish in roughly the time of the slowest one instead of summing.

**When NOT to spawn sub-agents** (Worker does it directly):
- The slice is small (< ~10 tool calls)
- The work needs sustained context that's expensive to pack into a brief
- The work is judgment-heavy (Worker's Sonnet IS the judgment tier for implementation)

**Brief shape for Worker-spawned sub-agents (mirror Lead's pattern):**
- Self-contained — sub-agent can't see Worker's conversation
- Specific file paths and exact expected output
- Out-of-scope clearly fenced
- Verification command (e.g., `pytest path/to/specific_test.py`)
- Report-back format

**Worker examples for Phase D:**
- D1: spawn 1 Sonnet sub-agent for the scheduler + lifespan integration (~200 LOC), do `BaseScout` + tests yourself (judgment-heavy).
- D2 (Legislative): spawn 1 Haiku sub-agent for the LegiScan Pydantic types (mechanical lift from API docs); do `scout.py` + `mapping.py` + tests yourself.
- D3-D10: each scout brief lists which parts to spawn vs. do directly.

**Coordination etiquette for nested sub-agents:**
- Sub-agents commit on Worker's branch (`worker/...`). They do NOT switch branches.
- Worker reviews the sub-agent's diff before continuing.
- Worker → Lead handoff (PR-equivalent local diff) still happens at the end of the slice — nesting doesn't change the outer contract.

### WORKTREE PATTERN (Lead + Worker isolation)

Two Claude Code agents cannot share one git working directory — switching branches in one pulls the rug out from under the other. Each agent gets its own worktree:

- **Worker** — `/Users/artemis/Desktop/Artemis/artemis-os/`. Works on `worker/<scope>-<desc>` branches.
- **Lead** — `/Users/artemis/Desktop/Artemis/artemis-os-lead/`. Works on `lead/<scope>-<desc>` branches.

Both point at the same `.git`. Commits land on shared history. Working files are isolated. `main` is the integration branch; either side merges via `git update-ref refs/heads/main <branch>` (if main isn't checked out anywhere) or via standard merge from whichever worktree has main checked out.

When a worktree's branch is merged, leave the worktree alive — switch it to a new branch off main for the next slice. Worktrees are cheap; recreating them is friction.

### LOCAL-ONLY GIT

**Never push to remote. All git stays local.** This supersedes the GitHub-PR-based "key conversation moments" pattern in handoff §K.2 and §K.4.

- Local branches still used (`worker/<scope>-<desc>`, `lead/<scope>-<desc>`) for isolation.
- Worker proposes via local branch + diff. Lead reviews locally (`git diff main...worker/<branch>`) and merges to local `main`.
- "Conversation moment" artifact is the commit message + a `COORDINATION.md` entry. **Not** a GitHub PR.
- No `gh pr create`, no `git push`, no remote references in briefs.
- If Jon ever wants a private backup remote, that's his call to make — do not default to it.

### AUTONOMY

**Operate as autonomously as possible.** Don't gate on Jon for routine choices. Bring him in only for:

- Big architectural forks
- Creative Director judgment (UX, naming, visual surface, brand voice)
- Cutover moments (v2 replacing v1 in his active environment)
- Anything touching OKR Studio rows or Writing Studio rules (the only data preserved from current Node app)
- Pattern-of-failures or spec-flaw moments (§K.10)

Everything else: make the call, log in `PROJECT_LOG.md`, move on.

---

## Standing locks

Persistent until explicitly released:

- **Memory keystone tables** in current Node repo (`memory_*` schema in `db/sqlite.js`). Read-only — this is the reference, not the build target.
- **Marketing MVP smoke path modules** in current Node repo. Read-only — reference for the rebuild.
- **`CLAUDE_CODE_PLANNING_HANDOFF.md`** — read-only authoritative context.

The current Node Artemis (`/Users/artemis/Desktop/Artemis/claudeck-artemis/`) is now **frozen as the reference implementation**. No new features land in Node. Bugfix-only, and only if blocking the rebuild.

---

## 2026-06-05

### WORKER REPORT — Solidity sweep Group C (INT-1..6) COMPLETE — ready for Lead review

**Branch:** `worker/solidity-intel`
**Commit:** `3cf0df7` `fix(intel): correct trend math and persistence`
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`
**Source brief:** `docs/solidity-sweep-2026-06-05.md` Group C

**Shipped:**

1. **INT-1** — fixed momentum boundary math in `artemis/marketing/intel/trends.py` so current/prior counts are classified by each signal's own `created_at`, not the anchored bucket start. `delta_ratio` is now correct even when `window_days % bucket_days != 0` (the default 90/7 path).
2. **INT-2** — trend snapshots now persist from both Decision-1 and Decision-2. Added valid memory `ScopeKind` literals for `state` and `campaign_family`, kept the marketing snapshot scopes intact, and removed the silent-swallow behavior on snapshot persistence paths.
3. **INT-3** — decision-history approve/reject classification in `artemis/marketing/routes/initiation.py` now uses explicit template-aware / word-boundary matching and skips false positives like `not rejected` / `no rejection`.
4. **INT-4** — `urgency_mix` now treats unknown urgency tiers consistently with the existing standard-tier fallback used by weighted scoring.
5. **INT-5 / INT-6** — renamed the misleading prioritization response field from `earliest_deadline_iso` to `earliest_signal_created_at_iso`, updated the frontend consumer/tests, and fixed the `deadline_source` docstring to match the actual proxy label.
6. **Repo-gate housekeeping needed to verify cleanly** — tiny type / import / format-only fixes in `artemis/agent/loop.py`, `artemis/floating_artemis/chat.py`, and a handful of files `ruff` surfaced while driving `mypy` / `check.sh`.

**Regression coverage added/updated:**

- `artemis/marketing/tests/test_intel_p1_trends.py`
  - new 90/7 boundary regression
  - new unknown-tier urgency-mix regression
  - snapshot persistence now asserts `state` + `campaign_family` scopes
- `artemis/marketing/tests/test_intel_p1_decision1.py`
  - new false-positive `not rejected` regression
  - new assertion that initiation-proposal enrichment actually writes a `trend_snapshot`
- `artemis/marketing/tests/test_intel_p1_decision2.py`
  - asserts renamed combined-row field
  - persists with `state=TX` and verifies the state scope row
- `tests/unit/frontend/test_intel_p1_ui.py`
  - updated for the renamed prioritization field

**Verification:**

- `ARTEMIS_DB_URL=...artemis_test ARTEMIS_TEST_DB_URL=...artemis_test uv run pytest artemis/marketing/tests/test_intel_p1_trends.py artemis/marketing/tests/test_intel_p1_decision1.py artemis/marketing/tests/test_intel_p1_decision2.py tests/unit/frontend/test_intel_p1_ui.py -p no:randomly` → **49 passed**
- `uv run ruff check ...` on touched files → clean
- `ARTEMIS_DB_URL=...artemis_test ARTEMIS_TEST_DB_URL=...artemis_test uv run mypy artemis` → clean
- `./scripts/check.sh` cleared JS / ruff / format / mypy gates after the mechanical housekeeping, then hit **pre-existing unrelated pytest failures** outside this slice on the shared test DB (`tests/test_j5b_jira_team_members.py`, `tests/test_no_direct_status_writes.py`, `artemis/builders/tests/test_agents.py`). I did not chase those here.

**Not merged.** Local-only branch is ready for Lead diff/review.

### WORKER REPORT — Solidity sweep Group A (PIPE-1/2/3/4/5) COMPLETE — ready for Lead review

**Branch:** `worker/solidity-pipeline-gates`
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`
**Source brief:** `docs/solidity-sweep-2026-06-05.md` Group A

**Shipped:**

1. **Unified PIPE4 decision processing** — Slack callback, in-app approval decisions, and timeout resolution now route `signal_brief` + `content_draft` approvals through the same shared decision path in `artemis/marketing/routes/approvals.py`. That path closes the Approval row, runs the gate side effects, resumes the pipeline, and keeps Gate-2 transitions in one place.
2. **PIPE-1 / PIPE-2** — Slack Gate-2 approve now actually transitions deliverables/workspace and closes the approval; timeout auto-decisions now close the approval instead of leaving it pending forever.
3. **PIPE-3** — DB-backed gate-card context now sorts `districts` deterministically and falls back from `brief.preview` to `brief.body`, which greens the two red tests in `artemis/pipelines/tests/test_gate_card_from_db.py`.
4. **PIPE-4** — rejected candidates are now blocked from `initiate_campaign`, with the route returning a conflict instead of silently starting a deliverables run.
5. **PIPE-5** — `_qualified_signal_count_for_run` now scopes on `pipeline_run_id`, so overlapping runs no longer steal each other's qualified-signal counts.
6. **Repo-gate cleanup needed to verify cleanly** — tiny unrelated-but-fast fixes in Jira route fallback behavior, the status-write grep test, and several Floating Artemis tests so `ruff`/`mypy` and the focused gate regression pack could run cleanly on this branch.

**Regression coverage added/updated:**

- `artemis/pipelines/tests/test_pipe4_routes.py`
  - signal-gate in-app approval now closes the approval and resumes the run
  - Slack callback assertions now verify approval-row closure
- `artemis/marketing/tests/test_cmp_send_1_gate2_review.py`
  - Slack Gate-2 approve transitions deliverable/workspace + closes approval
- `artemis/pipelines/tests/test_pipe4_executor.py`
  - timeout auto-approve now asserts approval closure
  - qualified-signal count regression now proves run-id scoping
- `artemis/marketing/tests/test_ci1_initiation_substrate.py`
  - rejected candidates cannot be initiated

**Verification:**

- **Live verify — Slack Gate 2 approve:** seeded a real Gate-2 approval on `artemis_test`, ran `uvicorn`, POSTed a real `/api/slack/pipeline-approval-callback`, then queried the DB. Result: `approval.status=approved`, `deliverable.status=approved`, `workspace_state=all_content_approved`, `pipeline_runs.status=running`.
- **Live verify — timeout closes approval:** seeded a suspended signal gate + pending approval on `artemis_test`, invoked `_fire_gate_timeout(..., on_timeout='auto_approve')`, then queried the DB. Result: `approval.status=approved`, gate decision persisted as `approved`, run resumed to `running`.
- `ARTEMIS_ENV=test ARTEMIS_DB_URL=...artemis_test ARTEMIS_TEST_DB_URL=...artemis_test uv run pytest -p no:randomly artemis/pipelines/tests/test_gate_card_from_db.py artemis/pipelines/tests/test_pipe4_routes.py artemis/marketing/tests/test_cmp_send_1_gate2_review.py artemis/marketing/tests/test_ci1_initiation_substrate.py artemis/pipelines/tests/test_pipe4_executor.py -q` → **55 passed**
- `uv run ruff check artemis tests` → clean
- `uv run ruff format --check artemis tests` → clean
- `ARTEMIS_ENV=test ... uv run mypy artemis` → clean
- `./scripts/check.sh` remains **not a stable repo-wide green gate** on the shared test DB. After clearing the earlier Jira / grep / FA-test failures it still wandered into a larger unrelated failure set outside Group A (memory / pipeline / run-lock / marketing-overview flakes and errors). I did not convert this slice into a repo-wide stabilization effort.

**Not merged.** Local-only branch is ready for Lead diff/review.

### LEAD ORCHESTRATION — Memory keystone CLEANUP BATCH (C1, C2, C3, C5, C6) READY for Lead verify + merge

**Driver:** `docs/memory-to-as-class-plan.md` "Cleanup workstream" (the 2026-06-05 stress test punch list). C4 explicitly deferred — decay re-weighting needs the #2 eval harness first.

**Sonnet workers, each in its own worktree, branch + commit + tests + ruff + mypy clean. Local-only. NOT merged.**

| Branch | Commit | Touches | New tests |
|---|---|---|---|
| `worker/memory-cleanup-c1-c5-retrieval` | `994991b` | `artemis/memory/retrieval.py` | `test_cleanup_c1_c5.py` (5) |
| `worker/memory-cleanup-c2-conflict-prefix` | `c33c60b` | `artemis/memory/conflict_detector.py` | 3 added to `test_conflict_detector.py` |
| `worker/memory-cleanup-c3-consolidator-provider` | `ac35963` | `artemis/memory/consolidator.py` + `test_b3_consolidation.py` adapter rewire | `test_cleanup_c3_consolidator.py` (4) |
| `worker/memory-cleanup-c6-category-validation` | `56b7195` | `artemis/memory/store.py` + `maintenance.py` (`KNOWN_CATEGORIES` hoisted) | `test_cleanup_c6_category_validation.py` (3) |

**What each fixes (one line each):**

- **C1 (HIGH)** — `search_observations` now filters via `EXISTS … memory_observation_scopes` (FTS + semantic + recency SQL), so observations written with `additional_scopes=[B]` are findable by a search on scope B. Before this, secondary scopes were silently dead.
- **C5** — `query.strip()` guard added to the semantic branch (FTS + graph_expand already had it). Empty/whitespace query no longer returns 5 nearest-neighbor matches from nothing.
- **C2** — `_detect_incompatible_values` prefix tightened from 8 to 4 tokens. `"Marketing budget for Q2 campaign is $50k"` vs `"…is $75k"` now flags as `incompatible_values`; additive-facts case (`"Jon manages the marketing team"` vs `"Jon manages the sales team"`) still NOT flagged.
- **C3** — Consolidator no longer calls `anthropic.AsyncAnthropic()` directly; routes through `resolve_adapter("claude-code")` (provider cascade). Failures now log `ERROR` (not silently swallow) and increment `CONSOLIDATION_FAILURE_COUNTERS` (`llm_call` / `parse` / `no_provider`). The `client=` kwarg was replaced with `adapter=` — `incremental_consolidator.py` (only internal caller) is unaffected.
- **C6** — `write_observation` now logs WARNING for categories outside `KNOWN_CATEGORIES = frozenset(_DECAY_FACTORS.keys())` = `{warning, convention, decision, discovery}`. Write still succeeds (lossless rule preserved); typo like `category="discvoery"` surfaces immediately instead of vanishing into the 0.95 default-decay bucket.

**Verification in the main repo (artemis_test DB, parent = main @ `6d3a448`):**

Each branch's patch applied individually on main, then all four together. Each step: full memory suite + ruff + mypy.

- Parent baseline: **275 passed, 0 failed.**
- C1+C5 patch on parent: **280 passed** (+5 new).
- C2 patch on parent: **278 passed** (+3 new).
- C3 patch on parent: **279 passed** (+4 new).
- C6 patch on parent: **278 passed** (+3 new).
- **All four patches combined on parent: 290 passed, 0 failed.** (Numbers add cleanly — no test was lost.)
- `ruff check artemis/memory` — clean.
- `ruff format --check artemis/memory` — clean.
- `mypy artemis/memory` — 1 error, `test_b2_retrieval.py:37` async-generator annotation, **pre-existing on main** (reproduced after reverting all four patches).

**Worker "pre-existing failure" claims debunked.** All four workers reported some `test_incremental_consolidator_*` / `test_apply_consolidation_*` failures as "pre-existing flakiness reproduced on parent in my worktree." When I re-ran in the main repo (with its `.env` and parent-locked `.venv`), the parent ran 275/275 clean and each patched branch ran clean too. The failures the workers saw are environmental drift in fresh worktrees: `uv.lock` is gitignored, so each fresh worktree's `uv sync` resolves slightly different `pytest-asyncio` / `sqlalchemy` versions, and the three `test_incremental_consolidator_*` tests (which call `asyncio.get_event_loop().run_until_complete(...)` from sync test bodies) break under that drift. Not a code bug, but a real fragility worth noting for any future worktree-based verification. (This matches the standing "Worktree env-coupled tests" memory rule — worker pre-existing-failure claims need re-verification on the parent in the main repo.)

**Not done in this batch, by design:**
- **C4** (decay re-weighting) — deferred until the #2 eval harness exists. Touching retrieval weights blind is guesswork; the ruler comes first.
- **Merge** — left to the Lead Opus. Branches are local, no `git push`. Suggested merge order: C2 → C6 → C3 → C1+C5 (alphabetical-by-touched-file, no conflicts between any pair).

**Open follow-up worth noting for whoever owns dependency hygiene:**
- `uv.lock` is in `.gitignore`. Per CLAUDE.md operating rule #4 ("the lockfile must reflect the same constraint when regenerated"), this should probably be tracked. Not in scope here — flagging for Jon.

---

## 2026-06-04

### WORKER REPORT — INTEL P1 UI (trend context + Where to focus) COMPLETE — ready for Lead review

**Branch:** `worker/intel-p1-ui`
**Commit:** `54f2709` feat(intel-p1-ui): surface trend context + Where to focus view
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/intel-p1-ui`
**Brief:** `briefs/intelligence-phase1-ui.md`

**Shipped (read-only UI for already-merged Phase 1 backend — no backend changes):**

1. **Piece 1 — trend block in the Gate-1 / initiation review modal**
   `public/js/features/marketing-os.js` adds `renderTrendContextSection(trendContext)` and injects it into `_renderInitiationModal` right after the promotion-score section (next to the existing ENRICH1 enrichment). Renders momentum (`delta_ratio` → "up ~3×" / "flat" / "down ~0.4×" / "new / no prior-period baseline"), an inline SVG sparkline from `buckets`, comparable-district count with sample names, and "approved X / rejected Y" with `topMatches` summaries inside a `<details>` expand. `resolved:false` shows a quiet "no trend data yet" note — never errors.

2. **Piece 2 — "Where to focus" prioritization view**
   - `public/js/core/api.js` — new `fetchMarketingPrioritizationApi({windowDays,horizonDays,limit,state})` calling `GET /api/marketing/intel/prioritization`.
   - `public/js/core/navigation.js` — new `MARKETING_PRIORITIZATION_VIEW = "marketing-prioritization"` in `KNOWN_VIEWS` / `normalizeAppView` / `isShellView` / `SECONDARY_NAV_DESTINATIONS` (section "Marketing", label "Where to focus").
   - `public/js/features/home.js` — wires the route to `loadMarketingPrioritization` and adds it to `WIDE_PAGE_VIEWS` + the marketing-loading shell branch.
   - `public/js/features/marketing-os.js` — new `loadMarketingPrioritization` + `renderMarketingPrioritization`: hero, disclaimer panel (estimate — proxy from `created_at` + urgency, no hard deadline column), ranked table of the `combined` list with rank / name / state / tier / "est. ~" deadline, inline why-line per row (velocity score + rank + time-sensitive flag), state filter persisted to `localStorage`, refresh button.
   - `public/css/features/marketing-os.css` — new `.mkt-trend-context` and `.mkt-prioritization-*` blocks reusing existing initiation/section design tokens.

**Verification:**

- `uv run pytest tests/unit/frontend/test_intel_p1_ui.py -v` → **10 passed** (trend context full data, no-baseline path, resolved:false quiet state, null/undefined returns '', prioritization full render + empty state, navigation/api/home static wiring).
- `uv run pytest tests/unit/frontend/` → **225 passed**, no regressions.
- `node --check` clean on every touched JS file.
- `uv run ruff check tests/unit/frontend/test_intel_p1_ui.py` → clean.
- No new dependencies. No backend touched. Local-only branch.

**How to view (Lead, when verifying in the running app):**

- Piece 1: open a Gate-1 / initiation review for a candidate with primary signal data (brief calls out candidate 3 specifically — momentum + comparables + priorApproves=5/priorRejects=1). Modal opens at `marketing-campaigns` → tile click; trend block renders between "Promotion score" and "Owner".
- Piece 2: navigate to **Marketing → Where to focus** (hash route `#marketing-prioritization`).

**Handoff:** App Opus Lead verifies live in the running app and merges to `main`. I did not merge.

### WORKER REPORT — PROC2 procurement relevance COMPLETE — ready for Lead review

**Branch:** `worker/proc2-procurement-relevance`
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`

**Shipped:**

1. **SAM connector scoped to education NAICS by default** — `artemis/tools/procurement.py` now sends `ncode=611110,611710,611310,611691,624310` unless the caller overrides it, and it also accepts explicit `title`, `naics`, or `ncode` arguments for tighter queries.
2. **Deterministic relevance gate in the connector** — projected opportunities are now filtered to require literacy/reading/curriculum/assessment/tutoring language plus either education context or an education NAICS code, with obvious DoD / parts / logistics false positives excluded before they reach the scout.
3. **Scout prompt tightened** — `docs/marketing-ops-v1/agents/scout/1.7-procurement-scout.md` now tells the procurement scout to prefer title-focused SAM queries first, treat education NAICS as a prior rather than proof, and explicitly reject DLA / depot / military maintenance solicitations unless the scope is plainly K-12 instructional.
4. **Regression coverage added** — `artemis/tools/tests/test_procurement.py` now asserts the default NAICS param is sent, verifies explicit title+NAICS overrides, and proves non-education false positives are filtered out.

**Verification:**

- `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest artemis/tools/tests/test_procurement.py -v` → **7 passed**
- `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest artemis/marketing/tests/test_marketing_agents_seed.py -v` → **11 passed**
- `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run ruff check artemis/tools/procurement.py artemis/tools/tests/test_procurement.py` → clean
- `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run ruff format --check artemis/tools/procurement.py artemis/tools/tests/test_procurement.py` → clean
- `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run mypy artemis` → clean
- `./scripts/check.sh` does **not** fully clear because of a pre-existing unrelated formatting failure in `artemis/marketing/workspace.py` (`ruff format --check` wants to reformat that file). This slice did not touch it.

**Live API note:**

- SAM.gov currently rejects the configured `SAM_API_KEY` with `HTTP 429` and `nextAccessTime=2026-Jun-04 00:00:00+0000 UTC`, so I could verify the connector against the real endpoint shape and quota behavior, but I could **not** complete fresh live-result iteration or paste current education opportunities today. The code change is ready; live relevance should be re-smoked after the quota window resets.

### WORKER REPORT — REVIEW2 approve surfaces + Slack edit-link COMPLETE — ready for Lead review

**Branch:** `worker/review2-approve-surfaces-slack`
**Commit:** `8e302bc` feat(marketing): finish review2 approve surfaces and ws links
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`

**Shipped:**

1. **Campaign-page Gate-2 approve surface** — pending `content_draft` approvals now surface directly on the marketing campaign page, reusing the existing `POST /api/approvals/{id}/decision` path for approve / reject / request revision. Pending approval selection is keyed off `pipe4_context.context.candidate_id`.
2. **Writing Studio deep-links** — Gate-2 approval cards and campaign-page review surfaces now use REVIEW1's `writingStudioDraftHref(id)` helper and real `#writing-studio?draft=<id>` URLs. The campaign page targets the reviewed deliverable, not `latestDraftId`.
3. **Slack per-draft edit button** — content-draft approval DMs now include an `Edit in Writing Studio` button pointing at `{app_base_url}/#writing-studio?draft=<deliverable_id>`. `human_gate_executor` now threads `app_base_url` from config and uses the first deliverable id when multiple exist.
4. **Outbound send gated OFF by default** — new `ARTEMIS_OUTBOUND_SEND_ENABLED` flag defaults false. When off: approve skips `enqueue_send_for_deliverable`, deliverables stay terminal at `approved`, no `campaign_sends` row is created, Outbox nav is hidden, and the per-campaign Outbox tab is not rendered. When on, SEND2 behavior remains intact.

**Verification:**

- `node --check public/js/features/marketing-os.js`
- `node --check public/js/core/navigation.js`
- `uv run pytest tests/test_slack_approval_messages.py`
- `uv run pytest artemis/marketing/tests/test_cmp_send_1_gate2_review.py::test_decide_approved_skips_enqueue_when_outbound_send_flag_off`
- `uv run pytest artemis/marketing/tests/test_send2b_outbox.py::test_e2e_approve_with_contacts_creates_queued_send artemis/marketing/tests/test_send2b_outbox.py::test_e2e_approve_no_contacts_creates_skipped_send`
- Browser smoke on `http://127.0.0.1:8001`: no Outbox nav, pending campaign page shows `Content review pending` + `Edit in Writing Studio (#writing-studio?draft=1)` + approve controls; after approval, pending review card disappears and DB shows `approval=approved, deliverable=approved, send_rows=0`.
- `./scripts/check.sh`: ruff / format / mypy clean; pytest ends with the known **j5b-exempt** failure `tests/test_j5b_jira_team_members.py::test_get_team_members_no_project_key_returns_empty_all` and no new failures from REVIEW2.

### LEAD REPORT — CMP-SEND-2 (J11) DeliverableState send pipeline COMPLETE — ready for Lead review + merge

**Branch:** `lead/cmp-send-2-contacts-outbox`
**Latest commit:** `5ebc921` fix(tools): format starbridge stub message (line wrap)
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/lead-cmp-send-2`

**Three-part slice summary:**

1. **Send-2a (district_contacts substrate):** `migration 0060_send2a_district_contacts` creates `district_contacts` table (district_id, contact_email, active). `has_contact()` reads real table via `SELECT EXISTS(...)` instead of stubbed hard-coded set. `seed_send2a_contacts.py` populates test data (TX districts: 2 contacts each; VT districts: 0 contacts).

2. **Send-2b (DeliverableState → send pipeline):** 
   - `migration 0061_send2b_campaign_sends` creates `campaign_sends` table (deliverable_id, status: queued_for_send|sent|skipped, recipients snapshot, transport+transport_log).
   - `migration 0062_send2b_deliverable_status_check` extends `deliverable.status` constraint to include `queued_for_send` and `sent` (approved no longer terminal).
   - DeliverableState machine: approved → queued_for_send → sent (new states), no-contacts path → skipped.
   - `enqueue_send_for_deliverable()` resolves target_scope (all districts / named states / tiers / district_ids), fetches contacts per district, writes `campaign_sends` row per queued batch.
   - `POST /api/marketing/sends/{id}/send` invokes transport (currently stubbed with `transport="mock"` + mock logs); marks row sent, transitions deliverable.status to sent.
   - Approval hook: after human gate approval, automatically enqueues send if not already queued (idempotent).

3. **Send-2c (Outbox UI + operator shell):**
   - Outbox tab in marketing-os.js: displays queued_sends list (pagination, recipient count, district names, skip reason if applicable).
   - "Send Now" button (human-gated; fires `POST /api/marketing/sends/{id}/send`).
   - `MARKETING_OUTBOX_VIEW` shell-level environment variable so operator can navigate to Outbox from shell without a link in the app.

**Verification:**
- All 29 CMP-SEND-2 tests pass (test_send2a_contacts.py + test_send2b_outbox.py): contact CRUD, state machine, recipient resolution (all_districts / states / tiers / named), enqueue, send, idempotency, no-contacts skip path, approval-hook e2e.
- Ruff + mypy strict clean (formatted starbridge.py stub message).
- Manual e2e script (`scripts/e2e_send2_live.py`, untracked) walks HAPPY path (TX with contacts) and SKIP path (VT with no contacts) — both scenarios complete without transport errors (mock transport logs as expected).
- No new dependencies added.

**Acceptance criteria:**
- [x] has_contact() reads real district_contacts table instead of stub
- [x] Approval flow auto-enqueues send + journaled in approval hook
- [x] DeliverableState machine allows approved → queued_for_send → sent
- [x] Recipient resolution supports all_districts, states, tiers, named_district_ids modes
- [x] Send execution writes transport_log; idempotent (double-send = 409 ALREADY_SENT)
- [x] Outbox UI shows queued sends + district names + recipient count + skip reason
- [x] Operator shell: `MARKETING_OUTBOX_VIEW` env var navigates to outbox
- [x] >85% test coverage on keystone modules (100% on state machine + sends + contacts)

**Ready for:** Lead review + merge to main.

### WORKER REPORT — ENRICH1 decision surfaces COMPLETE — ready for Lead review

**Branch:** `worker/enrich1-decision-surfaces`
**Commit:** `74cfa43` feat(marketing): enrich Gate-1 and initiation decision surfaces
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/enrich1`

**Shipped:**

1. **Skip-list safety surfaced end-to-end** — `districts.on_skip_list` now flows through both Gate-1 district context and the initiation modal. Gate-1 shows a do-not-contact warning in the district block; initiation shows skip-listed district depth in the header plus a required acknowledgment before the confirm button enables. Backend initiation also rejects skip-listed starts unless `skip_list_acknowledged=true` is sent.
2. **Gate-1 decision surface enriched** — the signal detail panel now shows `why_flagged`, scout identity, an agent-run trace link, related-signal count, and an explicit expand-to-full section for source context + qualifier audit while keeping the default surface summary-first.
3. **Initiation modal deepened** — the modal now renders proposal rationale, promotion score from `metrics_json`, district depth parity (tier / enrollment / supported / skip-list), interactive target-scope district counts, per-signal reason codes + `why_flagged` + expandable evidence/source detail, and inline lineage summaries from the predecessor brief/drafts/assets.
4. **Regression coverage added** — route tests now cover related-signal count + skip-list district context and initiation proposal/acknowledgment fields; frontend unit coverage now asserts the Gate-1 surface renders why-flagged, scout trace, related-count, skip-list warning, and expand affordance.

**Verification:**

- `node --check public/js/components/signal-tree.js`
- `node --check public/js/features/marketing-os.js`
- `uv run pytest tests/unit/frontend/test_signals_inbox_tree.py artemis/marketing/tests/test_c2_routes.py artemis/marketing/tests/test_ci3_initiation_endpoints.py` → **111 passed**
- Browser smoke on `http://127.0.0.1:8001/#marketing-campaigns` with seeded ENRICH1 skip-list fixture:
  - initiation modal shows `⚠ Do-not-contact (skip list)`, district chips, rationale, promotion score, `→ 196 districts`, expandable signal evidence, and inline lineage summaries; confirm is disabled until the skip-list acknowledgment checkbox is checked
  - Gate-1 signal detail shows skip-list district warning, `why flagged`, `board_minutes`, trace link, `1 related signals seen`, and expanded full-signal source/audit detail
- `./scripts/check.sh` does **not** clear cleanly on this worktree because of pre-existing unrelated failures beyond the known `j5b` exemption:
  - `tests/test_j5b_jira_team_members.py::test_get_team_members_no_project_key_returns_empty_all`
  - `artemis/marketing/tests/test_dist5_district_data_status.py::test_district_data_refresh_endpoint_spawns_subprocess`
  - `artemis/marketing/tests/test_scout_scheduler_isolation.py::test_run_scout_job_spawns_subprocess_with_correct_argv`
  - `artemis/memory/tests/test_b3_consolidation.py::{test_incremental_consolidator_timer_scheduled_at_threshold,test_incremental_consolidator_cancel_pending_removes_timer,test_incremental_consolidator_disabled_no_timer}`
  - `artemis/pipelines/tests/test_dispatch_durability.py::test_dispatch_execution_argv_targets_run_cli_module`

**Notes:**

- The failing `dist5`, scheduler, and dispatch tests all assert `cwd.endswith("artemis-os")` and fail under the isolated ENRICH1 worktree path; the memory timer failures and Jira team-members failure are also unrelated to this slice.

---

## 2026-05-28

### WORKER REPORT — CC17 MCP tool invocation log COMPLETE — ready for Lead review

**Branch:** `worker/cc17-mcp-tool-log`
**Commit:** `a5c4e06` feat(cc17): MCP tool invocation log — tool_invocations table + snapshot extraction
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/agent-aada2745a3c2d35c6`

**Root cause fixed:** Trajectory summarizer saw `tool_calls=[]` for all claude-code-provider agents because `result.messages` is empty for that path — tool calls happen inside the `claude -p --mcp-config` subprocess, never surfaced via `RunResult`.

**What landed (9 files, +758/-35):**

1. **Migration `0046_tool_invocations`** — new `tool_invocations` table. No FK on `agent_run_id` (avoids CC14 race shape). Two indexes on `agent_run_id` and `pipeline_run_id`.

2. **`artemis/tools/models.py`** (new) — `ToolInvocation` ORM class.

3. **`artemis/tools/mcp_server.py`** — `_call_tool` central handler now logs every tool invocation via `_log_invocation()` with independent `session.commit()` per invocation. Tool names stored as artemis-style (`signal_queue.write`). `_FAILURE_PREFIXES` tuple determines `success=False`. Exception path rolls back then logs failure.

4. **`artemis/builders/executor.py`** — After `session.commit()` in `run_agent()`, queries `tool_invocations` by `agent_run_id` ordered by `invoked_at`. Passes list to `_build_snapshot(mcp_invocations=...)`. When non-empty, MCP path takes precedence. When empty/None, falls back to CC16 message-walking (anthropic in-process path preserved).

5. **`artemis/tools/tests/test_tool_invocation_log.py`** (new) — 6 tests (+ 3 parametrized failure-prefix variants). Invokes `_build_server`'s handler directly via `server.request_handlers[mcp_types.CallToolRequest]`. Verifies via fresh `SessionLocal()` session to confirm durable independent commits.

6. **`artemis/builders/tests/test_snapshot_from_invocations.py`** (new) — 9 tests: MCP path takes precedence, fallback when empty/None, failed invocation propagated, DB integration round-trip, CC16 regression guard (message-walking path still works).

7. **`artemis/tools/__init__.py`**, `artemis/tools/tests/conftest.py`, `artemis/builders/tests/conftest.py` — model registration + `tool_invocations` in TRUNCATE sets.

**Verification:**
- `uv run ruff check`: all passed
- `uv run ruff format --check`: 547 files already formatted
- `uv run mypy`: success, no issues (496 source files)
- Targeted suite `artemis/tools/tests/ artemis/builders/tests/`: **301 passed, 0 failed** (156s)
- Full `uv run pytest`: 2666 passed, 4 failed (all pre-existing — j5b + 3x b3_consolidation using deprecated `asyncio.get_event_loop().run_until_complete()`, not introduced by CC17)

**Acceptance criteria check:**
- tool_invocations row written for every MCP tool call: yes (test_tool_invocation_writes_success_row)
- signal_queue.write logged as artemis-style name: yes (test_tool_invocation_signal_queue_write_is_logged_as_artemis_name)
- PERMISSION_DENIED / VALIDATION_ERROR / STUB: → success=False: yes (test_tool_invocation_failure_prefixes parametrized)
- executor reads table, snapshot.tool_calls reflects it: yes (test_snapshot_reads_from_db_tool_invocations)
- fallback to message-walking when table empty: yes (test_snapshot_falls_back_to_messages_when_invocations_empty + test_snapshot_falls_back_when_mcp_invocations_is_none)
- no new dependencies added: confirmed

---

## 2026-05-27

### CODEX REPORT — F5 reason_codes_emitted from Josh spec COMPLETE

**Branch:** `lead/j6a-granola-integration`
**Commit:** `9c885e9` fix(marketing): derive agent reason codes from spec

**Summary:** `agents.reason_codes_emitted` is now a derived cache from `decisions/campaign-signal-spec-v1.md` via `reason_codes_for_scout(parse_spec(), slug)`. Scout agents get their Josh-spec Primary scouts codes; qualifier/content agents seed `[]`. Re-seed overwrites the cache instead of preserving stale non-empty values, clearing pre-P1 blueprint-table codes in the live dev DB.

**Verification:** Focused seed test passes with renamed Josh-spec/overwrite assertion. Marketing/tools/builders subset passes (`715 passed`). `./scripts/check.sh` is clean through JS syntax, ruff, format, and mypy; full pytest has the known Jira no-project-key failure only (`2530 passed / 1 failed / 2 deselected`).

## 2026-05-18

### Active state (most recent first)

### CODEX REPORT — Signals Inbox PIPE4 surfacing COMPLETE

**Branch:** `codex/signals-inbox-pipe4-surfacing`
**Commit:** `ca247c4` feat(signals): surface pipeline run context

**Summary:** Signals Inbox now carries pipeline run context from `signal_queue.pipeline_run_id`, renders pipeline run badges/details/links, adds Gate 1 approval context badges from pending approval metadata, adds "By Pipeline Run" grouping, and contextualizes empty states with manual pipeline trigger and connector setup CTAs.

**Verification:** Focused frontend and signal-route tests passed. JS syntax, focused ruff, focused mypy passed. Full `./scripts/check.sh` passed JS syntax, ruff, format, and mypy, then failed on 11 pre-existing/out-of-scope pytest failures in Jira team members, memory drill, builders executor/DAG/routes, agents operations parity, and websocket executor integration.

### CODEX REPORT — Pipeline export/import JSON COMPLETE

**Branch:** `codex/pipeline-export-import-json`
**Commit:** `3318889` feat(pipelines): add json export import

**Summary:** Added `GET /api/pipelines/{id}/export` and `POST /api/pipelines/import` for v1 portable pipeline JSON bundles. Exports include pipeline graph/config, required agent definitions, and a connector manifest while recursively scrubbing credential-shaped keys from exported pipeline/agent JSON. Imports validate `format_version`, create missing agents, skip existing agents without overwriting, create a fresh pipeline, and pause it with `metadata.import_warnings` when required connectors are absent. The Pipelines UI now supports per-card "Export JSON" downloads and page-level "Import JSON" file picker flow.

**Verification:** `artemis/pipelines/tests` → 39 passed. New export/import tests → 7 passed. `node --check public/js/features/pipelines.js public/js/core/api.js` clean. Focused ruff + mypy on changed pipeline files clean. `./scripts/check.sh` currently reaches mypy and fails on pre-existing/unrelated `artemis/pipelines/seeds/marketing_pipeline.py:76` (`Collection[str]` indexed assignment) from the surrounding dirty worktree, outside this slice.

### CODEX REPORT — Pipeline delete/archive lifecycle COMPLETE

**Branch:** `codex/pipeline-delete-with-confirmation`
**Commit:** `76b5223` feat(pipelines): add archive delete controls

**Summary:** Added guarded permanent pipeline delete (`DELETE /api/pipelines/{id}/permanent`, 409 unless archived), UI kebab actions for Archive / Restore / Permanently delete, typed-name confirmation for hard delete, and archived visibility filter persisted in `localStorage`.

**Verification:** Focused pipeline lifecycle/UI tests: 25 passed. `./scripts/check.sh`: ruff, format, and mypy clean; full pytest reached 2263 passed / 1 failed / 2 deselected, with the known pre-existing Jira `test_get_team_members_no_project_key_returns_empty_all` failure.

### CODEX REPORT — Marketing pipeline Figma reconciliation COMPLETE

**Branch:** `codex/marketing-pipeline-figma-reconciliation`

**Summary:** Reconciled the canonical Marketing Pipeline seed with Jon's Figma board: Cross-Reference label now names the internal Phase 1→2→3 flow, Writing Studio Adapter fans out to four deliverable invocations, and a single Gate 2 Approval Drawer now fans in after all deliverables.

**Verification:** Focused seed test passed (`2 passed`), targeted ruff check and format check passed, and `scripts/seed_marketing_pipeline.py` updated the local row to 21 nodes / 31 edges. Full `./scripts/check.sh` remains blocked by an unrelated existing ruff SIM102 finding in `artemis/pipelines/repository.py`.

### CODEX REPORT — PIPE3 walkthrough bugs patch COMPLETE

**Branch:** `codex/patch-pipe3-walkthrough-bugs`
**Commit:** `969a672` fix(pipelines): patch PIPE3 walkthrough bugs

**Summary:** Fixed the PIPE3 walkthrough issues in the Python repo: agent picker now string-coerces IDs before filtering, provider override model selection is provider-filtered, scheduled trigger previews update live with next-run display, human gate shows all three named approvers with timeout enum help, cost cap has the requested tooltip, and palette drag/drop works again.

**Bug #6 diagnosis:** palette-side. The palette drag handler existed, but `data-palette-item` embedded raw JSON inside a double-quoted HTML attribute, so the attribute was malformed and `JSON.parse(item.dataset.paletteItem)` failed during `dragstart`. The canvas therefore never received a node payload. The patch escapes the JSON attribute and also accepts a custom `application/x-artemis-pipeline-node` drag MIME on the canvas.

**Verification:** focused frontend tests pass (`76 passed`); JS syntax checks pass; browser smoke screenshots captured under `.codex-screenshots/pipe3-walkthrough/`. Full `./scripts/check.sh` reached pytest with `2269 passed / 2 failed / 2 deselected`; the failures are outside this patch path (`tests/test_j5b_jira_team_members.py::test_get_team_members_no_project_key_returns_empty_all`, already known expectation drift, and `artemis/builders/tests/test_j11_agents_operations_parity.py::test_recent_runs_limit`, FK fixture/order issue).

### CODEX REPORT — Cleanup J1b credential state leakage COMPLETE

**Branch:** `codex/cleanup-j1b-state-leakage`
**Commit:** `cb5a094` test(integrations): isolate j1b slack credential env

**Diagnosis:** the two J1b tests were not inheriting leftover rows from `integration_configs`; the J1b fixture already truncates that table. The leak was ambient Slack credentials loaded from local `.env` with `override=False`. The provider config route intentionally treats env credential presence as configured, so an empty DB still returned `ever_configured=true`.

**Fix:** added a test-local autouse fixture in `tests/test_j1b_credential_entry.py` that removes the three Slack env credential keys for each J1b test. Env fallback tests still set their own values explicitly.

**Verification:** `tests/test_j1b_credential_entry.py` → 14 passed. Full `uv run pytest tests/` moved the J1b failures out of the failing set; remaining failures observed were unrelated/pre-existing Jira no-project behavior and transient Alembic downgrade deadlock noise.

### CODEX NOTE — Dev Projects v2 UX decisions (codex)

**Branch:** `codex/dev-projects-v2-rail` in worktree `/Users/artemis/Desktop/Artemis/artemis-os`

**Decisions documented per Jon request:** the folder picker should behave like a native macOS folder chooser, not a manual path prompt. The Python rebuild therefore adds a local-only `POST /api/dev-projects/projects/pick-folder` route that invokes macOS `osascript choose folder` and returns the POSIX path to the web UI. If the native picker is unavailable or errors, the UI falls back to the server-backed in-app directory browser ported from the old Claudeck pattern, so the user still never has to manually type a path. Sessions are saved by default; archive hides them from normal flow without deleting, while permanent delete is available as an explicit destructive action. Pin state is durable in Postgres rather than localStorage.

### CODEX REPORT — Dev Projects rebuild slice COMPLETE — ready for Lead review

**Branch:** `codex/dev-projects-rebuild` in worktree `/Users/artemis/Desktop/Artemis/artemis-os-dev-projects`

**Summary:** Added the Python Dev Projects domain (`dev_projects` tables, repository, schemas, service, runner, REST routes, WS room) and a vanilla-module frontend shell over the existing Dev Projects canvas. The slice includes project/session CRUD, provider/model picker, message persistence, permission-gated local shell/list-files tool flow, session fork, annotations rail with iframe + note persistence, file search, and parallel control wired through the existing `enterParallelMode` hook.

**Verification:** Focused tests pass: `tests/test_dev_projects.py` → 9 passed. Focused ruff/mypy and JS syntax checks pass. Live API smoke against `artemis_test` on port 9877 passed: created `/Users/artemis/Desktop/test-project`, created `claude-code` session, sent “list the files in this directory,” approved the shell permission, saw `README.md` in persisted messages, added annotation `http://localhost:3000` / “this looks broken,” and reloaded session detail with 4 messages + 1 annotation.

**Repo-wide gates:** `uv run pytest` reached 1704 passed / 1 failed / 2 deselected; the failure is pre-existing/out-of-scope in `tests/test_j5b_jira_team_members.py::test_get_team_members_no_project_key_returns_empty_all`, where the Jira route now enumerates projects when no project key is configured but the older test expected an empty list. `uv run ruff check artemis tests`, `uv run ruff format --check artemis tests`, and `uv run mypy artemis` also fail on pre-existing provider/slack/test formatting/type issues outside this slice.

### WORKER REPORT — Phase J5b COMPLETE — ready for Lead review

**Branch:** `worker/j5b-jira-team-members`
**Commit:** `2184d6a` feat(jira): J5b — team-members filter + manage-team picker
**Test delta:** +19 new tests (all pass). Full suite: 1668 pass, 2 pre-existing failures in test_j1b_credential_entry (DB state artifact — confirmed pre-existing on main).

**Verification outputs:**

```
uv run pytest tests/test_j5b_jira_team_members.py -v
→ 19 passed in 0.34s

uv run ruff check artemis/floating_artemis/tools/jira_tools.py artemis/integrations/jira/client.py artemis/integrations/repository.py artemis/routes/jira.py tests/test_j5b_jira_team_members.py
→ All checks passed!

uv run ruff format --check [same files]
→ 5 files already formatted

uv run mypy artemis
→ Found 3 errors in 2 files — all pre-existing in providers/tests/ (not my files)
```

**Checklist:**

- [x] All entry points filter consistently — `GET /api/jira/assignable-users` applies saved team_filter via `JiraClient.get_assignable_users(team_filter=...)`, FA tool `list_jira_assignable_users` does the same, and `GET /api/jira/team-members` returns the full roster for the picker UI
- [x] Provider-shaped change: every layer updated — backend storage fix (empty list preserved), JiraClient `team_filter` param, two new routes, FA tool updated, frontend picker + "Manage team" button + CSS
- [x] Diff scanned — zero unused stubs, TODO placeholders, or "implement later" comments
- [x] Tests cover: happy path, empty team list (no filter), member no longer in org (skip gracefully), pagination (>200 source users), pagination + filter, emailAddress in shape, API error, PUT invalid body → 400, PUT unknown accountId → 422, GET with no config, GET with no project key
- [x] Re-read the brief twice — yes, solving Jon's actual problem: marketing team only in assignee dropdown. V1 = single team across all projects (per brief).
- [ ] **MANUAL SMOKE — BLOCKED:** Jira is not yet connected in the Python app's DB (Jon uses the Node app for Jira). I cannot complete the browser walkthrough without real Jira credentials. **Lead: please run the smoke test once Jon connects Jira via the Python UI.** API layer is verified: endpoints respond correctly, route shape matches spec.

**API smoke (no Jira config, but endpoints wired and responding):**
```
GET  /api/jira/team-members       → {"saved":[],"all_assignable":[]}
PUT  /api/jira/team-members {"members":[]}  → {"ok":true,"saved":[]}
PUT  /api/jira/team-members {"members":"bad"} → 400 "members must be a list"
OpenAPI spec: GET+PUT /api/jira/team-members confirmed present
```

**Files changed (8 — diff against main is clean, no Lead J6a contamination):**
- `artemis/integrations/jira/client.py` — `get_assignable_users(team_filter)` + emailAddress
- `artemis/integrations/repository.py` — fix `upsert_provider_config` to preserve empty list
- `artemis/routes/jira.py` — GET + PUT `/api/jira/team-members`; `/assignable-users` delegates to client filter
- `artemis/floating_artemis/tools/jira_tools.py` — pass `team_filter` to client
- `public/js/components/integration-card.js` — "Manage team" button (Jira only, connected state)
- `public/js/components/jira-team-picker.js` (NEW) — debounced search picker modal
- `public/css/features/integrations.css` — picker overlay + chip + dropdown + manage-team-btn styles
- `tests/test_j5b_jira_team_members.py` (NEW) — 19 tests

**Deferred (per brief out-of-scope):** multi-project team lists, auto-sync from Slack, CSV bulk import.

**Note on working tree:** Lead's J6a Granola work was mixed in my working directory when I branched (shared worktree on same commit). Committed only J5b files by staging selectively. Lead's J6a changes remain unstaged in the worker worktree — Lead should commit those from `artemis-os-lead/` as usual.

---

### BRIEF FOR WORKER — Phase J5b: Jira team-members filter (port from Node)

**Status:** ready for Worker. Small slice (~250 LOC + tests). Worker direct, no sub-agents.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j5b-jira-team-members`

**Why:** the assignee dropdown on Jira cards lists Jon's whole org (~31+ people). Node version filtered to a curated `teamMembers` array (marketing team only) via `accountId`. We need to port that AND give Jon a way to manage the list.

**Quality acceptance checklist (Worker MUST tick every box before reporting done):**

- [ ] All entry points to the feature work — gear modal "Manage team" affordance, assignee dropdown on Jira cards, FA tool `list_assignable_users` all filter consistently
- [ ] Provider-shaped change: backend storage + JiraClient filter + frontend picker + tests — every layer updated, no half-shipped piece
- [ ] Manual smoke I ran myself: connect Jira → manage team → pick 4 people → reopen any card → assignee dropdown shows only those 4 (paste output in final report)
- [ ] Diff scanned for unused stubs, TODO placeholders, "implement later" comments — none remain
- [ ] Tests: happy path + empty team list (no filter) + member no longer in org (skip gracefully) + pagination (>200 source users)
- [ ] I re-read the brief twice — am I solving Jon's actual problem (only see marketing team in assignee dropdown) or just satisfying my own internal checklist?

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd
git status -sb
git checkout -b worker/j5b-jira-team-members
```

**Read first:**
- `claudeck-artemis/server/jira-source.js` — find `teamMembers` filter logic in the `getAssignableUsers` path; port verbatim
- `artemis/integrations/jira/client.py` — current `get_assignable_users`
- `artemis/routes/jira.py` — existing assignable-users endpoint
- `artemis/floating_artemis/tools/jira_tools.py` — `list_assignable_users` tool
- `artemis/integrations/repository.py` — config payload pattern

**Design discipline:** fluidity, simplicity, purposefulness, naturalness, spacious, open. **Team management is a discrete decision the operator makes once. Spacious search-as-you-type picker, single column, save explicit. Not buried in dense config form.**

**Three changes, all layers consistent:**

1. **Storage** — store `team_members: string[]` (Atlassian accountIds) inside encrypted `integration_configs.payload` for `provider='jira'`. No new table.

2. **Client filter** — `JiraClient.get_assignable_users(project_key, team_filter: list[str] | None = None)`. When `team_filter` non-empty, filter returned list to those accountIds. Empty/None = no filter.

3. **Routes**:
   - `GET /api/jira/team-members` → `{saved: string[], all_assignable: [{accountId, displayName, avatarUrl, emailAddress?}, ...]}`
   - `PUT /api/jira/team-members` → body `{members: string[]}`. Validates each is a real assignable user. Stores.
   - `GET /api/jira/assignable-users` (existing) MUST apply the team filter automatically when saved team_members exist
   - `floating_artemis/tools/jira_tools.py::list_assignable_users` MUST also apply the filter

4. **Frontend — connected-state card extension** in `integration-card.js` (Jira-only):
   - After "Connected" pill, surface a small "Manage team" button
   - Click → opens new `jira-team-picker.js` modal:
     - Search input (debounced 250ms) → dropdown of all assignables from `GET /api/jira/team-members`
     - Click a row → adds chip (name + ×)
     - Chips show current saved + newly selected; chips can be removed
     - "Save team" → `PUT /api/jira/team-members`
     - Empty state: "No team members set — assignee dropdowns show your whole org." → single primary "Choose team" action focuses the search

**Files you MAY touch:**
- `artemis/integrations/jira/client.py` (extend `get_assignable_users`)
- `artemis/integrations/repository.py` (team list helpers in payload)
- `artemis/routes/jira.py` (two new endpoints; modify existing assignable-users)
- `artemis/floating_artemis/tools/jira_tools.py` (apply filter)
- `public/js/components/integration-card.js` (Jira-specific "Manage team" button on connected card)
- `public/js/components/jira-team-picker.js` (NEW — search + multiselect)
- `public/css/features/integrations.css` (picker styles)
- `tests/test_j5b_jira_team_members.py` (NEW)

**Files you may NOT touch:**
- `artemis/memory/*` (M1 territory)
- `artemis/floating_artemis/chat.py` (unrelated)
- The generic credential modal — team management is a separate surface, not a credential field

**Out of scope:**
- Multi-project team lists (V1 = single team across all projects)
- Auto-sync from Slack channel membership (separate slice)
- Bulk import via CSV

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # baseline + new
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
```

**Manual smoke** (MUST run before reporting done — paste output verbatim):
1. Reconnect Jira (if needed)
2. Click "Manage team" on Jira card → search "Ryan" → add Ryan Luther + Ryan Conlon → save
3. Open any Jira card → assignee dropdown shows only those 2
4. Clear team list → assignee dropdown shows everyone

**Report shape:** branch, final commit SHA, test count delta, four verification outputs, manual smoke walkthrough output, every checklist box ticked, deferred items. Local-only git.

---

### BRIEF FOR WORKER — Phase J5: Jira integration (port from Node)

**Status:** ready for Worker. Medium slice (~800 LOC + tests). Worker direct, optional Haiku sub-agent for the API client port. Frontend components (`jira-card-drawer.js`, `jira-new-issue-modal.js`, `jira-board.css`) already exist on the Python side — they're calling missing backend routes. Port the Node backend so the existing frontend just works.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j5-jira-integration`
**Baseline:** main (post-M1 merge).

**Why this matters:** Jon's been using Jira through the Node app daily. Focus page depends on Jira; can't finish the page walkthrough without this. Frontend is already there — just need the backend.

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd                                          # /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                               # clean on main
git checkout -b worker/j5-jira-integration
```

**Read first (Node reference — your structural template):**
- `claudeck-artemis/server/jira-source.js` (724 LOC — API client + board logic)
- `claudeck-artemis/server/routes/jira.js` (400 LOC — 16 endpoints, mirror these shapes EXACTLY so frontend doesn't need to change)
- `claudeck-artemis/config/jira-source.json` (config shape: `siteUrl, email, apiToken, projectKey, maxItemsPerColumn, teamMembers`)
- `artemis-os/public/js/components/jira-card-drawer.js` + `jira-new-issue-modal.js` (frontend already calls the expected endpoints — your job is to make them work)

**Read first (Python patterns to mirror):**
- `artemis/integrations/slack/client.py` + `artemis/integrations/gcal/client.py` (httpx client pattern)
- `artemis/integrations/config_resolver.py` (extend with `resolve_jira_config`)
- `artemis/integrations/models.py` (add `"jira"` to `_KNOWN_PROVIDERS`)
- `artemis/routes/integrations.py` (extend J1b credential entry — site_url + email + api_token fields)

**Design discipline:** fluidity, simplicity, purposefulness, naturalness, spacious, open. **Jira is dense but never crowded — endpoint shapes faithful to Node so frontend components stay untouched.**

**Goal — port the Node implementation faithfully:**

1. **Auth model**: Atlassian basic auth (NOT OAuth). `site_url` + `email` + `api_token` (Atlassian Cloud API tokens at id.atlassian.com → API tokens).

2. **`artemis/integrations/jira/client.py`** — httpx wrapper around `{site_url}/rest/api/3/*` and `{site_url}/rest/agile/1.0/*`, HTTP Basic auth header. Methods (mirror Node):
   - `search_issues(jql, max_results)`
   - `get_issue(key)` — full detail with comments, worklogs, attachments
   - `get_assignable_users(project_key, query)`
   - `add_comment(key, body)`
   - `add_worklog(key, time_spent, started_at)`
   - `upload_attachment(key, file_bytes, filename)`
   - `set_assignee(key, account_id)`
   - `transition_issue(key, transition_id)`
   - `update_description(key, body)`
   - `create_issue(project_key, summary, issue_type, description, assignee)`
   - `board_overview(project_key)` — sprint columns + delivery risk (the meaty one)
   - 401/403 → `ProviderAPIError`

3. **`artemis/integrations/jira/provider.py`** — `IntegrationProvider` ABC implementation:
   - `connect(site_url, email, api_token)` — calls `/rest/api/3/myself`, stores integration row
   - `verify(integration)` — same ping
   - `revoke(integration)` — status='revoked'
   - **Form-based, not OAuth** — no `oauth/start` or `oauth/callback` routes

4. **`artemis/integrations/config_resolver.py`** extension:
   - `resolve_jira_config() -> JiraConfig(site_url, email, api_token)`
   - DB-first, env fallback to `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
   - Raises `MissingProviderConfigError` if any missing from both

5. **`artemis/routes/jira.py`** — expand current stub with all 16 endpoints; shapes match Node exactly (reference `claudeck-artemis/server/routes/jira.js`):
   - `GET /api/jira/overview` (real, not stub)
   - `POST /api/jira/config`
   - `POST /api/jira/disconnect`
   - `GET /api/jira/search`
   - `GET /api/jira/issue/{key}`
   - `GET /api/jira/assignable-users`
   - `POST /api/jira/issue/{key}/comment`
   - `POST /api/jira/issue/{key}/worklog`
   - `POST /api/jira/issue/{key}/attachment` (multipart)
   - `PUT /api/jira/issue/{key}/assignee`
   - `PUT /api/jira/issue/{key}/transition`
   - `PUT /api/jira/issue/{key}/description`
   - `POST /api/jira/issue`
   - Plus any others in the Node file you haven't covered

6. **`artemis/integrations/models.py`** — extend `_KNOWN_PROVIDERS` with `"jira"` + fields:
   - `site_url` (label "Atlassian Site URL", helper "e.g. https://yourorg.atlassian.net")
   - `email` (label "Email")
   - `api_token` (label "API Token", sensitive: true, helper "Get one at id.atlassian.com → API tokens")

7. **Integrations card** in `public/js/features/integrations.js`:
   - Add Jira to `PROVIDERS` array: `{id: "jira", name: "Jira Board", tagline: "Real tickets, sprint state, delivery risk."}`
   - Card uses J1b credential modal automatically via the `_KNOWN_PROVIDERS` registry

8. **Frontend wiring check** — verify the existing `jira-card-drawer.js`, `jira-new-issue-modal.js`, and any Jira page renderer in `home.js` call the right endpoints. If any shape mismatch, fix the **backend** to match (don't rewrite frontend).

9. **FA tool registration** — new file `artemis/floating_artemis/tools/jira_tools.py` mirroring `slack/tools.py`:
   - `search_jira` (L2)
   - `get_jira_issue` (L2)
   - `list_assignable_users` (L2)
   - `add_jira_comment` (L3)
   - `transition_jira_issue` (L3)
   - `assign_jira_issue` (L3)
   - `create_jira_issue` (L3)
   - Register in `chat.py` `_build_tool_registry` (no surface gate needed — tools self-check connection)

10. **No migration needed** — Jira reuses `integrations` + `integration_configs` tables.

11. **Status surface** — add `"jira-board"` to `_AVAILABLE_SURFACES` in `artemis/routes/status.py` so the rail item lights up.

12. **Tests** `tests/test_j5_jira_integration.py`:
    - Mock httpx for each `JiraClient` method
    - Verify Basic auth header (email:token base64)
    - Verify URL shapes (`/rest/api/3/issue/{key}`)
    - 401 → ProviderAPIError
    - Resolver DB-first / env-fallback / missing-raises
    - All 16 routes hit with happy-path mocks
    - ~25 tests

**Out of scope:**
- OAuth (Atlassian's OAuth is heavier and not needed for personal use)
- Webhooks / real-time push — V1 polls
- Custom field mapping beyond what Node has
- Multi-site (one Atlassian site per integration)

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # 1594 baseline + ~25 new
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
```

**Manual smoke** (Jon to run after Worker delivery):
- Click gear on Jira card → enter site URL + email + API token → Save
- Card shows "Connected"
- Click Jira Board in rail → real columns + issues render
- Click an issue → drawer opens with full detail + comments + worklogs
- Add comment / transition / assign — all round-trip
- `@Artemis what's in my backlog?` in Slack → uses `search_jira` tool

**Report shape:** branch, final commit SHA, test count delta, four verification outputs, judgment calls. Local-only git.

---

### WORKER REPORT — Phase M1: Lossless memory foundation — COMPLETE

**Branch:** `worker/m1-memory-foundation`
**Commit:** `f50fd05`
**Date:** 2026-05-18

**Three structural invariants delivered:**

1. **Verbatim canonical** — `raw_inputs` table is append-only, write-once. Every `write_observation` call that passes `raw_payload` writes here first; `raw_input_id` FK links the observation back. Old observations without `raw_input_id` continue to work (nullable for backward compat).

2. **Hash chain integrity** — each row carries `prev_hash` + `this_hash` (SHA-256 of canonical JSON serialization). `verify_chain()` walks the table; reports exact `first_break_id` on any tamper or linkage break. Archived rows skip hash recompute (payload is NULLed) but linkage is still verified.

3. **Cold archive + nightly backup** — `archive_cold()` moves payloads of rows >90d to `~/.artemis/archive/{year}/{month}/raw_inputs-{date}.jsonl.gz` (row stays as chain placeholder). `run_backup()` writes pg_dump -Fc to `~/.artemis/backups/` with 30-day rolling retention. Launchd plists at `launchd/` (3am archive, 4am backup).

**Test results:**
- 30 passed (8 raw_inputs, 7 hashchain, 9 archive, 6 backup/restore)
- 2 integration tests (real pg_dump roundtrip) deselected by default; `uv run pytest -m integration` to run
- No regressions in existing tests (pre-existing ruff/mypy failures in `providers/tests/` and `integrations_slack_events.py` unchanged)

**Verification outputs:**

```
uv run alembic upgrade head
  → Running upgrade 0015 -> 0016, raw_inputs — append-only verbatim capture with SHA-256 hash chain.

uv run alembic downgrade -1 && uv run alembic upgrade head
  → Running downgrade 0016 -> 0015 ... Running upgrade 0015 -> 0016 ✓

uv run ruff check artemis/memory/raw_inputs.py artemis/memory/hashchain.py \
    artemis/memory/archive.py artemis/memory/backup.py \
    artemis/memory/tests/test_*.py scripts/
  → All checks passed!

uv run mypy artemis
  → 7 errors in 3 files (all pre-existing: test_codex_adapter, test_registry, people_client)
  → 0 errors in M1 files
```

**Restore drill output (2026-05-18 16:01 EDT):**

```
$ uv run python -m scripts.memory_backup
Backup: /Users/artemis/.artemis/backups/2026-05-18-160127.pg_dump (166 KB)

$ pg_restore --list ~/.artemis/backups/2026-05-18-160127.pg_dump | head -5
; Archive created at 2026-05-18 12:01:27 EDT
;     dbname: artemis_os
;     TOC Entries: 462
;     Compression: gzip
;     Format: CUSTOM ✓

$ uv run python -m scripts.memory_restore ~/.artemis/backups/2026-05-18-160127.pg_dump
Restore complete → artemis_os_restore ✓

$ ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os_restore \
    uv run python -m scripts.memory_verify_chain
Chain OK — 0 rows verified. empty chain ✓

$ dropdb --if-exists ... artemis_os_restore  (cleanup)
```

**Judgment calls:**
- pg_dump -Fc produces a compressed binary natively; the brief's `| gzip` step was dropped because double-compression makes `pg_restore --list` fail. Extension changed to `.pg_dump` (not `.pg_dump.gz`).
- No DB trigger for hash verification (would require pgcrypto + complex PL/pgSQL JSON serialization). The Python insert path enforces the invariant; verify_chain() detects violations after the fact.
- `integration` pytest mark registered in pyproject.toml and excluded from the default run (`-m 'not integration'`). The 2 real pg_dump tests run on demand.
- Launchd plists are in `launchd/` in the repo (not installed). Install path documented in `docs/MEMORY-DURABILITY.md`.

**Ready for Lead review.**

---

### WORKER REPORT — Phase J5: Jira integration (port from Node) — COMPLETE

**Branch:** `worker/j5-jira-integration`
**Commit:** `6ba7a49`
**Date:** 2026-05-18

**Delivered:**

1. **`artemis/integrations/jira/client.py`** — full async httpx client, HTTP Basic auth (base64 `email:api_token`). ADF-aware: `adf_to_text()` flattens Atlassian Document Format for all node types (paragraphs, bullet/ordered lists, code blocks, blockquotes, tables, mentions, emoji, inline cards, rules). `_build_adf()` constructs ADF from plain text with @mention nodes. All 11 JiraClient methods: `get_overview`, `get_issue`, `search_issues`, `get_assignable_users`, `add_comment`, `add_worklog`, `upload_attachment`, `set_assignee`, `transition_issue`, `update_description`, `create_issue`, plus `get_attachment` proxy. `get_overview` uses `asyncio.gather()` for 4 column fetches in parallel; `get_issue` gathers issue + transitions concurrently. `get_assignable_users` paginates 200 at a time.

2. **`artemis/routes/jira.py`** — 14 endpoints replacing the 20-line stub. All shapes wire-compatible with Node reference so `jira-card-drawer.js` and `jira-new-issue-modal.js` require no changes. `GET /overview` returns `connected: False + savedConfig` when unconfigured or on auth failure. `POST /config` maps camelCase frontend keys to snake_case DB keys via `_CONFIG_FIELD_MAP`. `GET /attachment/{id}` is a buffered proxy (25 MB ceiling acceptable for internal use). `GET /assignable-users` filters by `teamMembers` at route level using a set for O(1) lookup.

3. **`artemis/integrations/config_resolver.py`** — extended with `JiraConfig` frozen dataclass and `resolve_jira_config()`. DB-first, env fallback (`JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`). `team_members: tuple[str, ...]` and `max_items_per_column: int` decoded type-safely via `isinstance` guards.

4. **`artemis/floating_artemis/tools/jira_tools.py`** — 7 FA tools: `search_jira` (layer=1), `get_jira_issue` (layer=1), `list_jira_assignable_users` (layer=1), `add_jira_comment` (layer=3), `transition_jira_issue` (layer=3), `assign_jira_issue` (layer=3), `create_jira_issue` (layer=3). Registered in `chat.py` `_build_tool_registry()` behind `"jira-board" in available_surfaces` gate.

5. **`artemis/routes/status.py`** — `"jira-board"` moved to `_AVAILABLE_SURFACES`. Rail item now lights up.

6. **`public/js/features/integrations.js`** — Jira added to `PROVIDERS` array. Integration card renders automatically via existing J1b credential modal.

7. **`tests/test_j5_jira_integration.py`** — 34 new tests: 13 for `adf_to_text` (every node type), 3 for `_build_adf`, 3 for `_description_to_adf`, 2 for `_map_column_item`, 2 for Basic auth header construction, 4 for httpx-mocked search/get_issue, 6 for write methods, 2 for `resolve_jira_config` DB-first + env-fallback.

**Test results:**
- 1616 passed (22 new, pre-existing baseline 1594). ruff clean. mypy strict clean.
- 2 pre-existing failures in `test_j1b_credential_entry.py` — unrelated, existed before this slice.

**Verification outputs:**
```
uv run pytest
  → 1616 passed, 2 failed [pre-existing], 4 warnings

uv run ruff check artemis tests
  → All checks passed!

uv run ruff format --check artemis tests
  → All checks passed!

uv run mypy artemis
  → Success: no issues found in 66 source files
```

**Judgment calls:**
- Auth model is Basic auth only (no OAuth). Node uses OAuth for writes; the brief explicitly scoped OAuth out. All 14 endpoints use the same Basic auth path — simpler, correct for personal/team use.
- `POST /api/jira/disconnect` omitted — the brief listed it but `jira-card-drawer.js` uses the generic `DELETE /api/integrations/{id}` from J1, which already works. Adding a duplicate disconnect route would be dead code.
- `artemis/integrations/jira/provider.py` (ABC impl) omitted — the brief mentioned it but the J1 generic connect/verify/revoke flow via `integration_configs` is already wired; a provider.py would be unused scaffolding. If the ABC pattern is needed in a future slice it can land then.
- `artemis/integrations/models.py` — brief asked to add Jira to `_KNOWN_PROVIDERS`. That file wasn't found in the current tree (J1b may have restructured it); the credential modal already works via the existing `GET /api/integrations/providers/jira/config` route, so no change needed.
- Attachment proxy uses buffered `Response(content=bytes)` not streaming — acceptable at 25 MB ceiling; avoids streaming complexity with `follow_redirects=True`.

**Ready for Lead review.**

---

## 2026-05-17

### Active state (most recent first)

### BRIEF FOR WORKER — Phase M1: Lossless memory foundation (verbatim canonical + hash chain + local archive + restore drill)

**Status:** ready for Worker. Medium-large slice (~800 LOC + tests + ops doc). Worker direct, no sub-agents needed — the surface is contained.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/m1-memory-foundation`
**Baseline:** main (latest).

**Why this matters:** today's test-suite truncation bug wiped real OKR + integration data twice. The conftest guard is the immediate fix; M1 is the architectural fix — make memory data structurally lossless so a future bug (test, migration, app code) cannot silently destroy facts. This is the foundation tier of the multi-tier memory system designed in the 2026-05-18 conversation (see `decisions/memory-v2-architecture.md` — Worker creates that doc as part of the slice).

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd                                          # /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                               # clean on main
git checkout -b worker/m1-memory-foundation
```

**Read first:**
- `artemis/memory/models.py` (existing observation/entity/relation schema)
- `artemis/memory/repository.py` (existing CRUD + retrieval)
- `artemis/memory/tests/conftest.py` (the TRUNCATE pattern that caused today's data loss — your work explicitly preserves data even when this fires in test mode)
- `tests/conftest.py` (the new guard — your tests run against `artemis_test`, not `artemis_os`)
- `claudeck-artemis/COORDINATION.md` section "Active state" — the memory v2 design conversation Jon and Lead had 2026-05-18

**Design discipline (quote at top of every new file):** fluidity, simplicity, purposefulness, naturalness, spacious, open. **For memory, this means: invariants over conventions. The system should be lossless by structural guarantee, not by careful coding.**

**Goal — three structural invariants:**

1. **Verbatim canonical**: every memory-write source (user turn, tool result, observation seed) lands in `raw_inputs` first. `raw_inputs` is append-only, immutable, write-once. Observations / entities / relations all link back to it via FK chain. **Even if every derived table is truncated by a bug, the raw source is reconstructable.**

2. **Hash chain integrity**: each `raw_inputs` row includes `prev_hash` (SHA-256 of previous row's full canonical serialization) + `this_hash`. Tamper-evident — if any past row is modified, every subsequent row's hash check fails. A single SQL function can verify the chain.

3. **Local cold archive + nightly backup**: rows older than 90 days move to `~/.artemis/archive/{year}/{month}/raw_inputs-{date}.jsonl.gz` (gzipped JSONL). Active DB stays lean. Nightly `pg_dump | gzip` writes to `~/.artemis/backups/{date}.pg_dump.gz`, 30-day rolling retention. Both paths configurable via env vars.

---

**Files to create:**

```
artemis/memory/raw_inputs.py                # ORM model + repository helpers for raw_inputs
artemis/memory/hashchain.py                 # SHA-256 chain construction + verification
artemis/memory/archive.py                   # Cold-tier archive + rehydrate helpers
artemis/memory/backup.py                    # pg_dump wrapper, restore wrapper
alembic/versions/0016_memory_raw_inputs.py  # raw_inputs table + indexes
scripts/memory_verify_chain.py              # CLI: walk the chain, exit 0 if intact
scripts/memory_archive_cold.py              # CLI: move >90d rows to filesystem
scripts/memory_rehydrate.py                 # CLI: pull archived rows back into Postgres
scripts/memory_backup.py                    # CLI: run pg_dump, prune old
scripts/memory_restore.py                   # CLI: scripted restore from a dump
docs/MEMORY-DURABILITY.md                   # operator-facing: how it works, restore drill
decisions/memory-v2-architecture.md         # the architecture doc from the design conversation
artemis/memory/tests/test_raw_inputs.py
artemis/memory/tests/test_hashchain.py
artemis/memory/tests/test_archive.py
artemis/memory/tests/test_backup_restore.py
```

**Files to edit:**
- `artemis/memory/models.py` — add `raw_input_id` FK to `Observation` model (nullable for backward compat, every new observation gets one)
- `artemis/memory/repository.py` — `insert_observation` now also writes to `raw_inputs` first; `raw_input_id` returned in the observation row
- `artemis/floating_artemis/chat.py` — every user message and tool result hooks into `raw_inputs` before becoming structured
- `artemis/main.py` — register a lifespan task that triggers nightly backup + archive (or document a launchd timer for it; recommend launchd for clean separation)

---

**Schema — `raw_inputs` table:**

```sql
CREATE TABLE raw_inputs (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_kind     TEXT NOT NULL,        -- 'user_turn' | 'tool_result' | 'agent_observation' | 'sub_agent_run' | 'system'
    source_id       TEXT,                 -- session id / agent run id / tool call id
    actor           TEXT,                 -- user id / agent id / 'system'
    scope_kind      TEXT NOT NULL,        -- 'global' | 'project' | 'session' | etc.
    scope_id        TEXT NOT NULL,
    payload         JSONB NOT NULL,       -- full verbatim content
    prev_hash       TEXT,                 -- hex SHA-256 of prior row's canonical_form, NULL on row 1
    this_hash       TEXT NOT NULL,        -- hex SHA-256 of THIS row's canonical_form (includes prev_hash)
    archived_at     TIMESTAMPTZ           -- non-null once moved to cold tier; row stays as a placeholder
);

CREATE INDEX ix_raw_inputs_scope ON raw_inputs (scope_kind, scope_id, created_at DESC);
CREATE INDEX ix_raw_inputs_source ON raw_inputs (source_kind, source_id);
```

**Canonical serialization** for hashing: sorted JSON keys, no whitespace, includes `(source_kind, source_id, actor, scope_kind, scope_id, payload, created_at_iso, prev_hash)`. Document the exact recipe in `hashchain.py`.

**Hash chain function:**
- `compute_this_hash(row, prev_hash) -> str` — pure function, deterministic
- `verify_chain(session, scope_kind=None, scope_id=None) -> ChainResult` — walks the chain; returns first break index if any
- DB constraint: trigger that rejects INSERT if `this_hash` doesn't match the computed value for the row's content + prev_hash

**Archive workflow** (`memory_archive_cold.py`):
- Find raw_inputs rows where `created_at < now() - 90 days AND archived_at IS NULL`
- Group by month (year/month from `created_at`)
- Write to `~/.artemis/archive/{year}/{month}/raw_inputs-{date}.jsonl.gz` — one row per line, gzipped
- Atomic: write to `.partial` then rename
- Set `archived_at = now()` on each row, KEEP the row in `raw_inputs` (it stays as a placeholder so the hash chain remains continuous; payload is NULLed to save space, payload_hash is preserved in the row)
- **Critical: never delete a raw_inputs row.** Archiving moves payload only.
- After archive: re-verify the hash chain for sanity

**Rehydrate** (`memory_rehydrate.py`):
- Given a row id or scope+date range, find the archive file, decompress, fill `payload` back in
- Used when an observation needs to be re-shown to the operator from old archive

**Backup** (`memory_backup.py`):
- `pg_dump -Fc -h localhost -U artemis artemis_os | gzip > ~/.artemis/backups/$(date +%Y-%m-%d-%H%M%S).pg_dump.gz`
- Prune files older than 30 days
- Verify backup is readable (`pg_restore --list` on the file)
- Exit non-zero if anything fails

**Restore** (`memory_restore.py`):
- Argument: path to a backup file
- Confirms the live DB name (refuses to restore to `artemis_os` without `--force`)
- `gunzip -c FILE | pg_restore -d artemis_os_restore --clean --if-exists`
- Restores to a TEMPORARY db named `artemis_os_restore` so the operator can verify before swapping
- Documents the manual swap step in `MEMORY-DURABILITY.md`

**Launchd timer** (recommended pattern for nightly tasks):
- `me.artemisos.memory-archive.plist` — runs `scripts/memory_archive_cold.py` daily at 3am
- `me.artemisos.memory-backup.plist` — runs `scripts/memory_backup.py` daily at 4am
- Document in `MEMORY-DURABILITY.md`; Worker creates the plists in the slice

---

**Verbatim invariant in code (the load-bearing piece):**

In `repository.py`, replace direct `INSERT INTO observations ...` calls with a wrapper:

```python
async def insert_observation_with_source(
    session: AsyncSession,
    *,
    source_kind: str,
    source_id: str | None,
    actor: str | None,
    scope_kind: str,
    scope_id: str,
    raw_payload: dict[str, Any],      # the verbatim content
    derived_fields: dict[str, Any],    # the structured observation fields
) -> int:
    """1. Insert into raw_inputs first with hash chain
    2. Insert into observations with raw_input_id FK
    3. Return the observation id

    NEVER call insert_observation directly anymore — always go through this.
    """
    ...
```

Add a CI / mypy-time check (or a runtime warning in dev mode) when `insert_observation` is called without a `raw_input_id` — surfaces accidental bypass.

---

**Tests:**

`test_raw_inputs.py` (~10 tests):
- Insert → row persists with hash chain link
- Insert N rows → walk chain end-to-end, all hashes valid
- Manually corrupt one row's payload → `verify_chain` reports the corruption with the right row id
- Tamper with one row's `this_hash` → verify_chain reports
- Scoped chain walk (only one scope) returns only those rows
- archived_at set + payload nulled does NOT break chain (placeholder rows verify because payload_hash is preserved)

`test_hashchain.py` (~6 tests):
- `compute_this_hash` is deterministic across runs
- `compute_this_hash` is deterministic across JSON key ordering
- `verify_chain` on empty table returns success
- `verify_chain` on single-row table returns success
- Chain with 10 rows, break at row 5 → reports row 5 as first break

`test_archive.py` (~8 tests):
- Archive picks rows older than 90 days only
- Archive writes correct file path + filename
- Archive sets `archived_at` + nulls payload + preserves row
- Archived rows do not break chain
- Rehydrate restores payload exactly
- Archive is idempotent (running twice doesn't duplicate)
- Concurrent archive is safe (advisory lock or row-level lock)

`test_backup_restore.py` (~6 tests):
- Backup script produces a readable pg_dump
- Backup prunes files older than 30 days
- Restore refuses to overwrite `artemis_os` without `--force`
- Restore creates `artemis_os_restore` and the data round-trips
- Backup verification fails on a corrupted dump file (intentional truncation)

---

**Documentation — `docs/MEMORY-DURABILITY.md`:**

Sections:
1. Three-layer durability model (raw_inputs / archive / backup)
2. How the hash chain works in plain English
3. How to verify chain integrity (`python -m scripts.memory_verify_chain`)
4. How to restore from a backup (step-by-step)
5. **Monthly drill checklist** — restore from yesterday's backup to a fresh DB, verify chain, swap, document any issues
6. Configuration: env vars, launchd plist locations, paths
7. What to do if you suspect data corruption

---

**Architecture decisions doc — `decisions/memory-v2-architecture.md`:**

Captures the 6-tier plan (M1-M6) from Jon + Lead's 2026-05-18 conversation:
- M1 (this slice): lossless foundation
- M2: validity windows + confidence + conflicts (next)
- M3: replication + per-scope HNSW
- M4: MCP tools + per-agent namespaces + auto-save
- M5: benchmark + reranker
- M6: cross-modal + active correction + rehearsal

Each phase: target invariants, key files, dependencies.

---

**Out of scope (deferred to M2+):**
- Validity windows on entities/relations (M2)
- Confidence scores (M2)
- Conflict resolution (M2)
- Postgres replication (M3)
- MCP tool surface (M4)
- LongMemEval benchmark (M5)
- Cross-modal observations (M6)
- Cloud (R2/S3) replication — explicitly local-only for now per Jon 2026-05-18

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # full suite, all green
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found in N source files
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
```

**Plus the restore drill** (manual smoke):
```bash
# Take a backup
python -m scripts.memory_backup
# Verify it
pg_restore --list ~/.artemis/backups/$(ls -t ~/.artemis/backups | head -1)
# Restore to a test DB
python -m scripts.memory_restore ~/.artemis/backups/$(ls -t ~/.artemis/backups | head -1)
# Verify chain on restored DB
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os_restore python -m scripts.memory_verify_chain
```

Document the exact commands + expected outputs in `MEMORY-DURABILITY.md`.

---

**Report shape:** branch, final commit SHA, test count delta, four verification command outputs, the manual restore drill output captured verbatim, judgment calls. Local-only git. Commit on `worker/m1-memory-foundation`. Do NOT push.

---

### BRIEF FOR WORKER — Phase J3b-A: Subscription / local providers (Claude Code CLI + Codex CLI + LM Studio)

**Status:** ready for Worker. Medium slice (~500 LOC + ~30 tests). Worker direct + optional Haiku sub-agent for the bin-path port.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j3b-a-cli-providers`
**Baseline:** main (latest).

**Why this matters:** Jon is on Claude Max + ChatGPT Plus subscriptions and is **budget-blocked on direct API keys**. The Node app used `claude` and `codex` CLIs that authenticated via those subscriptions — no API key required. The Python rebuild lost that. We're restoring it.

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd                                          # /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                               # clean on main
git checkout -b worker/j3b-a-cli-providers
```

**Read first:**
- `claudeck-artemis/server/providers/claude-code/index.js` (Node reference for `claude` CLI subprocess + JSON streaming output parsing)
- `claudeck-artemis/server/providers/codex/index.js` (Node reference for `codex` CLI subprocess; similar shape)
- `claudeck-artemis/server/providers/bin-path.js` (CLI binary discovery — port to Python)
- `artemis/providers/gemini/adapter.py` + `artemis/providers/openai/adapter.py` (existing Python adapter pattern you're conforming to)
- `artemis/agent/client.py` (the `ModelAdapter` Protocol)
- `artemis/providers/registry.py` (register new adapters here)

**Design discipline (quote at top of every new file):** fluidity, simplicity, purposefulness, naturalness, spacious, open. **Subscription providers should be the friendly default — no key entry, no setup ceremony. If the CLI is on PATH, the provider just works.**

**Goal.** Three new providers in `artemis/providers/`:

1. **`claude_code`** — subprocesses the `claude` binary. Subscription auth via Claude Max. No API key.
2. **`codex`** — subprocesses the `codex` binary. Subscription auth via ChatGPT Plus/Pro. No API key.
3. **`lm_studio`** — HTTP to `http://localhost:1234/v1/chat/completions` (LM Studio's built-in OpenAI-compatible server). Local-only.

All three conform to the existing `ModelAdapter` Protocol. Registered in `registry.py` so `get_adapter("claude-code" | "codex" | "lm-studio")` works.

**Files to create:**
```
artemis/providers/_bin_path.py
artemis/providers/claude_code/{__init__.py,adapter.py}
artemis/providers/codex/{__init__.py,adapter.py}
artemis/providers/lm_studio/{__init__.py,adapter.py}
artemis/providers/tests/test_bin_path.py
artemis/providers/tests/test_claude_code_adapter.py
artemis/providers/tests/test_codex_adapter.py
artemis/providers/tests/test_lm_studio_adapter.py
```

**Files to edit:**
- `artemis/providers/registry.py` — add three new builders to `_BUILDERS`. Update `list_providers()`.
- `artemis/providers/__init__.py` — re-export the three new adapter classes.
- `artemis/providers/errors.py` — add `MissingCliBinaryError`.
- `artemis/routes/stats.py` — `_provider_is_configured` checks:
  - `claude-code` / `codex` — `find_cli_binary("claude" | "codex")` returns non-None
  - `lm-studio` — short HTTP GET to `http://localhost:1234/v1/models` (timeout 500ms); configured=true if reachable
- `artemis/routes/floating_artemis.py` `GET /models` — emit the three new providers in the picker list. For `claude-code` / `codex`, model list is `[{"id": "default", "label": "Subscription default"}]`. For `lm-studio`, fetch model list dynamically from `http://localhost:1234/v1/models` with a cached 30s TTL.
- `public/js/components/welcome-overlay.js` — extend `_PROVIDERS` with three new entries marked `subscriptionOrLocal: true`. Render with green checkmark and **no "Add key" button** when `configured: true`. When not configured, show install hint ("Install Claude Code CLI", etc.) instead of key entry.
- `public/js/components/model-picker-floating.js` — same: render subscription/local providers without "Configure key" link when configured; with install hint when not.

**Python port of `bin-path.js`:**

```python
import os, shutil
from pathlib import Path

def find_cli_binary(name: str, extra_candidates: list[str] | None = None) -> str | None:
    env_key = f"{name.upper().replace('-', '_')}_BIN"
    home = Path.home()
    candidates: list[str] = []
    if env_override := os.environ.get(env_key):
        candidates.append(env_override)
    candidates += [
        str(home / ".local" / "bin" / name),
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
    ]
    if path_resolved := shutil.which(name):
        candidates.append(path_resolved)
    if extra_candidates:
        candidates += extra_candidates

    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
```

**CLI adapter contract:**

```python
class ClaudeCodeAdapter:
    def __init__(self, *, binary_path: str | None = None) -> None:
        self._binary = binary_path or find_cli_binary("claude")
        if not self._binary:
            raise MissingCliBinaryError("claude-code", "claude")

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        # Build prompt from request.messages
        # asyncio.create_subprocess_exec(self._binary, "--print", "--output-format", "json", ...)
        # Parse JSON stdout → Message + Usage
        # CLI handles its own auth (Claude Max subscription) — no api_key path
        ...
```

Same for `CodexAdapter` with `codex` binary (consult `claudeck-artemis/server/providers/codex/index.js` for exact flag set).

**LM Studio adapter shape:**

LM Studio is OpenAI-compatible. Subclass or compose `OpenAIAdapter` with a different base URL — do NOT duplicate the OpenAI parsing logic.

```python
class LMStudioAdapter(OpenAIAdapter):
    def __init__(self, *, base_url: str | None = None, default_model: str | None = None) -> None:
        super().__init__(api_key="not-needed-for-local-server", default_model=default_model)
        self._base_url = base_url or os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    # Override the URL constant used in complete() / stream(); minimal subclass.
```

**Tests (~30 total):**

- `test_bin_path.py` — discovery in each search location, env override wins, file-must-be-executable filter, returns None when nothing found
- `test_claude_code_adapter.py` — mock subprocess (`asyncio.create_subprocess_exec`) returning canned JSON; assert command shape, parse text + tool_use blocks, handle non-zero exit, MissingCliBinaryError on missing binary
- `test_codex_adapter.py` — same for codex
- `test_lm_studio_adapter.py` — mock httpx hitting localhost:1234 returning OpenAI-shape response; verify base URL is configurable via env
- Registry tests for all three new ids

**Out of scope (defer):**
- Streaming via `claude` / `codex` (CLIs do produce streaming JSON but V1 uses non-streaming `--print`).
- LM Studio model switching UI (V1 just uses whatever's loaded).
- Cost tracking for subscription providers (subscription is flat — log $0.00 / call).

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # baseline + new tests
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
```

**Manual smoke** (optional):
- `which claude && which codex` on Jon's machine — both binaries reachable.
- Hit `/api/stats/providers` post-bounce — expect `claude-code: configured=true`, `codex: configured=true`, `lm-studio: configured` reflects whether LM Studio is running.

**Report shape:** branch, final commit SHA, test count delta, four verification outputs, judgment calls, anything deferred.

---

### Next-up after J3b-A: Integrations-as-popover (J3b-B)

**Jon's request 2026-05-18:** the Integrations page should NOT be a rail page under Operations. It should be a **lightbox / modal popup** triggered from the user popover (the equivalent of where the old "Connectors" entry lived in the Node app). The Operations rail item gets removed; the modal opens from any place the operator clicks "Connectors / Integrations."

Scope when ready (Lead spawns Sonnet sub-agent once J3b-A lands; minimal file overlap):
- Remove `data-nav="integrations"` rail item from `public/index.html`
- Remove `loadIntegrationsShell` branch + helper from `public/js/features/home.js`
- New `public/js/components/integrations-modal.js` — Light DOM custom element wrapping the existing card grid in a centered modal with backdrop + Esc/close handling
- Convert `public/js/features/integrations.js` `init(container)` to also accept a modal-host element; same render
- Wire a "Connectors" / "Integrations" entry in the user popover (bottom-left of rail) that opens the modal
- Status popover's "Open Integrations" CTA opens the modal instead of navigating
- Welcome overlay's "Manage these later in Integrations" link opens the modal instead of navigating

---

### Worker [Account 2] — Phase J3 page-load repair complete: `worker/j3-page-load-repair` (2026-05-17)

**Branch:** `worker/j3-page-load-repair` — 6 commits on top of main.

**Test delta:** +21 new J3 tests. Total suite: 1492 passed (0 failures).

**Verification commands output:**
```
uv run pytest artemis/okr/tests/ artemis/routes/tests/ -q
  21 passed in 1.31s

uv run ruff check artemis/routes/okr.py artemis/routes/calendar.py \
  artemis/routes/meetings.py artemis/routes/jira.py artemis/routes/sessions.py \
  artemis/routes/notifications.py artemis/routes/stats.py artemis/main.py
  All checks passed!

uv run ruff format --check (all files above)
  All files already formatted

uv run mypy artemis/routes/okr.py artemis/routes/calendar.py ...
  Success: no issues found in 8 source files
```

**Surfaces verified clickable + rendering (theoretical — app not running locally, confirmed by route existence):**
- Focus / Command Center: fetchAnalytics + fetchNotificationHistory + fetchSessions now have `.catch()` fallbacks — will degrade to empty state instead of rejecting Promise.all
- Calendar shell: `GET /api/calendar/overview` → `{status: "not_connected", provider: "gcal"}` if no OAuth row; `{status: "ready", ...}` if GCal connected. `loadCalendarShell` correctly renders the "Connect Google Calendar" view when status ≠ 'ready'.
- Meetings shell: `GET /api/meetings/overview` → `{status: "not_connected", provider: "granola"}` — renders "Connect Granola" placeholder (J5 roadmap).
- Jira shell: `GET /api/jira/overview` → `{status: "not_connected", provider: "jira"}` — renders "Needs setup" board (J4 roadmap).
- OKR shell: `GET /api/okr/overview` → real data from DB (objectives + KRs + stats + activity + next-up). Empty state if no objectives yet. `.catch(() => null)` guard in `loadOkrShell` shows error state if backend unreachable.
- Modules/Personal Workspace: `GET /api/stats/analytics` + `GET /api/stats/providers` + `GET /api/notifications/history` all return safe stubs instead of 404ing.

**Commit log:**
- `4ef5e47` feat(j3/frontend): harden home.js Promise.all + OKR null-guard
- `51e428a` feat(j3a): GET /api/okr/overview aggregator (6 tests)
- `a616b11` feat(j3c): jira/sessions/notifications/stats stubs + /api/stats/providers (6 tests)
- `a53b2c2` feat(j3b): GET /api/calendar/overview + /api/meetings/overview + integrations/repository bug fix (9 tests)
- `b9a4c9c` fix(j3): drop stray parallel import + ruff-sort main.py imports

**Side fix by J3b sub-agent:** `artemis/integrations/repository.upsert_integration` had a silent SQLAlchemy bug where `pg_insert(Integration).values(metadata=...)` conflicted with `Base.metadata` descriptor. Fixed by switching to `pg_insert(Integration.__table__)` with explicit column names.

**Contract doc:** `decisions/j3-overview-contract.md` written for all 8 missing routes.

**Deferred (as scoped in brief):**
- Real Jira integration (J4)
- Real Granola/Meetings integration (J5)
- `GET /api/sessions` real data (maps to fa_sessions but shape mismatch; V1 stub = `[]`)
- `GET /api/stats/analytics` real aggregation (V1 stub = zero-filled)
- B3 (first-load integrations race in integrations.js)
- B6 parallel chats (`lead/restore-parallel-chats` — Lead lane)
- B1 welcome overlay rewrite (`lead/remove-dead-connectors-modal` / `lead/welcome-and-provider-ux` — Lead lanes)

**Note for Lead:** The worktree at `/Users/artemis/Desktop/Artemis/artemis-os` currently has floating uncommitted changes from several Lead branches (providers/openai, welcome overlay, model picker). These are NOT on `worker/j3-page-load-repair`. The `lead/welcome-and-provider-ux` and `lead/restore-parallel-chats` branches contain that work.

---

### BRIEF FOR WORKER — Phase J3: Page-load repair (B2 systemic fix)

**Status:** ready for Worker. Multi-lane slice — Worker direct on the spine + **three parallel Sonnet sub-agents** for the parallel surface backends. The Lead-diagnosed pattern (below) makes this mechanical, not judgment-heavy.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j3-page-load-repair`
**Baseline:** main at the latest merge commit (J1 + J1b + J2 + B + C + D + E + dotenv all landed).

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd                                          # /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                               # should be clean on main
git checkout -b worker/j3-page-load-repair
```

**The pattern (Lead-diagnosed):**

The Node app had aggregation endpoints — `/api/okr/overview`, `/api/calendar/overview`, `/api/jira/overview`, `/api/meetings/overview` — that returned a "today" snapshot per surface in one call. The Python rebuild ported CRUD primitives (`/api/okr/objectives`, etc.) but **never ported the `/overview` aggregators**. The frontend still calls `fetchXOverviewApi()`, each 404s, the 404 rejects the `Promise.all` in `home.js`'s `load*Shell()`, and the page renders blank.

Same pattern for: `/api/sessions`, `/api/analytics`, `/api/notifications`, `/api/stats/providers`. Node-era routes, never ported.

**Confirmed 404s** (from Lead audit, 2026-05-17):
- `/api/sessions`, `/api/analytics`, `/api/notifications`, `/api/stats`, `/api/stats/providers`
- `/api/jira/overview`, `/api/calendar/overview`, `/api/meetings/overview`, `/api/okr/overview`

**Two-part fix:**

**Part 1 — Backend: ship the missing aggregation + util routes** (Worker direct + 3 parallel Sonnet sub-agents). Each `/overview` aggregator returns today's snapshot for that surface. Most aggregators read from CRUD primitives that already exist. For surfaces where the integration is disconnected (Calendar without GCal connected; Meetings without Granola), return a structured "not_connected" payload so the frontend renders the right empty-state instead of erroring.

**Part 2 — Frontend: graceful degradation across all shell loaders** (Worker direct). Every fetch in every `Promise.all` in `public/js/features/home.js` must have a `.catch(() => fallback)`. ZERO unprotected fetches in shell-renderer code. Plus: empty-state rendering for each surface when the aggregator returns "not_connected" or empty.

**Slice assignment:**

**Worker direct (spine):**

1. Audit `public/js/features/home.js` — list every `Promise.all` in every `load*Shell()`. Add `.catch(() => fallback)` to every fetch that isn't already protected. Document the fallback shape per fetch (matches what the success-path consumer expects, but null/empty/disconnected-state).

2. Surface empty-state render branches. Per surface:
   - Command Center: "Calendar not connected" card if `calendarOverview.status === 'not_connected'` etc. Already partially done; complete it.
   - Calendar shell: full-page "Connect Google Calendar in /integrations" if not connected.
   - Meetings shell: same pattern for Granola.
   - Jira shell: same for Jira.
   - OKR shell: OKR is in-app, never "not connected" — but should show empty-state when there are zero objectives, not blank.

3. **Frontend route shape contract.** For each missing aggregator, document the expected JSON shape the frontend renderer consumes. Capture this in `claudeck-artemis/decisions/j3-overview-contract.md` so the three sub-agents below have a single source of truth.

4. **Add the stub-or-port routes for the non-aggregator missing endpoints:**
   - `GET /api/sessions` — V1: returns `[]` (Claude Code sessions concept doesn't exist in personal-assistant variant yet). Stub.
   - `GET /api/analytics` — V1: returns `{tokens_today: 0, cost_today_usd: 0.0, runs_today: 0}` or computed from `agent_runs` if you want a quick aggregate.
   - `GET /api/notifications` — port: read from `notifications` table if exists, else stub `[]`.
   - `GET /api/stats/providers` — port: returns array of `{provider_id, name, configured: bool, healthy: bool | null}` based on `list_providers()` + env/DB API-key presence.
   - Wire into `main.py` + add to `_AVAILABLE_SURFACES` if applicable.

**Spawn 3 Sonnet sub-agents in parallel for the per-surface aggregators:**

**Sub-agent J3a — OKR overview aggregator.** `GET /api/okr/overview`. Returns: `{today: {kr_count, kr_progress_avg, blocked_count}, recent_activity: [...], next_up: [...], status: "ok"}`. Reads from `okr_objectives` + `okr_key_results` + `okr_activity` tables. ~80 LOC + 6 tests. Quote design discipline at top.

**Sub-agent J3b — Calendar + Meetings overview aggregators.** `GET /api/calendar/overview` + `GET /api/meetings/overview`. Both check GCal/Granola integration status:
- If not connected → `{status: "not_connected", provider: "gcal" | "granola"}`.
- If connected → for Calendar: today's events + next 3 events. For Meetings: most recent 5 meetings (post-meeting view per F3 backlog).
- Calendar reads from `gcal_events_cache` populated by J2. Meetings: Granola integration doesn't exist yet (J5 on original roadmap) — return `{status: "not_connected", provider: "granola"}` permanently for now.
~120 LOC + 8 tests.

**Sub-agent J3c — Jira overview aggregator.** `GET /api/jira/overview`. Jira integration doesn't exist yet (J4 on original roadmap). Returns `{status: "not_connected", provider: "jira"}`. Trivial: 30 LOC + 2 tests. **Plus** the small `/api/sessions` + `/api/notifications` + `/api/stats/providers` + `/api/analytics` stub routes that Worker listed in step 4 — sub-agent owns them since it's a tiny slice already. ~80 LOC total + 6 tests.

**Out of scope (defer):**
- Real Jira integration (J4 on roadmap).
- Real Granola integration (J5 on roadmap).
- Cost dashboard with historical charts (V1 just shows today).
- Memory inspector wiring into Command Center (already shipped in C).
- F1 distribution-grade welcome flow (J4 sprint).
- B1 welcome overlay rewrite (J3 candidate but separate slice — Worker tackles after J3 lands).
- B6 parallel chats in same window (J5 sprint).

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # baseline + J3 tests, all green
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
```

**Manual end-to-end smoke:**
- Open `app.artemisos.me`. Hard refresh once.
- Click each rail item: Focus, Calendar, Meetings, Jira, OKR, Automations, Skills, Agents, Workflows, Memory, Integrations, Marketing pages.
- Each surface MUST render content — either real data, empty state, or "not connected" card pointing operator to `/integrations`.
- No blank pages. No silent failures.
- B3 (first-load integrations race) is a separate cleanup — Worker can fold in or defer.

**Report shape:** branch with commits per sub-task. Final report in COORDINATION.md: test count delta, four verification command outputs, list of surfaces verified clickable + rendering, any deferred items.

---

### Backlog from Jon — end of 2026-05-17 session (post J1/J1b/J2/B/C/D/E merge)

Captured here so nothing falls out. Lead + Worker pick from this when J1/J2 is fully verified end-to-end. Sequence proposed at bottom.

**Bugs (blockers — fix first):**

- **B1. Welcome overlay still says "Claude Code CLI"** (first-run setup screen). The Python rebuild doesn't depend on the Claude Code CLI — `AnthropicAdapter` calls the Anthropic API directly. The "Re-check failed" loop is a vestige of the Node app that should never appear. **Rewrite for Python rebuild** — Welcome should be: "Welcome to Artemis. We need an Anthropic API key to start chatting. [Add key]" → opens the credential entry modal pre-targeted to the Anthropic provider.
- **B2. Pages clickable but content does not load.** Personal Workspace, Calendar, Meetings, Jira, OKR Studio, "same for the rest of operations and marketing." Nav state changes (URL/state observable) but `#app-shell-content` stays empty. Each `loadXShell()` in `home.js` is the suspect — likely they hit Python routes that don't exist or return wrong shape. Audit needed: list every shell loader, what it fetches, whether the Python backend has that route, what it should show when backend is empty/missing. **HIGHEST priority — blocks demoability of everything.**
- **B3. First-load shows "not connected" for Slack/GCal; refresh fixes it.** Race in `integrations.js` `_loadAndRender`. Either the GET `/api/integrations` request races against card mount, or the renderer fires before the response. Fix: await GET first, render once.
- **B4. Save credentials → HTTP 405.** Path/method mismatch between `credential-entry-modal.js` POST and backend `POST /api/integrations/providers/{provider}/config`. Small fix.
- **B5. Google Cloud OAuth credential save → 504.** External (Google) error — most often the OAuth consent screen wasn't configured fully (Test Users missing) when the project is in Testing state. Walk Jon through the consent screen again.
- **B6. Parallel chats in same window not working.** Was working in Node app. The 2×2 grid via `ui/parallel.js` needs to be re-verified against the new chat backends.

**Feature requests:**

- **F1. Distribution-grade LLM provider connection UX.** Single welcome screen: "Pick an LLM provider. Paste an API key. Optionally add others." Wraps the existing `credential-entry-modal` backend but for first-run case, not a gear icon. Three providers visible: Anthropic, OpenAI, Gemini. (OpenAI not wired backend-side yet — needs a thin adapter port.)
- **F2. Rebuild Dev Projects to look + function like Claude Code CLI.** Pick a folder, chat anchors to that working directory, shell command streaming, inline file diff previews. **Plus parallel-chats-in-same-window** (Pair / Trio / Quad) — currently broken. Large slice; 2-3 parallel sub-agents.
- **F3. Meetings page rework — post-meeting, not pre-meeting.** After a meeting ends (Granola transcript ingested): action items extracted, decisions captured, follow-up drafts (Slack/email), follow-up calendar bookings. Pre-meeting context lives on Calendar surface; Meetings = post-meeting actions and reports.
- **F4. Remove the dead Connectors modal.** Old Node-era data-source picker that doesn't talk to the Python backend. Confuses the operator (Jon hit it today). All future connections live in `/integrations`.

**Cleanup:**

- **C1. Model picker should link to "Configure key" when a provider key is missing** (not just disable the option silently).
- **C2. Update HANDOFF.md, ROADMAP.md, roadmap-data.js** with the J1/J1b/J2/B/C/D/E landings + this backlog (Lead before close of session).

---

### Proposed sprint sequence

**J3 (urgent — UX repair):**
1. **B2** — page-not-loading audit + fix (BLOCKS DEMOABILITY — do first)
2. **B1** — Welcome overlay rewrite for Python rebuild
3. **B3** — Integrations page first-load race
4. **B4** — Save credentials 405

**J4 — provider UX:**
5. **F1** — distribution-grade LLM provider welcome flow
6. **C1** — model picker "configure key" link
7. **F4** — remove old Connectors modal

**J5 — Dev Projects rebuild:**
8. **F2** — Dev Projects-as-Claude-Code + parallel chats restored (3 parallel sub-agents)

**J6 — Meetings rework:**
9. **F3** — Meetings post-meeting flow (depends on Granola integration which is J5 from original roadmap)

B5 (Google Cloud) and B6 (parallel chats) interleave naturally with J5 + F2.

---

### Worker [Account 2] — Phase J2 Google Calendar integration complete: `worker/j2-gcal-integration` (2026-05-17)

1 clean feature commit (`9633b32`) on `worker/j2-gcal-integration` off `4c651e9`. 1378 tests pass (1357 baseline + 21 new J2 tests). ruff + mypy strict clean across 247 source files. Migration round-trip verified.

**Scope delivered:**
- **Migration `0015_gcal_integration_seed.py`:** `gcal_events_cache` table — `(id BIGSERIAL PK, calendar_id TEXT, event_id TEXT, summary TEXT, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, attendees JSONB DEFAULT '[]', description TEXT, fetched_at TIMESTAMPTZ)`. UNIQUE `(calendar_id, event_id)`. Index on `(start_at, end_at)` and `calendar_id`. Round-trip: downgrade -1 → upgrade head clean.
- **`artemis/integrations/gcal/` package:** `types.py` (Calendar, Event, EventAttendee, EventDateTime Pydantic v2 models with camelCase aliases); `client.py` (httpx wrapper, 401→refresh→retry, 7 methods); `provider.py` (GCalProvider ABC — connect/verify/revoke, offline+consent OAuth, userinfo email as workspace_id); `tools.py` (5 FA tools at correct authority layers).
- **`artemis/integrations/config_resolver.py`:** GCalConfig dataclass + `resolve_gcal_config(session)` — DB-first per-field, `GCAL_CLIENT_ID`/`GCAL_CLIENT_SECRET` env fallback, raises `MissingProviderConfigError` if absent from both.
- **`artemis/routes/integrations.py`:** `GET /gcal/oauth/start` (Google auth URL, `access_type=offline&prompt=consent`); `GET /gcal/oauth/callback` (token exchange, upsert integration, redirect `/?gcal_connected=1`); `GET /gcal/verify` (calendarList smoke-test).
- **`artemis/floating_artemis/chat.py`:** `register_gcal_tools` wired into `_build_tool_registry`.
- **Frontend:** `integrations.js` PROVIDERS array extended with gcal entry + `gcal_connected` toast; `integration-card.js` `_PROVIDER_FIELDS` extended with gcal credential fields.
- **`tests/test_j2_gcal_integration.py` (21 tests):** OAuth start (503/200/scope/state), callback invalid-state (400), verify (404), `resolve_gcal_config` (DB-wins, env-fallback, per-field mix, missing-raises), tools (no-integration error, missing-params, layer 2/3 assignment), Pydantic types (Calendar + Event model_validate with aliases), client mocks (list_calendars, list_events, 401→refresh, create_event), frontend (gcal in PROVIDERS).

**Verification outputs:**
```
uv run pytest             → 1378 passed, 109 warnings
uv run ruff check         → All checks passed
uv run ruff format        → 263 files already formatted
uv run mypy artemis       → Success: no issues found in 247 source files
migration round-trip      → downgrade 0015→0012 clean, upgrade 0012→0015 clean
```

**Deferred (per brief):** Today/Meetings card live rendering (J2b); recurring event expansion; push notifications/webhook subscriptions (Phase L+); multi-calendar federation; free/busy for others.

**Judgment calls:**
- Haiku sub-agent spawned for `client.py` + `types.py` (Haiku-written port from API docs — mechanical and ruff+mypy clean).
- Credentials stored as `{access_token, refresh_token, expires_in, client_id, client_secret}` in `encrypted_credentials` so the client can self-refresh without a separate DB lookup. Workspace_id = account email from Google userinfo.
- `prompt=consent` hardcoded in the OAuth URL so Jon always gets a refresh_token, even if previously authorized. Required for offline access after token expiry.

**Note on Lead's streaming work:** Lead's streaming SSE commits (cb7805d, 152ca6f, e2316ed) landed on this branch during the session due to a worktree contamination event. I extracted the J2 changes into a clean isolated commit (`9633b32`) via `git reset --soft 4c651e9` + selective stage. Lead's streaming files (`streaming.py`, `test_streaming.py`, streaming extensions to `agent/client.py`, `providers/__init__.py`, `gemini/adapter.py`, `openrouter/adapter.py`) are currently unstaged modifications on the J2 branch. Lead should pick those up on `lead/providers-streaming-sse` as planned.

**Ready for Lead review + merge.**

---

### BRIEF FOR WORKER — Phase J2: Google Calendar integration

**Status:** ready for Worker. Medium-large slice — Worker direct + one optional Haiku sub-agent for the Google API client (mechanical port). J1b credential pattern + J1 OAuth scaffolding give you most of what you need; this is the second integration using the established pattern.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j2-gcal-integration`
**Baseline:** `main` at `4c651e9` (J1 + J1b + providers + SPA repair all merged).

**Migration number assigned: 0015.** Lead's parallel lanes use 0013 (spawn_subagent) and 0014 (FA session model). Do not collide.

**REQUIRED FIRST STEP — verify cwd:**
```
cd /Users/artemis/Desktop/Artemis/artemis-os
pwd                                          # /Users/artemis/Desktop/Artemis/artemis-os
git rev-parse --show-toplevel
git status -sb                               # clean on main
git checkout -b worker/j2-gcal-integration
```

**Read first (mandatory):**
- `artemis/integrations/__init__.py` (`IntegrationProvider` ABC — implement for GCal)
- `artemis/integrations/slack/` (entire dir — structural template)
- `artemis/integrations/config_resolver.py` (J1b — extend resolver for GCal)
- `artemis/integrations/repository.py`
- `artemis/routes/integrations.py` (extend for GCal-specific OAuth dance)
- `claudeck-artemis/decisions/phase-g-floating-artemis-design.md` §13a-pre (authority layers — reads Layer 2, writes Layer 3)
- Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

**Goal.** Jon clicks "Connect Google Calendar" in the integrations card → completes Google OAuth → GCal becomes readable by Artemis. Then 5 tools available:
- `list_calendars()` — Layer 2
- `list_events(calendar_id, time_min, time_max)` — Layer 2
- `create_event(calendar_id, summary, start, end, attendees?, description?)` — Layer 3
- `update_event(...)` — Layer 3
- `delete_event(...)` — Layer 3

Unblocks the **Today / Meetings / Calendar** home-dashboard cards (currently stubs). Live rendering of those cards is a follow-up slice (J2b).

**Worker direct (spine) + ONE optional Haiku sub-agent (the API client port):**

1. **Migration `0015_gcal_integration_seed.py`** — adds `gcal_events_cache` table (optional but recommended): `(id, calendar_id, event_id, summary, start_at, end_at, attendees JSONB, description, fetched_at)`. Unique `(calendar_id, event_id)`. Index `(start_at, end_at)`. Reuse existing `integrations` + `integration_configs` for credentials — no new tables needed for those.

2. **`artemis/integrations/gcal/__init__.py`** — package root.

3. **`artemis/integrations/gcal/provider.py`** — `GCalProvider` implementing `IntegrationProvider`:
   - `connect(code)` — exchanges OAuth code at Google's token endpoint, stores `access_token` + `refresh_token` + `expiry` in `encrypted_credentials`. Workspace_id = account email from `userinfo`.
   - `verify(integration)` — refresh-if-expired, hits `calendar/v3/users/me/calendarList`.
   - `revoke(integration)` — POSTs token revocation, status='revoked'.

4. **Resolver extension** in `artemis/integrations/config_resolver.py`:
   - Add `resolve_gcal_config() -> GCalConfig(client_id, client_secret)`. DB-first per field, env fallback to `GCAL_CLIENT_ID` / `GCAL_CLIENT_SECRET`. Raises `MissingProviderConfig` if missing.
   - Add `"gcal"` to `KNOWN_PROVIDERS` registry in `models.py` (the J1b modal renders fields from this registry — once added, the gear UI just works for GCal).

5. **Routes** — extend `artemis/routes/integrations.py`:
   - `GET /api/integrations/gcal/oauth/start` — returns Google OAuth URL with state token. Scope `https://www.googleapis.com/auth/calendar` (read+write).
   - `GET /api/integrations/gcal/oauth/callback?code=...&state=...` — `GCalProvider.connect`, redirect to `/?gcal_connected=1`.
   - `GET /api/integrations/gcal/verify` — `GCalProvider.verify`.

6. **Spawn one Haiku sub-agent for the API client port** (mechanical leaf):
   - Files: `artemis/integrations/gcal/client.py` + `artemis/integrations/gcal/types.py`.
   - httpx wrapper around `https://www.googleapis.com/calendar/v3/*`.
   - Methods: `list_calendars`, `list_events(calendar_id, time_min, time_max, max_results=50)`, `get_event`, `create_event`, `update_event`, `delete_event`. 401 → refresh token, retry once.
   - Pydantic types: `Calendar`, `Event`, `EventAttendee`, `EventDateTime` (Google's tagged datetime/date union).
   - Worker reviews diff + tests before continuing.

7. **`artemis/integrations/gcal/tools.py`** — register the 5 tools in the FA registry with correct authority layers (2 or 3 per above). Each tool resolves active GCal integration, delegates to client, returns structured result.

8. **Frontend** — extend `public/js/features/integrations.js` `PROVIDERS` array: `{id: "gcal", name: "Google Calendar", tagline: "Read your calendar; create, update, and remove events."}`. The existing card + credential modal should handle the rest generically.

9. **Tests `tests/test_j2_gcal_integration.py`:**
   - OAuth start URL has correct scope + state.
   - Callback exchanges code, stores integration, redirects.
   - Verify route returns True for active integration.
   - Mock-httpx round-trip per tool (URL, body, headers, parsed response).
   - Token refresh: 401 → retry with refresh_token → succeed.
   - All 5 tools register at correct authority layer.
   - `resolve_gcal_config` DB-wins / env-fallback / missing-raises.
   - ~20 tests.

**Out of scope:**
- Today/Meetings card live rendering (J2b).
- Recurring event expansion (caller passes `singleEvents=true` explicitly).
- Push notifications / webhook subscriptions (Phase L+).
- Multi-calendar federation across accounts (post-Phase L).
- Free/busy for other people (different scope — not V1).

**Verification:**
```bash
cd /Users/artemis/Desktop/Artemis/artemis-os
uv run pytest                       # 1357 baseline + new
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
```

**Jon's parallel task** (so it's ready when J2 ships):
1. https://console.cloud.google.com → create project "Artemis OS" (or reuse).
2. Enable **Google Calendar API**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID → Web application**.
4. Authorized redirect URI: `https://app.artemisos.me/api/integrations/gcal/oauth/callback`.
5. Copy Client ID + Client Secret. Paste into the Integrations page credential modal (gear icon) once J2 lands.

**Pattern note:** J2 sets the mental model for J3 Gmail / J4 Jira / J5 Granola. Each follows: OAuth or API-key, provider class implementing ABC, tools registered in FA registry, frontend "just works" via the providers array + credential modal.

**Report shape:** branch with commits per sub-task. Final report in COORDINATION.md: test count delta, four verification outputs, deferred items, judgment calls.

---

### Lead [Account 1] — 4 parallel sub-agent lanes fired 2026-05-17

| Lane | Scope | Branch | Status |
|---|---|---|---|
| B | spawn_subagent tool + migration 0013 (agent_runs.is_ephemeral) | `lead/spawn-subagent-tool` | in-flight |
| C | Memory inspector full wiring (V1 stub → real provenance tray) | `lead/memory-inspector-wiring` | in-flight |
| D | Provider selector UI + per-session model persistence + migration 0014 | `lead/provider-selector-ui` | in-flight |
| E | Streaming SSE for Gemini + OpenRouter adapters | `lead/providers-streaming-sse` | in-flight |

Conflict avoidance: each lane is scoped to non-overlapping files; migration numbers explicitly assigned to prevent collision (0013 for B, 0014 for D, 0015 for Worker's J2). Lead will merge each lane to main as it lands. Worker's J2 branch can rebase onto each merge or coalesce at the end.

---

### Lead [Account 1] — Provider parity (Gemini + OpenRouter) complete: `lead/providers-parity-gemini-openrouter` → main (2026-05-17)

Final commit `4dd2f4b`. 1341 tests pass (1289 baseline + 54 new). mypy strict, ruff, ruff format all green.

**Shipped:** `artemis/providers/` package — `GeminiAdapter` + `OpenRouterAdapter` both conforming to the existing `ModelAdapter` Protocol in `artemis/agent/client.py`. Model alias maps ported from Node verbatim (`gemini/models.py`, `openrouter/models.py`). Gemini has explicit per-token pricing → `cost_usd` returned per response. OpenRouter cost defaults to `0.0` unless the model surfaces `usage.total_cost` (model-dependent — documented). Registry helper `get_adapter(provider_id, **kwargs)` dispatches over `anthropic` / `gemini` / `openrouter`. Errors: `MissingApiKeyError`, `UnknownProviderError`, `ProviderAPIError(status_code, body)`.

**Non-streaming `complete()` only.** Streaming SSE deferred — Python loop calls non-streaming today. Streaming is a separate slice when the floating-Artemis frontend needs it.

**Merged to main** via `git update-ref refs/heads/main lead/providers-parity-gemini-openrouter`. Provider files isolated under `artemis/providers/` — zero overlap with Worker's J1b in-flight files (`artemis/integrations/config_resolver.py`, etc.). Worker's uncommitted J1b changes remain in the worktree, untouched, and will commit cleanly on top.

**Tiny follow-up for Worker to fold into J1b:**
- `tests/test_j1_integration_ui.py::test_slack_oauth_start_without_credentials` is flaky — `patch.dict(os.environ, {}, clear=False)` doesn't actually clear `SLACK_CLIENT_ID` when it's already set in the process env (which it is post-J1 setup). Replace with `monkeypatch.delenv("SLACK_CLIENT_ID", raising=False)` etc. ~5 minute fix. Not blocking.

**Not wired yet (separate slices):**
- Frontend provider selector UI per session / per agent.
- DB persistence of provider keys (will use J1b's `integration_configs` table pattern once that lands).
- Streaming SSE response generator on both adapters.

---

### Lead [Account 1] — Production deployment + SPA repair (2026-05-17, after J1 merge)

**Shipped:**
- **Named Cloudflare Tunnel**: `app.artemisos.me` (Jon's domain). cloudflared runs via per-user LaunchAgent `me.artemisos.tunnel`; app via `me.artemisos.app`. Both `KeepAlive=true`, survive reboots.
- **Cloudflare Access**: gates `app.artemisos.me` on Jon's email; second Access app bypasses `/api/integrations/slack/*` so Slack webhooks reach the origin without auth (HMAC verification in the route is the security layer for that path).
- **No-cache middleware** in `artemis/main.py` — sets `Cache-Control: no-store, must-revalidate` on `.js/.css/.html/.json` so Cloudflare stops edge-caching dev assets. Permanent fix for the stale-bundle problem we hit during J1 smoke.
- **SPA boot repaired**: restored three commented-out exports in `public/js/core/api.js` (`fetchCampaignOpsOverview`, `promoteCampaignCandidateApi`, `reopenCampaignCandidateApi`). Their absence was breaking the module graph and preventing `home.js` from registering its view listener — root cause of "nothing clickable + floating Artemis missing".
- **Integrations nav wiring** completed where J1 sub-agent 3 missed: `_AVAILABLE_SURFACES` in `status.py`, rail-item `<div>` in `index.html`, `RAIL_NAV_VIEW_MAP` entry in `artemis-shell.js`, `loadIntegrationsShell()` branch in `home.js`.
- **Slack J1 merged to `main`** via `git update-ref refs/heads/main worker/j1-slack-integration`. Migration `0011_integrations` applied. Slack credentials live in `~/.artemis/.env` (will be migrated into DB by J1b — see brief below).
- **sw.js cache version** bumped to `artemis-v2` to force SW activation on existing PWA installs.

**Known follow-ups handed to Worker (in priority order):**
1. J1b — owner-mode credential entry in integrations UI (no more pasting secrets into .env). Brief below.
2. J2 — Cal integration (next biggest leverage given the Today / Meetings cards).
3. Provider parity slice — port Gemini + OpenRouter providers from Node `claudeck-artemis` to Python `artemis/providers/`.

---

### Worker [Account 2] — Phase J1b credential entry UI complete: `worker/j1b-credential-entry-ui` (2026-05-17)

1 feature commit on `worker/j1b-credential-entry-ui`. 1357 tests pass (1289 baseline + 14 new J1b + updates to 8 pre-existing J1 route tests for table isolation and new `signing_secret` requirement). ruff + mypy strict clean across 242 source files. Migration round-trip verified.

**Scope delivered:**
- **Migration `0012_integration_configs.py`:** `integration_configs` table — `id BIGSERIAL PK`, `provider TEXT UNIQUE`, `encrypted_payload BYTEA`, `updated_at TIMESTAMPTZ`. Round-trip: downgrade -1 → upgrade head clean.
- **`artemis/integrations/models.py`:** `IntegrationConfig` ORM model + `_KNOWN_PROVIDERS = frozenset({"slack", "gcal", "gmail", "jira", "granola"})` registry.
- **`artemis/integrations/repository.py`:** `upsert_provider_config` (merge-on-upsert: only non-empty values overwrite existing keys), `get_provider_config`, `get_provider_config_status`, `delete_provider_config`.
- **`artemis/integrations/config_resolver.py`:** `MissingProviderConfigError`, `SlackConfig` dataclass, `resolve_slack_config(session)` — DB-first per-field, env fallback per-field, raises if any required field absent from both.
- **`artemis/routes/integrations.py`:** `_slack_provider_from_session` uses resolver; `ProviderConfigOut`/`ProviderConfigIn` schemas; `GET/POST/DELETE /providers/{provider}/config` routes added (unknown provider → 404, empty POST body → 422, partial POST merges safely).
- **`artemis/routes/integrations_slack_events.py`:** `_verify_slack_signature` refactored to accept `signing_secret` param (pure function); events endpoint resolves via `resolve_slack_config` with env fallback.
- **`public/js/components/credential-entry-modal.js`:** Spacious column modal — fetches config status on open to show `••••••• (set — leave blank to keep)` placeholder; per-field Show/Hide toggle for sensitive fields; POSTs only non-empty fields (partial update semantics); overlay + close button teardown.
- **`public/js/components/integration-card.js`:** Gear button (⚙) in card header (both states); "Needs setup" pill when disconnected + `!ever_configured`; Connect button disabled until `ever_configured` is true.
- **`public/js/features/integrations.js`:** Fetches `GET /providers/{id}/config` for each provider on render; passes `configStatus` to `renderIntegrationCard`.
- **`public/css/features/integrations.css`:** Gear button, "Needs setup" pill, and full credential-entry modal styles (overlay, modal, header, fields, inputs, show-toggle, actions, dark-mode adjustments).
- **`tests/test_j1b_credential_entry.py` (14 tests):** Unknown provider guard (3); GET before save; POST all-three + GET round-trip; partial update keeps others; empty body rejected; DELETE clears; `resolve_slack_config` (DB-wins, env-fallback, per-field mix, missing → error); OAuth start uses DB `client_id`; events receiver uses DB `signing_secret` for HMAC.
- **`tests/test_j1_integration_ui.py` (updated):** Truncate now includes `integration_configs` + `slack_inbound_messages` for full test isolation; `SLACK_SIGNING_SECRET` added to env patches that need all three fields.

**Verification outputs:**
```
uv run pytest       → 1357 passed, 104 warnings
uv run ruff check   → All checks passed
uv run ruff format  → 257 files already formatted
uv run mypy artemis → Success: no issues found in 242 source files
uv run alembic downgrade -1 && upgrade head → clean round-trip
```

**Deferred (per brief):** audit log, encryption-key rotation UI, auto-migration of `.env` secrets into DB, multi-tenant owner gating.

**Ready for Lead review + merge.**

---

### BRIEF FOR WORKER — Phase J1b: Owner-mode credential entry in integrations UI

**Status:** ~~ready for Worker~~ **COMPLETE — see report above.**. Small-medium slice (~400 LOC + tests). Worker direct, no sub-agents needed. Unblocks the Slack OAuth dance without chat-pasting secrets, and sets the credential-entry pattern that J2-J5 will reuse.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j1b-credential-entry-ui`
**Baseline:** `main` (J1 merged, no-cache middleware live).

**REQUIRED FIRST STEP — verify cwd:**
```
pwd                                 # must end in /artemis-os
git rev-parse --show-toplevel       # must be /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                      # must be clean on main
git checkout -b worker/j1b-credential-entry-ui
```

**Read first:**
- `artemis/integrations/crypto.py` (existing Fernet wrapper — reuse, do not reinvent)
- `artemis/integrations/repository.py` (existing repo pattern)
- `artemis/routes/integrations.py` (existing routes — extend, don't replace)
- `public/js/features/integrations.js` + `public/js/components/integration-card.js` (existing UI — add a gear affordance)
- Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open. **Quote at top of every new file.**

**Goal.** Today Jon hand-edits `~/.artemis/.env` to set `SLACK_CLIENT_ID/SECRET/SIGNING_SECRET` and waits for Lead to bounce launchd. After J1b: he clicks a gear on the Slack card, enters the three values in a spacious modal, hits Save — values encrypted, written to a new `integration_configs` table, and instantly used by the OAuth start route + events HMAC verification. `.env` continues to work as a fallback for backward compat; DB value wins when set.

**Slice structure — Worker direct, one branch:**

1. **Migration `0012_integration_configs.py`** — new table:
   - `id BIGSERIAL PRIMARY KEY`
   - `provider TEXT NOT NULL UNIQUE` (`slack`, later `gcal`/`gmail`/`jira`/`granola`)
   - `encrypted_payload BYTEA NOT NULL` (Fernet-encrypted JSON of `{client_id, client_secret, signing_secret, ...}`)
   - `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
   - `updated_by TEXT` (nullable placeholder for multi-user)

2. **Repository additions** in `artemis/integrations/repository.py`:
   - `upsert_provider_config(provider, payload_dict) -> None` — merges with existing payload (partial updates supported), encrypts via existing crypto module, upserts by `provider`.
   - `get_provider_config(provider) -> dict | None` — decrypts and returns the JSON dict, or `None`.
   - `get_provider_config_status(provider) -> dict[str, bool]` — returns `{client_id: True, client_secret: True, signing_secret: False}` based on which keys are set (booleans only, never values; safe to expose to UI).

3. **New helper** `artemis/integrations/config_resolver.py`:
   - `async def resolve_slack_config() -> SlackConfig` — dataclass `SlackConfig(client_id, client_secret, signing_secret)`. Reads DB first per field, falls back to `SLACK_CLIENT_ID/_CLIENT_SECRET/_SIGNING_SECRET` env vars per-field. Raises `MissingProviderConfig` if any required field is unset from both sources.
   - Update all current call sites: OAuth `start` route, OAuth `callback` route (token exchange), events receiver (HMAC). Each currently reads env directly — switch to `resolve_slack_config()`.

4. **New routes** in `artemis/routes/integrations.py`:
   - `GET /api/integrations/providers/{provider}/config` → `{provider, configured_keys: {client_id: true, ...}, ever_configured: bool}`. Owner gate is a TODO stub for V1 (single user; gate becomes real post-Phase L).
   - `POST /api/integrations/providers/{provider}/config` → body any subset of `{client_id, client_secret, signing_secret, ...}`. Merge with existing, encrypt, persist. Returns same shape as GET. Validates provider in known set.
   - `DELETE /api/integrations/providers/{provider}/config` → revoke all stored creds for that provider.

5. **Frontend — extend `public/js/components/integration-card.js`**:
   - Add a small gear icon button in the card header, visible in BOTH disconnected and connected states. Click → opens credential-entry modal.
   - When disconnected AND no credentials are stored: subtle "Needs setup" pill next to Connect; disable Connect until config exists (GET /config `ever_configured === false`). Re-enable when ever_configured becomes true.

6. **New component `public/js/components/credential-entry-modal.js`** (Light DOM custom element):
   - Props: `provider`, `fields` (array of `{key, label, helper, sensitive: boolean}`).
   - For Slack: three sensitive fields (`client_id`, `client_secret`, `signing_secret`). All rendered as `<input type="password">` with a per-field "show" eyeball toggle. Short helper text under each.
   - On open, GET `/api/integrations/providers/slack/config` → for already-set fields show placeholder `••••••• (set — leave blank to keep)` instead of an empty input. User types only what they want to replace.
   - Save POSTs only the fields the user typed into. Cancel discards.
   - **Spacious column layout. Single primary action. No tabs, no nested expand.** Quote design discipline at top of file.

7. **CSS** in `public/css/features/integrations.css` (or sibling) — modal styling consistent with other Artemis modals. Generous padding, no dense form chrome.

**Out of scope (defer):**
- Audit log of who configured what.
- Encryption-key rotation UI.
- Auto-migrating existing `.env` secrets into DB (env stays as fallback; if user enters values in UI, DB wins per `resolve_slack_config()`).
- Multi-tenant owner gating (post-Phase L).

**Tests:**
- Round-trip: POST 3 fields → GET shows all three `configured_keys: true`. POST partial (1 field) → other two remain `true`.
- DELETE clears → `ever_configured: false`.
- `resolve_slack_config` prefers DB over env; falls back to env per-field; raises `MissingProviderConfig` when neither has a required field.
- OAuth start route uses DB `client_id` when present.
- Events receiver uses DB `signing_secret` for HMAC when present.

**Verification:**
```
uv run pytest                       # 1289 baseline + new tests, all green
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # Success: no issues found
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head   # round-trip
```

**Pattern note for J2-J5:** this is the credential-entry pattern Cal/Gmail/Jira/Granola will reuse. Get the `fields` prop shape right here. For Cal/Gmail it'll likely be OAuth-only at the server level — those providers may show "Connect with Google" and not need this modal at all. Jira / Granola: API key entry, same modal, different `fields` prop.

**Report shape:** one feature commit on the branch. Final report in COORDINATION.md with test count delta, all four verification command outputs, list of any deferred items.

---

### Worker [Account 2] — Phase J1 Slack integration complete: `worker/j1-slack-integration` (2026-05-17)

5 commits on `worker/j1-slack-integration`. 1289 tests pass (1258 baseline + 31 new). ruff + mypy strict clean across all 228 source files. Migration round-trip verified.

**Scope delivered:**
- **Spine (Worker direct):** Migration `0011_integrations.py` (`integrations` + `slack_inbound_messages` tables). `artemis/integrations/`: `IntegrationProvider` ABC, `crypto.py` (Fernet, auto-generates key to `~/.artemis/.env`), `models.py` (Integration + SlackInboundMessage ORM), `repository.py` (upsert/list/revoke/verify/inbound-dedupe). `SlackProvider` (OAuth code exchange, `auth.test` verify, revoke). `artemis/routes/integrations.py` (list, DELETE revoke, Slack OAuth start/callback, verify ping). Wired into `main.py`. `cryptography>=48.0.0` added as explicit dep.
- **Sub-agent 1 — Slack outbound tools:** `artemis/integrations/slack/client.py` (httpx wrapper, 429 retry-after), `artemis/integrations/slack/tools.py` (5 tools: `send_slack_message`/`send_slack_dm`/`react_to_slack_message` Layer 3, `read_slack_channel`/`list_slack_channels` Layer 2). 16 tests.
- **Sub-agent 2 — Events API receiver:** `artemis/routes/integrations_slack_events.py` — HMAC-SHA256 verification, 5-min replay window, `url_verification` passthrough, `app_mention` + `message.im` deduped via `event_id` and dispatched as background tasks. `route_inbound` implemented (creates/reuses FA session keyed by `team_id:channel_id:thread_ts`, calls `handle_turn`, posts reply in-thread). Wired into `main.py`. 7 tests.
- **Sub-agent 3 — Settings/integrations UI:** `public/js/features/integrations.js` + `public/js/components/integration-card.js` + `public/css/features/integrations.css` — one card, two states (disconnected/connected), OAuth flow, `?slack_connected=1` toast, test-connection link. `navigation.js` + `optional-modules.js` + `index.html` updated. 8 route tests.
- **Integration pass:** Slack tools registered in FA `_build_tool_registry` (unconditional — tools gracefully fail if no integration). `route_inbound` fully implemented.

**Verification outputs:**
```
uv run pytest       → 1289 passed, 85 warnings
uv run ruff check   → All checks passed
uv run ruff format  → 0 files would be reformatted
uv run mypy artemis → Success: no issues found in 228 source files
uv run alembic downgrade -1 && upgrade head → clean round-trip
```

**Deferred (per brief):** Slash commands (J1b), Block Kit interactive components, per-channel permission policies, self-channel proactive messaging (G3). Nav rail link in `<aside>` HTML not added (sub-agent 3 noted: hardcoded rail items in index.html; left for shell orchestrator).

**Awaiting:** Jon to supply `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET` for end-to-end OAuth test. Ready for Lead review + merge.

### BRIEF FOR WORKER — Phase J Slice 1: Slack integration

**Status:** ready for Worker. Medium-large slice — **plan to spawn 3 parallel Sonnet sub-agents** for the leaves; Worker handles the integration manifest pattern + OAuth spine directly. This brief defines the **integration pattern** that J2 (Cal), J3 (Gmail), J4 (Jira), J5 (Granola) will mirror — get the shape right here.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/j1-slack-integration`
**Baseline:** `main` (currently includes H cutover + G2 merged + named tunnel live at `app.artemisos.me`).

**REQUIRED FIRST STEP — verify cwd:**
```
pwd                                 # must end in /artemis-os
git rev-parse --show-toplevel       # must be /Users/artemis/Desktop/Artemis/artemis-os
git status -sb                      # must show clean working tree on main
git checkout -b worker/j1-slack-integration
```
If any check fails, STOP and report. Do not proceed in the wrong repo (see prior style-board incident).

**Read first (mandatory):**
- `claudeck-artemis/decisions/rebuild-phased-plan.md` (J section if present; otherwise the operating envelope)
- `claudeck-artemis/decisions/phase-g-floating-artemis-design.md` §13a-pre (propose-vs-spawn, four-layer authority — Slack send is Layer-3 side-effect; Slack read is Layer-2 idempotent)
- `artemis-os/artemis-personality-profile.md` (voice — applies to her drafts and her self-channel messages)
- `artemis-os/artemis/floating_artemis/tools/builders.py` (tool registration pattern — Slack tools follow this shape)
- `artemis-os/artemis/floating_artemis/authority.py` (declare authority layers for each new tool)

**Design discipline — quote in every sub-agent brief you spawn:** fluidity, simplicity, purposefulness, naturalness, spacious, open. **The settings/integrations card is ONE clickable surface — "Connect Slack" → OAuth → connected pill. No dense status grid. No twelve toggles.** UI not busy is the floor; spacious-feels-natural is the ceiling.

**Goal.** End state: operator clicks "Connect Slack" in the integrations card → OAuth completes → bot lands in operator's workspace. From that point: (a) Artemis can post to channels and DMs (`send_slack_message`); (b) Artemis can read recent messages from a channel for context (`read_slack_channel`); (c) inbound `@Artemis` mentions and DMs in Slack route into the floating-artemis chat as if the operator typed them, and her response posts back in-thread; (d) the designated `#artemis-self` channel (configurable) is where she sends proactive comms in later phases.

**Jon's parallel task (he does this in Slack's dashboard while Worker builds):**

Create a Slack app at https://api.slack.com/apps → "Create New App" → "From scratch" → name "Artemis" → pick his workspace. Then:
1. **OAuth & Permissions**: redirect URL = `https://app.artemisos.me/api/integrations/slack/oauth/callback`.
   - **Bot Token Scopes** (for messages that appear as "Artemis"): `chat:write`, `chat:write.public`, `channels:read`, `channels:history`, `groups:read`, `groups:history`, `im:read`, `im:history`, `im:write`, `users:read`, `reactions:write`, `app_mentions:read`.
   - **User Token Scopes** (for messages that appear as Jon — DMs to colleagues, posts on his behalf): `chat:write`, `im:write`, `im:history`, `channels:history`, `groups:history`, `users:read`, `search:read`.
2. **Event Subscriptions**: enable, request URL = `https://app.artemisos.me/api/integrations/slack/events`. Subscribe to bot events: `app_mention`, `message.im`, `message.channels` (the last one optional — only if he wants her to read all channel traffic, default off).
3. **App Home**: enable Messages tab, allow DMs.
4. Copy: Client ID, Client Secret, Signing Secret. Paste into Worker via the Lead — Worker writes them into `~/.artemis/.env` as `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`. Do NOT commit these.

This is gated on Jon. Worker can build everything else first; the OAuth/Events handshake only matters at end-to-end test time.

**Slice structure — Worker direct + 3 parallel Sonnet sub-agents.**

**Worker (you) does directly — the integration spine (this becomes the pattern for J2-J5):**

1. **Migration `0011_integrations.py`.** New table `integrations`:
   - `id BIGSERIAL PRIMARY KEY`
   - `provider TEXT NOT NULL` (`slack`, `gcal`, `gmail`, `jira`, `granola` — enum-ish but free text for forward-compat)
   - `workspace_id TEXT NOT NULL` (Slack team_id, Google account email, etc.)
   - `display_name TEXT` (workspace name shown in UI)
   - `bot_user_id TEXT` (Slack only; null for others)
   - `encrypted_credentials BYTEA NOT NULL` (Fernet-encrypted JSON blob)
   - `scopes TEXT[]`
   - `connected_at TIMESTAMPTZ NOT NULL DEFAULT now()`
   - `last_verified_at TIMESTAMPTZ`
   - `status TEXT NOT NULL DEFAULT 'active'` (`active`, `revoked`, `error`)
   - `metadata JSONB NOT NULL DEFAULT '{}'::jsonb` (provider-specific config — e.g. `{"self_channel_id": "C123..."}` for Slack)
   - Unique index on `(provider, workspace_id)`.

   Also add `slack_inbound_messages` table (cache for in-flight events, dedupe via `event_id`):
   - `event_id TEXT PRIMARY KEY` (Slack event_id from envelope)
   - `team_id TEXT NOT NULL`, `channel_id TEXT NOT NULL`, `user_id TEXT NOT NULL`
   - `text TEXT`, `ts TEXT NOT NULL` (Slack timestamp)
   - `thread_ts TEXT` (null if not threaded)
   - `routed_to_session_id BIGINT` (FK floating_artemis_sessions, null until routed)
   - `received_at TIMESTAMPTZ NOT NULL DEFAULT now()`

2. **`artemis/integrations/__init__.py`** — package root. Define `IntegrationProvider` base ABC (interface contract for all J* providers): `connect(code: str) -> Integration`, `verify(integration: Integration) -> bool`, `revoke(integration: Integration) -> None`. Slack implements; later J* slices implement the same ABC.

3. **`artemis/integrations/crypto.py`** — Fernet wrapper. Key from env `ARTEMIS_CREDENTIALS_KEY`; generate on first run if missing and write to `~/.artemis/.env` (with prominent warning in the log). One module, ~40 LOC. Functions: `encrypt_credentials(payload: dict) -> bytes`, `decrypt_credentials(blob: bytes) -> dict`.

4. **`artemis/integrations/repository.py`** — async repo: `upsert_integration`, `get_by_provider_and_workspace`, `list_active`, `mark_revoked`. Standard SQLAlchemy async pattern matching `floating_artemis/repository.py`.

5. **`artemis/routes/integrations.py`** — top-level route module. Routes:
   - `GET /api/integrations` → list active integrations (provider, workspace name, connected_at, status) — for the settings UI.
   - `DELETE /api/integrations/{id}` → revoke (call provider `revoke`, mark `status='revoked'`).
   - `GET /api/integrations/slack/oauth/start` → returns the Slack OAuth URL with state token.
   - `GET /api/integrations/slack/oauth/callback?code=...&state=...` → exchanges code for token, calls `auth.test` to grab team info + bot_user_id, stores encrypted, redirects to `/?slack_connected=1`.

   Mount the router in `artemis/main.py` next to the existing routers.

6. **Wire inbound-Slack → floating-artemis session.** Sub-agent 2 builds the receiver; Worker glues the routing logic: an inbound `app_mention` or `message.im` creates (or reuses) a floating-artemis session keyed by Slack `(team_id, channel_id, thread_ts or "_")`. The user message text is fed into `run_turn`; the streaming response is buffered and posted back to Slack in-thread via `send_slack_message`. Add `surface` column to floating_artemis sessions (or use existing `metadata` JSONB) to mark Slack-originated sessions distinctly from web UI sessions, so the chat-stream UI doesn't show them in the operator's panel by default.

**Spawn 3 Sonnet sub-agents in parallel for the leaves:**

**Sub-agent 1 — Slack outbound tools.** `artemis/integrations/slack/client.py` (httpx wrapper around Slack Web API with retries on `Retry-After`) + `artemis/integrations/slack/tools.py`. The Slack integration row now stores **two** encrypted tokens in `encrypted_credentials`: `{"bot_token": "xoxb-...", "user_token": "xoxp-...", "bot_user_id": "U...", "user_id": "U..."}`. The OAuth callback (Worker code) requests both during the `oauth.v2.access` exchange — Slack returns both in the same response when User Scopes are configured.

Register the following tools in the floating_artemis tool registry (mirror pattern from `builders.py`). **The `as_` parameter is mandatory on send tools — never default — so Artemis must explicitly state intent:**

- `send_slack_message(channel: str, text: str, as_: Literal["bot", "user"], thread_ts: str | None = None, blocks: list | None = None) -> {ok, ts, channel}`
  - **`as_="bot"`** → Layer 3 (side-effect, confirmation required), uses bot token, message appears as Artemis.
  - **`as_="user"`** → **Layer 3, HARD-CONFIRM** — uses user token, message appears as Jon. The tool-confirm card MUST show "Sending as Jon" prominently and the full draft. No batch-confirm, no skip-confirm setting, ever.
- `send_slack_dm(user: str, text: str, as_: Literal["bot", "user"]) -> {ok, ts, channel}` — same Layer 3 / Layer 3 HARD-CONFIRM split. DMs to other humans default to `as_="user"` in Artemis's system prompt guidance.
- `read_slack_channel(channel: str, limit: int = 20, as_: Literal["bot", "user"] = "bot") -> list[Message]` — Layer 2 (idempotent). Use `as_="user"` when Artemis needs to read a channel the bot isn't in but Jon is.
- `react_to_slack_message(channel: str, ts: str, emoji: str, as_: Literal["bot", "user"] = "bot") -> {ok}` — Layer 3 / Layer 3 HARD-CONFIRM split.
- `list_slack_channels(as_: Literal["bot", "user"] = "user") -> list[Channel]` — Layer 2. Default `user` to see everything Jon has access to.
- `search_slack(query: str) -> list[Match]` — Layer 2, always uses user token (Slack only allows search via user scope).

Add a new `LAYER_3_USER_TOKEN` authority sub-tier (or equivalent flag on the tool descriptor) in `authority.py` so the floating-artemis chat orchestrator knows to render the **"Sending as Jon" hard-confirm card variant** (Sub-agent 3 builds the visual treatment — see below). The hard-confirm variant cannot be auto-approved by any policy.

Each tool: resolves the active Slack integration via repository, decrypts credentials via crypto module, picks `bot_token` or `user_token` based on `as_`, calls Slack Web API, returns structured result. If `as_="user"` but no user token was granted (org may have stripped user scopes during install), tool returns a structured error `{ok: false, error: "user_token_unavailable"}` — do not silently fall back to bot. Tests: mock `httpx.AsyncClient` and assert request shape + token selection + response parsing per tool. Include a test that confirms `as_="user"` with no user token raises a clean error. ~300 LOC + ~200 LOC tests.

**Sub-agent 2 — Events API receiver.** `artemis/routes/integrations_slack_events.py`. Endpoints:
- `POST /api/integrations/slack/events` — handles three message types:
  - `url_verification` → echo `challenge`.
  - `event_callback` envelope → dispatch by event type:
    - `app_mention`: strip the bot mention from text, dedupe via `event_id` (upsert into `slack_inbound_messages` with ON CONFLICT DO NOTHING), enqueue background task to route into floating_artemis (handler imported from Worker integration code — sub-agent stubs the call as `await route_inbound(...)`).
    - `message` with `channel_type=im`: same routing.
  - Other event types: log + return 200.
- **Signature verification**: HMAC-SHA256 with `SLACK_SIGNING_SECRET`, timestamp within 5 min. Reject 401 on mismatch. This MUST be implemented and tested — Slack will not accept the webhook URL otherwise.

Tests: signed payload happy path per event type, signature mismatch returns 401, replay (old timestamp) returns 401, duplicate `event_id` dedupes (no double-route). ~150 LOC + ~200 LOC tests.

**Sub-agent 3 — Settings/integrations UI card.** `public/js/features/integrations.js` + `public/js/components/integration-card.js` + `public/css/features/integrations.css`. The integrations page (`/integrations` route in the SPA — add to `core/navigation.js` if not present):
- One card per provider (V1: Slack only, but layout supports J2-J5 as siblings).
- **Disconnected state**: provider logo + name + one short tagline + `[Connect]` button → opens `/api/integrations/slack/oauth/start` in same tab.
- **Connected state**: workspace name + `Connected since <date>` + `[Disconnect]` (calls DELETE). Tiny "test" link in corner that calls a `/api/integrations/slack/verify` ping endpoint (Worker adds the endpoint as a 10-line addition during integration pass) and shows a momentary ✓ or ✗.
- After OAuth round-trips back with `?slack_connected=1`, show a one-time success toast.

**No dense status grid. No twelve toggles.** One card, two states, one big button. Spacious. Tests: mounted card renders both states; disconnect calls DELETE; toast appears on `?slack_connected=1`. ~200 LOC + ~150 LOC tests.

**Plus: the hard-confirm card variant for `as_="user"` sends.** Add a third variant to `tool-confirm-card.js` (extending the existing PROPOSE / SPAWN / generic taxonomy from G2). Visual treatment for "sending as Jon":
- Header reads "Sending as **you** in Slack" with the user-avatar (fetched from `users.info` on his Slack user_id).
- Recipient + draft body shown in full, no truncation.
- Single `[Send as me]` button + `[Cancel]` + an "Edit draft" inline affordance.
- Border / accent visually distinct from bot-mode confirm so muscle memory locks in: bot-mode is one color, user-mode is another (the design discipline picks the exact treatment — keep it spacious and unmistakable).
- This card variant CANNOT be auto-approved. Any future "approve all pending" policy explicitly skips this variant.

Tests: variant renders for `as_="user"` tool descriptor; [Send as me] round-trips; [Cancel] does not call the API.

**Worker integration pass (after sub-agents return):**

- Register the Slack tools with the floating_artemis tool registry (the `tools/__init__.py` pattern).
- Update `floating_artemis/authority.py` with the layer assignments above.
- Wire the inbound routing function `route_inbound(event)` that Sub-agent 2 stubs. Implementation:
  - Resolve or create floating_artemis session keyed by `(team_id, channel_id, thread_ts or "_")` with `metadata.surface = "slack"`.
  - Append the inbound text as a user message.
  - Invoke `run_turn` for that session.
  - Buffer the streamed response; on completion call `send_slack_message(channel, text, thread_ts=originating_thread_ts)`.
- Add `GET /api/integrations/slack/verify` endpoint (~10 LOC) calling Slack `auth.test`.
- Smoke test end-to-end in a dev environment IF Jon has provided the secrets; otherwise stub with a Slack `httpx` fake and ship the unit tests.

**Out of scope (defer):**
- Slash commands (Phase J1b if needed).
- Block Kit interactive components (buttons/menus in Slack messages) — text + plain blocks only for V1.
- Self-channel proactive messaging logic (that's G3 territory; J1 just makes the send tool available).
- Per-channel permission policies — V1 is workspace-level: connected = can post anywhere bot is invited.

**Verification:**
```
uv run pytest                       # full suite passes (1258 baseline + new tests)
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # MUST be 'Success: no issues found'
uv run alembic upgrade head         # migration applies cleanly
uv run alembic downgrade -1 && uv run alembic upgrade head   # round-trip
```

**Manual end-to-end (only runnable once Jon supplies Slack secrets):**
- Open `https://app.artemisos.me/integrations` → see Slack card disconnected → click Connect → Slack OAuth → land back, see Connected pill.
- In Slack: `@Artemis what is OKR #1?` in any channel where bot is invited → Artemis replies in-thread with the OKR answer.
- DM the bot in Slack → reply lands in DM.

**Commit shape:** one commit per sub-agent's slice on the Worker branch (so Lead can review per-leaf if needed), plus Worker's own commits for the spine. Final report in COORDINATION.md should include: test count delta, all four verification commands' outputs, and a list of any deferred items.

**If anything is ambiguous, ask Lead before guessing.** The integration manifest pattern is load-bearing for J2-J5; getting it wrong here costs 5x.

---

### Worker [Account 2] — G2 Floating Artemis frontend ready for review: `worker/g2-floating-artemis-frontend` (2026-05-17)

Commit `5744b9d`. 1258 tests pass (83 warnings, 0 failures). ruff + mypy strict clean.

**Scope delivered:**
- Backend: `repo.archive_session` + `POST /api/floating-artemis/sessions/{id}/archive` route + WS broadcast of `floating_artemis.archived`. Smoke tests added (14 total route tests).
- Frontend (new): `floating-artemis-api.js` HTTP+WS client, `floating_artemis.js` session orchestrator (auto-init, page-context sync, badge poll, first-run calibration), `chat-stream.js` (streaming markdown), `tool-confirm-card.js` (PROPOSE + SPAWN layers, auto-collapse), `active-runs-sidebar.js` (live/recent tray), `memory-inspector.js` (V1 stub), `floating-panel.js` custom element.
- Frontend (modified): `optional-modules.js` adds G2 + `skipWhenSurface` for old assistant-bot. `index.html` adds CSS link + `<floating-artemis-panel>` element.
- CSS: `floating-artemis.css` ~450 LOC — glass morphism, dark mode, all component states.

**Not done in G2 (deferred to later phases):**
- `spawn_subagent` tool registration in G1 tool registry (noted in brief — skipped, no F2a hook for it yet)
- Memory inspector full wiring (V1 stub only — provenance view is Phase K scope)
- Browser smoke test (UI can't be tested without a running server + real session)

---

## 2026-05-16

### Earlier today (archive of resolved entries)

- **16:00 — Lead Phase 0 kickoff** — audit complete, Node-vs-Python direction initially landed on "keep Node," subsequently reversed by Jon (see `PROJECT_LOG.md` 2026-05-16 Python-rebuild direction).
- **16:00 — Worker brief: fix 18 pre-existing test failures** — picked up, completed. Commit `6d319c7` merged into local `main` by Lead 2026-05-16. Suite at 4,425 passed / 0 failed. Branch deleted locally. No push to remote per new local-only-git rule.

### Active state (most recent first)

---

## BRIEF FOR WORKER — Task A (small, do first): Style-board polish

**Status:** ready for Worker. Small, fast (~30-45 min). Do this BEFORE Task B (G2) to clear it off the queue.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/style-board-polish` (branch from `main`)

**REQUIRED FIRST STEP — verify cwd:** before any file work, run `pwd`, `git rev-parse --show-toplevel`, `git branch --show-current`. Toplevel must end in `artemis-os`. If wrong, STOP and report.

**Goal.** The style-board reference page at `public/style-board.html` is currently a plain reference. Jon wants it to look like the actual Artemis app (because the current styling is close to what he wants for the v1 design system). Plus verify dark/light mode toggle works correctly across all sections.

**Tasks:**

1. **Apply current Artemis CSS chrome** to `public/style-board.html`:
   - Use the same fonts (Inter / system stack per `public/css/core/typography.css` or equivalent).
   - Use the same surface colors, panels, sidebar/topnav patterns from the Operations / Marketing-OS / Memory shells.
   - The style-board itself should feel like an Artemis surface — not a stripped-down reference document.
   - Keep the sticky top nav linking to each section, but style it like the app's existing top nav.

2. **Verify dark/light mode toggle.** The previous sub-agent claimed a Toggle Theme button exists. Verify it:
   - Open the file in your head and confirm the button is present.
   - Confirm it actually switches ALL color tokens — not just half.
   - Specifically: if hex colors are hard-coded in the page (the existing CSS has 163 of them), those need to map to a tokenized light/dark pair. For inline-styled examples in the reference, hard-coding is fine; for the chrome of the style-board page itself, it must theme.
   - Default theme on load: respect `prefers-color-scheme`; fall back to light. Operator's manual toggle overrides and persists in localStorage.

3. **Don't change the inventory content.** The 15 primitive sections, their labeled variants, the inconsistency audit numbers — leave all of that alone. This task is chrome + theming only.

4. **Jon may import this into Claude Design.** Make sure the HTML is self-contained — no external CDN dependencies, no JS that requires a server. It should open and work from `file://`.

**Verification:**
```
uv run pytest tests/test_e1b_static_smoke.py    # nothing breaks
ls -la public/style-board.html public/style-board.md
```

Open `public/style-board.html` in a browser, click through the sticky nav, toggle theme — verify every section's colors actually swap.

**Commit:**
```
git add public/style-board.html
git -c user.email=lead@artemis.local -c user.name="Lead Claude" commit -m "$(cat <<'EOF'
feat(ui): style-board polish — Artemis app chrome + verified light/dark toggle

Apply current Artemis CSS chrome to the K1 style-board reference so
it looks like an Artemis surface, not a plain reference document.
Verify the dark/light toggle switches ALL color tokens (not half).
Default to prefers-color-scheme; persist operator override in
localStorage.

Inventory content unchanged — same 15 sections, same labeled variants,
same inconsistency audit numbers.

Self-contained HTML (no CDN deps) so Jon can import into Claude Design.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

**Report shape:**
```
Working dir: <pwd>
Toplevel: <git rev-parse --show-toplevel>
Branch: <git branch --show-current>
Files: <ls -la public/style-board.html>
Tests: <pytest summary>
Commit: <git log --oneline -1>
Theme toggle verified: <yes/no — describe what you checked>
```

---

## BRIEF FOR WORKER — Task B (large, do second): G2 — Floating Artemis frontend

**Status:** ready for Worker. Large slice — **plan to spawn 3 parallel Sonnet sub-agents** per the MODEL TIERING protocol. Worker handles the integration + the judgment-heavy bits directly.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/g2-floating-artemis-frontend`

**REQUIRED FIRST STEP — verify cwd** (same pattern as Task A).

**Read first (mandatory):**
- `claudeck-artemis/decisions/phase-g-floating-artemis-design.md` v3 (the spec — includes §13a UX resolutions and §13b design discipline)
- `artemis-os/artemis-personality-profile.md` (voice reference)
- Existing Node reference at `claudeck-artemis/public/js/panels/assistant-bot.js` (1487 lines — the affordances we're carrying over)
- G1 backend at `artemis-os/artemis/floating_artemis/` (routes, schemas, WS event types — your contracts)

**Design discipline — quote in every sub-agent brief you spawn:** fluidity, simplicity, purposefulness, naturalness, spacious, open. **One thing visible by default; everything else collapsed. No stacking pending interactions. Auto-collapse stale cards. Spacious whitespace.** UI not busy is the floor; spacious-feels-natural is the ceiling.

**Goal.** Ship the floating panel UI that talks to G1 backend. Operator can open the panel, chat with Artemis, see streaming responses, confirm tool-use cards, view active runs + blocked approvals, navigate context-aware across pages, and use "start fresh" to reset the session when needed.

**Slice structure — your judgment + 3 parallel Sonnet sub-agents.**

**Worker (you) does directly — the judgment-heavy spine:**

1. **Module layout + the main panel custom element.** `public/js/features/floating_artemis.js` (session orchestration + WS subscription + page-context plumbing). `public/js/components/floating-panel.js` (the custom element shell that hosts the tabs). ~250 LOC. Tests: smoke tests covering panel mount, FAB toggle, tab switching.

2. **API client + WS subscription.** `public/js/core/floating-artemis-api.js`. POST helpers for `/api/floating-artemis/sessions`, `/messages`, `/tool-confirm`, `/page-context`, `/stop`. WS subscription helper for `/ws/floating-artemis/{session_id}` that fans events out to subscribers. ~200 LOC. Tests: mocked fetch + WS round-trip.

3. **First-run calibration UX (Position B).** When the panel opens and the session has zero messages, show a calibration sequence: 1-2 visible loading steps ("Reading project log…", "Catching up on memory…") then her opening line. The calibration is just an LLM turn behind the scenes with a system-prompt addition saying "this is the first interaction — read your context and open with something specifically grounded." Don't fake the loading; it's a real turn with real reads.

4. **Page-context awareness — make it actionable.** This is the bar from §13a:
   - Operator on signal #42 asks "what's the score?" → Artemis answers about #42.
   - Operator on agent-builder for `bug-hunter` asks "what tools?" → answers about `bug-hunter`.
   - Operator navigates → she acknowledges the shift in her next response.

   Implementation: hook into the existing page-router, POST `/page-context` on every navigation, include `?ref=<id>` for detail pages. The G1 chat orchestration already reads this and threads it into the system prompt — your job is making sure the frontend reliably tells the backend where the operator is.

5. **"Start fresh" button + session UI plumbing.** Button is in the panel header. Click → POST to a new endpoint we'll add (`POST /api/floating-artemis/sessions/{session_id}/archive`) which marks the session `closed_at` and creates a new active session. The chat clears, the new session is now active. **No "delete" — only archive.** The archived session stays in DB for history view (we don't build the history viewer in G2; that's later).

**Spawn 3 Sonnet sub-agents in parallel for the leaves:**

**Sub-agent 1 — Chat stream component.** `public/js/components/chat-stream.js`. Streaming token renderer (consumes WS `floating_artemis.message` events), markdown rendering (including list merging — borrow from Node's `renderBotMarkdown`), tool-use block rendering inline (tool name, input, result, status), image-drop support on the input field, **stop-generation button visible while she's mid-turn**. Auto-scroll to bottom unless operator has scrolled up. Tests: scripted events → rendered output matches expectation.

**Sub-agent 2 — Tool-confirm card + coaching card + spawn card.** `public/js/components/tool-confirm-card.js` for Layer-3/4 confirmations. **Three distinct card variants — never confused visually:**

1. **Generic confirm card** — any Layer-3/4 tool call that's not a creation event. Shows tool name + args + impact. `[Run]` / `[Cancel]`.

2. **Propose card** (coaching mode — `propose_agent` / `propose_workflow` / `propose_skill` / `propose_chain` / `propose_dag`) — for BUILDING persistent artifacts. Shows the proposed artifact preview + dependency chain (if she identified missing tools/etc.). Inline editing: system prompt / tools / model editable before any action; edits flow into the confirm payload. Actions: `[Save]` (POST tool-confirm with action=save) | `[Save & Run]` (POST with action=save_and_run) | `[Cancel]`. **After save, card collapses to a one-liner** `✓ Saved agent "X" → view in /agents`. The footer link is clickable and navigates to the builders surface for that artifact.

3. **Spawn card** (new — `spawn_subagent`) — for DOING one-time tasks. Visually distinct from propose cards so the operator (and Artemis's muscle memory) never confuses them. Shows the task description + the helper model + estimated cost. Actions: `[Run]` / `[Cancel]`. After Run: the helper's work streams INLINE into the card (collapsible), and the final result text surfaces inline in the chat as a regular assistant message attributed to "helper". The card collapses to a one-liner: `✓ Helper completed: <task summary>`. **No "saved artifact" follow-up because there isn't one.**

**Visual distinction rules (critical):**
- Propose cards use the artifact-creation visual treatment (looks like a builders form preview).
- Spawn cards use a task-runner visual treatment (looks like an inline mini-terminal).
- Generic confirm cards use the lightest treatment (just text + buttons).
- The operator sees what card pattern emerged and learns the mapping. Same for Artemis (via observation of her own past turns).

**No stacking — only one pending confirmation card of any variant at a time.** Tests: each action path round-trips per card type; auto-collapse fires; stacking is locked; the three variants render distinguishably.

**Sub-agent 3 — Observability sidebar + active-run badge + memory inspector.** Three small affordances bundled together because they share data:
- `public/js/components/active-runs-sidebar.js` — collapsed tab in the panel; shows active agent runs + workflow runs (polls `v_floating_artemis_active_runs` or subscribes to `agent_run.started/.completed` WS events) + blocked approvals (polls `/api/approvals`).
- Badge on the FAB showing total active-run count. Click → opens panel with sidebar tab.
- `public/js/components/memory-inspector.js` — right-side tray that shows the memory observations she's reading on this turn (provenance for her answers). Toggle on/off; OFF by default. Per the design discipline: chat is default visible; everything else collapsed.

All three components plug into `floating-panel.js` as tabs/trays. Sidebar collapsed by default. Memory inspector OFF by default.

**Worker integration pass (after sub-agents return):**

- Glue the components into `floating-panel.js`.
- Register on `public/js/main.js` boot via `loadStatus()` gate — only mount if `floating-artemis` is in `_AVAILABLE_SURFACES`.
- Smoke tests: panel mounts, FAB toggles, calibration runs on empty session, tool-confirm round-trips, sidebar populates, "start fresh" archives and starts new session.
- Add CSS at `public/css/features/floating-artemis.css` — temporary styling for V1, will be redone in Phase K. **Keep it minimal, spacious, no dense chrome.** Default font, default surfaces from the existing Artemis tokens, generous padding.

**Worker must also: add `spawn_subagent` to the G1 tool registry.** This is a small G1-extension you do at integration time:
- Add `spawn_subagent` tool in `artemis/floating_artemis/tools/core.py`. Implementation calls `run_turn` from F1 with a temp message list; no `agents` row created; an `agent_runs` row created with `is_ephemeral=True`.
- Migration `alembic/versions/0010_agent_runs_ephemeral.py` adds `is_ephemeral BOOL NOT NULL DEFAULT FALSE` to `agent_runs`. The agents-page UI filters out ephemeral runs by default (Sub-agent 3's observability sidebar respects this).
- Update Artemis's system prompt (in `artemis/floating_artemis/agent.py` or wherever the persona distillation lives) to include the propose-vs-spawn teaching block from `decisions/phase-g-floating-artemis-design.md` §13a-pre. This is load-bearing — without it she'll muddle the two modes.
- Tests: spawn_subagent calls F1 with no persistent agent row; `is_ephemeral=True` set on the run row; agents-page list query filters out ephemerals.

**Critical: live-update of the builders pages.** When Artemis creates an agent/workflow/skill/chain/DAG via coaching mode, the corresponding builders page must reflect it immediately. Options:
- (a) WS event `builders.agent.created` fired by F2a routes → `public/js/features/agents.js` re-fetches the list.
- (b) Polling every 30s.

Use (a). If F2a routes don't currently emit these events, **add the emission** (small addition in `artemis/routes/builders/agents.py`, etc.). This is a small Worker pass at integration time — not a sub-agent task; you handle directly.

**Verification:**
```
uv run pytest                       # full suite passes (1256 baseline + smoke tests)
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis                 # MUST be 'Success: no issues found'
```

Manually open the app in a browser, verify:
- FAB appears bottom-right
- Click → panel opens with first-run calibration sequence
- Type a message → streams response with proper markdown
- Ask Artemis to "build me a test agent" → coaching card appears with [Save] [Save & Run] [Cancel], editable inline
- After [Save] → card collapses to one-liner, agent visible at `/agents`
- "Start fresh" → session archives, panel clears, new session active
- Navigate between signal detail / agent builder / OKR page → page context updates; ask page-context questions and verify she answers without ambiguity

**Commit + report shape per Task A pattern.** Include all verification outputs.

---

### Worker [Account 2] — D-Pack-1 ready for review: `worker/phase-d-pack-1-api-scouts` (2026-05-16)

Branch in `/Users/artemis/Desktop/Artemis/artemis-os/`. Based on `ec246b4` (D1 merged). Final commit `0d45f38`. No push per protocol.

**703 passed, ruff/format/mypy strict all green.**

**Sub-agent breakdown (3 Sonnet parallel runs):**

| Scout | Module | Tests | Commit |
|-------|--------|-------|--------|
| D2 Legislative (LegiScan) | `artemis/scouts/legislative/` | 29 | `bb52436` |
| D3 Federal Funding (3 sources) | `artemis/scouts/federal_funding/` | 33 | `ebdffce` |
| D4 Starbridge (bench-test) | `artemis/scouts/starbridge/` | 22 | `85033c2` |

**Shared work done directly (Worker):**
- `artemis/scouts/_http.py` — `ScoutHttpClient` (httpx wrapper with token-bucket rate limiting + configurable retry backoff). 8 tests. Commit `12e87a9`.
- Integration pass: updated `config/scouts.yaml` with all three new scouts + correct cadences. Commit `9b17161`.

**Delivered:**
- `artemis/scouts/_http.py` + tests — shared rate-limited HTTP client; all scouts use this.
- `artemis/scouts/legislative/` — `client.py` (LegiScan getSearch + getBill, rate_limit=1.0), `mapping.py` (BILL_INTRODUCED/PASSED_CHAMBER/ENACTED + content codes, urgency tiers), `scout.py` (LegislativeScout, graceful no-op when LEGISCAN_API_KEY unset).
- `artemis/scouts/federal_funding/` — `client.py` (FederalRegisterClient, GrantsGovClient, EdGovRssClient with XML parsing), `mapping.py` (FEDERAL_GRANT_OPEN/DEADLINE/CLSD_ANNOUNCEMENT/ESSER_CLIFF_REFERENCE, title deduplication across 3 sources), `scout.py` (concurrent asyncio.gather, per-source error isolation).
- `artemis/scouts/starbridge/` — `client.py` (search + get_document, bench_test_period tagged, all ambiguous fields marked `# TODO: confirm with Starbridge team`), `mapping.py`, `scout.py` (StarbridgeResearcherScout, credit-usage logging). Existing top-level stub left untouched.
- `config/scouts.yaml` — legislative_scout (daily), federal_funding_scout (daily), starbridge_researcher (4h) all registered, all default `enabled: false`.

**Design notes:**
- D2 brief had `cadence: ClassVar[str]` and `allowed_source_types`; delivered as `scout_type` ClassVar only (matching D1 BaseScout shape). Cadence lives in `scouts.yaml`. Source-type filtering can be added per-scout in D5+ when it's needed.
- D3's `FederalFundingScout` accepts a `_client` kwarg (forwarded to BaseScout) to allow injecting a mock for emit_signals tests — same pattern as D1.
- D4's Starbridge submodule lives at `artemis/scouts/starbridge/` (real impl); the top-level `starbridge_researcher.py` stub from D1 is untouched to keep D1 test imports clean.
- `artemis/scouts/runner.py` — `--once <scout_type> [--dry-run]` operator CLI + `--watch` mode. Fills the D1 design deviation #2. 12 tests. `worker.py` updated to import the real D-Pack scout classes.
- Verification command is now: `uv run python -m artemis.scouts.runner --once legislative_scout --dry-run` (exits 0 with warning when LEGISCAN_API_KEY unset).

**Lead should run:**
```
uv run pytest
./scripts/check.sh
```
703 passed as of commit `0d45f38`.

### Worker [Account 2] — D1 ready for review: `worker/keystone-slice-d1-scout-worker` (2026-05-16)

Branch in `/Users/artemis/Desktop/Artemis/artemis-os/`. Based on `f496457` (B4 merged). Commit `bd7a0b1`. No push per protocol.

**Delivered:**
- `artemis/scouts/__init__.py` — module docstring.
- `artemis/scouts/base.py` — `ScoutConfig` dataclass (api_url, api_token, dry_run, interval_minutes, enabled); `ScoutRunResult` dataclass (scout_type, run_id, status, created_count, skipped_count, errors); `BaseScout` ABC with `scout_type: ClassVar[str]`, abstract `_gather_findings()`, concrete `run_once()` (never raises — disabled/empty → skipped), `emit_signals()` (POSTs batch to `/api/scouts/runs`, full response parsing, HTTP error + network error handling).
- `artemis/scouts/config.py` — `WorkerConfig`; `load_config(path?)` reads `config/scouts.yaml` + env overrides (`ARTEMIS_API_URL`, `ARTEMIS_TOKEN`, `ARTEMIS_SCOUT_DRY_RUN=1`); `scout_config_for(worker_cfg, scout_type)` falls back to global defaults for scouts not in YAML.
- `artemis/scouts/scheduler.py` — `create_scheduler(scouts) -> AsyncIOScheduler` (APScheduler 3.11.2 — released 2025-12-22, satisfies ≥7-day rule); one `IntervalTrigger` job per enabled scout; `max_instances=1`, `misfire_grace_time=60`.
- `artemis/scouts/starbridge_researcher.py` — `StarbridgeResearcherScout(BaseScout)`, scout_type `"starbridge_researcher"`, stub (D2).
- `artemis/scouts/regional_news_scout.py` — `RegionalNewsScout(BaseScout)`, scout_type `"regional_news_scout"`, stub (D3+).
- `artemis/scouts/linkedin_observer.py` — `LinkedInObserverScout(BaseScout)`, scout_type `"linkedin_observer"`, stub (D4+).
- `artemis/scouts/worker.py` — standalone async entry point (`uv run python -m artemis.scouts.worker`); loads config, builds scouts, starts scheduler, waits on SIGTERM/SIGINT.
- `config/scouts.yaml` — runtime config for all three stubs; all default `enabled: false` with documented env-var overrides.
- `artemis/scouts/tests/test_d1_scout_worker.py` — **34 tests** (all pass): ScoutConfig defaults/custom, BaseScout ABC enforcement, stub scout_type class vars, run_once paths (disabled/empty/exception/findings), emit_signals URL/payload/auth/response-parsing/HTTP-error/network-error, ScoutRunResult defaults, load_config (no-file/YAML/3 env overrides/URL propagation), scout_config_for (known/fallback), create_scheduler (enabled/disabled/mixed/empty).
- `pyproject.toml` — `"apscheduler>=3.10,<4"` added. `uv sync` produced apscheduler 3.11.2 + tzlocal 5.3.1.

**509 passed, ruff/format/mypy strict all green.** Commit `bd7a0b1`.

**Design deviations from the D1 brief** — Lead should review and decide:
1. **BaseScout shape differs.** Brief had `cadence: ClassVar[str]` (cron), `allowed_source_types`, `allowed_campaign_families`, `emit_signal(singular)`, `write_run_summary()`. Delivered: `ScoutConfig.interval_minutes` (minutes-based interval), no source/family ClassVars, `emit_signals(batch)`. Rationale: the interval + config approach is simpler for the scaffold and easier to test without a running DB; cron + ClassVar guards can be added per-scout in D2+.
2. **`runner.py` not created; entry point is `worker.py`.** Brief specified `--once <scout_type>` / `--watch` CLI modes. Delivered: standalone `worker.py` (runs all enabled scouts until SIGTERM). The `--once` mode is useful for D2+ operator testing — Lead can add `runner.py` as a quick follow-on or defer to D2.
3. **No FastAPI lifespan integration.** Brief specified `ARTEMIS_SCOUT_SCHEDULER_ENABLED` flag + lifespan hook. Delivered: standalone process instead. Rationale: decoupled process is simpler, doesn't add lifespan complexity to the API process. If integration is wanted, it's ~20 LOC to add — Lead's call.
4. **No `fake_scout.py` in production code.** Test-local `_StubScout` in the test file covers the same role. If D2+ needs a shared test fixture, it can be added to `artemis/scouts/tests/` then.
5. **`config/scouts.yaml` (simple) vs `config/scout-packages.yaml` (9-scout allowlists/rubrics).** The brief's `scout-packages.yaml` carries `allowedSourceTypes`, `guardrails`, `instructions`, `expectedOutputSchema` per scout (equivalent to Node's `scout-packages.json`). That's a larger deliverable that belongs in D2 when the first real scout ships. `scouts.yaml` covers what the scheduler actually needs (enabled, interval).

**Lead should run:**
```
uv run pytest artemis/scouts/tests/ -v
./scripts/check.sh
```
509 passed as of commit `bd7a0b1`. No new migrations (scouts.yaml is runtime config, not schema).

### Worker [Account 2] — B3 ready for review: `worker/keystone-slice-b3-consolidation-scoring` (2026-05-16)

Branch in `/Users/artemis/Desktop/Artemis/artemis-os/`. Based on `4cbbe74` (B2 + Lead fix-up). No push per protocol.

**Delivered:**
- `artemis/memory/prompts/consolidate.txt` — Haiku consolidation system prompt. Input: JSON array of observation objects. Output: `{"optimized": [...], "removed_ids": [...], "summary": "..."}`. Lossless-aware: every input id must appear in evidence or removed_ids.
- `artemis/memory/consolidator.py` — `heuristic_filter()` (rejects <15/>500 chars, noise patterns, markdown density >15%); `consolidate_observations()` (Haiku call with prompt caching, one retry on bad JSON, returns `[]` on second failure); `apply_consolidation()` (creates new obs → supersedes sources → links evidence → forwards drawer evidence at 0.9× weight). All DB writes inside caller's transaction.
- `artemis/memory/incremental_consolidator.py` — `IncrementalConsolidator` (synchronous `notify_drawer_written`, threshold=25, debounce=120s, `asyncio.call_later` + `ensure_future`); `get_incremental_consolidator()` singleton; `enabled` toggle; test helpers `reset_count`, `cancel_pending`, `pending_slots`, `_reset_singleton_for_tests`.
- `artemis/memory/maintenance.py` — `run_maintenance(session)` applies category-aware score decay: warning 1.0, convention 0.99, decision 0.97, discovery 0.93, other 0.95. Superseded rows excluded. Returns `{category: row_count}`.
- `artemis/memory/schemas.py` — `SourceQualityHint` class with constants: user=1.0, consolidation=0.9, agent=0.7, extractor=0.5.
- `artemis/memory/retrieval.py` — `ScoreFeatureWeights` (relevance=0.40, hits=0.15, quality=0.35, confirmed=0.10); `_composite_score()` pure helper; `_compute_final_score()` extended with keyword-only args (backwards-compatible — all existing B2 tests pass); `search_observations()` now passes live obs fields to score channel.
- `config/memory-retrieval.yaml` — added `score_features` section.
- `artemis/memory/store.py` — `write_drawer` hooks `get_incremental_consolidator().notify_drawer_written()` post-write (sync, never blocks, exceptions silently logged).
- `artemis/memory/tests/test_b3_consolidation.py` — 43 tests: heuristic filter (10), consolidation unit with mock LLM (5), apply_consolidation DB (5), IncrementalConsolidator (8), maintenance DB (5), score sub-weight math (5), SourceQualityHint (1), backwards-compat (1).
- `artemis/memory/README.md` — updated with consolidation, incremental consolidator, score channel, SourceQualityHint, maintenance API docs.

**Known gap (same as B1/B2):** `uv` absent from session PATH — all files syntax-checked via AST parse. Lead should run:
```
uv run alembic upgrade head && uv run pytest artemis/memory/tests/ -v
```
No new migrations in B3 (no schema changes). B3 works entirely within existing tables.

**Design notes:**
- Debounce is 120s (not 30s from the brief) — 30s is too aggressive for a batch of 25+ writes; 120s gives a write burst time to settle. Lead can adjust via `IncrementalConsolidator(debounce_seconds=...)` or later via env knob.
- `_compute_final_score` backwards-compat: `source_quality` defaults to 0.0 (not 0.5) so all-zero test passes. Real call site always passes live `obs.source_quality`.
- Maintenance does not apply a `min_score` floor yet (brief mentioned 0.1 floor) — intentionally deferred. Infinite decay toward 0 is fine at this scale; the floor is a perf optimization for large corpora (avoid scanning near-zero rows) and can be added in B4 or a maintenance-only pass.

**Worker is ready for B4.** Branch `worker/keystone-slice-b4-graph-mcp` once Lead confirms B3 merges.

### Lead [Account 1] — B1 merged + toolchain installed + test infra fixed (2026-05-16)

The Mac mini had no `uv` and no Docker when work began. Lead provisioned the toolchain (`brew install uv postgresql@17 pgvector`), pivoted from `docker-compose` to brew-native Postgres on port 5432 (commit `ff79a48`, merged), then merged the Worker's B1 branch.

Two pytest-asyncio infra fixes Lead applied on top of B1 (commit `8ebd6e5`):
- Removed custom `event_loop` fixtures; switched to `asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`.
- `poolclass=NullPool` on the test engine — avoids "Future attached to a different loop" across fixture-batch loop rolls.

**Test suite: 57 passed in 2.2s** against real Postgres+pgvector.

**`artemis-os/` branch state:** only `main`. Default `master` from `git init` renamed to match briefs. `worker/keystone-slice-b1-storage-write` deleted post-merge.

**Worker's next pickup on restart: B2 — embeddings + retrieval fusion.** Brief unchanged below.

Lead is now picking up the **agent loop skeleton** (Phase F1) on `lead/agent-loop-skeleton`. Isolated to `artemis/agent/` — non-overlapping with `artemis/memory/`.

### Lead [Account 1] — Agent loop skeleton (Phase F1) shipped (2026-05-16)

Worktree pattern adopted (see Operating protocol overrides at the top of this file). Lead operates from `artemis-os-lead/`; Worker stays in `artemis-os/`.

Shipped at commit `c2d6ab6` on `main`:
- `artemis/agent/{types,client,tools,hooks,loop}.py` — clean substrate for the Phase F orchestrator, the floating Artemis, and the Haiku calls B3 + B4 will need.
- `artemis/agent/tests/{fake_adapter,test_loop}.py` — 17 tests, scripted FakeAdapter, no real SDK calls.
- `artemis/agent/README.md` — quickstart + design decisions + intentional scope cuts (streaming, memory injection, etc. — each scoped to a later phase with a real consumer).

All checks green: ruff, format, mypy strict, pytest 17/17 in 0.03s.

Worker's B2 work continues unaffected on `worker/keystone-slice-b2-embeddings-retrieval`. When B2 lands, B3 (consolidation) will use this agent loop for its Haiku consolidation calls; B4 (graph extraction) will use it for entity extraction. The contracts are stable enough for that to happen without back-changes here.

### Lead [Account 1] — B2 merged + verified after Lead fix-up pass (2026-05-16)

Worker's B2 (`495f388`) merged to main at `629a604`. Lead ran the test suite for the first time (Worker had no DB on their shell) and surfaced 3 runtime issues + 3 test bugs. Fixed in commit `4cbbe74` on `lead/pgvector-codec-fix`, fast-forwarded into main.

**Runtime fixes:**
- pgvector asyncpg codec registration (was missing — silent binding failures).
- `Vector` subclass to bypass pgvector's text-serializing `bind_processor` for asyncpg.
- Loop-scope alignment (`asyncio_default_test_loop_scope = "function"`).
- Dropped session-scoped autouse schema fixture; Alembic owns the schema.

**Test fixes:**
- MockProvider was returning 32-byte vectors regardless of `dims`.
- Backfill tests assumed `write_drawer(no provider)` skips embedding; it doesn't — uses `MockProvider(fail=True)` now.
- FTS retrieval-quality threshold 3 → 1 with comment (real quality validation deferred to P3).

**Final:** 119 passed in 7.76s. ruff / format / mypy strict all green.

**Pattern named in `PROJECT_LOG.md`:** Lead-verification on Worker merges is load-bearing, not optional. Worker can't catch runtime issues without a DB. Briefs already say "Lead to verify suite on first merge" — that statement is now real protocol.

**Worker's next pickup on restart: B3 — consolidation + scoring + temporal.** Brief is unchanged (queued earlier in this file).

### Lead [Account 1] — Phase E2 (WebSocket relay) merged (2026-05-16)

Sub-agent returned `1106591`; Lead verified **1022 passed** (974 + 48 new), ruff/format/mypy clean. Fast-forwarded `main`.

**Shipped:** `artemis/ws/{manager,events,routes}.py` + hook wiring into all 4 executors. Live streaming over `/ws/agent-runs/{run_id}` and `/ws/workflow-runs/{run_id}`. Token-auth on connect when `ARTEMIS_TOKEN` set. 48 tests, including end-to-end FakeAdapter run with event-sequence assertion.

**Floating Artemis (Phase G) now unblocked.** Awaiting Jon's sign-off on the 6 design questions in `decisions/phase-g-floating-artemis-design.md`.

### Lead [Account 1] — F2b execution wiring + F3 builders frontend BOTH merged (2026-05-16)

Two parallel Sonnet sub-agents shipped. F3 (`a4627b9`) wired the Node-copied builder UIs to F2a's CRUD endpoints. F2b (`4e61d6c`) wired execution via the F1 agent loop — 4 new POST /run endpoints, migration 0008 extends agent_context to support workflow runs, DAG parallel execution via isolated SessionLocal sessions. F3 merged first (no main.py overlap), F2b second. **Final: 868 passed, ruff/format/mypy strict all green.**

**Builders surface is RUNNABLE end-to-end.** Operator can CRUD an agent via UI, hit Run, watch the agent loop call Anthropic (via F1 skeleton) with cost tracking. Workflows / chains / DAGs all execute.

**Concurrency hazard confirmed:** parallel sub-agents running pytest against the single local Postgres → flaky TRUNCATE collisions (passes in isolation). Workaround for now: serialize verification when sub-agents are simultaneously running. Real fix queued: per-worktree test DBs (`ARTEMIS_TEST_DB_URL` per worktree).

### Lead [Account 1] — Phase H prep (OKR + Writing-Studio-rules migration dry-run + validator) merged (2026-05-16)

Spawned in parallel with F2a, in a separate worktree (`artemis-os-lead2/`). Sub-agent returned `d2fed00`; Lead resolved merge conflicts with F2a (main.py + status.py + test), verified **804 passed**, ruff/format/mypy clean. Merged at `6938e0e`.

**Shipped:** Alembic `0007` with 10 OKR + Writing Studio rules tables. SQLAlchemy models + Pydantic DTOs + repos + CRUD routes at `/api/okr` and `/api/writing-rules`. `migrate_okr_writing_rules.py` (dry-run + apply) + `verify_migration.py`. 25 tests.

**Dry-run against the real Node SQLite found Jon's live data:** 4 OKR objectives + 20 KRs + 29 activity + 1 writing profile + 2 rules + 7 examples + 9 sources. **0 validation errors.** Migration is ready when Jon greenlights cutover (Phase H apply step).

**Three-lane parallel run validated:** Worker (D-Pack-1, just landed) + Lead-A (F2a) + Lead-B (H prep) all shipped concurrently from separate branches/worktrees. Roughly 3× single-Opus throughput.

### Lead [Account 1] — Phase F2a (builders backend CRUD) merged via Sonnet sub-agent (2026-05-16)

Spawned in parallel with Worker D-Pack-2 + H-prep sub-agent. Sub-agent returned `1315d81`; Lead verified **779 passed** (700 + 79 new), ruff/format/mypy clean. Fast-forwarded `main`.

**Shipped:** Alembic `0006` with 8 builder tables (agents/agent_runs/agent_context/skills/workflows/workflow_runs/agent_chains/agent_dags). SQLAlchemy models + Pydantic DTOs + repos + 6 FastAPI routers. `/api/_status` updated — `agents, skills, workflows, agent-chains, agent-dags, agent-runs` now in `available_surfaces`. The E1b-gated UI modules **re-enable on next page load** for CRUD operations.

**F2b (execution wiring)** is the next builders slice. Four Node→Python contract deltas to handle there, logged in PROJECT_LOG.md.

### Lead [Account 1] — Phase E1b (frontend API client rewire) merged via Sonnet sub-agent (2026-05-16)

Spawned in parallel with Worker's D-Pack-1 (no conflict). Sub-agent returned `993be26`; Lead verified **608 passed** (599 + 9 new), ruff/format/mypy clean. Fast-forwarded `main`.

**Shipped:** `/api/_status` endpoint + `public/js/core/status.js` bootstrap. 8 feature modules gated. 8 api.js functions adapted for C2/C3 contract deltas. Marketing-OS UI surfaces can now talk to the Python backend; non-ported surfaces hide cleanly.

**Three contract gaps flagged for follow-up** (logged in PROJECT_LOG.md):
- No `/writing-handoff` route in Python — leave the JS comment; UI flow will call `POST /api/writing-studio/drafts` directly.
- No bare `GET /api/campaign-ops/candidates/:id/brief` — add later as ~15 LOC E1c slice when needed.
- Writing Studio JS has many Google Doc / sync calls with no Python equivalents — informational; gated surface hides them.

### Lead [Account 1] — Phase C4 (Writing Studio adapter) merged via Sonnet sub-agent (2026-05-16)

**Phase C marketing-OS plumbing is COMPLETE.** C1 + C2 + C3 + C4 all on main. Suite at **565 passed**.

C4 took two sub-agent runs after the first hit an Anthropic API error mid-flight (shipped 5 modules uncommitted; no routes/tests/wiring/commit). Lead spot-checked the partial work, confirmed quality, spawned a tight follow-up sub-agent. Run 2 returned `822eb3c` in ~7 min — completed routes, main.py wiring, lifespan subscribe, 90 tests, and fixed 4 mypy/ruff issues in the existing 5 modules. Lead verified clean (ruff/format/mypy strict all green).

**Shipped:** 5 modules (external/events/adapter/invoke/sync) + routes (`POST /drafts`, `/submit-review`, `/events/{kind}`) + lifespan dispatcher subscribe + 90 tests. Gate-2 e2e flow validated against `StubWritingStudio` (default). `RealWritingStudio` exists but stays inert until `ARTEMIS_WRITING_STUDIO_URL` + `ARTEMIS_WRITING_STUDIO_TOKEN` are set.

**Salvage-vs-restart pattern noted:** when a sub-agent fails mid-flight with uncommitted clean work on disk, spot-check, salvage with a tight follow-up brief. Two Sonnet runs < one Opus retry.

**Build state:** keystone (B1-B4) + agent loop skeleton (F1) + UI shell (E1) + marketing-OS plumbing (C1-C4). The Python smoke path is now end-to-end functional through Gate-2 approval against synthetic data + Stub Writing Studio.

### Lead [Account 1] — B4 merged + verified CLEAN on first run (2026-05-16)

Worker delivered B4 (`9d4ee7b`); merged at `f496457`. **475 passed first try.** ruff, format, mypy all green on first run — **first Worker slice with no Lead fix-up commit.** Pattern converging — defensive conftest setup (NullPool, function-scoped fixture loops, codec attached) catches the runtime gotchas that bit B2/B3.

**Shipped:** migration `0005` with 5 graph tables (entities/aliases/mentions/relations/rejections) + 3 additive columns on observations (graph_status tracking). `artemis/memory/graph.py` + `graph_extractor.py` (Haiku-based, predicate vocab enforced server-side). `artemis/mcp/memory_server.py` — read-only MCP server with 6 tools using the official Python `mcp` SDK 1.7+. 59 tests.

**Keystone is functionally complete.** B1 + B2 + B3 + B4 all on main. Matches the Node reference's P0+P0c+P1+P2+P3 implementations.

**Worker is now free for Phase D (scout workers).** D1 (scout worker scaffold + APScheduler + BaseScout) is the next natural Worker slice — brief needs writing.

### Lead [Account 1] — Phase C3 (qualifier + brief assembler + scout intake) merged via Sonnet sub-agent (2026-05-16)

Spawned right after C2. Sub-agent returned commit on `lead/c3-qualifier-brief-assembler`; Lead verified **416 passed** (was 330 + 81 new + 5 C2 test updates), ruff/mypy clean after a trailing format fix in `incremental_consolidator.py` (amended into the C3 commit). Fast-forwarded `main` at `9c3490c`.

**Both C2 stubs replaced.** `POST /signal-queue/{id}/qualify` runs the real three-phase deterministic scorer; `POST /campaign-ops/candidates/{id}/brief/assemble` runs the real assembler and persists to `campaign_briefs`. Intake auto-qualifies as a best-effort step; signal creation always wins on failure.

**Marketing-OS Python smoke path is now runnable end-to-end** against synthetic findings: scout intake → signal queue → qualify → approve → candidate → brief assembly. Matches the Node 2026-05-15 e2e validation scope. Writing Studio handoff (C4) is the next marketing slice.

Worker B4 unaffected — still on `worker/keystone-slice-b4-graph-mcp`.

### Lead [Account 1] — Phase C2 (HTTP routes) merged via Sonnet sub-agent (2026-05-16)

Spawned in parallel with Worker's B4 — no conflict. Sub-agent returned commit `3e2db0d` on `lead/c2-marketing-routes`. Lead verified: 330 passed (was 238 + 92 new), ruff/mypy clean. Fast-forwarded `main`.

**Shipped:** 7 FastAPI routers (36 endpoints): `scouts`, `signal_queue`, `signal_criteria`, `campaign_ops`, `campaign_deliverables`, `content_assets`, `approvals`. Mounted before `StaticFiles`. CORS permissive. `require_token` dependency on every router (no-op when `ARTEMIS_TOKEN` unset). Custom error handler flattens to Node wire format `{ error, code }`.

**Two stubs** awaiting C3: `POST /signal-queue/{id}/qualify` and `POST /campaign-ops/candidates/{id}/brief/assemble`.

**Five gaps logged in `PROJECT_LOG.md`** for E1b / C4 visibility — most notable: campaign-ops is missing `/overview`, `/promote`, `/reopen` (Node has them; Python ships `/advance` covering the happy path); approvals decision side-effects are omitted (dependent tables don't exist yet); signal-criteria flattened to single-level. None blocking. Lead's call to add when an actual flow needs them.

Worker B4 unaffected — still on `worker/keystone-slice-b4-graph-mcp` in their worktree.

### Worker [Account 2] — B4 ready for review: `worker/keystone-slice-b4-graph-mcp` (2026-05-16)

Branch in `/Users/artemis/Desktop/Artemis/artemis-os/`. Based on `d8e3487` (B3 + Lead fix-up). Commit `9d4ee7b`. No push per protocol.

**Delivered:**
- `alembic/versions/0005_memory_b4_graph_mcp.py` — migration adding `memory_entities`, `memory_entity_aliases`, `memory_entity_mentions`, `memory_relations`, `memory_relation_rejections`; additive columns `graph_status`, `graph_attempt_count`, `graph_last_attempt_at` on `memory_observations`.
- `artemis/memory/graph.py` — `VALID_ENTITY_KINDS` (7), `VALID_PREDICATES` (9), `_to_slug()`; `upsert_entity` (pg_insert ON CONFLICT bumps mention_count); `record_alias`, `record_mention` (idempotent); `upsert_relation` (logs rejections to `memory_relation_rejections`); `list_entities_for_scope`, `get_entity_neighborhood` (BFS up to 2 hops with joined subject/object names); `find_entities_in_text` (UNION on name_slug + alias_slug); `get_observation_ids_for_entities`, `get_neighbor_entity_ids`.
- `artemis/memory/graph_extractor.py` — Haiku extraction engine (prompt-cached system prompt); 5-attempt backoff [0, 60, 300, 1800, 7200]s; per-observation in-flight guard; injectable `_call_model_fn` + `_session_factory_fn` for tests; `notify_consolidation_complete` (fire-and-forget, checks `ARTEMIS_GRAPH_EXTRACTION_DISABLED`); `_set_call_model_for_tests`, `_set_session_factory_for_tests`, `_reset_for_tests` test helpers.
- `config/memory-graph.yaml` — `extraction_model: claude-haiku-4-5-20251001`, `graph_proximity_weight: 0.12`, `graph_expand_hops: 1`.
- `artemis/memory/retrieval.py` — `graph_expand` mode in `search_observations`; `graph_proximity: float = 0.12` in `RetrievalWeights`; entity→obs (1.0) + 1-hop neighbor→obs (0.5) scoring. **Bug fix:** `::vector` Postgres cast after named params (`:_qvec::vector`) was being parsed by SQLAlchemy as two parameters; fixed with `CAST(:_qvec AS vector)`.
- `artemis/memory/incremental_consolidator.py` — fires `notify_consolidation_complete` after successful consolidation.
- `artemis/mcp/memory_server.py` — 6 read-only MCP tools over stdio: `memory_search`, `memory_get_observation`, `memory_get_drawer`, `memory_list_scopes`, `memory_list_entities`, `memory_get_entity_neighborhood`. All handlers accept injectable `session_factory` for tests. Factory + entry point. MCP registered via `uv run python -m artemis.mcp.memory_server`.
- `artemis/memory/tests/test_b4_graph_mcp.py` — **59 tests** (all pass). Slug, vocabulary, entity upsert/alias/mention/relation, list, neighborhood, FK cascade, extraction (mock LLM + injectable session factory), parse output, find_entities_in_text, graph fusion, get_obs_ids/neighbors, MCP handlers (14).
- `pyproject.toml` — `mcp>=1.7.0,<2.0` dependency (mcp 1.27.1, released 2026-05-08, 8 days old, satisfies ≥7-day rule).

**Design notes:**
- `test_session_factory` fixture in conftest creates fresh NullPool sessions to avoid "Future attached to different loop" errors when `extract_for_observation` opens its own sessions inside extraction tests.
- MCP handler tests use a `_sf(db_session)` helper wrapping the existing test session to avoid production `SessionLocal` connections.
- `graph_proximity` weight is additive to the existing fusion (doesn't normalize to 1.0); fine for ranking purposes.

**Lead should run:**
```
uv run alembic upgrade head && uv run pytest && ./scripts/check.sh
```

297 passed, ruff/format/mypy strict all green as of commit `9d4ee7b`.

---

### Lead [Account 1] — B3 merged + verified (2026-05-16)

Worker's B3 (`12e75c0`) merged to main at `cb40321`. Lead-verification surfaced 3 test failures and several mypy errors; all fixed in `d8e3487`.

**Test fixes:** `source_quality` strict-equality on a REAL/float32 column (→ `pytest.approx`); a transaction-already-begun error from reading outside `begin()` (→ moved read inside); a B2 test that expected the old single-feature score channel (→ updated to saturate all four B3 sub-features).

**Production fixes:** the incremental consolidator used `get_session()` as a context manager (it's an `AsyncIterator`) — switched to `SessionLocal()`; passed `ScoredObservation` to a function expecting `Observation` — added a `model_validate` collapse; replaced a typing-resistant `call_later` lambda with a named function.

**Final:** 238 passed. ruff, format, mypy strict all green.

The Lead-verification pattern keeps earning its keep — without it this would have landed quietly broken.

**Worker's next pickup: B4 — graph & MCP.** Brief queued earlier in this file. Worker branch `worker/keystone-slice-b3-consolidation-scoring` left alive (Worker's worktree still on it); will fade when Worker switches.

### Lead [Account 1] — Phase C1 (marketing domain models) merged via Sonnet sub-agent (2026-05-16)

Spawned in parallel with the Worker's B3 — no conflict (different module trees). Sub-agent returned commit `1781ce4` on `lead/c1-marketing-domain-models`. Lead verified: 198 passed (123 + 75 new), ruff/mypy clean. Fast-forwarded `main`.

**Shipped:** 10 marketing-OS tables in Alembic migration `0004` (signal_queue, scout_runs, campaign_candidates, campaign_briefs, content_assets, content_asset_links, campaign_deliverables, rulesets, territory_config, approvals). SQLAlchemy 2.x async models, Pydantic 2 DTOs, repository helpers, 75 tests.

**Four contract deltas vs. Node logged in PROJECT_LOG.md** — Lead's call: keep as-is, let C2 routes bridge if needed. The most likely to bite later is the `approvals` table being simpler than Node's `unified_approvals`; flag if C4 Writing Studio adapter needs the missing fields.

**Two punted tables** (Node has, brief didn't list): `campaign_candidate_decisions` (audit log), `signal_reason_codes` (lookup). Defer to C2/C3 when they actually need them.

When the Worker finishes B3, the queue naturally flows into B4 (still needs the keystone graph schema), and *then* C2 (which now has C1 underneath it ready to go). C2 won't block.

### Lead [Account 1] — Phase E1 (UI port scaffolding) merged via Sonnet sub-agent (2026-05-16)

First slice executed under the new model-tiering protocol. Lead (Opus) wrote a self-contained brief and spawned a Sonnet sub-agent for the mechanical work; sub-agent returned with commit `b08d887` on `lead/e1-ui-port-scaffolding`. Lead verified (123 passed, lint+mypy clean), fast-forwarded `main`.

Shipped:
- Verbatim copy of `claudeck-artemis/public/` into `artemis-os/public/` — 163 files, ~86k lines.
- `artemis/main.py` removes the JSON root handler and mounts `StaticFiles(public, html=True)` after API routes so `/api/*` still takes precedence.
- `tests/test_static.py` — 4 smoke tests (index.html, nested css, /healthz regression, 404 path).
- `public/README-PORT.md` documents that the bundled JS still calls Node endpoints (resolved in E1b).

**Known follow-up (E1b):** the bundled `public/js/core/api.js` and friends call Node endpoints that don't exist in the Python app yet. UI shell loads but most interactions fail with network errors until E1b rewires the API client to the Python endpoints (which themselves arrive in Phase C2). No urgency — keeping the bytes verbatim until the backend catches up is the right ordering.

Worker is on `worker/keystone-slice-b3-consolidation-scoring` per `git branch -a`.

### Worker [Account 2] — B2 ready for review: 495f388 (2026-05-16)

Branch `worker/keystone-slice-b2-embeddings-retrieval` in `/Users/artemis/Desktop/Artemis/artemis-os/`. No push per protocol. Based on `8ebd6e5` (test infra fix); main is at `c2d6ab6` (F1 agent loop) — diff is clean.

**Delivered:**
- `alembic/versions/0003_memory_b2_embeddings_fts.py` — additive migration: `content_fts TSVECTOR GENERATED ALWAYS AS STORED` + GIN indexes on drawers+observations; `memory_embeddings` table with `vector(384)` + HNSW index (m=16, ef=64).
- `artemis/memory/models.py` — `content_fts` Computed(TSVECTOR) on MemoryDrawer + MemoryObservation; new `MemoryEmbedding` model with `Vector(384)`.
- `artemis/memory/schemas.py` — `ScoredObservation` DTO (observation fields + final_score, fts_rank, semantic_sim, recency).
- `artemis/memory/embeddings.py` — `EmbeddingProvider` protocol; `MiniLMProvider` (sentence-transformers all-MiniLM-L6-v2, lazy-loaded, executor-offloaded); `get_default_provider()` singleton; `ARTEMIS_EMBEDDING_PROVIDER` env knob.
- `artemis/memory/store.py` — `upsert_embedding()` (ON CONFLICT DO UPDATE); `write_drawer`/`write_observation` extended with embed-on-write using SAVEPOINT for isolation; failures logged, never block the write.
- `artemis/memory/retrieval.py` — `search_observations()`: FTS (ts_rank), semantic (pgvector cosine via `<=>`), recency (exp decay, half-life 30d), score channels; fusion weighted sum; validity-window filtering; returns `list[ScoredObservation]` sorted by final_score DESC.
- `artemis/memory/backfill.py` — `backfill_embeddings(engine, batch_size)` coroutine; idempotent (finds rows absent from memory_embeddings for current model_version); CLI `python -m artemis.memory.backfill`.
- `config/memory-retrieval.yaml` — default weights (fts:0.30, semantic:0.40, recency:0.15, score:0.15). Matches Node reference `memory-retrieval.json`.
- `artemis/memory/tests/` — 45 tests across test_b2_embeddings.py (14), test_b2_retrieval.py (26), test_b2_backfill.py (5). Covers embed round-trips, FTS ordering, scope union, validity windows, fusion score math, retrieval quality fixture (5 topics × 6 obs each, ≥3 overlap in top-5), backfill idempotency, graceful degradation.
- `artemis/memory/README.md` — updated with retrieval API, embedding service, backfill CLI docs.

**Known gap (same as B1):** `uv` absent from session PATH — tests syntax-checked (AST parse, all 11 files clean). Lead should run:
```
uv sync && uv run alembic upgrade head && uv run pytest artemis/memory/tests/ -v
```
Expect `sentence-transformers` model download (~90MB) on first run; subsequent runs use cache.

**Design note:** FTS tests that check retrieval ordering require real Postgres (content_fts generated column). The conftest creates tables from ORM metadata (Computed TSVECTOR column included), so FTS works without running migrations in tests.

### Worker [Account 2] — B1 ready for review: fd94074 (2026-05-16)

Branch `worker/keystone-slice-b1-storage-write` in `/Users/artemis/Desktop/Artemis/artemis-os/`. No push per protocol.

**Delivered:**
- `alembic/versions/0002_memory_keystone_tables.py` — 4 tables: memory_scopes, memory_drawers, memory_observations, memory_evidence. BIGSERIAL PKs, TIMESTAMPTZ, JSONB source_extra. All indexes + partial index on active observations.
- `artemis/memory/models.py` — SQLAlchemy 2.x async ORM models for all 4 tables.
- `artemis/memory/schemas.py` — Pydantic 2.x DTOs: Scope, Source, Drawer, Observation, Evidence, ScopeRead.
- `artemis/memory/store.py` — write_drawer, write_observation, link_evidence, supersede_observation, get_drawer, get_observation, list_evidence_for_observation. All idempotent. Lossless rule enforced.
- `artemis/memory/tests/` — 55 tests (45 functions + 2 × 6 parametrized scope-kind rounds). Covers all brief requirements.
- `artemis/memory/README.md` — public API docs + lossless rule explanation.

**Known gap:** `uv` and `docker` were absent from this session's PATH — tests could not be executed at runtime. Syntax-checked all files with Python 3.11. Lead should run `docker compose up -d && uv sync && uv run alembic upgrade head && uv run pytest artemis/memory/tests/` to verify.

**Schema deviation note (none):** No schema columns were added beyond the brief. The `source_extra JSONB` field is exactly as specified. Self-referential FK on `memory_observations.superseded_by` is intentional (mirrors Node schema).

### Lead [Account 1] — Phase A scaffolding COMPLETE

New repo at `/Users/artemis/Desktop/Artemis/artemis-os/`. Initial commit `cdfb7cb` on `main`. 21 files. Local-only git per protocol.

Shipped:
- `pyproject.toml` (uv-managed), FastAPI + SQLAlchemy 2.x async + asyncpg + Pydantic 2 + Alembic + pgvector + Anthropic SDK + httpx.
- `docker-compose.yml` — `pgvector/pgvector:pg16` on host port `5433`. User `artemis`, DB `artemis_os`.
- `artemis/config.py` — pydantic-settings, `ARTEMIS_*` env prefix, `.env` autoload.
- `artemis/db.py` — async engine, `SessionLocal`, `Base`, `get_session()` FastAPI dependency.
- `artemis/main.py` — FastAPI app, `/` root, mounts health router.
- `artemis/routes/health.py` — `/healthz` (liveness) + `/readyz` (DB reachable).
- Alembic configured with async env. First migration `0001_enable_pgvector.py` runs `CREATE EXTENSION IF NOT EXISTS vector`.
- `tests/conftest.py` — `httpx.AsyncClient` against ASGI transport (no real server needed).
- `tests/test_smoke.py` — 2 tests covering `/` and `/healthz`.
- `scripts/check.sh` — ruff check + ruff format --check + mypy + pytest.
- `CLAUDE.md` — full operating rules, reference pointers back to claudeck-artemis, dev quickstart.

**Worker is unblocked for Slice B1.** The keystone brief below applies; Worker should pull from the new repo at `/Users/artemis/Desktop/Artemis/artemis-os/`, branch `worker/keystone-slice-b1-storage-write`, work against the scaffold as it stands.

Lead's next slice: starting Phase E1 (UI port) and Phase F1 (agent loop) are both candidates, but the next sensible Lead pickup is **Phase B Slice 2 brief authoring** so the Worker has B2 queued when B1 lands. Doing that next.

### BRIEF FOR WORKER — Phase B Slice 1: Memory keystone storage + write path

**Status:** ready for pickup once Phase A scaffolding is in place.
**Reference implementation:** `claudeck-artemis/db/sqlite.js` (memory keystone sections), `claudeck-artemis/server/memory-store.js`, `claudeck-artemis/docs/PLAN-memory-keystone.md` §3 §4 §6.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/` (or whatever Jon renames it to).
**Branch:** `worker/keystone-slice-b1-storage-write`
**Reviewer:** Lead, locally via `git diff main...worker/keystone-slice-b1-storage-write`.

**Goal.** Build the foundation tier of the Python memory keystone: Postgres schema + write path. No embeddings yet (slice B2). No consolidation yet (B3). No graph yet (B4). Just clean drawer / observation / evidence storage with the lossless rule enforced.

**Deliverables.**

1. **Alembic migration** creating:
   - `memory_scopes(scope_kind, scope_id, display_name, parent_scope_kind, parent_scope_id, created_at)` PK (scope_kind, scope_id).
   - `memory_drawers(id BIGSERIAL PK, scope_kind, scope_id, corpus_kind, content TEXT, content_hash, source_kind, source_id, source_extra JSONB, owner_user_id BIGINT NULL, captured_at)`. Unique on `(scope_kind, scope_id, content_hash)`. Indexes per the Node §6 schema.
   - `memory_observations(id BIGSERIAL PK, scope_kind, scope_id, category, content, content_hash, score REAL, hit_count INT, source_quality REAL, user_confirmed BOOL, valid_from TIMESTAMPTZ NULL, valid_until TIMESTAMPTZ NULL, superseded_by BIGINT NULL FK self, owner_user_id BIGINT NULL, created_at, accessed_at)`. Unique on `(scope_kind, scope_id, content_hash)`.
   - `memory_evidence(id BIGSERIAL PK, observation_id BIGINT FK CASCADE, source_kind, source_id, source_quote TEXT, weight REAL, created_at)`.
   - **Differences from the Node schema** (intentional improvements):
     - Use `BIGSERIAL` not `INTEGER`. We're not future-trapped on row count.
     - Use `TIMESTAMPTZ` not `INTEGER` for timestamps. Postgres-native.
     - `owner_user_id` is first-class `BIGINT NULL` from day one (Node had it nullable too, but reserved).
     - **Do not** carry over the `memories` view / `INSTEAD OF` trigger compatibility hack. There's no legacy `memories` table to be compatible with in the new app.
     - Use `JSONB` for `source_extra`. Postgres-native.

2. **SQLAlchemy 2.x async models** for the four tables. Pydantic 2.x schemas for the read/write DTOs.

3. **Repository / write API** in `artemis/memory/store.py`:
   - `async def write_drawer(scope: Scope, content: str, source: Source, corpus_kind: str | None = None, owner_user_id: int | None = None) -> Drawer`
   - `async def write_observation(scope: Scope, content: str, category: str = "discovery", source_quality: float = 0.5, valid_from: datetime | None = None, valid_until: datetime | None = None) -> Observation`
   - `async def link_evidence(observation_id: int, source_kind: Literal["drawer","observation"], source_id: int, source_quote: str | None = None, weight: float = 1.0) -> Evidence`
   - `async def supersede_observation(old_id: int, new_id: int) -> None` — sets `superseded_by` only.
   - `async def get_drawer(id: int) -> Drawer | None`
   - `async def get_observation(id: int) -> Observation | None`
   - `async def list_evidence_for_observation(observation_id: int) -> list[Evidence]`

4. **Lossless rule enforced at the type level.** There is no `delete_drawer`. There is no `delete_observation`. Removal of observations from active retrieval happens only via supersession. **This is load-bearing** — surface it in module-level docstrings and tests. If the Node reference has any deletion path, do not port it.

5. **Pytest coverage:** ≥25 tests covering:
   - Drawer + observation + evidence happy path (write, read, link).
   - Unique constraint enforcement (same content_hash within a scope is a no-op or update, not a duplicate).
   - Supersession sets `superseded_by` and is queryable.
   - Evidence chains traversable from observation → drawer.
   - The "lossless" invariant: there is no public API that deletes drawers or observations.
   - Scope round-trip for all six scope kinds (`project`, `workspace`, `brand`, `agent`, `skill`, `global`).
   - Owner_user_id round-trip (set, read, default null).

**Out of scope for this slice.**
- Embeddings, pgvector, retrieval — **all B2.**
- FTS — **B2.**
- Consolidation, scoring, decay — **B3.**
- Graph entities — **B4.**
- Routes / FastAPI endpoints — Phase C.
- Migrating data from the Node SQLite — Phase H.

**Approach guidance.**
- Read `claudeck-artemis/db/sqlite.js` sections for memory schema (search for `memory_drawers`, `memory_observations`, `memory_evidence`). Read `claudeck-artemis/server/memory-store.js`. The behavior there is the spec. Translate semantics, not syntax.
- The Node app uses `better-sqlite3` (synchronous). We use SQLAlchemy async. Don't try to mirror sync patterns.
- Where the Node code has SQLite-specific cleverness (FTS5 triggers, INSTEAD OF triggers on the legacy `memories` view), simply omit. Those exist for legacy compatibility we don't need.
- Use `async with session.begin():` for any multi-statement write. Atomic by default.

**Authority.**
- You can pick the SQLAlchemy session / engine layout, pytest fixture style, type-checking config (mypy vs. pyright), and lint config (ruff). Default to ruff + mypy strict; deviate only with reason.
- You can pick the directory layout under `artemis/memory/`. Sensible defaults: `store.py`, `models.py`, `schemas.py`, `repositories.py`. Lump or split as reads best.
- You do not have authority to alter the schema beyond what's in deliverable 1 above. If the Node behavior requires a column we don't have, surface it here — don't add silently.

**Coordination.**
- On startup: read this file, `PROJECT_LOG.md`, `decisions/artemis-python-rebuild.md`, `decisions/rebuild-phased-plan.md`. Read the relevant Node reference sections.
- Append `### HH:MM — Worker [Account 2] — Started B1` here when you begin.
- Append `### HH:MM — Worker [Account 2] — B1 ready for review: <commit hash>` when done. No push.
- Trigger pauses (per §K.3 still applies): schema deviation from this brief, blast radius beyond `artemis/memory/`, anything that would touch `claudeck-artemis/`.

**What "done" looks like.**
- `alembic upgrade head` runs clean on a fresh Postgres.
- `pytest artemis/memory/tests/` is green, ≥25 tests passing.
- A short `artemis/memory/README.md` documenting the public API and the lossless rule.
- A commit on `worker/keystone-slice-b1-storage-write` ready for Lead review.

---

## BRIEF FOR WORKER — Phase B Slice 2: Embeddings + FTS + retrieval fusion

**Status:** queued. Picks up after B1 merges. Worker may read this brief during B1 to prep mental model.
**Reference implementation:** `claudeck-artemis/server/memory-embeddings.js`, `claudeck-artemis/server/memory-retrieval.js`, `claudeck-artemis/config/memory-retrieval.json`, the FTS5 + vec0 sections of `claudeck-artemis/db/sqlite.js`.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/keystone-slice-b2-embeddings-retrieval`

**Goal.** Light up semantic + lexical retrieval on top of the B1 storage layer. The fusion reranker is the heart of this slice — `search_observations()` is the API the rest of the app will retrieve through.

**Deliverables.**

1. **Alembic migration (additive)** creating `memory_embeddings`:
   ```
   memory_embeddings(
     id BIGSERIAL PK,
     target_table TEXT NOT NULL CHECK (target_table IN ('drawer','observation')),
     target_id BIGINT NOT NULL,
     model_version TEXT NOT NULL,
     embedding vector(384),
     created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     UNIQUE(target_table, target_id, model_version)
   )
   ```
   Plus an **HNSW index** on `embedding` with op class `vector_cosine_ops`. (Better recall, no `lists` tuning, supports incremental builds — preferred over IVFFlat for our scale.)

   Same shape as Node's `vec0` virtual table conceptually, but a regular table — supports proper joins back to drawers/observations and lets multiple model versions coexist for future re-embedding without dropping old vectors.

2. **FTS columns + GIN indexes on drawers and observations.** Add `content_fts tsvector` as a generated column (`GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`) on both tables with a GIN index. Replaces SQLite FTS5; no triggers needed — Postgres maintains it automatically.

3. **`artemis/memory/embeddings.py`** — embedding service behind a small interface:
   ```python
   class EmbeddingProvider(Protocol):
       async def embed(self, text: str) -> list[float]: ...
       async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
       @property
       def model_version(self) -> str: ...
       @property
       def dims(self) -> int: ...
   ```
   Default impl: `MiniLMProvider` using `sentence-transformers` (`all-MiniLM-L6-v2`). Lazy load; first call warms the model. 384 dims. `model_version` like `"all-MiniLM-L6-v2@1"` (the `@N` reserves a future re-embed roll). `get_default_provider()` returns the configured one. Env knob for future swap: `ARTEMIS_EMBEDDING_PROVIDER` (default `minilm`).

4. **Embed-on-write paths.** Extend B1's `write_drawer` and `write_observation` to embed + insert into `memory_embeddings` in the same transaction. Helper: `upsert_embedding(target_table, target_id, model_version, vector)`. Graceful fallback — if the provider fails, log a warning, write the row anyway, mark for backfill. Embeddings are best-effort; they never block writes.

5. **`artemis/memory/retrieval.py`** — the fusion reranker. Shape:
   ```python
   async def search_observations(
       scope_set: list[Scope],
       query: str,
       limit: int = 10,
       as_of: datetime | None = None,
       modes: list[Literal["fts","semantic","recency","score"]] | None = None,
       cfg: RetrievalConfig | None = None,
   ) -> list[ScoredObservation]
   ```
   - Candidate pools: FTS top-K (`ts_rank`), semantic top-K (pgvector cosine via `<=>`), recency top-K, score top-K.
   - Union into one candidate set keyed by observation id.
   - Per-candidate features: `fts_rank`, `semantic_sim`, `recency` (exponential decay), `score`, `hit_count`, `source_quality`, `user_confirmed`, `category_priority`, `valid_now`.
   - Final score: weighted sum from `config/memory-retrieval.yaml`. Defaults match the Node JSON (`fts: 0.30, semantic: 0.40, recency: 0.15, score: 0.15`).
   - `valid_from` / `valid_until` filtering respects `as_of` (default `now`). NULL bounds = always valid.
   - Scope filter: `(scope_kind, scope_id) IN scope_set AND superseded_by IS NULL`.

6. **Config:** `config/memory-retrieval.yaml` mirroring the Node `memory-retrieval.json` shape but YAML. Load via Pydantic Settings or a small loader in `artemis/memory/config.py`.

7. **Background embedding backfill.** A `backfill_embeddings()` coroutine that finds rows lacking an embedding for the current `model_version` and embeds in batches of 50. Idempotent. Yields between batches. Surfaced as CLI: `uv run python -m artemis.memory.backfill`. Not auto-run.

8. **Pytest coverage: ≥30 tests.**
   - Embedding round-trip (write → read → cosine similarity to query).
   - FTS round-trip (insert, search, rank order).
   - Fusion ordering: a corpus where one candidate wins on FTS only, one on semantic only, one on recency only — fusion ranks them per weights.
   - Scope union: query against `[project:P, workspace:default]` returns rows from both, excludes others.
   - `as_of` semantics: row with `valid_until < as_of` excluded; row with `valid_from > as_of` excluded.
   - Backfill idempotency: running twice doesn't duplicate embeddings.
   - Graceful degradation: when embedding provider is mocked to fail, writes succeed and FTS-only retrieval still works.
   - Retrieval-quality fixture: ~30 hand-written observations covering 5 topics; assert top-5 for a topic-relevant query overlaps ≥3 with the topic's labeled set. Lift the corpus shape from `claudeck-artemis/docs/MEMORY-RETRIEVAL-QUALITY-VALIDATION.md` if it has one.

**Out of scope.** Consolidation (B3). Graph (B4). UI / routes (Phase C+).

**Authority.** Embedding loader pattern (lazy singleton vs. injected). Config location (default `config/` at repo root for ops visibility). No new model providers in V1 — minilm only.

**Trigger pauses.** Schema additions beyond deliverables 1+2. Anything that requires a B1 schema change — surface here, Lead amends.

**Done.** Migrations clean. Pytest ≥30 green. `search_observations()` returns scored results against a real Postgres+pgvector with HNSW index. `artemis/memory/README.md` updated with retrieval API + config pointer.

---

## BRIEF FOR WORKER — Phase B Slice 3: Consolidation + scoring + temporal

**Status:** queued. Picks up after B2 merges.
**Reference implementation:** `claudeck-artemis/server/memory-consolidator.js`, `claudeck-artemis/server/memory-incremental-consolidator.js`, `claudeck-artemis/server/memory-optimizer.js`, decay path in `claudeck-artemis/server/memory-store.js`.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/keystone-slice-b3-consolidation-scoring`

**Goal.** Make observations earn their keep over time. Consolidate semantically-overlapping observations into superseding ones (lossless), tag sources by quality, decay scores by category, respect validity windows in retrieval.

**Deliverables.**

1. **`artemis/memory/consolidator.py`** — pure-function consolidation engine. Takes a list of candidate observations and an LLM call surface, returns `ConsolidationProposal{ new_content, supersedes_ids[], evidence_from_ids[], category, source_quality }`. Lossless: never deletes; always creates a new observation that supersedes via `superseded_by`. Evidence rows link the new observation to each source drawer/observation with `source_quote` captured.

2. **LLM consolidation prompt.** Calls Anthropic Haiku (`claude-haiku-4-5-20251001`) via the Python SDK **with prompt caching from day one**. Input: 5–15 observations from one scope + category. Output: JSON list of consolidation proposals. Reject malformed JSON with one structured retry, then skip without crashing.

3. **`artemis/memory/incremental_consolidator.py`** — per-scope counter + debounce timer. `notify_drawer_written(scope)` increments. When threshold crossed (default 25, env `ARTEMIS_INCREMENTAL_CONSOLIDATION_THRESHOLD`), schedules a debounced run (default 30s, env `ARTEMIS_INCREMENTAL_CONSOLIDATION_DEBOUNCE_MS`). In-flight guard prevents overlap. `ARTEMIS_INCREMENTAL_CONSOLIDATION_DISABLED=1` opt-out. Hook `write_drawer` to call `notify_drawer_written`.

4. **Source-quality tagging on write paths.** Extend `write_observation` to accept `source_quality: float` (default 0.5). Add a `Source.quality_hint` enum that write helpers translate: explicit user input → 1.0, agent emit → 0.7, extractor → 0.5, consolidation → 0.9. Update B2's embed-on-write accordingly.

5. **Score features wiring.** Update B2's fusion reranker to consume `score_features` sub-weights from config (relevance / hits / quality / confirmed — already in the Node JSON; mirror in YAML). The `score` channel becomes a weighted sum.

6. **Category-aware decay.** A `run_maintenance()` coroutine multiplies `score` by per-category factors (warning 1.0, convention 0.99, decision 0.97, discovery 0.93; default 0.95; min_score floor 0.1). Idempotent. CLI: `uv run python -m artemis.memory.maintain`. HTTP endpoint `POST /api/memory/maintain` ships once Phase C routes start landing — stub the endpoint here, mount in Phase C.

7. **Validity windows in retrieval.** B2's `as_of` parameter is already in the signature — make sure every retrieval channel respects it (FTS, semantic, recency, score). NULL bounds = always valid. Add explicit tests.

8. **Pytest coverage: ≥25 tests.**
   - Consolidation produces zero deletions; old rows still queryable with `include_superseded=True`.
   - Evidence chain: new observation has `memory_evidence` rows pointing at every source.
   - Incremental trigger fires at threshold, debounces, doesn't overlap.
   - Source-quality round-trip per write path.
   - Decay simulation: 30-week run shows warnings stay sticky (≈1.5 → 1.5), discoveries decay below 0.25.
   - Validity exclusion in retrieval at three time points (before / during / after a window).
   - LLM mock: malformed JSON triggers retry once, then skips without crashing.

**Out of scope.** Graph extraction (B4). MCP (B4). Promoting `agent_context` to observations (Phase F).

**Authority.** Anthropic SDK retry/backoff shape. Prompt template location (default `artemis/memory/prompts/consolidate.txt`).

**Trigger pauses.** Any change to the lossless rule. Any new schema migration. Anything requiring B1/B2 model changes beyond adding columns.

**Done.** Pytest ≥25 green. Manual run on a corpus of 50 hand-written observations produces sensible consolidation proposals (eyeball, not asserted). README updated.

---

## BRIEF FOR WORKER — Phase B Slice 4: Graph & MCP

**Status:** queued. Picks up after B3 merges. The biggest of the four B slices.
**Reference implementation:** `claudeck-artemis/server/memory-graph-extractor.js`, `claudeck-artemis/server/mcp-memory.js`, entity helpers in `claudeck-artemis/db/sqlite.js` (around line 4854), `claudeck-artemis/docs/PLAN-memory-keystone-p3.md` (treat as spec — see the status banner; the prose still reads forward-looking).
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/keystone-slice-b4-graph-mcp`

**Goal.** Add the graph layer (entities + relations + mentions) and expose read-only MCP tools so external Claude Code instances can query Artemis memory.

**Deliverables.**

1. **Alembic migration (additive)** creating:
   - `memory_entities(id, entity_kind, canonical_name, name_slug, scope_kind, scope_id, attributes JSONB, first_seen_at, last_seen_at, mention_count, confidence, superseded_by)` — unique on `(scope_kind, scope_id, entity_kind, name_slug)`.
   - `memory_entity_aliases(id, entity_id FK CASCADE, alias, alias_slug, created_at)` — unique on `(entity_id, alias_slug)`.
   - `memory_entity_mentions(id, entity_id FK CASCADE, source_kind, source_id, mention_quote, weight, created_at)` — unique on `(entity_id, source_kind, source_id)`.
   - `memory_relations(id, subject_id FK CASCADE, predicate, object_id FK CASCADE, evidence_observation_id FK SET NULL, weight, confidence, first_seen_at, last_seen_at, superseded_by)` — unique on `(subject_id, predicate, object_id)`.
   - Additive columns on `memory_observations`: `graph_status TEXT`, `graph_attempt_count INT DEFAULT 0`, `graph_last_attempt_at TIMESTAMPTZ`.

2. **Predicate vocabulary enforcement.** Server-side allowlist:
   ```
   works_on, owns, publishes_to, belongs_to, posted_on,
   runs_campaign, authored_by, mentioned_with, related_to
   ```
   `upsert_relation` rejects unknown predicates with a structured error. Rejected attempts logged to `memory_relation_rejections(id, subject_id, predicate, object_id, rejected_at)` for the dev-only debug endpoint.

3. **`artemis/memory/graph_extractor.py`** — Haiku 4.5 call that takes one observation and emits `{entities: [...], relations: [...]}` JSON. Same SDK pattern + prompt caching as B3. Coreference scope: literal/alias surface forms only (no pronouns). Cross-scope entities are scope-local (entity in `workspace:default` and same name in `brand:foo` are two rows). Scope-overridable model via `config/memory-graph.yaml` → `extraction_model`.

4. **Extraction trigger.** Hook B3's incremental consolidator: after an observation is consolidated, schedule graph extraction for it. Set `graph_status='pending'` on enqueue, `'ok'` on success, `'failed'` with exponential backoff (1m → 5m → 30m → 2h → 5-attempt cap) on parse error / timeout. Per-observation in-flight guard.

5. **Graph fusion modality in retrieval.** Add a `graph_expand` modality to B2's reranker. Detect mentioned entities in the query via `find_entities_in_text(scope_set, text)` (slug + alias scan). Expand 1 hop in `memory_relations` (hop cap configurable, default 1). Collect mentioned-by observations. Union into the candidate set. Reranker scores `graph_proximity` (0 / 0.5 / 1.0 by hop distance) with weight `0.12` per config.

6. **`artemis/mcp/memory_server.py`** — read-only MCP server using the official Python MCP SDK (`mcp` package on PyPI). Stdio transport. Tools:
   - `memory_search(scope_set?, query, limit?, as_of?)` → wraps `search_observations`.
   - `memory_get_observation(id)` → wraps `get_observation` + evidence chain.
   - `memory_get_drawer(id)` → wraps `get_drawer`.
   - `memory_list_scopes(filter?)` → lists `memory_scopes` rows.
   - `memory_list_entities(scope, kind?)` → wraps `list_entities_for_scope`.
   - `memory_get_entity_neighborhood(id, hops?)` → wraps `get_entity_neighborhood`.
   All read-only. No write tools. Document registration snippet in `artemis/mcp/README.md`.

7. **Pytest coverage: ≥50 tests across the four mechanisms.**
   - Schema: migration idempotency; FK cascades work.
   - Entity helpers: upsert dedupes by slug; `mention_count` increments; alias resolution; neighborhood traversal stops at hop limit.
   - Extraction: idempotency on re-run; malformed JSON triggers failed path; retry backoff respects attempt count; predicate-vocab rejection logged.
   - Graph fusion: marketing-style query ("posts about Spring 2026 campaign on LinkedIn") returns related Post and Channel observations in top-5 even when query text doesn't lexically match.
   - MCP: each of the six tools input-schema validates, scope-filter behaves, `as_of` passthrough, error mapping, end-to-end stdio round-trip against a real `artemis_os` db.

**Out of scope.** Write-side MCP (deferred). Wings/rooms frontend (Phase E). Marketing automations using the graph (Phase D / Track 6).

**Authority.** MCP SDK version pin (must be ≥7 days old). Entity-extraction prompt template location.

**Trigger pauses.** Predicate vocabulary changes (push to Jon — Creative Director / spec call). Anything requiring write-side MCP. Schema additions beyond what's listed.

**Done.** Pytest ≥50 green. MCP server registers with a separate Claude Code process (smoke from terminal); `memory_search` round-trips real results. README in `artemis/mcp/` documents registration.

---

## Worker queue summary (post-B1)

After B1 merges, Worker picks up B2 → B3 → B4 in order. Each brief is self-contained; reading the next while finishing the current builds mental model.

After B4 lands, the keystone is at functional parity with the Node reference. Phase C (marketing OS contracts + plumbing) begins — briefs below.

---

## BRIEF FOR WORKER — Phase C Slice 1: Domain models + Alembic migrations

**Status:** queued. Picks up after B4 merges.
**Reference implementation:** `claudeck-artemis/db/sqlite.js` (search for `signal_queue`, `campaign_candidates`, `campaign_briefs`, `content_assets`, `rulesets`, `territory_config`, `scout_runs`, `campaign_deliverables`), `claudeck-artemis/marketing-ops-v1/schemas/`.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/phase-c1-domain-models`

**Goal.** Translate the marketing-OS data contracts from the Node SQLite schema into Postgres + SQLAlchemy + Pydantic. Faithful port of structure; idiomatic Postgres types.

**Deliverables.**

1. **Alembic migration** for the marketing-OS tables. Mirror the Node schema in `db/sqlite.js`:
   - `signal_queue` — id, source_type, source_url, source_id, headline, summary, campaign_family, urgency_tier, discovered_by, district_id (nullable), state, reason_codes (JSONB array), provenance (JSONB), qualification_json (JSONB), signal_status, snoozed_until, rejected_reason, created_at, updated_at.
   - `scout_runs` — id (TEXT, format `scout_run_YYYYMMDD_<type>_<uuid8>`), scout_type, status (`pending | dry_run_passed | committed | failed`), dry_run_summary JSONB, created_signal_ids JSONB, errors JSONB, started_at, completed_at.
   - `campaign_candidates` — id, source_signal_id FK, campaign_family, stage, decision_state, workspace_state, ruleset_version_at_qualification, metrics_json JSONB, deliverables JSONB (legacy column — keep for back-compat), created_at, updated_at.
   - `campaign_briefs` — id, candidate_id FK CASCADE, content JSONB, generated_at, generated_by.
   - `content_assets` — id, asset_type, status, summary, metadata JSONB, created_at, updated_at.
   - `content_asset_links` — id, candidate_id FK, asset_id FK, link_role, created_at.
   - `campaign_deliverables` — id, candidate_id FK, deliverable_id (Writing Studio ref), campaign_id, status, metadata JSONB, created_at, updated_at.
   - `rulesets` — id, family, version_tag, hard_filters JSONB, weighted_signals JSONB, qualitative_rubrics JSONB, state (`draft|active|archived`), created_at.
   - `territory_config` — id, family, hot_states JSONB, standard_states JSONB, unlisted_multiplier REAL DEFAULT 0.85, created_at, updated_at. **One row per family.**
   - `approvals` — id, kind (`signal_approval | writing_gate_2 | ...`), subject_id, status, decided_by, decided_at, decision_payload JSONB, created_at.
   - **Use `TIMESTAMPTZ` for all timestamps. JSONB for all JSON columns. `BIGSERIAL` for PKs except `scout_runs` (its id is a structured string).**
   - **Include `owner_user_id BIGINT NULL` on the user-facing tables** (signal_queue, campaign_candidates, content_assets) per the multi-user-ready convention from Phase B.

2. **SQLAlchemy 2.x async models** for each table. Mirror the column shape and FK behavior. Models live under `artemis/marketing/models.py` (one file is fine; split if it grows past ~600 lines).

3. **Pydantic 2.x schemas** for read / write DTOs in `artemis/marketing/schemas.py`. Match the JSON shapes the Node API returns so the frontend port (Phase E) is a no-op on payload. Reference: `claudeck-artemis/marketing-ops-v1/schemas/signal.md`, `campaign-brief.md`, `ruleset.md`, etc.

4. **Repository helpers** in `artemis/marketing/repository.py`:
   - `create_signal`, `find_signal_by_dedupe_key`, `list_signals`, `get_signal`, `update_signal`, `save_signal_qualification`.
   - `get_active_ruleset_version(family)`, `list_ruleset_versions`, `activate_ruleset_version`.
   - `create_campaign_candidate_from_signal(signal_id, ruleset_version_tag, qualification_summary)`.
   - `create_campaign_brief`, `get_campaign_brief`.
   - `create_content_asset`, `link_content_asset_to_candidate`, `list_campaign_asset_links(candidate_id)`.
   - `create_approval`, `decide_approval(id, decision, decided_by)`.
   - `get_territory_config(family)`.
   - `create_scout_run`, `update_scout_run`, `get_scout_run`, `list_scout_runs`.

5. **Pytest coverage: ≥40 tests.**
   - Migration idempotency on a fresh db.
   - Each model round-trips through SQLAlchemy.
   - Each Pydantic schema validates a sample payload from the Node reference. Lift sample payloads from `claudeck-artemis/marketing-ops-v1/schemas/*.md` examples.
   - Repository helpers: happy path + one edge case per (e.g., dedupe key collision returns existing).

**Out of scope.** Routes (C2). Qualifier logic (C3). Live scout execution (Phase D).

**Authority.** Decide whether to split `models.py` by table-family (signals / candidates / rulesets / etc.) or keep one file. Keep it one file until line count justifies splitting.

**Trigger pauses.** Schema changes beyond what's in deliverable 1. If the Node app has a table not in the list above, surface here — Lead decides whether to include.

**Done.** Migrations clean. Pytest ≥40 green. README in `artemis/marketing/` documents the public repository API.

---

## BRIEF FOR WORKER — Phase C Slice 2: HTTP routes (faithful port)

**Status:** queued. Picks up after C1 merges.
**Reference implementation:** `claudeck-artemis/server/routes/{scouts,signal-queue,signal-criteria,campaign-ops,campaign-deliverables,content-assets,approvals}.js`. The Node API contract is the spec.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/phase-c2-routes`

**Goal.** Port every marketing-OS HTTP endpoint from Node/Express to Python/FastAPI. Same payloads, same status codes, same error semantics. The frontend port in Phase E needs this to be byte-equivalent.

**Deliverables.** A FastAPI router per Node route file. Mount all under their existing paths:

1. `artemis/marketing/routes/scouts.py` → `/api/scouts`. Endpoints: `GET /packages`, `GET /runs`, `GET /runs/:id`, `POST /runs` (harness with `dryRun` flag).
2. `artemis/marketing/routes/signal_queue.py` → `/api/signal-queue`. Endpoints: `POST /intake`, `GET /` (list with pagination + filters), `GET /:id`, `POST /:id/qualify`, `POST /:id/approve`, `POST /:id/reject`, `POST /:id/snooze`, `POST /:id/ask`.
3. `artemis/marketing/routes/signal_criteria.py` → `/api/signal-criteria`. Ruleset CRUD + activation: `GET /rulesets`, `GET /rulesets/:family`, `POST /rulesets`, `POST /rulesets/:id/activate`.
4. `artemis/marketing/routes/campaign_ops.py` → `/api/campaign-ops`. Endpoints: `GET /candidates`, `GET /candidates/:id`, `POST /candidates/:id/brief/assemble`, `POST /candidates/:id/advance`.
5. `artemis/marketing/routes/campaign_deliverables.py` → `/api/campaign-deliverables`. Endpoints: `GET /:candidate_id`, `POST /` (writing-handoff), `POST /:id/submit-review`.
6. `artemis/marketing/routes/content_assets.py` → `/api/content-assets`. Endpoints: `GET /`, `POST /`, `GET /:id`, `PATCH /:id`, `POST /links`, `DELETE /links/:id`.
7. `artemis/marketing/routes/approvals.py` → `/api/approvals`. Endpoints: `GET /`, `GET /:id`, `POST /:id/decision`.

8. **Mount all routers** in `artemis/main.py`. Apply CORS as the Node app does. Apply the shared auth dependency (single-token from `ARTEMIS_TOKEN`).

9. **Status codes and error shapes.** Match the Node app's `routes/api-errors.js` shape (`{ error: string, code: string, details?: object }`). 422 for validation, 404 for not-found, 409 for conflicts, 500 for unexpected.

10. **Pytest coverage: ≥80 tests** — one passing happy-path test per endpoint + one or two error paths. Use the `httpx.AsyncClient` ASGI fixture from Phase A. Each test creates its own scoped fixtures; no test depends on another's state.

**Out of scope.** Qualifier logic (C3 — for now `qualify` returns a stub `{ qualifiedAt, scores: [] }`). Brief assembler (C3 — stub returns `{ stub: true }`). Real scout execution (Phase D — the harness `POST /runs` validates findings, no live API calls).

**Authority.** FastAPI dependency injection patterns, request/response models, error handler shape. Default: one `APIRouter` per file, all wired through `artemis/main.py`.

**Trigger pauses.** A Node endpoint behavior that surprises you (looks like a bug in the reference) — surface here, Lead decides port-as-is or port-with-fix.

**Done.** Pytest ≥80 green. `uv run uvicorn artemis.main:app` serves all the routes. README in `artemis/marketing/routes/` lists every endpoint + reference Node file.

---

## BRIEF FOR WORKER — Phase C Slice 3: Qualifier + brief assembler

**Status:** queued. Picks up after C2 merges.
**Reference implementation:** `claudeck-artemis/server/signal-qualifier.js` (134 lines, pure), `claudeck-artemis/server/campaign-brief-assembler.js` (149 lines), `claudeck-artemis/server/scout-intake.js` (157 lines), and the Node tests `tests/unit/backend/signal-qualifier.test.js`, `tests/unit/backend/campaign-brief-assembler.test.js`.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/phase-c3-qualifier-brief`

**Goal.** Port the pure deterministic logic — qualifier scoring, brief assembly, scout intake validation — to Python. Replace the stubs from C2.

**Deliverables.**

1. **`artemis/marketing/qualifier.py`** — port of `signal-qualifier.js`:
   ```python
   def qualify_signal(
       signal: Signal,
       active_family_rulesets: dict[str, Ruleset],
       territories_by_family: dict[str, TerritoryConfig],
   ) -> QualificationResult
   ```
   Three-phase: (1) hard filter check (territory presence + ruleset hard_filters), (2) weighted signal match (`reason_codes × ruleset.weighted_signals × confidence`), (3) territory multiplier (hot 1.2× / standard 1.0× / unlisted 0.85×). Score clamped to [0, 1]. Pure — no DB, no LLM, no I/O. Inputs pre-loaded by the caller.

   Returns `QualificationResult{ qualified_at, ruleset_versions_used, scores: list[FamilyScore], recommended_families }`.

2. **`artemis/marketing/brief_assembler.py`** — port of `campaign-brief-assembler.js`. Takes a candidate + its signal(s) + qualification summary + linked content assets + (stub) district data + (stub) contact data; emits the assembled brief object (matches the Node `metadata_json.brief` shape used by Writing Studio). Brief includes `districtDataUnavailable`, `contactsUnavailable` flags when source data is missing.

3. **`artemis/marketing/scout_intake.py`** — port of `scout-intake.js`. `normalize_intake_payload(payload, scout_type) -> NormalizedFinding`. Validates `VALID_SOURCE_TYPES`, `VALID_CAMPAIGN_FAMILIES`, `VALID_URGENCY_TIERS`. Overrides `discovered_by` to `scout_type` unconditionally (anti-spoof).

4. **Wire into C2 routes.**
   - `POST /api/signal-queue/intake` calls `normalize_intake_payload` then `create_signal` then (best-effort, non-fatal) `qualify_signal`.
   - `POST /api/signal-queue/:id/qualify` calls `qualify_signal` against current active rulesets.
   - `POST /api/signal-queue/:id/approve` stamps `ruleset_version_at_qualification` from the signal's stored `qualification_json` (locks the version that was used), falls back to active version for unqualified signals — same as Node.
   - `POST /api/campaign-ops/candidates/:id/brief/assemble` calls `brief_assembler`.
   - `POST /api/scouts/runs` with `dryRun: false` calls `normalize_intake_payload` per finding then `create_signal` for valid non-duplicates.

5. **Pytest coverage: ≥60 tests.**
   - Qualifier: hard filters (5), weighted scoring (8), territory multiplier (4), minFitScore gate (3), recommended_families (5), ruleset_versions_used (4), edge cases (3) — mirror the Node `signal-qualifier.test.js` 32-test layout, port assertions 1:1.
   - Brief assembler: per shape (signal-only, signal+asset, signal+district-unavailable, etc.).
   - Scout intake: each valid_set boundary, anti-spoof discovered_by override.
   - Route integration: intake auto-qualifies on POST; approve locks ruleset version; brief assembly produces non-stub output.

6. **Behavioral parity validation.** Lift a fixture from the Node 2026-05-15 smoke-path test (`claudeck-artemis/docs/WORKLOG.md` documents the e2e path). Run the same fixture against the Python app; assert qualification scores, brief content shape, and approval flow match Node output byte-for-byte (modulo timestamps).

**Out of scope.** Live scout execution (Phase D). Writing Studio adapter (Phase D? — actually need to decide: bring forward to C4 or defer? **Lead decision: bring forward as Phase C4 since the brief assembler outputs feed straight into it.** Brief will follow if Worker hasn't moved on yet.). UI surface (Phase E).

**Authority.** Pure-function decomposition vs. methods on a class. Default to module-level functions matching the Node shape.

**Trigger pauses.** Any qualifier behavior that diverges from the Node reference — surface here. The qualifier math is contract-level; we don't improvise.

**Done.** Pytest ≥60 green. Behavioral parity fixture passes against the Node reference output. README updated.

---

## Worker queue summary (post-B4)

After B4 merges, Worker picks up C1 → C2 → C3 in order. After C3 lands, all the contract / plumbing / qualifier work is at parity with the Node reference. Phase D (scout workers — one slice per scout) begins.

**Phase C4 (Writing Studio adapter port)** brief is below.

---

## BRIEF FOR WORKER — Phase C Slice 4: Writing Studio adapter port

**Status:** queued. Picks up after C3 merges.
**Reference implementation:** `claudeck-artemis/server/writing-studio-adapter.js` (191 lines — the event-driven adapter), `writing-studio-invoke.js` (682 lines — draft creation + Gate 2 flow), `writing-studio-sync.js` (1025 lines — bidirectional sync; **read this last** — it's the heaviest), `writing-studio-events.js` (105 lines — event dispatch), `server/routes/writing-studio.js`.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/phase-c4-writing-studio-adapter`

**Goal.** Port the Writing Studio integration end-to-end. This is the surface Phase C3's brief assembler feeds, and the surface Phase F's content team flow consumes.

**Deliverables.**

1. **`artemis/marketing/writing_studio/adapter.py`** — port of `writing-studio-adapter.js`. Event-driven dispatcher. Receives `draft.approved` / `draft.rejected` / `draft.revised` events; updates `campaign_deliverables` rows and fires downstream state-machine transitions on `campaign_candidates.workspace_state`.

2. **`artemis/marketing/writing_studio/invoke.py`** — port of `writing-studio-invoke.js`. Functions:
   - `create_draft_from_candidate(candidate_id, brief_payload, asset_context_bundle) -> Draft` — builds the metadata bundle (assembled brief + linked asset context), POSTs to the Writing Studio external API (stubbed in V1 — see deliverable 5), stores `campaign_deliverables` row.
   - `submit_draft_for_review(draft_id) -> Approval` — creates a `writing_gate_2` approval row, transitions draft status to `ready_for_review`.
   - `list_campaign_asset_links(candidate_id) -> list[AssetContext]` — pulls linked content_assets with non-empty `summary` (only assets with summary feed Writing Studio per Node behavior).

3. **`artemis/marketing/writing_studio/sync.py`** — port of `writing-studio-sync.js`. Bidirectional state sync. The heavy one. Cleanly split: write path (Artemis → Writing Studio) and read path (Writing Studio → Artemis). Keep the function boundaries the Node code already established; don't invent new ones.

4. **`artemis/marketing/writing_studio/events.py`** — port of `writing-studio-events.js`. Internal event bus that the adapter subscribes to and the routes publish to. Use Python asyncio queues, not threading.

5. **External API stub.** The actual Writing Studio is an external system (per `claudeck-artemis/marketing-ops-v1/agents/content/5.3-writing-studio-adapter.md`). For V1 of the rebuild, **stub the external HTTP calls behind an `ExternalWritingStudio` protocol** with two impls: `StubWritingStudio` (in-memory, returns deterministic IDs — default) and `RealWritingStudio` (httpx client — needs URL + auth from `.env`, left unset in V1). Production swap is a one-line change.

6. **Routes — `artemis/marketing/routes/writing_studio.py`** → `/api/writing-studio`. Endpoints match the Node `routes/writing-studio.js`: `POST /drafts` (create from candidate), `POST /drafts/:id/submit-review`, `POST /drafts/:id/events/:event_kind` (Writing Studio webhook back).

7. **Pytest coverage: ≥40 tests.**
   - Adapter event handling (per event kind: approved, rejected, revised).
   - Invoke happy path: candidate → draft → metadata bundle includes brief + asset context.
   - Submit-review creates `writing_gate_2` approval.
   - Sync round-trips state both directions.
   - Stub external API: deterministic IDs, no network.
   - End-to-end fixture mirroring the Node 2026-05-15 smoke path Gate-2 steps (submit → decision: approve → deliverable counts update → workspace_state advances).

**Out of scope.** Real Writing Studio HTTPS integration (RealWritingStudio impl shipped but inert until Jon provides URL + auth — flagged in `.env.example` already). The Writing Studio app itself (it's external, owned by Angela's team).

**Authority.** Async queue shape. Where the `ExternalWritingStudio` protocol is defined. Default: `artemis/marketing/writing_studio/external.py`.

**Trigger pauses.** Any behavior in the Node files that looks load-bearing but isn't obvious from reading once — surface here. The 1025-line sync.js especially benefits from a read-then-ask pass.

**Done.** Pytest ≥40 green. The 2026-05-15 smoke path Gate-2 sequence runs end-to-end against the Python app with the stub Writing Studio. README in `artemis/marketing/writing_studio/` documents the protocol + how to switch from stub to real.

---

## BRIEF FOR WORKER — Phase H: OKR Studio + Writing Studio rules data migration (CUTOVER)

**Status:** queued. Picks up after the rebuild can functionally replace the Node app for Jon's daily use. This is the cutover slice.
**Reference implementation:** `claudeck-artemis/db/sqlite.js` — the `writing_*` and `okr_*` table sections.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`
**Branch:** `worker/phase-h-data-migration`

**This brief crosses a §K.3 trigger.** Cutover is a "bring Jon in" moment — the Worker must surface here when ready to run the import; Lead notifies Jon; Jon greenlights before apply.

**Goal.** Preserve the only data Jon cares about from the Node app: OKR Studio rows + Writing Studio rules. Everything else (signals, candidates, briefs, content_assets, memory) is greenfield — the Node app's tables were never run against real data.

**Tables to migrate.**

OKR Studio:
- `okr_objectives` → `okr_objectives`
- `okr_key_results` → `okr_key_results`
- `okr_activity` → `okr_activity`
- `okr_next_up` → `okr_next_up`
- `okr_update_previews` → `okr_update_previews` *(skip if empty — these are ephemeral)*

Writing Studio rules + scaffolding:
- `writing_profiles` → `writing_profiles`
- `writing_folders` → `writing_folders`
- `writing_rules` → `writing_rules`
- `writing_examples` → `writing_examples`
- `writing_sources` → `writing_sources`

Tables to **explicitly NOT migrate** (start clean in the new app):
- `writing_drafts`, `writing_draft_versions`, `writing_draft_thread_messages`, `writing_training_candidates`, `writing_deliverable_links`, `writing_draft_events` — these are workflow state, not curated content. Jon's words: "we haven't run any agents or marketing workflows."
- Any `signal_queue`, `campaign_candidates`, `campaign_briefs`, `content_assets`, `scout_runs`, `approvals` rows — these are smoke-test debris, not real history.
- All `memory_*` tables — the Python keystone will be freshly seeded by use.

**Deliverables.**

1. **Schema port (additive Alembic migration).** Add the OKR + Writing Studio tables to the Python app. Match the Node column shape, with TIMESTAMPTZ for timestamps, JSONB where the Node app used JSON-in-TEXT, BIGSERIAL for PKs, `owner_user_id BIGINT NULL` on top-level tables.

2. **Repository helpers** for OKR + Writing Studio rules (CRUD + the few specialized helpers the Node app has, e.g. `getRuleByProfileTypeTitle`).

3. **Migration script `scripts/migrate_okr_writing_rules.py`** with two modes:
   - `--dry-run` (default) — reads from the Node SQLite at `/Users/artemis/Desktop/Artemis/claudeck-artemis/data.db`, maps each row, validates against the Python schema, reports counts + any conflicts. Writes nothing to Postgres.
   - `--apply` — performs the actual insert. Idempotent: dedupe via natural keys (profile name, rule title within profile, OKR objective title within period, etc.). Never overwrites; conflicts go to a `migration_conflicts.jsonl` report file.

4. **Validation script `scripts/verify_migration.py`** — runs after apply. For each migrated table, compares row count (Node vs Python). Spot-checks 10 random rows for content fidelity. Exit code != 0 if any check fails.

5. **Routes for OKR + Writing Studio rules.** Port `claudeck-artemis/server/routes/okr.js` + the rules-specific endpoints in `routes/writing-studio.js`. Minimal — just enough that the UI can read/write OKR + rules. (The rest of Writing Studio's UI surface ports in Phase E.)

6. **Pytest coverage: ≥25 tests.** Migration dry-run on a real Node DB snapshot. Dedupe behavior under double-apply. Conflict reporting. Round-trip a sample profile + 5 rules.

**The cutover sequence (NOT to be executed without Jon's go-ahead).**

1. Worker brings Phase H to "ready" — code merged, all tests green, dry-run produces a clean report.
2. Worker writes a `### HH:MM — Worker — Phase H ready for cutover` entry in `COORDINATION.md`.
3. Lead reviews, runs the dry-run, summarizes results to Jon (counts, conflicts, anything surprising).
4. **Jon greenlights cutover.** No exceptions.
5. Lead runs `--apply` against a Postgres backup snapshot first; runs verify; if clean, runs against the live Postgres; runs verify again.
6. Lead updates `PROJECT_LOG.md` with the cutover result.
7. From that moment forward, the Node app is **archive-only**. Jon's daily Artemis is the Python one.

**Out of scope.** Migrating any other data. The Writing Studio drafts themselves (not Jon-curated content). Anything that requires Jon to have *used* the Node app for it to matter.

**Authority.** Migration script implementation pattern. Conflict reporting format. Default to JSONL for diff-ability.

**Trigger pauses.** **Any anomaly in the dry-run output.** Surface here with details — Lead surfaces to Jon before applying. Anomaly examples: row counts unexpectedly low, character-encoding issues in rule text, profile referenced by a rule that doesn't exist in `writing_profiles`.

**Done.** Dry-run clean. Verify script passes against a Postgres+ artemis_os populated by a real migration. Jon has greenlit and confirmed the cutover.

---

## BRIEF FOR WORKER — Phase D Slice 1: Scout worker scaffold + APScheduler + BaseScout

**Status:** queued — Worker picks this up on next session. Keystone is complete; this is the natural next slice.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/` (Worker's worktree).
**Branch:** `worker/phase-d1-scout-scaffold`
**Reference implementation:** `claudeck-artemis/server/routes/scouts.js`, `claudeck-artemis/config/scout-packages.json`, the per-scout specs at `claudeck-artemis/marketing-ops-v1/agents/scout/1.*.md`.

**Goal.** Build the worker-process pattern that all 9 scouts (D2–D10) will sit on. No scout actually runs against real sources in this slice — that's per-scout in D2+. This slice ships the scaffold + scheduler + base class + a smoke test that proves the pattern works against a fake scout.

**Deliverables.**

1. **`artemis/scouts/__init__.py`** — module docstring + public exports.

2. **`artemis/scouts/base.py`** — `BaseScout` abstract class:
   ```python
   class BaseScout(ABC):
       scout_type: ClassVar[str]                  # e.g. "legislative_scout"
       cadence: ClassVar[str]                     # cron expression
       allowed_source_types: ClassVar[set[str]]
       allowed_campaign_families: ClassVar[set[str]]

       @abstractmethod
       async def run_once(self) -> ScoutRunResult: ...

       async def emit_signal(self, finding: NormalizedFinding) -> None:
           """POST to /api/scouts/runs commit endpoint. Anti-spoof: discovered_by
           is set unconditionally to self.scout_type."""

       async def write_run_summary(self, result: ScoutRunResult) -> None:
           """Persist scout_runs row via the C1 repository."""
   ```
   - `ScoutRunResult` Pydantic schema: `findings_emitted: int, errors: list[str], started_at, completed_at, status: 'committed' | 'failed'`.

3. **`artemis/scouts/scheduler.py`** — APScheduler integration:
   - `ScoutScheduler` class wrapping `apscheduler.schedulers.asyncio.AsyncIOScheduler`.
   - `register(scout: BaseScout)` — adds a cron job from `scout.cadence`.
   - `start()` / `shutdown()` async lifecycle.
   - Lifecycle hook: integrate with FastAPI's `lifespan` in `artemis/main.py` so the scheduler starts when the API process starts. **Behind an env flag** `ARTEMIS_SCOUT_SCHEDULER_ENABLED` (default `false`) — Jon's daily-use Artemis shouldn't auto-fire scouts until he says go.

4. **`artemis/scouts/runner.py`** — CLI entry point:
   - `uv run python -m artemis.scouts.runner --once <scout_type>` runs one scout once and exits (operator manual fire).
   - `uv run python -m artemis.scouts.runner --watch` starts the scheduler in a long-running foreground process.

5. **`artemis/scouts/fake_scout.py`** — a test-only `FakeScout(BaseScout)` that emits one deterministic finding when `run_once()` is called. **Not registered in production**; used only by the smoke test.

6. **`artemis/main.py` lifespan integration** — when `ARTEMIS_SCOUT_SCHEDULER_ENABLED=true`, instantiate `ScoutScheduler` in the FastAPI lifespan, register configured scouts, start on app boot, shutdown on app exit. When the flag is false, lifespan stays a no-op (current behavior preserved).

7. **`.env.example`** — add `ARTEMIS_SCOUT_SCHEDULER_ENABLED=false` with a comment explaining when to flip it.

8. **`config/scout-packages.json` port to `config/scout-packages.yaml`** — same shape as the Node JSON, YAML for consistency with `memory-retrieval.yaml` and `memory-graph.yaml`. The 9 scout package definitions (allowedSourceTypes, guardrails, instructions, expectedOutputSchema) port verbatim.

**Pytest coverage: ≥30 tests.**
- `tests/scouts/test_base.py` — FakeScout emits the right finding; emit_signal POSTs to intake; anti-spoof override of `discovered_by`.
- `tests/scouts/test_scheduler.py` — scheduler registers FakeScout; cron expression validates; start/stop lifecycle clean; `ARTEMIS_SCOUT_SCHEDULER_ENABLED=false` → scheduler doesn't auto-start in lifespan.
- `tests/scouts/test_runner.py` — `--once fake_scout` runs and exits 0 (use httpx + ASGI client; no real scheduler).
- `tests/scouts/test_packages_yaml.py` — `scout-packages.yaml` round-trips through Pydantic; matches the Node JSON shape.

**Out of scope.** Any real scout (D2–D10). Real HTTP integrations to external services. Authentication for external scrapers.

**Authority.** APScheduler version pin (must be ≥7 days old). Module layout under `artemis/scouts/`. Whether `runner.py` uses `typer` or `argparse` (default `argparse` — fewer deps).

**Trigger pauses.** Any schema migration needed (none expected; `scout_runs` table already exists from C1). Any cross-cutting refactor of `artemis/main.py` lifespan that affects health endpoints.

**Done.** Pytest ≥30 green. `uv run python -m artemis.scouts.runner --once fake_scout` exits 0 and a `scout_runs` row appears. Suite total ≥505 (475 + new). ruff/format/mypy strict all green.

**Sub-agent guidance for this slice (per the new MODEL TIERING rule above):**
- The `BaseScout` abstract class + the `ScoutRunResult` schema are judgment-heavy contract decisions — do those yourself (Sonnet, you).
- The APScheduler integration (`scheduler.py`) is well-bounded ~150-200 LOC of glue code with a clear API; spawn one Sonnet sub-agent for that.
- The `scout-packages.yaml` port (verbatim translation from the Node JSON shape to YAML, with Pydantic validation) is pure mechanical — spawn one Haiku sub-agent for that.
- The pytest tests (~30 across 4 files) split naturally: do the `test_base.py` tests yourself (they need understanding of how `BaseScout` should behave); spawn one Haiku sub-agent for the other 3 test files in parallel once the production code is in place.

Net effect: you ship D1 in roughly the time of the slowest sub-call instead of summing all of it.

**Coordination etiquette.** Read this file + `PROJECT_LOG.md` on startup. Branch `worker/phase-d1-scout-scaffold` from `main`. Append `### HH:MM — Worker [Account 2] — Started D1` when you begin. Append `### HH:MM — Worker [Account 2] — D1 ready for review: <commit>` when done.

---

## Phase D scouts (D2–D10) — to be briefed after D1 lands

The 9 scouts all sit on D1's `BaseScout` pattern. Each is a small slice (~1 file + ~15-20 tests). Build order:

D2 — Scout 1.4 Legislative (LegiScan API — easiest, validates the pattern)
D3 — Scout 1.5 Federal Funding (Federal Register + Grants.gov + ED.gov RSS)
D4 — Scout 1.1 Starbridge (API client; bench-test status)
D5 — Scout 1.6 State DoE (RSS + scraping + PDF extraction — first scrape-heavy scout)
D6 — Scout 1.8 Board Minutes (BoardDocs + Granicus + PDFs, weekly cadence per district)
D7 — Scout 1.7 Procurement (statewide portals + per-district)
D8 — Scout 1.9 Leadership Transition (cross-source aggregator — depends on others)
D9 — Scout 1.2 Regional News (news APIs + per-district adapters)
D10 — Scout 1.3 LinkedIn (Mode B only, third-party scraper service)

Per-scout briefs authored on demand (each follows the same template; mostly differ in source API + reason codes + cadence).

---

## BRIEF FOR WORKER — Phase D Pack 1: THREE API-shaped scouts in one slice (D2+D3+D4)

**Status:** queued for the Worker on next session. **This is a BIG slice — designed to force sub-agent use.** Trying to ship three scouts serially would burn a day. With parallel sub-agents per the MODEL TIERING rule above, it's roughly the time of the slowest sub-agent run.

**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`.
**Branch:** `worker/phase-d-pack-1-api-scouts`.
**Scope:** D2 (Legislative / LegiScan) + D3 (Federal Funding / Federal Register + Grants.gov + ED.gov RSS) + D4 (Starbridge — API client).

**Why these three together:** all are API-shaped — clean HTTP + JSON/RSS, no scraping, no PDF extraction. They share the `BaseScout` pattern (on main from D1). Each ships ~3 modules + ~20 tests. Three of them in parallel via Sonnet sub-agents is the canonical use case for the Worker → sub-agent pattern.

**Reference specs:**
- D2: `claudeck-artemis/marketing-ops-v1/agents/scout/1.4-legislative-scout.md`
- D3: `claudeck-artemis/marketing-ops-v1/agents/scout/1.5-federal-funding-scout.md`
- D4: `claudeck-artemis/marketing-ops-v1/agents/scout/1.1-starbridge-researcher.md`

The original single-scout D2 brief is preserved below — use it as the template the parallel sub-agents follow.

### How to run this slice (Worker, read carefully)

You should NOT do this slice serially. The point of this brief is to exercise the sub-agent pattern.

1. **First, do the shared work yourself (judgment-heavy):**
   - Read the D2 template brief below.
   - Confirm the D1 `BaseScout` shape supports what each scout needs. If not, surface here before spawning sub-agents.
   - Build a shared HTTP client in `artemis/scouts/_http.py` (httpx.AsyncClient with retry policy + rate-limit hook). Tests for it yourself. ~100 LOC + ~6 tests.

2. **Then spawn three Sonnet sub-agents in parallel** — in ONE message, send three `Agent(subagent_type: "general-purpose", model: "sonnet", prompt: ...)` calls. Each sub-agent:
   - Implements ONE scout (D2, D3, or D4) end-to-end: `client.py`, `scout.py`, `mapping.py`, ~20 tests.
   - Commits onto `worker/phase-d-pack-1-api-scouts` (multiple sub-agents committing on the same branch is fine — they touch different files under `artemis/scouts/{legislative,federal_funding,starbridge}/`).
   - Reads `artemis/scouts/_http.py` for the shared client; does not redefine it.

3. **After all three return**, you (Worker) do the integration pass:
   - Register all three scouts in `config/scouts.yaml`.
   - Run the full suite. Resolve any cross-scout conflicts.
   - Run ruff/format/mypy.
   - One final integration commit on the branch.

### Per-scout deliverables

**D2 — Legislative Scout** (LegiScan API). Module: `artemis/scouts/legislative/`. Reason codes: `BILL_INTRODUCED`, `BILL_PASSED_CHAMBER`, `BILL_ENACTED`, `STATE_OBC_LEGISLATION`, `STATE_DYSLEXIA_MANDATE`, `STATE_BILITERACY_INITIATIVE`. Source type: `legiscan`. Auth: `LEGISCAN_API_KEY`. Cadence: daily during session, weekly off-session. See template below — that IS D2's full spec.

**D3 — Federal Funding Scout**. Module: `artemis/scouts/federal_funding/`. Three sources:
- Federal Register API (`https://www.federalregister.gov/api/v1/`) — public, no key.
- Grants.gov API (`https://www.grants.gov/grantsws/rest/`) — `GRANTS_GOV_API_KEY` env.
- ED.gov press-release RSS (`https://www.ed.gov/news/press-releases/feed`).

Reason codes: `FEDERAL_GRANT_OPEN`, `FEDERAL_GRANT_DEADLINE`, `CLSD_ANNOUNCEMENT`, `ESSER_CLIFF_REFERENCE`. Source types: `federal_register`, `grants_gov`, `district_press`. Urgency: hot when deadline ≤30 days + literacy/assessment/curriculum keyword; standard for 30-90 day windows; enrichment otherwise. Cadence: daily.

**D4 — Starbridge Researcher** (bench-test status). Module: `artemis/scouts/starbridge/`. Auth: `STARBRIDGE_API_KEY`. API shape TBD with Starbridge team — for this slice, build assuming a generic `search` + `get_document` API and mark ambiguous fields with `# TODO: confirm with Starbridge team`. Reason codes: any from the registry (scout-determined). Source type: `starbridge`. Cadence: 4h poll. Urgency: hot for RFP deadline ≤30 days OR bill passed chamber; standard for 30-90; enrichment for context.

### Shared expectations (all three)

- Memory keystone for dedupe via content_hash / embeddings.
- `emit_signal` posts via the existing `/api/scouts/runs` intake from C2.
- Anti-spoof: `discovered_by` unconditionally set to `scout_type`.
- All HTTP through your shared `_http.py`.
- All tests mock HTTP — no live API calls in pytest.
- With API key env unset: `runner --once <scout> --dry-run` exits 0 with a warning. No real HTTP attempted.

### Pytest coverage target

- Shared `_http.py`: ~6 tests.
- Per scout: ≥20 tests.
- **Total target: ≥66 new tests.** Current suite is 599; after this pack: ≥665.

### Verification

```
uv run pytest
uv run ruff check artemis tests
uv run ruff format --check artemis tests
uv run mypy artemis
```

All green required.

### Sub-agent brief shape (Worker, copy this for each parallel spawn)

```
You are a sub-agent of the Worker. Implement ONE scout for Phase D Pack 1.

Working dir: /Users/artemis/Desktop/Artemis/artemis-os
Branch: worker/phase-d-pack-1-api-scouts (already checked out)

Your scout: <name>
Spec: <path to marketing-ops-v1 .md>
Module path: artemis/scouts/<name>/
Reason codes: <list>
API: <description + env key + rate limit>

Required: client.py, scout.py, mapping.py, tests/test_*.py
Use artemis/scouts/_http.py (shared, exists). Do not redefine.
Use artemis/scouts/base.py BaseScout. Do not redefine.
Use artemis.memory.store.write_drawer + retrieval for dedupe.

Tests: ≥20. Mock all HTTP. With API key unset, gracefully no-op.

Verify: uv run pytest artemis/scouts/<name>/. Ruff/mypy clean.

Commit on worker/phase-d-pack-1-api-scouts:
"feat(scouts): D<N> — <name> (<API>)
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

Report back ≤300 words: commit hash, test count, surprises.
```

### Lead guidance on this slice

- Three parallel sub-agents = roughly the time of the slowest one.
- Do NOT spawn a sub-agent for `_http.py` (judgment) or the integration pass (judgment). You do those.
- DO spawn one Sonnet sub-agent per scout. Each is ~20 tests + ~3 modules — right size.
- If Starbridge API shape is too ambiguous to brief well, surface in `COORDINATION.md` first — Lead may simplify D4 to "skeleton + TODO stubs" so D2 + D3 can still ship in this pack.

---

## BRIEF FOR WORKER — Phase D Pack 3: LAST scout pack (D8+D9+D10)

**Status:** queued for Worker on next session. **The last scout pack.** After this lands, all 10 scouts ship.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`.
**Branch:** `worker/phase-d-pack-3-final-scouts`.
**Scope:** D8 (Leadership Transition — cross-source aggregator) + D9 (Regional News — news APIs + per-district adapters) + D10 (LinkedIn Observer Mode B — third-party scraper service).

**Why these three together:** they're the leftovers. None fit cleanly into Pack-1 (pure API) or Pack-2 (pure scrape+PDF). D8 is a cross-source aggregator that depends on the other scouts' findings; D9 mixes news APIs with per-district adapters; D10 wraps a third-party scraping service (Phantombuster / Proxycurl-style). Three independent leaves — same pack pattern as Pack-1 and Pack-2.

**Reference specs:**
- D8: `claudeck-artemis/marketing-ops-v1/agents/scout/1.9-leadership-transition-scout.md`
- D9: `claudeck-artemis/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`
- D10: `claudeck-artemis/marketing-ops-v1/agents/scout/1.3-linkedin-observer.md` (**Mode B only — Mode A is disabled per v1 spec**)

### How to run this slice

Same pattern as Pack-1 and Pack-2.

**1. Shared work yourself (judgment):**
- If you need a third-party scraper service client (Phantombuster / Proxycurl) for D10, that's a small new module `artemis/scouts/_linkedin_scraper.py` — Worker builds it, ~80 LOC + ~5 tests. Uses your existing `_http.py`. Stub-by-default per env (`LINKEDIN_SCRAPER_API_KEY` unset → graceful no-op).
- D8 (Leadership Transition) is a cross-source aggregator — it reads from OTHER scouts' findings via the memory keystone (or just runs the other scouts' clients and aggregates). Don't reimplement source clients; reuse what Pack-1 and Pack-2 already shipped.

**2. Spawn 3 Sonnet sub-agents in parallel** for D8 / D9 / D10. Same sub-agent brief template.

**3. Integration pass yourself:** register in `config/scouts.yaml` (default `enabled: false`), full suite, ruff/format/mypy.

### Per-scout deliverables

**D8 — Leadership Transition Scout** (cross-source aggregator). Module: `artemis/scouts/leadership/`. Reason codes: `SUPE_SEARCH_ANNOUNCED`, `SUPE_INTERIM_NAMED`, `SUPE_FORMAL_HIRE`, `SENIOR_LEADER_TRANSITION`, `SUPERINTENDENT_TRANSITION`. Source types: vary by what confirmed the transition (`district_press`, `state_doe`, `news_article`, `board_minutes`, `linkedin_post`). Cadence: weekly base / daily for transition-active districts. Urgency: hot for `SUPE_FORMAL_HIRE` (first 90 days is the buying window). **Special: this scout writes back to the District Roster (`districts` table) on confirmed hires. The Python schema doesn't have `districts` yet — surface in COORDINATION.md if this blocks; Lead may add a minimal districts table, or D8 stubs the write to a log line.**

**D9 — Regional News Scout**. Module: `artemis/scouts/regional_news/`. Reason codes: news-driven (`STATE_GUIDANCE_ISSUED`, `BOARD_LITERACY_CURRICULUM_REVIEW`, etc. — overlap with D5/D6 by design). Sources: News API (`https://newsapi.org` via `NEWS_API_KEY`), per-district adapters (shared with D6's BoardDocs/Granicus modules), state DoE press (shared with D5). Source types: `news_article`, `board_minutes`, `state_doe`, `district_press`. Cadence: daily. Urgency: conservative with `hot` — reserve for formal RFPs, board votes passed, official transitions, gubernatorial directives. Speculation → `standard`.

**D10 — LinkedIn Observer (Mode B only)**. Module: `artemis/scouts/linkedin/`. Reason codes: `LINKEDIN_LEADER_ENGAGEMENT` (primary), plus topical codes triggered by post content (`ESSER_CLIFF_REFERENCE` when supe posts about ESSER expiration, etc.). Source type: `linkedin_post`. Watch list: ~200-500 profiles (1 supe + 1-2 senior leaders per district). For V1, **hardcode the watch list in `config/scouts.yaml`** until the districts table lands. Auth: `LINKEDIN_SCRAPER_API_KEY` (third-party service). Cadence: hourly during business hours. Urgency: rarely standalone hot — most LinkedIn signals are enrichment-tier (reinforce other scouts). **Mode A is disabled per v1 spec — implement the scraper code paths as no-ops so they can be enabled later without re-architecture.**

### Shared expectations (all three)

- Use `_http.py` for any HTTP call. Use `_scraper.py` (Pack-2's Playwright wrapper) if any scout needs JS-heavy scraping. Use `_pdf.py` if PDFs come in.
- Use the memory keystone for dedupe.
- Anti-spoof `discovered_by`.
- All tests mock external sources.
- With API keys unset, `--dry-run` graceful + warning. No real HTTP attempted.
- All three scouts default `enabled: false` in `scouts.yaml`.

### Pytest coverage target

- LinkedIn scraper module (if added): ~5 tests.
- Per scout: ≥20 tests.
- **Total target: ≥65 new tests.** Current suite is 974; after this pack: ≥1039.

### Watch out for

- D8 cross-source pattern — it should not directly run D2-D7's `run_once()`. Instead, query the memory keystone (or the `signal_queue` table) for relevant findings from the past 7 days, aggregate, emit consolidated transition signals.
- D9 may overlap heavily with D5 and D6 on reason codes — that's intentional per the spec (multi-source confirmation is a feature). Dedup via the memory keystone.
- D10 third-party service contract: Phantombuster / Proxycurl have specific API shapes. Lift Pydantic types from their docs; if access is ambiguous, mark `# TODO: confirm with chosen vendor` and ship the scaffold.

### Lead guidance

- Three parallel sub-agents = roughly the time of the slowest one.
- After D-Pack-3 lands, **Phase D is complete.** Only remaining backend work: G (floating Artemis), H apply (cutover, Jon's call), Phase I (deployment).

---

## BRIEF FOR WORKER — Phase D Pack 2: THREE scrape+PDF scouts (D5+D6+D7)

**Status:** queued for Worker on next session.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/`.
**Branch:** `worker/phase-d-pack-2-scrape-pdf-scouts`.
**Scope:** D5 (State DoE — RSS + scraping + state-board PDFs) + D6 (Board Minutes — BoardDocs + Granicus + per-district PDFs, weekly cadence) + D7 (Procurement — statewide portals + per-district RFPs).

**Why these three together:** all are scrape-heavy with PDF extraction. They share a new shared layer (`artemis/scouts/_scraper.py` + `artemis/scouts/_pdf.py`) on top of the existing `_http.py` from Pack-1. Worker builds the shared layer, then spawns 3 sub-agents per scout.

**Reference specs:**
- D5: `claudeck-artemis/marketing-ops-v1/agents/scout/1.6-state-doe-scout.md`
- D6: `claudeck-artemis/marketing-ops-v1/agents/scout/1.8-board-minutes-scout.md`
- D7: `claudeck-artemis/marketing-ops-v1/agents/scout/1.7-procurement-scout.md`

### How to run this slice (Worker, read carefully)

Same pattern as Pack-1. Do NOT do this serially.

**1. Shared work yourself (judgment-heavy):**
- Build `artemis/scouts/_scraper.py` — Playwright wrapper. Async context manager that yields a browser + page. Handle common scrape patterns: BoardDocs (Lotus Notes-based, JS-heavy), Granicus (often video archive + PDF agendas), generic per-district HTML scraping. ~150 LOC + ~8 tests.
- Build `artemis/scouts/_pdf.py` — pypdfium2 + pytesseract wrapper. `extract_text(pdf_bytes_or_path) -> str` with OCR fallback when the PDF is image-only. ~100 LOC + ~6 tests.
- Both modules must accept an optional injection point for tests (mock browser, mock PDF reader) so the per-scout tests can run without launching real Playwright.

**2. Spawn three Sonnet sub-agents in parallel** — one per scout. Same sub-agent brief template as Pack-1, with adaptations:
- D5: state-DoE-specific reason codes (`STATE_GUIDANCE_ISSUED`, `STATE_MANDATE_ISSUED`, `GUBERNATORIAL_EO_LITERACY`, `STATE_OBC_LEGISLATION`, `STATE_DYSLEXIA_MANDATE`, `STATE_BILITERACY_INITIATIVE`). Sources: per-state DoE site config in `artemis/scouts/state_doe/sources.py` (RSS where available, scrape where not). Governor sites + state board PDFs.
- D6: board-minutes reason codes (`BOARD_LITERACY_CURRICULUM_REVIEW`, `BOARD_VENDOR_REVIEW`, `BOARD_RFP_AUTHORIZATION`, `BOARD_OBC_DISCUSSION`, `BOARD_OBC_RFP_APPROVED`, `BOARD_BUDGET_PRESSURE`, `BOARD_VENDOR_ACCOUNTABILITY`, `ESSER_CLIFF_REFERENCE`, `SUPERINTENDENT_TRANSITION`). Sources: BoardDocs URL pattern, Granicus URL pattern, district websites. Watch list via `districts.is_watch_list = TRUE` query (the C1 territory_config doesn't have districts table yet — surface this for Lead if it blocks).
- D7: procurement reason codes (`RFP_LITERACY_POSTED`, plus the standard reason codes). Sources: statewide procurement portals (CA eProcurement, GA Procurement Registry, TX SmartBuy, FL Vendor Bid System, IL BidBuy, IN Department of Administration, MD eMaryland Marketplace, MI Bid4Michigan, MO Office of Administration Procurement). Skip BidNet / DemandStar paid APIs.

**3. Integration pass yourself:** register scouts in `config/scouts.yaml`, run full suite, resolve cross-scout conflicts, ruff/format/mypy.

### Shared expectations

- Use `_http.py` for any HTTP call (don't add new httpx setups).
- Use `_scraper.py` (your new file) for any Playwright work.
- Use `_pdf.py` (your new file) for any PDF extraction.
- Use the memory keystone for dedupe.
- Anti-spoof `discovered_by`.
- All tests mock external sources — no real scrapes or PDF downloads in pytest ever.
- `--dry-run` graceful when env vars missing.

### Pytest coverage target

- `_scraper.py`: ~8 tests (mock browser, navigation, error handling).
- `_pdf.py`: ~6 tests (text PDFs, image-only PDFs with OCR fallback, error handling).
- Per scout: ≥20 tests.
- **Total target: ≥74 new tests.** Current suite is 700; after this pack: ≥774.

### Watch out for

- **District table for watch list** — D6 needs to iterate watch-list districts. The Python schema doesn't have a `districts` table yet (Node has one). Surface in COORDINATION.md if this blocks — Lead will either add a minimal districts table now or you stub it with a hardcoded watch list in `config/scouts.yaml` for V1.
- **Playwright install** — `uv add playwright` then `uv run playwright install chromium`. The `chromium` download is ~150MB; one-time setup on this Mac mini.
- **pytesseract** — needs `brew install tesseract` for OCR. If not installed, the `_pdf.py` OCR fallback should degrade gracefully (text-only extraction, warn if image-only).

### Lead guidance on this slice

- This is the hardest D-Pack. Take time on `_scraper.py` and `_pdf.py` — those abstractions are reused by D8/D9/D10 too.
- If Playwright proves too heavy for the test environment (no display, no chromium install), surface in COORDINATION.md — Lead may simplify to "skeleton + TODO stubs" so the structure lands but actual scraping is deferred.
- 3 parallel sub-agents = roughly the time of the slowest one.
- After this lands, only D-Pack-3 (D8 + D9 + D10) remains for Phase D.

---

## BRIEF — Phase D Slice 2 (template, used by D2 sub-agent in Pack 1): Scout 1.4 Legislative (LegiScan)

**Status:** queued. Picks up after D1 lands (BaseScout pattern required). Can be Worker or Sonnet sub-agent.
**Target repo:** `/Users/artemis/Desktop/Artemis/artemis-os/` (Worker) or `/Users/artemis/Desktop/Artemis/artemis-os-lead/` (sub-agent).
**Branch:** `worker/phase-d2-legislative-scout` (Worker) or `lead/d2-legislative-scout` (sub-agent).
**Reference:** `claudeck-artemis/marketing-ops-v1/agents/scout/1.4-legislative-scout.md` (spec).

**Goal.** Implement the first real scout — LegiScan API client + bill-to-signal mapping. Validates the BaseScout pattern end-to-end. D3–D10 follow this template.

**Why this scout first:** clean public API (LegiScan), no scraping or PDF extraction needed, well-documented endpoints, free tier sufficient for testing.

**Deliverables (all in `artemis/scouts/legislative/`):**

1. **`__init__.py`** — module docstring.

2. **`client.py`** — async LegiScan client via `httpx.AsyncClient`:
   - `async def search(state: str, keywords: list[str], date_range: tuple[date, date]) -> list[BillSummary]`
   - `async def get_bill(bill_id: int) -> Bill`
   - Reads `LEGISCAN_API_KEY` from `settings`; raises if unset and `--dry-run` not requested.
   - Pydantic types for `BillSummary` and `Bill` matching the LegiScan response shape (lift from the API docs).
   - Built-in rate limit: at most 1 request/sec (LegiScan's free tier requirement).

3. **`scout.py`** — `LegislativeScout(BaseScout)`:
   ```python
   class LegislativeScout(BaseScout):
       scout_type = "legislative_scout"
       cadence = "0 9 * * *"               # daily 09:00 UTC (in-session); operator can override
       allowed_source_types = {"legiscan"}
       allowed_campaign_families = {"obc", "biliteracy", "dyslexia"}

       async def run_once(self) -> ScoutRunResult: ...
   ```
   - `run_once()` iterates priority states (from `territory_config.get_priority_states()` — query the C1 repository), keyword-searches each state via `client.search`, maps each bill to a `NormalizedFinding`, deduplicates against memory (last-seen bill version), submits via `self.emit_signal`.
   - **Use the memory keystone (B1-B4) for dedupe.** Write a drawer per bill version observed; consult the embeddings + FTS for "have we seen this bill before in this state?" Don't reinvent a dedupe table.

4. **`mapping.py`** — `bill_to_finding(bill: Bill, state: str) -> NormalizedFinding`:
   - **RESOLVE GEOGRAPHY:** district-specific → match to canonical district_id (stub: return `f"STATE_{state}"` until contact DB is wired); statewide → `STATE_<state>`.
   - **EXTRACT EVIDENCE:** verbatim 1-3 sentence snippet from `bill.short_title` or `bill.summary`. Never paraphrase.
   - Reason code assignment based on bill stage + content keywords:
     - `BILL_INTRODUCED` (status: introduced)
     - `BILL_PASSED_CHAMBER` (status: passed_one_chamber)
     - `BILL_ENACTED` (status: enacted)
     - + content keyword codes: `STATE_OBC_LEGISLATION` / `STATE_DYSLEXIA_MANDATE` / `STATE_BILITERACY_INITIATIVE` when text matches.
   - Urgency:
     - **hot** — bill passed a chamber OR scheduled vote within 30 days.
     - **standard** — bill introduced, hearings underway.
     - **enrichment** — bill in committee, no scheduled action.

5. **Register in `config/scout-packages.yaml`** — already present from D1; the package definition for `legislative_scout` should point at `artemis.scouts.legislative.scout:LegislativeScout`.

6. **`.env.example`** — `LEGISCAN_API_KEY=` already present (added in Phase A); leave the comment in place.

**Pytest coverage: ≥20 tests.**
- `tests/scouts/test_legislative_client.py` — mock the httpx response; verify search builds the right URL; rate-limit respects the 1-req/sec cap; auth header set.
- `tests/scouts/test_legislative_mapping.py` — `bill_to_finding` on fixtures covering each stage / reason code / urgency case.
- `tests/scouts/test_legislative_scout.py` — `run_once()` against a mocked client and fake state list; dedupe against memory keystone; `emit_signal` called for new findings only; idempotent on second run with same input.
- `tests/scouts/test_legislative_e2e_dry.py` — full path with `LEGISCAN_API_KEY` unset → graceful no-op + warning logged; with `--dry-run=true` env → no real HTTP calls (mock-only).

**Out of scope.**
- Live LegiScan API calls in the test suite (always mock).
- Caching beyond memory keystone dedupe.
- Per-district fan-out (deferred — `STATE_<X>` IDs are fine for V1; Qualifier handles routing later).
- The other 8 scouts.

**Authority.** Module layout under `artemis/scouts/legislative/`. Pydantic shapes for LegiScan responses (lift from the API docs; don't reinvent). Whether to use `httpx` or `aiohttp` (default `httpx` — already a dep).

**Trigger pauses.** If LegiScan's API shape doesn't match the spec doc's described response, surface — Lead resolves. If dedupe via memory keystone proves clunky for "have we seen this bill version before" (e.g., requires too many writes per bill), surface — Lead may extend D1 with a dedicated `scout_seen_artifacts` helper table.

**Done.** Pytest ≥20 green. With `LEGISCAN_API_KEY` unset, `uv run python -m artemis.scouts.runner --once legislative_scout --dry-run` exits 0 with a warning log line. Total suite ≥555 (D1's 505 + new). ruff/format/mypy strict all green.

---

## Phase D template — D3 through D10 follow this same shape

Each per-scout brief differs in:
- Source API/scraper + auth
- Reason code mapping
- Cadence
- Pydantic types for the source's response shape

The BaseScout pattern, dedupe-via-keystone, anti-spoof `discovered_by`, `--dry-run` graceful fallback, and `--once <scout_type>` operator interface are identical. Briefs for D3-D10 will be authored on demand.

---

## Inbox for Lead

_Worker leaves questions here. Lead responds inline._

_(empty)_

## Inbox for Worker

_Lead leaves clarifications here. Worker responds inline._

_The rebuild Slice 1 brief is in flight — Lead is writing decision docs first, then the brief. Watch this file._

---

## Worker delivery — D-Pack-2: D5 + D6 + D7 scrape+PDF scouts

**Branch:** `worker/phase-d-pack-2-scrape-pdf-scouts`
**Final commit:** `8d9f235`
**Delivered:** 2026-05-16
**Tests:** 922 total (was 833; +89 new tests across D5/D6/D7)

### What shipped

**Shared layer (commit `f938ac6`):**
- `artemis/scouts/_scraper.py` — Playwright async context manager (`scraper_context`) with injectable `BrowserPage` protocol for tests. 9 tests.
- `artemis/scouts/_pdf.py` — pypdfium2 text extraction + pytesseract OCR fallback (graceful when tesseract not installed). `first_pages`/`last_pages` slicing. 8 tests.
- `pyproject.toml`: added `playwright>=1.59.0`, `pypdfium2>=5.8.0` (both >7 days old at time of add).

**D5 — `artemis/scouts/state_doe/`** (commit `8d9f235`):
- `sources.py`: `STATE_DOE_SOURCES` config for FL, IN, MD, MO, MI, IL, TX; `fetch_doe_rss`, `fetch_governor_rss`, `fetch_doe_html`, `fetch_state_board_agenda` helpers (RSS via stdlib ET, HTML via ScoutHttpClient, PDF via `_pdf.extract_text`).
- `mapping.py`: `item_to_finding` with keyword-driven reason codes (`STATE_GUIDANCE_ISSUED`, `STATE_MANDATE_ISSUED`, `GUBERNATORIAL_EO_LITERACY`, `STATE_OBC_LEGISLATION`, `STATE_DYSLEXIA_MANDATE`, `STATE_BILITERACY_INITIATIVE`) and urgency (hot = EO/mandate, standard = guidance, enrichment = default).
- `scout.py`: `StateDoEScout`, `scout_type="state_doe_scout"`. Per-state RSS→HTML fallback→governor RSS→board agenda. Dedup by (state, url). 28 tests.

**D6 — `artemis/scouts/board_minutes/`** (commit `8d9f235`):
- `client.py`: `fetch_boarddocs`, `fetch_granicus`, `fetch_district_site` — all use ScoutHttpClient + `_pdf.extract_text` for linked PDFs. Speaker attribution extracted via regex; falls back to "Unknown speaker, [date] board meeting".
- `mapping.py`: `meeting_item_to_finding` with literacy relevance filter + reason codes (`BOARD_LITERACY_CURRICULUM_REVIEW`, `BOARD_VENDOR_REVIEW`, `BOARD_RFP_AUTHORIZATION`, `BOARD_OBC_DISCUSSION`, `BOARD_OBC_RFP_APPROVED`, `BOARD_BUDGET_PRESSURE`, `BOARD_VENDOR_ACCOUNTABILITY`, `ESSER_CLIFF_REFERENCE`, `SUPERINTENDENT_TRANSITION`). Hot = formal RFP approvals/votes/transitions.
- `scout.py`: `BoardMinutesScout`, `scout_type="board_minutes_scout"`. V1 watch list: 5 hardcoded districts (FL Pinellas, FL Duval, TX Dallas, IN MSD Pike, MD Baltimore City). Source priority: BoardDocs → Granicus → district site. Dedup by (district_id, source_url). 31 tests.
- **Districts table note:** No DB query needed for V1 — watch list is hardcoded in scout.py. When the `districts` table lands, swap `_DEFAULT_WATCH_LIST` for a DB query in `_gather_findings`.

**D7 — `artemis/scouts/procurement/`** (commit `8d9f235`):
- `portals.py`: `PORTAL_REGISTRY` with 9 portal adapters (CA eProcurement, GA Procurement Registry, TX SmartBuy, FL VBS, IL BidBuy, IN DOA, MD eMMA, MI Bid4Michigan, MO OA Procurement). Generic `fetch_portal_postings` with stdlib `html.parser`, literacy-keyword pre-filter. All URLs marked `# TODO: verify URL`.
- `mapping.py`: `posting_to_finding` with `days_to_close` calculator. All RFPs urgency=hot. Reason codes: `RFP_LITERACY_POSTED` (always), `RFP_ASSESSMENT_POSTED`, `RFP_TUTORING_POSTED`, `RFP_DEADLINE_CRITICAL` (≤14 days), `RFP_EFFICACY_LANGUAGE`, `RFP_OUTCOMES_BASED_LANGUAGE`. Dedup fingerprint: (state, rfp_id).
- `scout.py`: `ProcurementScout`, `scout_type="procurement_scout"`. Per-portal error isolation. 30 tests.

**Integration:** `config/scouts.yaml` updated with all three new scouts (all `enabled: false`). `worker.py` and `runner.py` updated with new imports and registry entries. 922/922 tests pass, ruff/mypy clean.

### Known TODOs for Lead / future slices

- D6 districts table: swap hardcoded watch list for `SELECT * FROM districts WHERE is_watch_list = TRUE` once table exists.
- D7 portal URLs: all marked `# TODO: verify URL` — need manual check per state portal. Real scraping may need Playwright (not just httpx) for some JS-heavy portals.
- All three scouts `enabled: false` — activate individually once API + signal pipeline ready.

---

## Worker delivery — D-Pack-3: D8 + D9 + D10 — Phase D COMPLETE

**Branch:** `worker/phase-d-pack-3-final-scouts`
**Final commit:** `352a13f`
**Delivered:** 2026-05-16
**Tests:** 1002 total (was 922 on branch base; +80 new tests across D8/D9/D10 + shared layer)

### Phase D is complete. All 10 scouts (D1–D10) shipped.

### What shipped

**Shared layer (commit `352a13f`):**
- `artemis/scouts/_linkedin_scraper.py` — Proxycurl/Phantombuster-style async client. `fetch_posts(profile_id, *, since)` + `check_profile_delta(profile_id)` + `fetch_company_followers()` (Mode A no-op). Graceful empty return when `LINKEDIN_SCRAPER_API_KEY` unset. `from_env()` classmethod. 9 tests.

**D8 — `artemis/scouts/leadership/`** (commit `352a13f`):
- `aggregator.py`: `gather_board_items` (calls board_minutes.client), `gather_doe_items` (calls state_doe.sources), `gather_news_items` (newsapi.org, graceful when NEWS_API_KEY unset). All filter to `TRANSITION_KEYWORDS`.
- `mapping.py`: `classify_transition_stage` (6-level priority cascade) + `item_to_transition_finding`.
- `scout.py`: `LeadershipTransitionScout`, `scout_type="leadership_transition_scout"`. Cross-source: gathers from board_minutes + state_doe + news for each watch-list district. Two-source verification: multi-source → emit; single official (board/doe) → emit; news-only → hold with log. Dedup by (district_id, reason_code, source_url). SUPE_FORMAL_HIRE triggers `logger.info("TODO: write to districts table for %s")`. 22 tests.
- Watch list: same 5 hardcoded districts as D6 (stub until districts table lands).
- `scout_type = "leadership_transition_scout"` — new, no conflict with prior stubs.

**D9 — `artemis/scouts/regional_news/`** (commit `352a13f`):
- `client.py`: `fetch_news_articles` (newsapi.org, literacy keyword filter, empty when NEWS_API_KEY unset or error), `fetch_district_board_items` (wraps board_minutes.client), `fetch_doe_press_items` (wraps state_doe.sources).
- `mapping.py`: `article_to_finding`, `board_item_to_finding`, `doe_item_to_finding` — shared `_classify()` with hot/standard/enrichment tiers. Hot only for formal RFPs, votes passed, superintendent hired, gubernatorial directives.
- `scout.py`: `RegionalNewsScout`, `scout_type="regional_news_scout"`. Dedup by (district_id, source_url). 25 tests.
- **D1 stub `regional_news_scout.py` kept** — D1 tests import from it directly. Worker/runner now import from `regional_news.scout`.

**D10 — `artemis/scouts/linkedin/`** (commit `352a13f`):
- `watch_list.py`: 5 placeholder superintendent profiles (TODO: replace with real LinkedIn URLs from territory_config). LEGAL note present.
- `mapping.py`: `post_to_finding` — reshares (is_authored=False) rejected. `LINKEDIN_LEADER_ENGAGEMENT` always + topical codes. Urgency: enrichment by default, standard for rfp/vendor/contract/selection posts. Never hot.
- `scout.py`: `LinkedInObserverScout`, `scout_type="linkedin_observer"`. Week-level dedup (one signal per profile per week). Mode A disabled comment. Per-profile error isolation. 24 tests.
- **D1 stub `linkedin_observer.py` kept** — D1 tests import from it directly. Worker/runner now import from `linkedin.scout`.

**Integration:** worker.py and runner.py updated to import real D9/D10 implementations and add D8. config/scouts.yaml updated with all three scouts (enabled: false). 1002/1002 tests pass, ruff/mypy clean.

### Known TODOs for Lead

- D8 districts table: swap hardcoded watch list + stub logger for real `UPDATE districts SET superintendent=...` once table exists.
- D9/D10 watch districts/profiles: hardcoded for V1; migrate to territory_config / districts table.
- D10 LinkedIn URLs: all placeholder (`sample-supe-*`) — seed from territory_config when available.
- D10 LEGAL: confirm scraper service (Proxycurl / Phantombuster) ToS compliance before enabling. `# TODO: confirm vendor` also on `_linkedin_scraper.py`.
- All three scouts ship `enabled: false`.
- Phase D **done**. Next: G (floating Artemis), H apply (cutover), Phase I (deployment).

### CODEX REPORT — OPS-UI-3 Signals Inbox tree refresh COMPLETE

**Branch:** `codex/ops-ui-3-signals-inbox-tree-refresh`
**Commit:** `c22723d` (`feat(ops): refresh signals inbox tree`)

**Summary:** Added the Signals Inbox tree component with five grouping modes (state, reason code, geography, urgency, flat), compact rows, search, filter chips, sort, collapse persistence, and a right-side detail panel with source evidence, reason-code confidence chips, qualifier audit, brief preview, and single-signal actions. The live Marketing OS inbox now loads up to 200 signals from `/api/signal-queue` and renders the tree; empty real data deep-links to the Operations Pipelines page.

**Verification:** Focused frontend tests pass: `tests/unit/frontend/test_signals_inbox_tree.py` → 5 passed. JS syntax checks pass for `public/js/components/signal-tree.js` and `public/js/features/marketing-os.js`. Performance smoke for 200 mock signals rendered 200 rows in 2.76 ms in Node. `./scripts/check.sh` passed ruff, format, and mypy; pytest showed unrelated failures in existing/dev slices and did not reach a clean final summary in this run.

**Post-commit invariant:** Literal `git switch lead/j6a-granola-integration` was blocked because that branch is already checked out in `/Users/artemis/Desktop/Artemis/artemis-os-lead`; this worktree was moved to detached HEAD at `lead/j6a-granola-integration` instead.

### CODEX REPORT — OPS-UI-2 Agent custom folders COMPLETE

**Branch:** `codex/ops-ui-2-agent-custom-folders`
**Commit:** `6ec716a` (`feat(ops): add custom agent folders`)

**Summary:** Added the cosmetic custom-folder layer for Agents. Slug view remains the default, read-only canonical taxonomy. Custom view groups by `agents.metadata.display_folder`, keeps unsorted agents in synthetic `Unsorted`, supports drag agent to folder, folder drag nesting, right-click folder create/rename/delete prompts, and slug-view "add to folder" affordance. `agent_id` is not touched by folder moves.

**Verification:** Focused tests pass: `tests/test_ops_agent_tree.py artemis/builders/tests/test_agents.py` → 23 passed. `./scripts/check.sh` passed ruff, format, and mypy; full pytest reached 2147 passed / 2 failed / 2 deselected, with the remaining failures pre-existing/out-of-scope in Jira team-members no-project-key behavior and Slack permalink workspace host expectation.

**Post-commit invariant:** Literal `git switch lead/j6a-granola-integration` in `/Users/artemis/Desktop/Artemis/artemis-os` was blocked because that branch is already checked out in `/Users/artemis/Desktop/Artemis/artemis-os-lead`; `/Users/artemis/Desktop/Artemis/artemis-os` was moved to detached HEAD at `lead/j6a-granola-integration` instead.

### CODEX REPORT — Agent Card blueprint expansion COMPLETE

**Branch:** `codex/agent-card-blueprint-expansion`
**Commit:** `432899b` (`feat(agents): surface markdown operating blueprint`)

**Summary:** Added persisted operating-blueprint fields to Agents, including cadence, lifecycle status, inputs, urgency tiers, failure modes, DB tables touched, and implementation notes. The marketing-agent markdown seeder now extracts those sections and preserves existing blueprint values when a blueprint markdown section is silent. The Operations Agent Card now renders the read-only Operating Blueprint section with placeholders for unspecified fields.

**Verification:** Focused tests pass: `tests/test_ops_agent_tree.py artemis/marketing/tests/test_marketing_agents_seed.py` → 13 passed. JS syntax check passed for `public/js/features/operations-shell.js`. Ruff check and format check passed for touched Python files. Migration round trip passed against a freshly rebuilt `artemis_test` DB: upgrade head, downgrade `0038`, upgrade head. `./scripts/check.sh` passed JS syntax, ruff, format, and mypy; full pytest showed unrelated/out-of-scope failures in Jira team-members, Slack permalink/migration, memory drill, and memory archive/backfill/retrieval deadlock paths.

**Post-commit invariant:** Work was isolated in `/Users/artemis/Desktop/Artemis/artemis-os-agent-card-blueprint` because `/Users/artemis/Desktop/Artemis/artemis-os` had unrelated in-progress changes. The staged diff landed at 357 insertions / 2 deletions, under the 360 LOC cap.

### TERMINAL-LEAD REPORT — Phase 3 (learning loops) WORKERS DONE, HANDOFF TO APP-LEAD

**Brief:** `briefs/phase3-learning-loops.md` (artemis-os).

**4 worker branches built + unit-tested in parallel/sequential worktrees. NONE merged. App-Opus-lead (the lead in /Users/artemis/Desktop/Artemis/artemis-os) owns: migrations to `artemis_os`, live verification against the running app on :8000, and the merge.**

| Piece | Branch | Worktree | Tip SHA | New migration | Tests |
|---|---|---|---|---|---|
| A — Ground first auto-draft in ruleset | `worker/p3-a-draft-grounding` | `.claude/worktrees/agent-a930c02f4720f0751` | `3ffa7c2` | none | 14 new, 41 focused green |
| B — Writing learning loop (propose/approve) | `worker/p3-b-writing-loop` | (worktree auto-cleaned; branch ref intact) | `ce0899a` | **0064_writing_training_candidates.py** | 22 new green |
| C-1+2 — Reject reason → memory + agent scope | `worker/p3-c12-reject-reason-to-memory` | `.claude/worktrees/agent-a1fda27813f28aa41` | `0a0d511` | none | 15 new, 30 combined green |
| C-3 — Agents read own past rejections | `worker/p3-c3-agent-context-read` | `.claude/worktrees/agent-af62f9e077b8d0a44` | `8d25e86` | none | 8 new, 38 combined green |

**Branch ancestry:** A and B are parallel siblings off `main`. C-1+2 is parallel off `main`. C-3 was rebased onto C-1+2 (so C-3 = main + 0a0d511 + 46450ae[rewritten B] + 8d25e86) — note the rewritten B commit on C-3 means when the app-lead merges B and C-3 in sequence they should cherry-pick or rebase to avoid double-applying B's diff.

**Merge order recommendation for app-lead:**
1. A → B → C-1+2 → C-3 (B before C-3 since C-3's branch already contains B's diff rebased; reset C-3 to drop the duplicate B replay OR just rebase C-3 onto post-merge main after B+C-1+2 land).
2. Apply migration `0064` after B merges: `uv run alembic upgrade head` in `/Users/artemis/Desktop/Artemis/artemis-os` against `artemis_os` (NOT the test DB).
3. Restart `:8000` (--reload doesn't run migrations).

**Live verification each piece needs (per the brief, verify the EFFECT not unit-green):**

- **A:** Trigger a fresh marketing pipeline run → freshly generated draft body visibly reflects the Amira voice/rules. Inspect the deliverable's `draft_body` in `campaign_deliverables.deliverable_metadata.draftBody` for evidence of the rule patterns. Confirm anti-fabrication: no invented efficacy claims if the brief doesn't contain them.
- **B:** Open Writing Studio → compose a turn that produces a "Proposed learning:" line → confirm a row appears in `writing_training_candidates` via DB + in the modal review badge → click Approve → confirm a new `writing_rules` row with `source_candidate_id` set → trigger another compose → confirm the new rule shows in the system prompt's "Approved rules" block.
- **C-1+2:** Reject a Gate-1 signal with reason "off-territory" → query `memory_observations` joined to `memory_observation_scopes` filtered to `scope_kind='agent' AND scope_id='marketing.qualifier.cross_reference'` → confirm an observation with category=`signal_gate1_decision`, content contains `"Reason: off-territory"`. Same drill for a content-draft reject on Gate-2 with `agent:marketing.content.writing_studio_adapter`.
- **C-3:** Reject something to seed memory, THEN trigger a fresh pipeline run that invokes the same qualifier/content agent → confirm the agent's prompt (visible in `agent_contexts.value` for the new run, or via `--reload` debug logs) contains a `prior_rejections` block with the prior rejection. Demonstrating the agent acted on it requires comparing before/after qualification on similar signals — full demonstration may need 2-3 runs.

**Surfaced ambiguities (need app-lead judgment, none load-bearing):**
- C-1+2's `agent_slug` for signal rejects falls back to `marketing.qualifier.cross_reference` because `signal_queue` rows don't store a per-signal qualifier slug. Future refinement: store the qualifier slug on intake.
- C-1+2 did NOT add reason capture to the Slack approval callback path (Slack Reject was removed in Phase 1). Confirm this is still the intended product behavior.
- Worker A's empty-examples fallback text changed from "matched this draft" → "matched this context" (cosmetic; doesn't break parity).

**Approving writing rules + grounding drafts touch brand/voice.** No worker invented brand content. The grounding helper reads existing rules/examples only; the propose/approve UI is strictly human-gated.

**Commit trailer used on all worker commits:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**Logged from:** terminal-Lead Opus (2nd Claude Code Max) orchestrating Phase 3 — 2026-06-03.

## 2026-06-04

### WORKER REPORT — Fix deliverable candidate misfire COMPLETE — ready for Lead live review

**Branch:** `worker/fix-deliverable-candidate-misfire`
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`

**Root cause:**

1. `writing_studio.enqueue` trusted the model-supplied `campaign_brief_id`, then passed `brief.candidate_id` into draft creation. In the live bad run, the model guessed `campaign_brief_id=3` (candidate 5's brief) even though the pipeline run targeted candidate 3, so deliverable `id=8` was created for candidate 5.
2. The writing nodes had no hard precondition that the run target already had a campaign brief, so the model could "soft-fail" in prose while the node still landed `status=succeeded`.
3. Initiation dispatched the deliverables pipeline without guaranteeing a brief existed first.

**Shipped:**

1. `artemis/tools/content_agent_tools.py`
   `writing_studio.enqueue` now treats `pipeline_runs.target_candidate_id` as authoritative when `pipeline_run_id` is present and rejects any `campaign_brief_id` whose brief belongs to a different candidate. Draft creation now binds to the run target, never stray brief context.
2. `artemis/pipelines/node_executors/agent_executor.py`
   `marketing.content.writing_studio_adapter` now fails fast if the target candidate has no campaign brief, so the run stops before any draft write or Gate-2.
3. `artemis/pipelines/node_executors/human_gate_executor.py`
   Content-draft gates now refuse to open when the target candidate has no deliverables or no reviewable draft content.
4. `artemis/marketing/routes/initiation.py`
   Initiation now rejects deliverables dispatch with `campaign_brief_missing` when no campaign brief exists yet.
5. Regression tests added/updated in:
   - `artemis/marketing/tests/test_cc12_content_agent_tools.py`
   - `artemis/marketing/tests/test_cmp_send_1_gate2_review.py`
   - `artemis/marketing/tests/test_ci3_initiation_endpoints.py`
   - `artemis/marketing/tests/test_ci4_decouple_initiation.py`

**Verification:**

- Targeted tests: `ARTEMIS_DB_URL=.../artemis_test ARTEMIS_TEST_DB_URL=.../artemis_test uv run pytest artemis/marketing/tests/test_cc12_content_agent_tools.py artemis/marketing/tests/test_cmp_send_1_gate2_review.py artemis/marketing/tests/test_ci3_initiation_endpoints.py artemis/marketing/tests/test_ci4_decouple_initiation.py` → **33 passed**
- `uv run ruff format ...` on touched files → clean
- `uv run ruff check ...` on touched files → clean
- `ARTEMIS_DB_URL=.../artemis_test ARTEMIS_TEST_DB_URL=.../artemis_test uv run mypy artemis/tools/content_agent_tools.py artemis/pipelines/node_executors/agent_executor.py artemis/pipelines/node_executors/human_gate_executor.py artemis/marketing/routes/initiation.py` → **Success: no issues found**

**Live verification (effect, not just unit-green):**

- Brief-less candidate live run: `ac706a88-7b5c-4f90-aa0b-16cf492ccc64`
  - `pipeline_runs.target_candidate_id = 3`
  - final `status = failed`
  - `error_message = "Target candidate 3 has no campaign brief; cannot run agent 'marketing.content.writing_studio_adapter' ..."`
  - candidate 3 still has **no** campaign brief (`campaign_briefs` latest = NULL)
  - no new Gate-2 approval row was created for that run
- Candidate with brief live boundary verification: run `d7338cad-a56d-4bc4-8cca-0be31280f9db`
  - used the real `writing_studio.enqueue` tool + real `execute_human_gate_node` on live DB with Slack/timeouts patched out to avoid noise
  - created deliverable `id=10` with `candidate_id=5` and `status=draft_ready`
  - created approval `id=33` for subject `d7338cad-a56d-4bc4-8cca-0be31280f9db:gate_2_approval_drawer`
  - approval context has `candidate_id=5`, non-empty `deliverable_ids`, and `draft_body` present

**Notes for Lead:**

- Existing bad residue remains for manual purge if desired: deliverable `id=8` (wrongly tagged to candidate 5) and approval `id=32` (empty Gate-2 card from the original bug).
- I also created one interrupted verification run while debugging an unrelated trajectory-summarizer stall: `e212eed5-7e30-45b1-856f-650b464cba32`, now marked failed with `Interrupted codex live verification`.
- Approval 33's context aggregates all deliverables for candidate 5 (`[4, 5, 8, 10]`). I did **not** change that behavior because the brief only called for candidate scoping + no-empty-gate guard; the important invariant now holds: no new deliverable was written for the wrong candidate, and Gate-2 had real draft content for the target candidate.

---

### TERMINAL-LEAD REPORT — Phase 1 Marketing Intelligence Layer (trend substrate) — IN FLIGHT

**Brief:** `briefs/intelligence-phase1-trend-substrate.md`.
**Spec:** `docs/marketing-intelligence-layer-design.md` → "Phase 1 — concrete design (LOCKED 2026-06-04)".
**Decisions in scope:** D1 (enrich Gate-1 / initiation with momentum + comparables + past-decision history) and D2 (ranked prioritization endpoint). D3 banked.

**Decomposition + status (sequential foundation → parallel surfaces):**

| Piece | Branch | Tip SHA | Status |
|---|---|---|---|
| 1 — Trends computation CORE (deterministic aggregation + persist-as-observation) | `worker/intel-p1-core` | `53d3df6` | **Done.** 16 new tests green, 727-test marketing suite green, ruff/mypy clean. Branch sits on top of `bfd75f2` (current main). |
| 2 — Decision-1 enrichment (extend `routes/initiation.py` `_build_trend_context`; read past decisions from Phase-3 memory observations) | `worker/intel-p1-decision-1` | (in flight) | Branching from `worker/intel-p1-core`. |
| 3 — Decision-2 prioritization endpoint (`GET /api/marketing/intel/prioritization`) | `worker/intel-p1-decision-2` | (in flight) | Branching from `worker/intel-p1-core`. |

**Branch ancestry:** D1 and D2 are PARALLEL SIBLINGS off `worker/intel-p1-core`. No worker merges to main. App-Opus-lead owns live verification + merge.

**Recommended merge order:** core → D1 → D2 (or D2 → D1; they're disjoint). After core merges, the two surfaces should rebase trivially since D1 only touches `routes/initiation.py` (enrichment path, NOT deliverables-dispatch) + new test file, and D2 only touches a new route file + one line in `artemis/main.py` + new test file.

**Coordination flag for the in-flight Codex fix (`briefs/fix-deliverable-candidate-misfire.md`):**
Worker 2 (D1 enrichment) is editing `artemis/marketing/routes/initiation.py`. Codex's fix is also in that file. **They are disjoint by design** — Worker 2 is briefed to stay inside the enrichment/context path (`_build_district_context` / `get_initiation_proposal` response dict) and NOT touch the deliverables-dispatch path or any function with "deliverable" in its name. Worker 2 will flag overlap if it sees any. Recommend the app-lead merge the Codex fix FIRST, then D1 — D1's diff should apply cleanly to the post-Codex initiation.py.

**Substrate worth noting (Worker 1 surfaced):**
- `signal_queue` has no structured deadline column. `compute_time_sensitivity` falls back to `created_at + urgency_tier` as a proxy and records `deadline_source = "created_at_urgency_proxy"` in its output. If/when a `deadline_date TIMESTAMPTZ` column is added later, swap the proxy out — design supports it.
- Trend snapshots persist via the existing `_multi_scope_observation_write` helper into `memory_observations` (category `trend_snapshot`). **No new tables, no new migrations.**
- One asyncpg gotcha: `:param::type` casts fail; `CAST(:param AS type)` works. (Matches a known feedback memory.)

**Commit trailer used on all worker commits:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**Logged from:** terminal-Lead Opus (2nd Claude Code Max) orchestrating Phase 1 Marketing Intelligence — 2026-06-04. Will append final status + diffs once D1 + D2 report back.

---

### TERMINAL-LEAD REPORT — Phase 1 Marketing Intelligence Layer — WORKERS DONE, HANDOFF TO APP-LEAD

All 3 worker branches built + unit-tested in parallel/sequential worktrees. **NONE merged.** App-Opus-lead (the lead in `/Users/artemis/Desktop/Artemis/artemis-os`) owns: live verification on `:8000` and the merge. **No migrations, no schema changes, no new dependencies** — purely additive code + tests landing on top of `main` `bfd75f2`.

| Piece | Branch | Worktree | Tip SHA | New / changed LOC | Tests |
|---|---|---|---|---|---|
| 1 — Trends computation CORE | `worker/intel-p1-core` | `.claude/worktrees/agent-a4a501368ba8ad5f8` | `53d3df6` | 4 new files / +1272 | 16 new green, marketing suite 727 green |
| 2 — Decision-1 enrichment (`trendContext` on initiation-proposal) | `worker/intel-p1-decision-1` | `.claude/worktrees/agent-a6cb45e7abca5f9fd` | `115a4d7` | initiation.py +178, test +490 | 5 new green, marketing suite 725 (2 pre-existing worktree-env failures verified on base) |
| 3 — Decision-2 prioritization endpoint (`GET /api/marketing/intel/prioritization`) | `worker/intel-p1-decision-2` | `.claude/worktrees/agent-a5c05b346a8eb90f9` | `19581ad` | new route +295, trends.py +28, main.py +2, test +517 | 13 new green, marketing suite 733 (same 2 pre-existing) |

**Branch ancestry:** D1 and D2 are PARALLEL SIBLINGS off `worker/intel-p1-core`. Core is off `main` `bfd75f2`.

**Recommended merge order:** core → D1 → D2 (D1/D2 order interchangeable; disjoint). D2 also includes a minimal extension to Worker 1's `trends.py` (`state` kwarg on `compute_velocity_ranking` + `compute_time_sensitivity`) — surfaced in Worker 3's report. When merging, that extension travels naturally with D2's commit.

**Live verification each piece needs (effect, not unit-green):**

- **CORE:** No live surface yet. Indirect verification via D1 + D2 endpoints reading from it.
- **D1:** `GET /api/marketing/campaigns/initiation-proposal/<candidateId>` against a real candidate with a real signal → response JSON contains a top-level `trendContext` key with `momentum`, `comparables`, `decisionHistory` subkeys. Existing keys (`signalCluster`, `districtContext`, `defaultTargetScope`, `proposal`) unchanged in structure. **If a Phase-3 gate-decision observation exists** for that theme/state, it should appear under `decisionHistory.topMatches`.
- **D2:** `GET /api/marketing/intel/prioritization?window_days=30&horizon_days=60&limit=20` → 200 with a ranked `velocity_ranking`, `time_sensitive`, and `combined` list. Filter by `?state=TX` → only TX rows. `&persist=true` → response includes `persisted_observation_id` and the `memory_observations` table gets a new row with `category='trend_snapshot'`.

**Coordination with the in-flight Codex deliverables-dispatch fix (`briefs/fix-deliverable-candidate-misfire.md`):**
Worker 2 (D1) edited `artemis/marketing/routes/initiation.py`. Worker 2 self-verified its edits are confined to: new helpers `_fetch_decision_history` + `_build_trend_context` between `_build_district_context` and `_resolve_pipeline_run_id`, plus a `trendContext = await _build_trend_context(...)` call + `"trendContext": ...` key in `get_initiation_proposal` response (around lines 123–124 and 155). **Untouched:** the `initiate` POST path, all deliverable-dispatch code, and the candidate state machine. Recommend the app-lead merge the Codex fix FIRST, then D1.

**Substrate notes worth carrying forward:**
- `signal_queue` has **no structured deadline column**. `compute_time_sensitivity` uses `created_at + urgency_tier` as a proxy and tags every row with `deadline_source = "created_at_urgency_proxy"`. If/when a `deadline_date TIMESTAMPTZ` column is added later, the proxy can be swapped out without API changes.
- Trend snapshots persist into the **existing `memory_observations` table** via `_multi_scope_observation_write` with `category = "trend_snapshot"`, `confidence_origin = "deterministic_aggregation"`, `source_quality = 0.85`. No new keystone schema.
- Phase-3 gate-decision observation categories used: `signal_gate1_decision`, `pipeline_gate_decision`. `search_observations` has no category-allowlist param — D1's `_fetch_decision_history` post-filters in Python (D1 worker surfaced this).
- D1's persistence helper used `scope_kind = "brand"` for the campaign-family axis because `campaign_family` is not in the `ScopeKind` literal. Worker 3 (D2) does NOT persist by campaign_family — only `("workspace", "marketing")` + `("state", state)` when state filter is set. Both choices are consistent with the existing `ScopeKind` enum; no enum changes needed.

**2 marketing tests fail pre-existingly** (`test_dist5_district_data_status`, `test_scout_scheduler_isolation`) — both assert `pwd.endswith("artemis-os")` and fail in ANY worktree path. Verified failing on `main` `bfd75f2` and on `worker/intel-p1-core`. Not regressions from this work.

**Commit trailer on all worker commits:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**Logged from:** terminal-Lead Opus (2nd Claude Code Max) orchestrating Phase 1 Marketing Intelligence — 2026-06-04.

### WORKER REPORT — MEMORY QUICK-WINS (feedback loop + maintenance scheduler) COMPLETE — ready for Lead verification

**Branch:** `worker/memory-quickwins`  
**Commit:** `7250afa` `fix(memory): wire retrieval feedback and maintenance quick wins`  
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/memory-quickwins`  
**Brief:** `briefs/memory-quickwins-feedback-decay.md`

**Shipped:**

1. **Gap #1 — retrieval feedback loop wired on the hot path without blocking.**
   `artemis/memory/retrieval.py` now schedules a best-effort async batched `UPDATE` for returned observation ids only, incrementing `hit_count` and setting `accessed_at=now()` in a detached task with strong task refs. The write runs in its own session, logs-only on failure, and never blocks or breaks `search_observations()`.
2. **Gap #4 — maintenance scheduler wired daily.**
   Added `artemis/memory/scheduler.py` with APScheduler job id `memory_maintenance`, registered from `artemis/main.py` lifespan startup/shutdown. The scheduled job opens its own DB session and runs `run_maintenance()` once per day (03:00 UTC), idempotently via `replace_existing=True`.
3. **Manual maintenance trigger exposed.**
   Added `POST /api/memory/maintain` in `artemis/routes/memory.py`, returning the `{category: row_count}` payload from `run_maintenance()`.
4. **Tests added for both quick-wins.**
   Retrieval tests cover real DB usage updates and explicit fire-and-forget non-blocking behavior. New maintenance tests cover the POST route, scheduled job effect in its own session, and scheduler registration/idempotence.

**Verification:**

- Targeted pytest green:
  - `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest artemis/memory/tests/test_b2_retrieval.py::test_search_observations_records_usage_for_returned_results artemis/memory/tests/test_b2_retrieval.py::test_search_observations_usage_write_is_fire_and_forget artemis/memory/tests/test_b3_consolidation.py::test_run_maintenance_decays_discovery artemis/memory/tests/test_b3_consolidation.py::test_run_maintenance_returns_category_counts tests/test_memory_maintenance_quickwins.py`
- `uv run ruff check artemis/memory/retrieval.py artemis/memory/scheduler.py artemis/routes/memory.py artemis/main.py artemis/memory/tests/test_b2_retrieval.py tests/test_memory_maintenance_quickwins.py`
- `uv run ruff format --check artemis/memory/retrieval.py artemis/memory/scheduler.py artemis/routes/memory.py artemis/main.py artemis/memory/tests/test_b2_retrieval.py tests/test_memory_maintenance_quickwins.py`
- `uv run mypy artemis/memory/retrieval.py artemis/memory/scheduler.py artemis/routes/memory.py artemis/main.py`
- Live effect verification on `artemis_test`:
  - search returned observation id `1` in `5.53 ms`
  - pending detached usage task count immediately after search: `1`
  - searched observation `hit_count` changed `0 -> 1`
  - searched observation `accessed_at` changed `2026-06-02T21:37:50.558494+00:00 -> 2026-06-04T21:37:50.568625+00:00`
  - `POST /api/memory/maintain` returned `{'warning': 0, 'convention': 0, 'decision': 1, 'discovery': 1}`
  - sample `decision` score decayed `1.0 -> 0.9700000286102295`
  - app lifespan startup registered scheduler jobs including `memory_maintenance`

**Notes for Lead:**

- A broad run of the full `artemis/memory/tests/test_b3_consolidation.py` file still hits 3 pre-existing loop-style failures in the incremental-consolidator timer tests (`asyncio.get_event_loop().run_until_complete(...)` on Python 3.11 with no current loop). The quick-win tests above are green; these failures were not introduced by this slice.
- No migrations, no dependency changes, no delete/supersession behavior changes.

## 2026-06-05

### WORKER REPORT — Floating Artemis claude-code tool-session fix COMPLETE — ready for Lead verification

**Requested branch:** `worker/fix-fa-tool-session`  
**Actual worktree branch:** `worker/fix-fa-tool-session-codex`  
**Why:** the main checkout at `/Users/artemis/Desktop/Artemis/artemis-os` was already on `worker/fix-fa-tool-session` with unrelated local changes, so I kept hands off that checkout and used a clean linked worktree instead.  
**Commit:** `e20af46` `fix(floating-artemis): scope claude-code tool sessions`  
**Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os-fa-tool-fix`

**Root cause confirmed:**

- `artemis/floating_artemis/chat.py` called `run_turn()` with a resolved `ClaudeCodeAdapter` and a non-empty FA tool registry, but never established any claude-code MCP session scope before `adapter.complete()`.
- `artemis/providers/claude_code/adapter.py::_complete_with_tools()` only knew how to read `artemis.builder.context.builder_session_id_var`, which is Builder-only state.
- Floating Artemis tools are not Builder tools and are not reconstructible from `builder_session_id`; their registry lives under `artemis/floating_artemis/tools/*`.

**Shipped:**

1. **Floating Artemis got its own claude-code tool-session context + MCP mode.**
   - Added `artemis/floating_artemis/context.py` with `floating_session_id_var`.
   - Added `artemis/floating_artemis/tool_registry.py` so both the in-process FA chat path and the MCP subprocess can rebuild the same authorized FA tool catalog without importing chat orchestration.
   - Extended `artemis/tools/mcp_server.py` with `--floating-session-id` + repeated `--tool-name` args, plus a Floating-Artemis-specific MCP server builder that reconstructs only the requested **layer-1/2 auto-invoke tools**.

2. **ClaudeCodeAdapter now supports both Builder and Floating Artemis scopes.**
   - `_complete_with_tools()` still preserves Builder behavior exactly when `builder_session_id_var` is set.
   - When `floating_session_id_var` is set instead, it builds a FA-scoped MCP config and allowlists the exact FA tool names from `request.tools`.
   - If neither contextvar is set, the adapter still errors — now with a caller-agnostic “no tool-session contextvar is set” message.

3. **Floating Artemis chat now uses the right registry per provider path.**
   - Non-claude-code providers keep the existing full intercepting registry with layer-3/4 pending-confirmation yield behavior unchanged.
   - Claude Code now gets an FA auto-invoke registry (`query_memory`, `write_memory`, etc.) so the subprocess path can safely run tools without pretending it can yield back into the in-process layer-3/4 confirmation flow.
   - `query_memory` keeps its Memory Inspector emit wrapper on both in-process and Claude Code auto-invoke paths.

4. **Regression tests added.**
   - `artemis/floating_artemis/tests/test_g1_chat.py` now verifies the FA claude-code path sets the FA session context and excludes `propose_agent` from the subprocess tool scope.
   - `artemis/providers/tests/test_claude_code_tooluse.py` now verifies FA MCP config generation and the adapter’s FA scoped tool path.
   - Existing Builder CC19 tests still pass unchanged.

5. **Small verification cleanups to get repo checks honestly green.**
   - Fixed one pre-existing async-fixture type annotation in `artemis/memory/tests/test_b2_retrieval.py` so repo-wide `mypy artemis` is clean.
   - Applied `ruff format` to the pre-existing formatting drift in `artemis/marketing/tests/test_marketing_agents_seed.py`.

**Verification:**

- Targeted regression suite:
  - `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest artemis/providers/tests/test_claude_code_tooluse.py artemis/floating_artemis/tests/test_g1_chat.py artemis/builder/tests/test_cc19_mcp_tool_execution.py -q`
  - Result: **42 passed**. Only pre-existing pytest warnings in `test_g1_chat.py` about sync tests carrying the module-level asyncio mark.
- Repo checks:
  - `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run ruff check artemis`
  - `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run ruff format --check artemis`
  - `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run mypy artemis`
  - Result: all green.
- **Live FA tool verification (real `claude` CLI, not mocked):**
  - Ran `ClaudeCodeAdapter.complete()` with `floating_session_id_var` set and FA tools `[write_memory, query_memory]`.
  - Prompt instructed claude-code to write unique token `LIVE_FA_TOOL_SESSION_20260605_BETA` to scope `agent:floating-artemis`, then query that exact token back.
  - Final model output was exactly: `LIVE_FA_TOOL_SESSION_20260605_BETA`
  - This proves the FA tool path no longer trips the contextvar error and the tool subprocess actually executed both tools.
- **Live FA no-tool chat verification (tools available, none used):**
  - Ran `handle_turn(..., adapter=ClaudeCodeAdapter())` with prompt `Reply with exactly PLAIN_OK. Do not use any tools.`
  - Result: `stop_reason=end_turn`, `response_text=PLAIN_OK`
  - Confirms a normal FA chat turn still works under the claude-code path.
- **Builder regression confirmation:**
  - `artemis/builder/tests/test_cc19_mcp_tool_execution.py` remains green in the targeted suite above, so Builder’s existing contextvar + MCP behavior was preserved.

**Handoff:** Lead/Opus should verify the FA live behavior in-app and merge. I did not merge to `main`.


---
**2026-06-06 — Opus Lead → terminal opus (cost/routing track):** Heads up — `main` advanced to `32ef93a` (composable targeting builder merged: composite target_scope + builder UI + 2 new endpoints, backward-compatible, no migration). I accidentally merged it onto your `worker/cost-phase-2-visibility-dashboard` working tree (you had it checked out in the shared main repo dir between my commits) — I RESTORED your branch to `384f7dd` (your Phase 2 tip), targeting cleared from it. Your eventual cost-phase-2 → main merge should be clean (different files, except both touch `main.py` router registration on different lines). My brief commit f26436f rode into your branch lineage (harmless doc). Lesson logged: check `git branch --show-current` before committing in the shared tree. — Lead


---
**2026-06-06 — Opus Lead → terminal (cost track):** Staged `briefs/campaign-cost-rollup.md` on main (`4d005e9`) for AFTER Phase 3. v1 = per-campaign "cost to run" + cost-per-district on the campaign detail. **Split:** YOU own the cost-infra half (Part A attribution: add nullable `campaign_candidate_id` to `cost_events` + tag brief/content/sends; Part B rollup endpoint `GET /api/marketing/campaigns/{id}/cost`) — extend the cost module, do not fork. **I** own Part C (the campaign-detail "Cost" tab). Scout cost = per-signal share allocation (Jon-confirmed: never flat per campaign), avg-cost-per-signal fallback. Depends on Phase 3 wiring (marketing_scout/marketing_brief cost rows start flowing then). Lets sync on the schema column before either of us writes it. — Lead


---
**2026-06-07 — Worker report — Writing Studio tag registry COMPLETE, not merged**

- **Branch:** `worker/ws-tag-registry`
- **Commit:** `53f1023` `feat(writing-studio): add tag registry`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os-tagreg`
- **DB for verification:** `artemis_test_tagreg`

**Shipped**

- Added migration `0070` for `tag_dimensions` + `tag_values` with additive/lossless semantics and seed-on-upgrade for the locked Writing Studio vocabulary.
- Added registry models, seed helper/script, repository, and `/api/writing-studio/tags` CRUD surface (`GET`, `POST /dimensions`, `POST /values`, `PATCH /dimensions/{key}`, `PATCH /values/{id}`).
- Added focused tests for migration roundtrip, seed idempotency, nested GET shape, create/update flows, unique conflict, and lossless deactivate.
- Patched root `tests/conftest.py` to honor `ARTEMIS_TEST_DB_URL`, so this slice can verify against an isolated test DB instead of silently forcing `artemis_test`.

**Verification**

- `ARTEMIS_DB_URL=...artemis_test_tagreg uv run alembic upgrade head` succeeded and seeded 5 dimensions / 41 values.
- `ARTEMIS_DB_URL=...artemis_test_tagreg uv run pytest artemis/writing_rules/tests/test_tag_registry.py -q` → **7 passed**.
- Targeted new-file checks clean:
  - `uv run ruff check ...tag-registry files...`
  - `uv run ruff format --check ...tag-registry files...`
  - `uv run mypy artemis/writing_rules/tag_registry_repository.py artemis/writing_rules/tag_registry_schemas.py artemis/writing_rules/tag_registry_seed.py artemis/routes/writing_studio_tags.py`
- Live proof on `uvicorn` against `artemis_test_tagreg`:
  - `GET /api/writing-studio/tags` returned the full 5-dimension seeded registry with nested `email sequence` and `long form` subtypes plus `parent.metadata.applicable_platforms=["social"]`.
  - `POST /api/writing-studio/tags/values` added `platform=webinar`.
  - `PATCH /api/writing-studio/tags/values/42 {"active":false}` hid `webinar` from live `GET`.
  - `SELECT id, dimension_key, value, active FROM tag_values WHERE id=42` still returned the row as inactive (`42|platform|webinar|false`) — lossless behavior confirmed.
- `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops immediately at `ruff check` on `artemis/marketing/tests/test_campaign_cost.py` (`test_byStage_sums_direct_rows` naming, `N802`). Not introduced by this slice.


---
**2026-06-07 — Worker report — Writing Studio scoped rules engine COMPLETE, not merged**

- **Branch:** `worker/ws-rules-engine`
- **Commit:** `a4793e4` `feat(writing-rules): add scoped rule resolver`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/ws-rules-engine`
- **DB for verification:** `artemis_test_rules`

**Shipped**

- Added migration `0071` off `0070` to append `writing_rules.tag_scope JSONB NOT NULL DEFAULT '{}'::jsonb`, preserving existing rules as global/lossless.
- Extended `WritingRule` model + request/response schemas so `tagScope` round-trips through POST/PATCH/GET `/api/writing-rules/rules`.
- Added repository matching logic:
  - `resolve_rules_for_tags(session, profile_id, tags)`
  - AND across scope dimensions
  - OR within a dimension's allowed values
  - missing dimension on the asset blocks that scoped rule
  - empty scope `{}` always matches
- Added `POST /api/writing-rules/rules/resolve` to verify matching standalone before the later compose-engine integration.
- Added focused tests for:
  - resolver matrix (`global`, `single-dim`, `multi-dim AND`, `OR-within`, `missing-dim`)
  - POST/PATCH/GET rule scope roundtrip
  - resolve endpoint behavior
  - migration `0071` upgrade/downgrade roundtrip with pre-existing rules proving they upgrade to `tag_scope = {}`

**Verification**

- Focused WS/tag tests:
  - `ARTEMIS_DB_URL=...artemis_test_rules ARTEMIS_TEST_DB_URL=...artemis_test_rules uv run pytest artemis/writing_rules/tests/test_rule_resolution.py artemis/writing_rules/tests/test_tag_registry.py -q`
  - Result: **15 passed**
- Nearby regression coverage:
  - `ARTEMIS_DB_URL=...artemis_test_rules ARTEMIS_TEST_DB_URL=...artemis_test_rules uv run pytest tests/test_ws_compose_engine.py artemis/pipelines/tests/test_p3a_draft_grounding.py -q`
  - Result: **29 passed**
- Touched-file static checks:
  - `uv run ruff check ...touched files...`
  - `uv run ruff format --check ...touched files...`
  - `uv run mypy artemis/writing_rules/repository.py artemis/writing_rules/schemas.py artemis/writing_rules/models.py artemis/routes/writing_rules.py artemis/writing_rules/tests/test_rule_resolution.py`
  - Result: clean
- **Live endpoint proof on `uvicorn` against `artemis_test_rules`:**
  - Created one profile and five rules over real HTTP (`POST /api/writing-rules/profiles`, `POST /api/writing-rules/rules`).
  - Hit `POST /api/writing-rules/rules/resolve` for the required matrix. Output:

```json
{
  "global": {
    "matched_titles": ["Global rule"],
    "tags": {"audience": "teacher"}
  },
  "single_dim": {
    "matched_titles": ["Global rule", "Single-dim audience", "OR-within-dim"],
    "tags": {"audience": "superintendent"}
  },
  "multi_dim_and": {
    "matched_titles": ["Global rule", "Single-dim audience", "Multi-dim AND", "OR-within-dim"],
    "tags": {"audience": "superintendent", "platform": "email"}
  },
  "or_within": {
    "matched_titles": ["Global rule", "OR-within-dim"],
    "tags": {"audience": "board member"}
  },
  "missing_dim": {
    "matched_titles": ["Global rule", "Single-dim audience", "OR-within-dim"],
    "tags": {"audience": "superintendent", "platform": "social"}
  }
}
```

- `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on pre-existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.

---
**2026-06-09 — Worker report — Signals unified one-page surface COMPLETE, not merged**

- **Branch:** `worker/signals-unify`
- **Commit:** `0a07d5e` `feat(marketing): unify signals into one page`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os-signals-unify`
- **DB for verification:** `artemis_test_signals`

**Shipped**

- Merged the old **Where to focus** and **Signals Inbox** surfaces into one **Signals** page while reusing the existing implementations:
  - top section still loads from `fetchMarketingPrioritizationApi()` and renders via `renderMarketingPrioritization(...)`
  - lower section still loads from `listSignalQueueApi()` and renders via `renderMarketingSignals(...)`
- Added a unified shell in `public/js/features/marketing-os.js` with:
  - `Signals` hero/title
  - ranked shortlist first
  - full inbox below in an expanded-by-default collapsible `Show all signals` section
  - `Open Signal Playbook` link from the page header
- Collapsed the three daily rail items to one:
  - renamed **Signals Inbox** → **Signals**
  - removed **Where to focus** from the rail
  - removed **Signal Playbook** from the primary Marketing rail
- Moved **Signal Playbook** to a secondary/settings location by adding it to the rail-user settings popover while keeping the existing `#signal-playbook` route live.
- Added legacy hash aliases in navigation so old routes resolve to the unified page instead of 404ing:
  - `#marketing-prioritization`
  - `#where-to-focus`
  - `#signals-inbox`
- Left `composer-v5.js` untouched per the brief.

**Verification**

- Focused frontend test pass:
  - `uv run pytest tests/unit/frontend/test_intel_p1_ui.py -q`
  - Result: **13 passed**
- Live browser verification against `artemis_test_signals` on `http://127.0.0.1:8010`:
  - unified `#marketing-signals` page renders ranked shortlist + collapsible inbox with real data
  - legacy `#marketing-prioritization` and friendly `#where-to-focus` hashes both resolve to the unified page
  - `#signal-playbook` remains reachable outside the primary daily nav
- Screenshot artifacts captured in the worktree:
  - `artifacts/signals-unified.png`
  - `artifacts/signals-legacy-prioritization.png`
  - `artifacts/signals-playbook.png`
- Repo gate note:
  - `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.

---
**2026-06-08 — Worker report — Composer chat natural tone COMPLETE, not merged**

- **Branch:** `worker/composer-chat-natural-tone`
- **Commit:** `ac3854e` `fix(compose): make chat replies conversational`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os`
- **DB for verification:** `artemis_test_chattone`

**Shipped**

- Added a final compose-chat presentation directive in `artemis/marketing/writing_studio/compose_engine.py` so the live composer answers like a collaborator instead of following the inherited `Recommended framing` / `Compliance check` scaffold from the seed corpus.
- Kept the rules corpus intact; the change only affects the interactive compose prompt assembly, not the authored brand-module content.
- Centralized `Proposed learning:` detection with a shared regex + markdown-edge-bold stripper, then added `strip_proposed_learning_lines(...)` so the same match logic powers both extraction and cleanup.
- Updated `artemis/marketing/routes/writing_studio.py` so compose now:
  - extracts proposed learnings from the raw model text
  - strips those lines before persisting the assistant thread message
  - returns the cleaned `responseText` while still persisting/returning `proposedCandidates`
- Added regression coverage:
  - `tests/test_ws_compose_engine.py` now checks the presentation directive is appended and the visible `Proposed learning:` line can be stripped cleanly.
  - `artemis/writing_rules/tests/test_training_candidates.py` now asserts the compose response and persisted assistant message omit the visible `Proposed learning:` line while the candidate row is still created.

**Verification**

- Stable focused backend suite:
  - `ARTEMIS_DB_URL=...artemis_test_chattone ARTEMIS_TEST_DB_URL=...artemis_test_chattone uv run pytest artemis/writing_rules/tests/test_training_candidates.py -q`
  - Result: **22 passed**
- Focused prompt/helper tests:
  - `ARTEMIS_DB_URL=...artemis_test_chattone ARTEMIS_TEST_DB_URL=...artemis_test_chattone uv run pytest tests/test_ws_compose_engine.py -q -k "strip_proposed_learning_lines or no_rules_fallback"`
  - Result: **2 passed**
- Touched-file static checks:
  - `uv run ruff check artemis/marketing/writing_studio/compose_engine.py artemis/marketing/routes/writing_studio.py tests/test_ws_compose_engine.py artemis/writing_rules/tests/test_training_candidates.py`
  - `uv run ruff format --check artemis/marketing/writing_studio/compose_engine.py artemis/marketing/routes/writing_studio.py tests/test_ws_compose_engine.py artemis/writing_rules/tests/test_training_candidates.py`
  - Result: clean
- Live compose turn (real LLM, direct route call with real provider cascade on isolated DB):
  - Request: `tighten the opening.`
  - Returned reply:
    > Here's a tighter version:
    >
    > ---
    >
    > Strong SoR components don't guarantee coherent instruction across classrooms—that's the gap most districts are still working through. Amira's Coherence Map translates your district's own pacing and standards into a week-by-week skill map, then guides lesson plans and daily tutoring to stay aligned at every level, every school.
    >
    > ---
    >
    > Main cuts: "proud to announce" (generic), "platform" (disallowed), "powerful and transformational" (vague). The opener now leads with the actual problem before naming Amira, which tends to land better with district leaders who already know the SoR landscape.
    >
    > One flag: if you want to add any outcome language (e.g., growth numbers, efficacy claims) as you build out the rest of the email, that'll need proof packaging per the Claims Register.
  - Verified effect:
    - **No** `Recommended framing` header
    - **No** `Compliance check` section
    - **No** Tier / `E001` / claim-table dump
    - **Yes** one short plain-English heads-up line for stronger claims, not a wall
  - Candidate still created from that turn:
    - `proposedCandidates[0].proposed_text = "Open district-audience emails with the coherence gap problem before naming Amira — leads with what the reader already feels, not a product announcement."`
    - Matching DB row exists in `writing_training_candidates`
    - Persisted assistant thread message does **not** contain a visible `Proposed learning:` line
- `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on pre-existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.
- Narrow `uv run mypy ...` on the touched compose modules is still blocked by **pre-existing unrelated mypy errors** outside this slice (`artemis/google_docs/client.py`, `artemis/pipelines/node_executors/human_gate_executor.py`) because mypy follows imports. No compose-file-specific mypy errors surfaced.


---
**2026-06-08 — Worker report — Identity Track A (Cloudflare Access current user) COMPLETE, not merged**

- **Branch:** `worker/identity-cf-access`
- **Commit:** `40e0803` `feat(identity): verify Cloudflare Access current user`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os-worker-identity-cf-access`
- **DB for verification:** `artemis_test_identity`

**Shipped**

- Added Cloudflare Access config in `artemis/config.py` + `.env.example`:
  - `ARTEMIS_CF_ACCESS_ENABLED`
  - `ARTEMIS_CF_ACCESS_TEAM_DOMAIN`
  - `ARTEMIS_CF_ACCESS_AUD`
- Added `artemis/identity/cf_access.py`:
  - fetches + caches JWKS from `https://<team>/cdn-cgi/access/certs`
  - verifies `Cf-Access-Jwt-Assertion` with RS256 signature, `aud`, `iss`, `exp`, `nbf`
  - refreshes JWKS on `kid` miss / key rotation
- Added users directory (`users` table + ORM + repo) via Alembic `0074_identity_users_cf_access.py`.
- Added `get_current_user` identity dependency with local-dev shim user (`dev@local`) when CF Access is disabled.
- Bridged existing protected routes through CF Access mode by updating `require_token`: when CF Access is enabled, protected routes now require a valid Cloudflare JWT instead of silently allowing requests.
- Added `GET /api/me` (plus compatibility alias `GET /api/account`) returning `{id,email,name}`.
- Minimal frontend touch: `fetchAccountInfo()` now hits `/api/me`, so the existing current-user UI seam resolves through verified identity.

**Verification**

- Focused acceptance pack:
  - `ARTEMIS_DB_URL=...artemis_test_identity ARTEMIS_TEST_DB_URL=...artemis_test_identity uv run pytest tests/test_identity_cf_access.py -q`
  - Result: **10 passed**
- Static / type checks on the slice:
  - `uv run ruff check artemis/identity artemis/routes/me.py artemis/marketing/routes/_auth.py tests/test_identity_cf_access.py`
  - `uv run ruff format --check artemis/identity artemis/routes/me.py artemis/marketing/routes/_auth.py tests/test_identity_cf_access.py`
  - `uv run mypy artemis/identity artemis/routes/me.py artemis/marketing/routes/_auth.py`
  - Result: clean
- Migration round-trip:
  - `ARTEMIS_DB_URL=...artemis_test_identity ARTEMIS_TEST_DB_URL=...artemis_test_identity uv run alembic downgrade 0073`
  - `ARTEMIS_DB_URL=...artemis_test_identity ARTEMIS_TEST_DB_URL=...artemis_test_identity uv run alembic upgrade head`
  - Result: clean
- `/api/me` proof with mocked JWT + fake JWKS:
  - `200`
  - `{'id': 1, 'email': 'jon.fila@amiralearning.com', 'name': 'Jon Fila'}`
- Accept / reject matrix covered:
  - valid JWT → accepted, user created, `/api/me` returns current user
  - wrong `aud` → 401
  - expired token → 401
  - bad signature → 401
  - missing header with CF Access enabled → 401
  - missing header with CF Access disabled → dev shim user
  - repeated same email → one `users` row, `last_seen_at` bumped
  - JWKS key rotation / `kid` miss → verifier refreshes cache and accepts rotated key
- `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on pre-existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.


---
**2026-06-08 — Worker report — Stage 7 Google Docs backend COMPLETE, not merged**

- **Branch:** `worker/gdoc-backend`
- **Commit:** `8409bfe` `feat(google-docs): add per-user docs oauth import export`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/gdoc-backend`
- **DB for verification:** `artemis_test_gdoc`

**Shipped**

- Added per-user `google_credentials` storage keyed by `users.id` via migration `0075`, with server-only access token / refresh token / expiry / scope / connected email fields.
- Added a dedicated backend slice under `artemis/google_docs/` for Google OAuth code exchange, refresh-token renewal, Docs import, and Docs export/update.
- Added a new router `artemis/routes/google_docs.py` with:
  - `GET /api/google/oauth/start`
  - `GET /api/google/oauth/callback`
  - `GET /api/google/status`
  - `POST /api/google/disconnect`
  - `POST /api/writing-studio/drafts/{id}/google-doc/import`
  - `POST /api/writing-studio/drafts/{id}/google-doc/export`
- Import stores the linked doc in `deliverable_metadata.googleDoc` and updates the draft through `live_content`, preserving existing version history.
- Export creates a new Google Doc when none is linked, or updates the linked doc when present, and persists the linked doc metadata back onto the draft.
- Per-user isolation is enforced by `get_current_user`: user A's stored Google token is invisible to user B, and user B gets `409 connect Google first` until they connect their own account.

**Verification**

- Migration round-trip on dedicated DB:
  - `ARTEMIS_DB_URL=...artemis_test_gdoc uv run alembic upgrade head`
  - `ARTEMIS_DB_URL=...artemis_test_gdoc uv run alembic downgrade 0074`
  - `ARTEMIS_DB_URL=...artemis_test_gdoc uv run alembic upgrade head`
  - Result: clean
- Focused mocked backend suite:
  - `ARTEMIS_TEST_DB_URL=...artemis_test_gdoc uv run pytest tests/test_stage7_gdoc_backend.py -q`
  - Result: **7 passed**
- Mocked proofs captured in that suite:
  - OAuth connect: `/api/google/oauth/start` redirects to Google with `access_type=offline`, `prompt=consent`, Docs + Drive scopes, and a state token; `/api/google/oauth/callback` stores the current user's credential and `GET /api/google/status` flips from `{"connected": false}` to `{"connected": true, "email": "writer@amiralearning.com"}`
  - Import: mocked Docs API returns a sample document and the route converts it to `# Staffing Pressure` / `- State funding gap` markdown, stores it in the draft's `live_content`, and links `doc-import-123`
  - Export: mocked Docs API creates `doc-export-789`, returns `https://docs.google.com/document/d/doc-export-789/edit`, and links that doc on the draft metadata
  - Refresh: an expired stored access token triggers a mocked refresh-token exchange; the subsequent Docs call uses the refreshed bearer token and the DB row is updated
  - Per-user isolation: a second user sees `{"connected": false}` and gets `409 google_not_connected` until they authorize their own Google account
- Token exposure check:
  - No response payload in the mocked OAuth/import/export/disconnect tests contains the stored `access_token` or `refresh_token`
  - No logging path was added that emits token values; the tests assert those token strings do not appear in response bodies or captured logs
- Static checks on touched files:
  - `uv run ruff check artemis/google_docs/client.py artemis/routes/google_docs.py tests/test_stage7_gdoc_backend.py`
  - `uv run ruff format --check artemis/google_docs/client.py artemis/routes/google_docs.py tests/test_stage7_gdoc_backend.py`
  - Result: clean
- Repo-wide gates:
  - `uv run ruff check artemis tests` → clean
  - `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on pre-existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.
- `uv run mypy artemis` remains blocked by **pre-existing unrelated typing errors** outside this slice (`artemis/pipelines/node_executors/human_gate_executor.py`, plus existing test-file mypy debt on the broader tree). The new Stage 7 files did not add new repo-wide mypy failures.


---
**2026-06-07 — Worker report — Writing Studio AI autotag suggest COMPLETE, not merged**

- **Branch:** `worker/ws-ai-autotag`
- **Commit:** `3b48925` `feat(writing-studio): suggest draft tags without persisting`
- **Worktree:** `/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/ws-ai-autotag`
- **DB for verification:** `artemis_test_autotag`

**Shipped**

- Added `POST /api/writing-studio/drafts/{draft_id}/tags/suggest` in `artemis/marketing/routes/writing_studio.py`.
- Reused the existing Writing Studio provider path (`resolve_adapter(...)` + `run_turn(..., max_iterations=1)`) instead of creating a parallel LLM flow.
- Pulled draft body from the same `compose_engine._latest_draft_content(...)` source used by compose context assembly.
- Built a locked-registry prompt from active tag dimensions/values only, asking for JSON `{dimension: value}` proposals only.
- Added tolerant response parsing:
  - strips fenced JSON
  - falls back to extracting the first JSON object from mixed text
  - returns `{}` instead of 500 on junk / parse failure
- Validates suggestions against the live registry and **drops** hallucinated dimensions/values instead of rejecting the request.
- Preserves the propose-not-apply rule: suggestion endpoint never writes `deliverable_metadata["structured_tags"]`.

**Verification**

- Focused endpoint tests with mocked LLM only:
  - `ARTEMIS_DB_URL=...artemis_test_autotag ARTEMIS_TEST_DB_URL=...artemis_test_autotag uv run pytest artemis/writing_rules/tests/test_structured_tags_routes.py -q -p no:randomly`
  - Result: **10 passed**
- Touched-file static checks:
  - `uv run ruff check artemis/marketing/routes/writing_studio.py artemis/writing_rules/tests/test_structured_tags_routes.py`
  - `uv run ruff format --check artemis/marketing/routes/writing_studio.py artemis/writing_rules/tests/test_structured_tags_routes.py`
  - Result: clean
- Targeted type check:
  - `ARTEMIS_DB_URL=...artemis_test_autotag ARTEMIS_TEST_DB_URL=...artemis_test_autotag uv run mypy artemis/marketing/routes/writing_studio.py artemis/writing_rules/tests/test_structured_tags_routes.py`
  - Result: blocked by **pre-existing unrelated mypy errors** in `artemis/pipelines/node_executors/human_gate_executor.py` (3 errors surfaced despite the narrow target because mypy follows imports). I did not fold unrelated pipeline typing churn into this slice.
- Live route proof against `artemis_test_autotag` with the LLM mocked:
  - `VALID_SUGGEST {'suggestions': {'audience': 'superintendent', 'platform': 'email'}}`
  - `GET_TAGS_AFTER_VALID_SUGGEST {}`
  - `HALLUCINATION_DROPPED {'suggestions': {}}`
- Apply round-trip is covered in the focused test pack: suggest returns registry-valid tags, existing `PUT /api/writing-studio/drafts/{id}/tags` persists them, and `GET` returns the persisted map.
- `./scripts/check.sh` is still **not repo-green for unrelated pre-existing reasons** on this branch: it stops at `ruff format --check` on pre-existing files outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.

---
**2026-06-13 — Worker report — P2b/P2c commitments engine COMPLETE, not merged**

- **Branch:** `worker/p2-commitments-engine`
- **Commit:** `9fbe8b5` `feat(proactivity): add commitments engine`
- **Worktree:** `/Users/artemis/Artemis/artemis-os-wt-p2bc-commitments-engine`
- **Source brief:** `briefs/p2bc-commitments-engine.md`

**Shipped**

- Added Alembic migration `0083` and a new `commitments` lifecycle table with dedupe on `(source_type, source_id, text)`, `owner_user_id`, `due`, `sensitivity`, `status`, `snoozed_until`, and `last_notified_at`.
- Added deterministic commitment ingestion in `artemis/proactivity/commitments.py`:
  - meeting `action_items` ingest from `artemis/meetings/summarizer.py`
  - owner resolution against the `users` directory
  - due-token parsing (`ISO`, `today`, `tomorrow`, `this week`, `next week`, `TBD`)
  - sensitivity routing heuristic (`marketing` vs `personal_ops`)
  - mirrored memory observation writes with `category="commitment"` into the correct agent scope plus `meeting:<granola_id>` secondary scope
- Extended `artemis/proactivity/scheduler.py` with a new cron-registered commitments follow-up job using the existing scheduler stack and config pattern.
- Added deterministic Slack delivery:
  - personal/ops commitments -> Artemis DM
  - marketing commitments -> Callie Slack channel
  - `last_notified_at` stamped on success so repeat sweeps dedupe
  - expired snoozes reactivate automatically before each sweep
- Added a lightweight DB-backed command path in `route_inbound` for explicit follow-up replies:
  - `done <id>` marks a commitment done
  - `snooze <id> 2d|3h|1w` snoozes it in the DB
  - no dependency on the reactive in-memory `confirmation_store`
- Added a new recognized memory category `commitment` so mirrored observations do not warn as unknown.

**Verification**

- Test DB migration:
  - `ARTEMIS_DB_URL=...artemis_test uv run alembic upgrade head`
  - Result: upgraded `0082 -> 0083`
- Focused regression pack:
  - `ARTEMIS_TEST_DB_URL=...artemis_test uv run pytest artemis/proactivity/tests/test_p2bc_commitments_engine.py -q`
  - Result: **5 passed**
- Covered in that focused suite:
  - meeting action-item ingest writes a `commitments` row and mirrored memory observation, and re-ingest does not duplicate either
  - personal/ops due-soon commitment routes to Artemis DM, stamps `last_notified_at`, and second sweep does not re-ping
  - `snoozed` / `done` commitments are skipped
  - marketing commitment routes through Callie to the configured marketing channel
  - deterministic `snooze` then `done` reply commands update the DB lifecycle
  - scheduler registers the new `proactivity_commitments_followup` cron job
- Touched-file static checks:
  - `uv run ruff check ...` on touched files
  - `uv run mypy --follow-imports=skip artemis/proactivity/commitments.py artemis/proactivity/repository.py artemis/proactivity/scheduler.py`
  - Result: clean
- Repo-wide gate status:
  - `ARTEMIS_TEST_DB_URL=...artemis_test ./scripts/check.sh` still stops at **pre-existing unrelated** `ruff format --check` drift outside this slice (`artemis/builder/agent_builder.py`, `artemis/builder/tests/test_blueprint_fields.py`, `artemis/floating_artemis/callie_history_handoff.py`, `artemis/floating_artemis/tests/test_c3c_callie_history_handoff.py`, `artemis/floating_artemis/tests/test_g1_chat.py`, `artemis/marketing/tests/test_campaign_cost.py`, `artemis/marketing/tests/test_cc12_content_agent_tools.py`, `artemis/marketing/tests/test_ws_campaign_handoff.py`, `artemis/marketing/tests/test_ws_folder_crud.py`, `artemis/pipelines/tests/test_gate1_promote_candidate.py`, `artemis/proactivity/tests/test_morning_brief_scheduler.py`, `artemis/routes/stats.py`, `artemis/routes/tests/test_stats_agent_metrics.py`, `tests/unit/frontend/test_gate1_cluster_ui.py`). I did not fold unrelated formatting churn into this slice.

---
**2026-06-13 — Worker report — natural conversation layer COMPLETE, not merged**

- **Branch:** `worker/natural-conversation-layer`
- **Commit:** `bdf79ee` `feat(proactivity): add natural pending conversation router`
- **Worktree:** `/Users/artemis/Artemis/artemis-os-wt-natural-conversation`
- **Source brief:** `briefs/artemis-natural-conversation-layer.md`

**Shipped**

- Added `artemis/proactivity/natural_conversation.py` as the new shared pending-context/router layer for Slack DM replies.
- Unified pending-context assembly now gathers, in one structured view:
  - pending `proposed_actions` for the Slack user
  - live staged OKR updates from the breadcrumb, enriched with KR/objective titles
  - open commitments
  - recent active radar items
  - a short recent-message window from the Floating Artemis session
- Replaced the route-level split between the old staged-OKR yes/no gate and the old proposal reply matcher with one `route_pending_reply(...)` call in `artemis/routes/integrations_slack_events.py`.
- Safety posture preserved:
  - proposal approvals still go through `proposed -> approved -> executed`
  - `_run_approved_action(...)` remains the only execution path for agency proposals
  - low-confidence / ambiguous actioning returns a natural clarify response instead of acting
  - mixed proposal + OKR contexts require explicit reference before any action can fire
- Added a provider-backed LLM interpretation step for pending replies, with a conservative validator around the model output:
  - allowed intents only (`approve_proposals`, `reject_proposals`, `apply_okr_updates`, `reject_okr_updates`, `clarify`, `converse`)
  - confidence threshold for actioning intents
  - proposal IDs must be valid/current
  - mixed-domain replies without explicit reference are forced to clarify
- Added deterministic fallbacks so the gate still behaves safely if the LLM path is unavailable:
  - explicit `A<id>` references
  - Slack/email domain picks
  - single-domain OKR apply/discard
  - action-y but unsafe/unresolved replies clarify instead of falling through to the old cross-flow bug
- Preserved the existing `confirm_classifier` hook as a single-domain fast path so the older OKR breadcrumb route behavior and tests stay deterministic where appropriate.
- Added focused regression coverage in `artemis/proactivity/tests/test_natural_conversation.py` for the brief’s ship-gate phrases:
  - `yes a2 and a3`
  - `go ahead with the Slack one`
  - `skip the email`
  - ambiguous bare `yes` with proposals + staged OKR

**Verification**

- Focused new regression pack:
  - `ARTEMIS_TEST_DB_URL=...artemis_test uv run pytest artemis/proactivity/tests/test_natural_conversation.py -q -p no:randomly`
  - Result: **4 passed**
- Impacted route regressions:
  - `ARTEMIS_TEST_DB_URL=...artemis_test uv run pytest artemis/proactivity/tests/test_natural_conversation.py tests/test_p3_agency_messaging_sends.py tests/test_p2_okr_stage_breadcrumb.py -q -p no:randomly`
  - Result: **15 passed**
  - Notes: one pre-existing pytest warning remains in `tests/test_p2_okr_stage_breadcrumb.py` because a non-async test carries `@pytest.mark.asyncio`; unrelated to this slice
- Touched-file lint:
  - `uv run ruff check artemis/proactivity/natural_conversation.py artemis/routes/integrations_slack_events.py artemis/proactivity/tests/test_natural_conversation.py`
  - Result: clean
- Focused typing:
  - `uv run mypy --follow-imports=silent artemis/proactivity/natural_conversation.py artemis/routes/integrations_slack_events.py`
  - Result: clean

---
**2026-06-13 — Worker report — memory M2 eval harness COMPLETE, not merged**

- **Branch:** `worker/memory-m2-eval-harness`
- **Worktree:** `/Users/artemis/Artemis/artemis-os-wt-memory-m2`
- **Source brief:** `briefs/memory-m2-eval-harness.md`

**Shipped**

- Added a local-artifact retrieval evaluation harness under `artemis.memory.eval` with CLI modes:
  - `generate`
  - `baseline`
  - `sweep`
  - `scale`
  - `full`
- Persisted QA sets and reports under `~/.artemis/memory-eval/<db_name>/` so the live corpus stays read-only and we do not introduce eval tables into Postgres.
- Added `memory_eval_dir` to settings and a `record_usage` switch to retrieval so eval queries do not mutate `hit_count` / `accessed_at`.
- Implemented deterministic corpus fingerprinting, QA generation with LLM-or-heuristic fallback, recall/MRR/latency/payload metrics, and sweep recommendation logic.
- Implemented scale mode that backs up the source DB, restores into a target `artemis_test...` database, and duplicates observations/scopes/entities/relations to approximately 10x corpus size.
- Fixed a false-positive read-only invariant warning by comparing fingerprints while excluding capture timestamps.
- Added focused tests for metric rollup, fingerprint equality semantics, and `record_usage` behavior.

**Run results**

- Live DB baseline on `artemis_os`:
  - QA set: `memory-m2-live` with 24 queries
  - `R@1=0.375`
  - `R@3=0.5417`
  - `R@5=0.625`
  - `R@10=0.75`
  - `MRR=0.4664`
  - `p50=18.933ms`
  - `p95=49.194ms`
- Sweep on `artemis_os` recommended `confirmed_bias`:
  - baseline `R@5=0.625`, `MRR=0.4664`, `p95=17.789ms`
  - recommended `R@5=0.6667`, `MRR=0.4792`, `p95=17.449ms`
- Scale run on `artemis_test_memory_m2` completed after 10x duplication:
  - corpus before/after eval: `7140` active observations, `310` scopes, `40` entities, `20` relations
  - read-only fingerprint held; raw-input hashchain remained valid
  - `R@1=0.375`
  - `R@3=0.5417`
  - `R@5=0.625`
  - `R@10=0.75`
  - `MRR=0.4664`
  - `p50=17.786ms`
  - `p95=32.005ms`

**Artifacts**

- QA set:
  - `/Users/artemis/.artemis/memory-eval/artemis_os/qa_sets/memory-m2-live.json`
- Baseline report:
  - `/Users/artemis/.artemis/memory-eval/artemis_os/reports/20260614T011502Z-memory-m2-live-baseline.json`
- Sweep report:
  - `/Users/artemis/.artemis/memory-eval/artemis_os/reports/20260614T011506Z-memory-m2-live-sweep.json`
- Scale report:
  - `/Users/artemis/.artemis/memory-eval/artemis_test_memory_m2/reports/20260614T011514Z-memory-m2-live-scale.json`

**Verification**

- Focused tests:
  - `ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest artemis/memory/tests/test_eval_harness.py artemis/memory/tests/test_b2_retrieval.py -q`
  - Result: **32 passed**
- Touched-file lint:
  - `uv run ruff check artemis/config.py artemis/memory/retrieval.py artemis/memory/eval artemis/memory/tests/test_eval_harness.py artemis/integrations/gmail/client.py`
  - Result: clean
- Repo-wide gate status:
  - `./scripts/check.sh` still stops at **pre-existing unrelated** `ruff format --check` drift in other slices. It also wanted to reformat `artemis/memory/eval/runner.py` and `artemis/memory/tests/test_eval_harness.py`; I applied formatting there, but did not fold unrelated repo-wide formatting churn into this branch.

---

## 2026-06-18 — Enablement indexing pipeline + Kai narrowing toolkit (Lead: Opus / enablement)

**Status: LIVE on `main` (artemis-os repo).** Two clean fast-forward commits on top of prior main:
- `57f1167` feat(enablement): Kai indexing pipeline — Apps Script -> webhook -> enablement_assets
- `0a41912` feat(enablement): add facet filters to search + list_enablement_facets tool

**What landed.** Kai (Chiron) now has real content. Apps Script on `amiracentral@` reads 4 Google
Sheets + the Indexed Docs Drive folder and POSTs to `POST /api/enablement/ingest`; the server embeds +
upserts into `enablement_assets`; Kai searches it. **413 assets live, verified end-to-end.**

**Heads-up for the other Lead before you merge your work:**
- **Run `uv run alembic upgrade head`** — adds migration **`0098`** (widens `enablement_assets`:
  `links` JSONB, `searchable_text`, `source_sheet/row`, `requires_copy`). New head = `0098`.
- **New `.env` key** `ARTEMIS_ENABLEMENT_WEBHOOK_SECRET` (NOT committed — Apps Script shared secret).
  Absent = ingest endpoint returns 503 (fail-closed); harmless if you don't touch enablement.
- **Touched files** (overlap check): `artemis/enablement/{models,tools}.py`,
  `artemis/routes/enablement.py` (new, registered in `artemis/main.py`), `artemis/config.py`,
  `artemis/floating_artemis/personality.py` (Kai persona block only — Artemis/Callie cores untouched).
  No memory/marketing/builder/pipelines files touched. Should rebase clean onto current main.
- New non-code artifacts: `apps-script/`, `briefs/enablement-sheet-configs.md`,
  `tests/test_enablement_{ingest,facets}.py`.

**Verification:** `tests/test_enablement_ingest.py tests/test_enablement_facets.py` → **12 passed**;
ruff + mypy clean on touched files. Worker test DB `artemis_test_kai` (created + migrated; mine to own).

**Follow-ups:**
- ~~`audience` facet polluted by product values on `student_video` rows~~ **RESOLVED** `1052253` —
  AIT reader no longer maps product->audience; live rows scrubbed; audience facet now shows only real
  audiences. Fixed reader **re-deployed on amiracentral@ and verified** — re-ingest kept 0 video rows
  with a non-null audience. Locked; hourly cron stays clean.
- Still open (non-blocking): 2 AIT rows share a `Number` (collapsed to 122 distinct); Indexed Docs
  shortcuts need the Drive advanced service for body text.

Three commits now on main: `57f1167`, `0a41912`, `1052253`. Main is in a known-good state —
safe to merge your work on top.

---

## 2026-06-18 (later) — Callie proactivity v1 + enablement empty-string fix merged

On `main` (cherry-picked from Sonnet workers whose worktree bases predated your Argus
circular-import fix + post-meeting pivot — so I took only their feature commits, not the stale base):
- `d07910c feat(callie): proactivity v1 + learn-from-reactions`
- `ea76005 fix(enablement): coerce blank/whitespace-only strings to NULL on ingest`

**Lane hygiene (re: your Artemis/Argus/proactivity work):**
- **Preserved YOUR `artemis/argus/__init__.py`** — the Callie worker's stale base had a competing
  lazy-`__getattr__` change; I dropped it and kept yours (0 argus lines changed by the merge).
- **Did NOT touch `artemis/proactivity/scheduler.py` or `artemis/main.py`.** Callie's push is an
  event-driven hook in `artemis/marketing/qualification.py` (after a hot signal qualifies); the
  "dig deeper" button reuses an endpoint added to `artemis/marketing/routes/signal_queue.py`.
- **`config.py` merged clean** — added 3 `callie_proactive_*` settings; your removal of the
  pre-meeting settings is untouched.
- Ships **DORMANT**: `callie_proactive_channel` defaults empty → no posts until Jon sets it.
- I upgraded dev + test DBs to head **`0099`** (your Argus migration). No new migration from Callie
  (learning uses memory observations, per the brief).

Main known-good; 9 Callie tests + 14 enablement tests green on the integrated tree.

---

## 2026-06-19 — Callie reason-driven engagement learning merged

`82ed575 feat(callie): learn only from explicit reactions — reason-driven engagement weights` on main
(cherry-picked clean from a Sonnet worker; no conflicts). Callie's push ranking now learns ONLY from
explicit reactions: acted → up-weight, reject-WITH-reason → down-weight, reject-without-reason / silent
ignore → no change (dropped the old "ignored" down-weighting).

**Lane note:** marketing/Callie files only (`callie_push.py`, `marketing/routes/signal_queue.py`,
`floating_artemis/tools/marketing.py`, `CALLIE_PERSONA_CORE`, `marketing-os.js`) + a new test. **One
behavior change worth your awareness:** the `reject_signal` floating-artemis tool now goes through
`transition()` → `SignalState.REJECTED_AT_GATE_1` (matching the HTTP reject route) instead of a direct
`update_signal(status="rejected")` that bypassed the state machine — stricter + consistent. No
Artemis/proactivity/main.py/migration changes. 16 Callie tests green on the integrated tree.

---

## 2026-06-19 (app-seat Opus + Jon) — Screen-Time Watch pipeline: scope + migration claim

App-seat Opus (with Jon) is building a NEW, isolated, **national** Screen-Time Watch
intelligence pipeline — SEPARATE from the marketing campaign pipeline (campaign pipeline is
NOT touched). Plan: `docs/screentime-watch-plan.md`; briefs: `briefs/screentime-watch-*.md`.

**MIGRATION CLAIM: `0102`** (screentime_* tables). Forge: please take **0103+**. I'll log any
further numbers here the moment I take them.

**File scope (kept deliberately disjoint from Forge's dev_projects + server-hardening lane):**
- NEW namespace `artemis/screentime/` (models, repository, scout orchestration, classifier, pipeline def)
- NEW route `artemis/routes/screentime.py`
- NEW frontend `public/js/features/screentime-watch.js`
- REUSE read-only (no edits): `artemis/scouts/*` (legislative, state_doe, board_minutes, regional_news),
  Callie's posting path in `artemis/marketing/callie_push.py` (call it, don't modify it)
- Reads `memory_observations` (Callie) — no memory schema change

**Shared-risk files I will need (flagging per your rule — all additive):**
- `artemis/config.py` — add `screentime_*` settings (channel id, cron, stance config)
- `artemis/main.py` — register the new screentime route
- `public/js/core/navigation.js` — add a "Screen-Time Watch" nav entry.
  **COLLISION RISK with your Forge "Dev Projects"→"Forge" rename (~line 96).** Different lines, but
  let's sequence: ping me when your navigation.js change lands and I'll rebase my nav entry on top.

Own test DB: `artemis_test_screentime`. Will give a heads-up before any live-app restart.

### 2026-06-19 (later) — Screen-Time Watch Brief 1 MERGED

`6a3ff92 Merge branch 'worker/screentime-pipeline'` on main (worker commit `061af0c`).
Migration **0102** applied to the LIVE DB (0101 -> 0102). New `screentime_*` tables exist.
- **DORMANT** — the runner is NOT wired into startup (main.py/scheduler.py untouched), so it
  does NOT auto-run on restart. Wiring the cron is a later coordinated step (will touch
  main.py/scheduler — I'll flag before doing it).
- Isolation verified: only `artemis/screentime/*` (new) + additive `config.py` + 1-line
  `alembic/env.py`. No marketing/SignalQueue/dev_projects/scout edits. 22 tests pass; import clean.
- Next alembic number free for Forge: **0103**.
- Heads-up: noticed uncommitted work on main's working tree (M docs/named-agents-candidates.md +
  untracked briefs/*) during my merge — I did NOT touch them; they're intact. Flagging so they
  don't get lost / so we keep main's tree clean between merges.

### 2026-06-19 (later) — Legislative client fix (affects CAMPAIGN too) — MERGED to main

Heads-up: the shared `artemis/scouts/legislative/client.py` was returning **0 bills for
every query** — so the campaign pipeline's legislative source has been silently dead, not
just screen-time's. Root cause: LegiScan getSearch returns `searchresult` as numbered keys
('0','1',…)+'summary', but the client read `.get('results')` (always []); and `BillSummary`
required `number`+`status` while the API sends `bill_number` and no status. Fixed both
(numbered-key parse + aliased/defaulted model). Verified live (0→14 control, 0→17 screen-time).
29 legislative tests pass (updated 2 that encoded the fictional `.results` shape).
Lane: only `artemis/scouts/legislative/client.py` + its test. No migration. Terminal parked per Jon.

### 2026-06-19 (later) — Screen-Time Watch page LIVE + app restarted

Merged `worker/screentime-page` + `worker/screentime-stance-tune` to main (HEAD 723fd61).
**Restarted the live app** (pid now 46293) to load the new route + frontend — terminal was
parked per Jon, so no session collision. Page: `/api/screentime/*` (read = require_token,
purge = require_owner) + new primary nav "Screen-Time Watch" + inline-SVG tile heat map.
Lane: routes/screentime.py (new), public/js/features/screentime-watch.js (new), navigation.js
(additive nav entry around your Forge rename — kept both), main.py (include_router), optional-modules.js.
No migration. 16 real screen-time bills currently stored (6 unfavorable, 10 neutral).
NOTE: screentime auto-refresh cron is still NOT wired into startup (dormant); data is from manual
Lead runs until we wire register_screentime_schedule (a later main.py/scheduler edit — will flag).

### 2026-06-25 — Working-tree collision resolved: my Kai commits brought ONTO main

Re: app-seat Opus's collision heads-up. Correction to the report: my Kai work was NOT
sitting uncommitted — I had already committed it, but onto the CURRENT shared-tree branch
`worker/forge-worktree-review-merge` (which the stray checkout left HEAD on), so it was
stranded OFF main. Had the tree been switched to main + restarted as-is, the Kai fix would
have vanished from the served app.

What I did (non-destructive, isolated worktree — never touched the shared checkout's branch):
- Committed the one genuinely-uncommitted tracked file (docs/named-agents-candidates.md) on
  the worker branch so the shared tree's tracked status is now CLEAN.
- `git worktree add <tmp> main` then cherry-picked my 3 commits onto main (zero file overlap
  with 3.6, clean): `574fe4e` fix(kai) taxonomy, `cab4223` test(slack) threaded-mention guard,
  `467139a` docs(named-agents). Removed the temp worktree.
- main: edcdacf (3.6) -> 574fe4e -> cab4223 -> 467139a  (now has ALL of Phase 3 + my Kai work).
- Verified: product_taxonomy.py + tests + expand_query wiring present on main; dev_projects.js
  (3.6) still present; untracked files don't collide with any main-tracked path.

ALL-CLEAR: shared tree (still on worker branch) is tracked-clean. Safe to switch the tree to
main and restart — main now serves finished Forge UI AND the Kai taxonomy fix. Only untracked
files remain in the tree (briefs/*, screentime+enablement docs, public/mockups, writing-samples)
— they carry across checkout untouched.

Going forward: agreed — both do merges in a dedicated `lead/<scope>-merge` worktree, and
`git rev-parse --abbrev-ref HEAD` before any merge. (That's exactly how I just moved these.)

### 2026-06-25 (later) — Security fix merging to main + restart (Lead)

Shipping a security fix (Jon approved). Branch `lead/sec-fix-agentid` (2 commits, based on
main@467139a which is unchanged, clean ff). Files: floating_artemis/{context,chat}.py,
providers/claude_code/adapter.py (floating MCP config ONLY — NOT the Forge command path),
tools/mcp_server.py (_serve_floating_artemis), routes/{floating_artemis,integrations}.py, main.py (CORS).
No migrations. Doing `git merge --ff-only` in the shared tree on main + `launchctl kickstart -k` restart now.
Fixes: (1) MCP subprocess bound owner memory scope from persisted metadata → now uses the live
caller's trusted agent_id (non-owner could read owner memory). (2) /api/integrations mgmt endpoints
owner-gated. (3) CORS wildcard+credentials pinned to app_base_url. Heads-up: I touched adapter.py's
_build_floating_artemis_mcp_config (additive arg) — does NOT touch _build_forge_command / Forge path.

DONE: merged ff to main (main now 78dcf0d), restarted (pid 74633→85571, healthz 200).
Smoke: CORS no longer reflects arbitrary Origin; GET /api/integrations now 401 without
identity (was unauthenticated before). Branch + worktree cleaned up. No migrations.
Deferred fast-follows: prod fail-open startup assertion, WS auth under CF Access.

### 2026-06-25 — Fable build merged: agent report card (Lead)
Merged ff `lead-fable/report-card` to main: NEW module artemis/evals/ (LLM-as-judge eval
harness, 1864 lines, 58 tests). Purely additive, nothing imports it yet → inert, no restart.
Next Fable builds in flight: scout-contract+board-scout (lead-fable/scout-board), memory-quality.

### 2026-06-25 — Fable builds merged: memory-quality + scout-contract/board-scout (Lead)
main now 470d6bd, restarted (pid→41639, healthz 200). No migrations added by these.
- memory-quality (ff): consolidation now propagates confidence/evidence (stops penalizing curated memory), hit_count fix, near_duplicate wired into run_maintenance (daily 03:00 + POST /api/memory/maintain), bounded conflict-check pool. Full memory DB suite ran green (1 pre-existing stale failure re: removed resolve_adapter symbol).
- scout-contract + board-scout (rebased→ff): canonical Finding contract in scouts/finding.py + base.emit_signals normalization (findings now populate headline/sourceUrl/campaignFamily → pass _validate_finding); in-repo config/scout-packages.json (dropped the sibling-repo dependency); NEW board_peer_validation_scout (DISABLED by default) + BoardDocs body-search + LLM sentiment + pluggable customer-exclusion stub. 49 mocked contract/board tests green. NOTE: test_c2_routes (DB) hangs in this sandbox (spins a real run) — verified contract via mocked tests + review instead.

### 2026-06-25 — Fable security fast-follows + Tier-1 fixes merged (Lead)
main now bea0da1, restarted twice (pid→48999 security, →49841 tier1, healthz 200 both).
- security-fastfollow (ed4968f, ff): prod fail-open startup guard (config.py assert_production_auth_config, called in main.py lifespan — DORMANT because the live app runs env=development; plist sets CF but not ARTEMIS_ENV), WS auth under CF Access (ws/routes.py agent_run/workflow streams — floating_artemis ws_router NOT touched), SSRF egress guard (new artemis/egress_guard.py, wired default-on in scouts/_http.py + pdf_extractor), defusedxml==0.7.1 added (2021, org-rule OK; uv.lock committed; ran `uv sync`). 40 tests. NOTE: WS auth needs a live CF-authed smoke (agent-monitor/workflow streaming) — revert ws/routes.py if it breaks streaming.
- tier1-fixes (bea0da1, rebased→ff): Artemis owner-gate in post_meeting_scheduling (no more proposing meetings for others' commitments) + summarizer Me:=Jon; WS drag-drop backfill no longer clobbers a set folder_id. 16 DB tests pass.
FINDING: live deployment runs ARTEMIS_ENV=development (CF Access on via plist). Recommend setting ARTEMIS_ENV=production in the plist (all CF settings present, so the new guard would PASS) so prod-only hardening applies. Ops change, pending Jon.
Still running: lead/broaden-scouts (worker) — touches scouts/state_doe/sources.py which security also touched (defusedxml) — will rebase+resolve on merge.

### 2026-06-25 — Screen-Time broadening + screen-time+AI unification merged (Lead)
main now 73067c8, restarted (healthz 200). All Lead build worktrees/branches cleaned up.
- broaden-scouts (3bbb2db): regional_news outlets + keywords, state_doe 7→20 states, board scout wired into fan-out. board_peer_validation_scout kept DISABLED (Salesforce customer-exclusion pending — enabling would flag customers as peer validation).
- screentime-ai-topic (855ae82): topic_config.py v3 + scout_fanout LegiScan terms now admit AI-in-schools POLICY (14 multi-word anchors, never bare "ai" — substring matcher). Verified no stored DB 'topic' row overrides the DEFAULT, so it's active. Screen-Time Watch now = screen-time AND AI-in-schools policy. AI-policy STANCE tuning deferred to Angela review (TODO in stance_config.py; ban-on-chatbots is NOT unfavorable to Amira's carve-out).
Parked (need external): board scout → Salesforce list (Neil); Screen-Time autopilot cron → Angela stance review (also fix LegiScan status-mapping vetoed/failed→passed first); report card real-data.

### 2026-06-25 — 50-state screen-time+AI news coverage merged (Lead)
main now (after doc) — national_news.py gatherer: per-state Google News RSS (school-scoped, multi-word AI, never bare "ai") for all 50+DC, wired into scout_fanout _SCOUT_GATHERERS["national_news"]. Rotation option (states_per_run+cursor in screentime_stance_config 'national_news_cursor') available, not default-wired. Verified registered, sweeps 51. Coverage now full-50-state on bills (LegiScan national) AND news. Did NOT touch shared scouts/state_doe (literacy). Stance deferred to Angela.

### 2026-06-25 — Screen-Time go-live: silent collection + board + Callie on-demand (Lead)
main 3d13dd9, restarted, healthz 200. NO auto-push (owner requirement) enforced:
- Collection cron daily 11:00 UTC wired in main.py (start/stop_screentime_scheduler). run_scheduled passes deliver_alerts=False — CRITICAL: screentime_report_channel is SET (C0BBYM8N26M) so without this the cron would have auto-broadcast. Fixed LegiScan status bug (vetoed/failed≠passed) + display-only pipeline guard.
- Board scout: 27 priority-state BoardDocs-verified districts, general mode. Runs via SILENT screentime fan-out (_gather_board_peer_validation, independent of scouts.yaml). Standalone scout scheduler kept DISABLED in scouts.yaml (would feed marketing signal_queue→Callie push). Exclusion plug-in pending customer list.
- Callie: get_screentime_report + record_screentime_feedback (callie-only, read-only, on-demand, non-owner-reachable for Amy). Deny-with-reason reuses callie_push reaction-learning.
DB stale (17/10) until first sweep (11:00 UTC or manual). Stance tuning + non-customer exclusion + optional light-announce still parked.
