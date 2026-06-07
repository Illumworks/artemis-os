# Solidity Sweep — consolidated findings + fix plan (2026-06-05)

Three parallel stress-tests (marketing pipeline, intelligence, floating Artemis) on the surfaces not
yet hammered. Cross-cutting pattern (same class as the already-fixed C1/C3/FA-P0/WS bugs):
**swallowed exceptions hiding dormant features, schema↔impl param mismatches, and divergent
gate-decision paths.** Memory + WS + FA-tool-path were freshly fixed; these are the rest.

## Ranked fix list

### P0 — Critical (core flows broken)
- **PIPE-1 — Slack Gate-2 approval is decorative for content drafts.** The Slack callback calls
  `_prepare_pipeline_resume` only (resumes the run) but NOT `_decide_content_draft_approval` — so
  deliverables stay `draft_ready`, the Approval row stays `pending`, workspace_state isn't recomputed;
  the run just goes `succeeded` with nothing processed. (`pipelines/routes.py:770`.) *Note: my earlier
  "Slack approve works" fix resumed the run but missed the content-draft side effects — this sweep
  caught the gap. All gate-decision paths (Slack callback, in-app decision, timeout) must funnel
  through the same decision logic.*
- **FA-1 — FA confirm/propose flow broken with a real LLM.** `resume_after_confirm` sends an orphaned
  `tool_result`: the assistant's tool_use message isn't persisted at suspend, and the tool_result uses
  a fresh UUID, not the model's ToolUseBlock.id → Anthropic 400. FakeAdapter tests masked it.
  (`chat.py:600-619, 868`.)
- **FA-5 — `propose_*` tools never persist.** propose_writing_rule/agent/workflow/skill/ruleset/fix all
  return JSON but write nothing to the DB → "confirmed" proposals don't land. (`tools/writing_rules.py`,
  `tools/builders.py`, `tools/marketing.py`, `tools/system.py`.) (FA-1 + FA-5 = the whole propose→confirm→persist chain is a facade.)

### P1 — High (broken tools / wrong numbers / dormant)
- **FA-2/3/4 — tool schema↔impl param mismatches** (permanently-broken tools): `query_memory` default
  `scope="all"` is an invalid scope kind; `fire_scout` schema `scout_id` vs impl `scout_type`;
  `submit_draft_for_review` schema `deliverable_id` vs impl `candidate_id`. **Audit ALL FA tools for
  this pattern.** (`tools/core.py:211`, `tools/marketing.py:453,416`.)
- **INT-1 — Momentum miscounts boundary signals** when `window_days % bucket_days != 0` (default 90/7
  always) → `delta_ratio` wrong by up to 6× (can invert "heating up"). The core Decision-1 number.
  (`trends.py:120-160`.)
- **INT-2 — Trend snapshots never persist** (same swallowed-exception class as C3): persist call uses
  invalid `scope_kind` `state`/`campaign_family` → ValidationError → swallowed. "Trends accumulate in
  memory" is dormant. Fix: add those scope kinds or map to valid ones; stop swallowing.
  (`initiation.py:495`, `intel_prioritization.py:274`, `memory/schemas.py`.)
- **PIPE-2 — Gate timeout never updates the Approval row.** auto_approve/auto_reject/escalation_timeout
  write node_states + resume but never call `decide_approval` → approvals stuck `pending` forever (stale
  UI + polluted monitoring). (`human_gate_executor.py:1125-1195`.)

### P2 — Medium
- **INT-3 — decisionHistory substring match** ("reject" matches "not rejected"/"no rejection concerns")
  → skewed priorApproves/priorRejects. (`initiation.py:383`.)
- **PIPE-5 — Concurrent-run signal mis-attribution:** `_qualified_signal_count_for_run` filters by
  `created_at >= run.created_at` not `pipeline_run_id = run.id` → overlapping runs steal each other's
  signal counts. (`executor.py:541`.)
- **PIPE-3 — Gate-card `districts` unsorted + brief_preview/body fallback ambiguity** → 2 RED tests on
  main (`test_gate_card_from_db.py`). Fixing greens main. (`human_gate_executor.py:399`.)
- **FA-7 — Mixed-layer tool responses drop earlier results** (query_memory + propose_agent → query
  result abandoned when the pending-confirmation BaseException propagates). (`agent/loop.py:117`.)
- **PIPE-4 — Rejected candidate can be re-initiated** (`initiate_campaign` checks only `initiated_at`,
  not `decision_state`). (`initiation.py:230`, `repository.py:437`.)

### P3 — Low / by-design / dormant / doc / infra
- INT-4 urgency_mix all-zero for unknown tier (inconsistent breakdown).
- FA-8 approve_signal carryover `fa_session_id` always "unknown". FA-6 list_content_assets stub.
  FA-9 `_get_voice_samples` dead code.
- PIPE-6 (DORMANT, blocked by flag) workspace recompute IllegalTransition on queued_for_send/sent —
  fix before enabling outbound.
- INT-5 `earliest_deadline_iso` proxy field name misleads; INT-6 deadline_source docstring mismatch.
- TEST-INFRA: parallel `TRUNCATE … CASCADE` deadlock → run tests `-p no:randomly` / serialized; fix the 2 red gate-card tests.

## Fix groups (mostly file-disjoint → parallelizable)

**Group A — Pipeline gate/approval unification** (PIPE-1,2,3,4,5): unify ALL gate-decision paths
(Slack callback, in-app decision, timeout) to run the same content-draft/signal decision processing +
update the Approval row; sort gate-card districts + fix brief fallback (greens 2 tests); guard
re-initiate on decision_state; scope the qualified-signal count by run_id. Files: `pipelines/routes.py`,
`node_executors/human_gate_executor.py`, `executor.py`, `marketing/routes/initiation.py`, `approvals.py`.

**Group B — Floating Artemis tools** (FA-1,2,3,4,5,7,8,6,9): fix the confirm-resume protocol (persist
assistant tool_use at suspend, use the model's tool_use id); make `propose_*` actually persist; align
EVERY tool's schema with its impl (audit all); fix mixed-layer result loss + the minor stubs/dead code.
Files: `floating_artemis/chat.py`, `floating_artemis/tools/*`, `agent/loop.py`.

**Group C — Intelligence correctness** (INT-1,2,3,4,5,6): fix the momentum boundary math; make trend
snapshots persist (valid scope kinds, no swallow); word-boundary/structured decisionHistory; urgency_mix
consistency; deadline field naming + docstring. Files: `marketing/intel/*`, `marketing/routes/initiation.py`
(trend-enrichment part — coordinate with Group A's initiation edits), `intel_prioritization.py`.

A + C both touch `initiation.py` (A: initiate/dispatch; C: trend-enrichment/persist) — keep disjoint or
merge A first. Each group: own worktree, own branch, unit tests + live verify, back to app Opus to merge.

---

## Group D — Personal Workspace + initial-load (audit 2026-06-05)

Same class of "looks done, silently 404s/dormant" — concentrated in frontend→backend path mismatches
and missing backend routes.

### P0 — the initial-load hang (ROOT CAUSE FOUND)
- **IL1 — boot forces `view='chat'` when `artemis-cwd` is in localStorage** (`home.js:303-304`). Any
  user who's opened a Dev Project has `artemis-cwd` set, so on every load the boot overrides the hash
  route + the persisted view and lands on the chat empty state — and clicking the rail then re-sets the
  view and loads it from cache (exactly the reported symptom). Fix: only default to chat when there's no
  valid hash/persisted view; don't let `artemis-cwd` override an explicit/persisted view. Small, high-value.

### P1 — broken writes (frontend↔backend path mismatches + missing routes)
- **C1 — Calendar mutations 404.** create/update/delete/RSVP call routes that don't exist
  (`PATCH/DELETE/POST /api/calendar/event...`, `/respond`). The GCalClient HAS the methods; the HTTP
  routes were never exposed. Every calendar save/delete/RSVP/drag-reschedule fails. (`routes/calendar.py`,
  `calendar-event-drawer.js`, `calendar-new-event-modal.js`.)
- **O1 — OKR writes broken (Jon APPROVED 2026-06-05; HARD REQUIREMENT: zero OKR data loss).** 13 OKR
  endpoints the frontend calls don't match the backend; the two PRIMARY writes are pure path mismatches:
  KR save calls `POST /api/okr/kr/{id}/update` but backend is `PATCH /api/okr/key-results/{id}`; activity
  log calls `POST /api/okr/log-activity` but backend is `POST /api/okr/activity`. Fix = align the
  frontend api.js paths to the EXISTING backend routes (+ add any genuinely-missing AI endpoints).
  ⚠ **GUARDRAIL: read existing OKR rows only — NO migration, reset, reseed, or schema change to any OKR
  table. Existing saved progress must be preserved exactly.** Verify post-fix that prior OKR data is
  intact + a save/log now persists.
- **M-ACTIONS — meeting action items never populated (Jon-reported: actions don't show/happen,
  transcripts do).** Meeting detail returns `row.action_items or []` (`routes/meetings.py:237`) but
  nothing extracts action items from the transcript/summary into that column, so the actions tab is
  empty and the jira/okr/slack/todo routing buttons have nothing to act on. There's a dual-path smell:
  `home.js:2927` does best-effort client-side extraction (Granola path) while `meetings.js` reads the
  server `action_items`. Trace where action_items SHOULD be extracted (the summary lazy-backfill /
  Granola sync) and wire it; reconcile the two paths. Also: created todos are invisible (M1) — surface
  them. (`routes/meetings.py`, `meetings.js`, `home.js`.)
- **C2 — attendee autocomplete always empty:** `searchContactsApi` calls missing
  `/api/google/contacts/search`; should be `/api/people/search` (exists). (`api.js:354`.)

### P2 — robustness + dormant
- **F2 — `loadStatus()` has no error guard** (`status.js:6`); a non-200/non-JSON at boot kills the whole
  module import → blank app. Add try/catch.
- **M1 — personal todos are created (meeting actions) but never displayed** — no UI calls `GET /api/todos`.
- **C3 — GCal token refresh not persisted** per-request (perf, extra round-trip on expiry).

### P3 — dead code / stubs
- F1/IL2 — `#primary-nav`/`#secondary-nav` don't exist in HTML → `renderNav()`/`handleNavClick` in
  home.js are permanently unreachable dead weight (the real nav is the rail in artemis-shell.js).
- M2/M3/J1 — exported-but-unused `disconnect*Api`, `fetchGranolaOverviewApi`, missing jira oauth-disconnect.

**Group D fix scope:** mostly frontend (`api.js` path alignment, `home.js` boot fix, `status.js` guard) +
a few backend routes (calendar mutations expose the existing GCalClient methods; optionally a todos UI).
Disjoint from Groups A/B/C (which are pipeline/FA/intel). **OKR (O1) carved out pending Jon's sign-off.**
