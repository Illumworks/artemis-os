# Lead Session Log

**Living doc. Updated after every meaningful exchange.**
**Last updated:** 2026-06-05 (marketing pipeline made fully working end-to-end; mid clean-run + planning review)

## CURRENT STATE (continuity — read this first)

**Marketing pipeline: now alive end-to-end** after a long test→fix→retest chain (all merged to main):
- Qualifier gap fixed (scouts qualify on write) → faithful per-family `josh_spec_v1` rulesets seeded
  (derived from `decisions/campaign-signal-spec-v1.md`) → `signal_status='qualified'` fit-gated (means
  passed min_fit, not just scored) → targeting defaults to signal's state (FL→FL, not all 1903) →
  scout MCP tool-deferral fixed (MCP_CONNECTION_NONBLOCKING=false) → pipeline tolerates a failed scout
  (continue_on_failure). Real campaign #7 banked (Florida dyslexia). Initiation modal closeable + no
  [object Object]. Scout cadence 4h→daily.
- **Pending app restart + re-seed** (do after the in-flight clean re-run `6b28ca77` finishes): restart
  app to load (a) new api.data.gov SAM_API_KEY Jon added, (b) cadence change, (c) resilience executor
  code; then `seed_marketing_pipeline()` to apply continue_on_failure to the live marketing.main def.
- **In flight:** clean re-run `6b28ca77` (monitored, bv8e2y8d2) — when at Gate-1, Lead drives the
  campaign (approve→initiate→verify).

**Planning review (terminal opus) — IN PROGRESS:** 3 workstreams (Memory UI redesign, App-wide Cost page,
Provider routing). Entry: `briefs/opus-lead-review-handoff.md`. Lead validating + answering 4 decision
asks + spawning the "defensive fix bundle" as Worker #1 (fixes 5 latent bugs incl. graph_extractor empty-
key = the empty-memory-graph mystery; 2 broken anthropic agents we independently found). After review,
terminal opus delegates the worker briefs while Jon+Lead plan other things.

**Open (tracked):** Writing Studio folder-delete no-op; initiation-modal loading skeleton; Campaigns-page
UI (blank-state/layout) vs #7; §4.1 hard-skips + stacked-signal boost (Phase 3); board_minutes 403 UA;
Starbridge key; AI Marketing Strategist + CRM (roadmap: docs/marketing-qualification-rulesets-and-strategist.md).

---


---

## 2026-06-06 (cont.16) — Campaign-UI modal polish + cost/routing go-no-go

**Campaign initiation modal — Lead-built, merged to main (c781fe7, 31bc01e), live-verified on #15:**
- Made it an app-level modal matching the Jira detail "drawer" Jon prefers: warm blurred backdrop,
  scale-in, sticky header + sticky footer, centered 960px card.
- **Portaled the backdrop to `<body>`** so it escapes `.canvas-scroll`'s gradient mask (a CSS mask
  establishes a containing block for `position:fixed` children → backdrop was trapped in the content
  pane). Now dims the WHOLE app incl. sidebar. The gradient mask Jon wants kept on containers is untouched.
- Fixed a pre-existing double-backdrop (success path emitted its own backdrop inside the created one →
  double dim + trapped backdrop-clicks). Single backdrop in all states now.
- **Regression I introduced + fixed same session:** portaling broke the Review button — switch-campaign
  and Review handlers cleared the modal via `container.querySelector` but it's on `<body>` now → stale
  modal survived, duplicate-guard blocked reopen. Fixed: document-wide clear in both handlers + one-time
  `hashchange` listener that removes an orphaned modal when leaving the campaigns route.
- **Target-scope state picker:** replaced the native `<select multiple>` (unreadable — 54 states
  scrolling, no obvious selection/toggle; an MI-only campaign *looked* like "all states selected") with
  clickable toggle chips. Default state accent-highlighted, one click toggles, district count updates
  live. The server-side default (`states=[campaign state]`) was already correct — this is legibility only.
  Verified: #15 shows MI alone @ 52 districts; add OH → 113 → remove → 52.
- NOTE (not fixed, pre-existing, out of scope): the All-districts/Specific-states/By-tier radio dots
  render pushed to center-right of their labels — minor alignment oddity to clean up in the redesign.

**Cost-page Phase 1 + Provider-routing Phase R — Lead go/no-go = GO on both, one guardrail.**
- Terminal opus built both (cost tracking foundation; provider-routing self-service page). Both delivered
  what their briefs specced; the LOC "overage" is tests + the specced full UI page, not bloat. Lossless
  respected on cost_events (append-only) + both routing tables (DELETE flips active=false).
- Smart backwards-compat call by the Worker: kept sync `resolve_adapter` byte-for-byte, added
  `resolve_adapter_async()`. **Consequence:** overrides don't fire until call sites migrate to async —
  the Routing page Apply buttons write a row but the next call ignores it until Phase 3.
- **Guardrail (the silent-no-op trap = exactly the class of bug we keep fighting):** until Phase 3 wires
  the new resolver into the ~4-5 high-value call sites (memory consolidator/graph_extractor, trajectory
  summarizer, brief_assembler, scout_runner; ~50 LOC, folded into Phase 3), the Apply buttons MUST show a
  "takes effect after Phase 3" state. Then terminal: pre-merge checks → merge Phase 1 first → renumber
  Phase R to 0067 (rename + revision edit staged together per CLAUDE.md gotcha) → browser-smoke both.
- Lead verifies no coordination damage after (as done for the gate-1 UI merge). Cost-phase-1 already
  shows merged in log (2f37406 + lead drop-unused-import 8b03a90).

## 2026-06-05 (cont.4) — Qualifier gap FIXED + merged + live-verified; ruleset-stub finding

- Merged `worker/qualifier-scout-signals` (a884835): scout signals now qualify on write (shared
  run_and_store_qualification called from tool + scout_runner, best-effort). 38 tests, ruff/mypy clean.
- Live-verified: caught + fixed 2 backfill-script bugs (settings.database_url→db_url; self-bootstrap
  sys.path) — worker's tests passed but never ran the script. Backfilled 156 pending → qualified.
  signal_status counts: pending 156→0, qualified 43→199.
- "qualified" = scored (transition is unconditional on success; fit ranking is downstream at gate) —
  matches the original route design, NOT a new bug.
- ⚠️ **REAL remaining blocker (domain config, Jon's call):** the only ACTIVE ruleset is `general_growth /
  smoke-1` with **0 weighted_signals + 0 hard_filters** → every signal scores rawScore 0.0,
  passesMinFitScore=false, but is still marked "qualified". Meaningful qualification/ranking/campaign
  proposals need a REAL active ruleset (weighted signals + hard filters per family). Rulesets are domain
  rules — surface to Jon, don't author unilaterally.
- Path is now mechanically unblocked: 199 qualified signals available in the Signals Inbox.

## 2026-06-05 (cont.3) — Campaigns cleared + qualifier gap ROOT-CAUSED

- Cleared all 6 test campaigns (backed up `.backups/campaigns-backup-2026-06-05.sql`; cascade clean;
  pipeline_runs preserved via SET NULL). Cancelled stale June-3 marketing.main run.
- Ran fresh marketing.main (1211d667) start→finish: all 9 scouts + 2 qualifiers succeeded, but
  gate_1 SKIPPED ("upstream produced no signals") → NO campaign. 14 fresh scout signals left at
  `pending_qualification`, never scored.
- **ROOT CAUSE (P0):** deterministic qualification (`qualify_signal`/`_run_and_store_qualification`)
  is route-bound (routes/signal_queue.py:502, called at intake L198 + qualify L282). Scouts DON'T use
  routes — they persist via `tools/signal_queue.py:172` (signal_queue.write agent tool) and
  `scout_runner.py:281`, BOTH of which create `pending_qualification` and never qualify. So scout
  signals never score → never qualify → never reach Gate-1 → no campaign EVER from fresh scouting.
  Qualifier pipeline nodes are LLM agent_invocations (compose briefs); they don't run the scorer either.
- Fix briefed: `briefs/qualifier-gap-scout-signals-unqualified.md` — extract shared
  run_and_store_qualification; call from both scout paths (best-effort, non-fatal); backfill the 149
  pending signals. No schema change, lossless.
- Campaigns UI items (blank-state robustness + layout) still pending — deferred until a real campaign
  exists to verify against (blocked by this qualifier gap).

## 2026-06-05 (cont.5) — Faithful rulesets SEEDED + live; scoring works

- Merged `worker/seed-josh-rulesets` (a617147) + Lead fix `_derive_weight` peak→FIRST/headline tier
  (VENDOR_DISSATISFACTION/PROCUREMENT_ELA_ADOPTION/DISTRICT_PROFICIENCY_GAP corrected 0.9→0.6; "standard;
  hot if X" is standard-by-default, the "hot if" is a Phase-3 boost). 27 tests, ruff/mypy clean.
- Applied to LIVE (artemis_os): 5 per-family rulesets active (obc 5, dyslexia 3, hit 3, general_growth 3,
  biliteracy 1), territory_config seeded (FL/IN/MD/MO/IL/TX standard), smoke-1 archived. Re-scored 257.
- **Scoring verified meaningful + faithful:** 204/257 signals pass min_fit in ≥1 family; 133 at 0.90;
  per-family routing correct (LEADER_TRANSITION_FORMAL → general_growth 0.9, all other families 0.0); 53 at
  0.0 carry the 4 reason codes Josh didn't route to families (faithful — not a bug). Caught + discarded my
  own buggy distribution query (read scores[0]=biliteracy, not max) before reporting a false finding.
- **Open next-steps:** (a) "qualified" status = scored, NOT = passed-fit (0.0 signals still status=qualified)
  — verify the campaign gate selects signals on fitScore≥0.5/passesMinFitScore, not bare status, so it
  proposes from the 204 real ones not the 53 zeros. (b) Should the 4 unmapped codes route somewhere?
  (refinement, Jon+Josh). (c) §4.1 hard-skips + stacked-signal boost = Phase 3.

## 2026-06-05 (cont.6) — "qualified" now means passed-fit; scout→campaign loop closed

- Merged `worker/qualified-means-passed` (fdfda30): fit-gate the pending→qualified transition (any family
  passes min_fit); demote qualified→pending on re-score if below threshold (added the state_machine edge).
  67 focused + 790 marketing tests pass; ruff/mypy clean.
- Applied to LIVE + app restarted: re-score demoted 39 wrongly-qualified signals (qualified 199→160).
  Load-bearing check: 0 `qualified` signals fail fit — status now honest. Gate-1/inbox/proposal (all
  status-based) are correct at the source; 0.0 signals no longer reach the gate or a campaign.
- **Scout→campaign loop now mechanically closed:** scouts qualify on write (fit-gated) → 160 genuinely-
  qualified signals → Gate-1 selects them. A fresh marketing.main should now propose a campaign; or
  initiate from an inbox signal.
- Marketing qualification chain status: gap fixed → faithful rulesets seeded → fit-gated status. DONE.
- Remaining (Phase 3 / later): §4.1 district hard-skips (HMH/single-school/<5000) + stacked-signal boost
  in qualifier logic; the 4 unmapped reason codes (route or leave); ruleset-editor UI; AI Strategist; CRM.

## 2026-06-05 (cont.7) — Real campaign banked; scouts = next blocker (tool invocation)

- Retest #2 (fresh marketing.main 7679fe7e): ran CLEAN (12 nodes succeeded, no crashes), gate_1 correctly
  SKIPPED (now because genuinely 0 signals, not the old false-skip). BUT 0 signals because SCOUTS are
  BLOCKED at tool level: "artemis MCP server tools are deferred and not yet fully connected for invocation"
  (legislative/federal_funding/state_doe), "SAM.gov API key not configured" (procurement), "403 CloudFront"
  (board_minutes), prompt-grounding confusion (starbridge). NOTE: scheduler run 9h earlier made 9 signals;
  this run fired ~6s post-app-restart → restart-timing hypothesis (MCP not connected yet) vs real wiring bug.
- **Real campaign BANKED (deterministic, no scouts):** approved qualified signal 211 (Florida S1340 dyslexia
  screening mandate, fit 0.9) → campaign candidate #7 (human_gate_1, in_inbox, unnamed). Surfaces in
  /api/marketing/campaigns. Signal→campaign loop proven end-to-end with real data. #7 is the pending-init
  state Jon flagged as "messed up" → use it for the Campaigns-UI fixes.
- Fired read-only scout-tool investigation (restart-timing vs MCP-wiring bug; cheap per cost focus).
- Next: (1) scout-tool diagnosis → fix (top of funnel — no scouts, no signals); (2) Campaigns-page UI
  (blank-state + layout) against #7; (3) provider-routing audit (terminal opus).

## 2026-06-05 (cont.8) — Scout-tool blocker DIAGNOSED (claude-code tool deferral)

- Root cause (high confidence): scouts emit 0 because claude-code presents the per-run Artemis MCP tools in
  a DEFERRED catalog; scout prompt doesn't fetch/load them → scout narrates "tools deferred and not yet
  fully connected for invocation" → gives up. Phrase is NOT in our code (it's the LLM describing CC
  deferral). Reproduced in isolation (scout_cli legislative → emitted 0). Per-run MCP subprocess
  (adapter.py:267-399) → NOT app-restart timing. Intermittent (scheduler run made 9 → tools eager then).
- Brief: `briefs/scout-mcp-tool-deferral.md` — find/fix: force tools eager (CLI flag/setting), or prompt
  scouts to fetch deferred tools, or scope per-scout tool lists below the deferral threshold. + secondary
  scout gaps logged (SAM.gov key, board_minutes 403, starbridge prompt-grounding). Verify: single scout
  invokes tools + emits, reliably.
- Ties into the provider-routing audit (claude-code tool-passing reliability).

## 2026-06-05 (cont.9) — Initiation targeting default fixed (FL signal → FL, not all 1903)

- Jon caught: campaign #7 (FL signal) targeting defaulted to All districts (1903). Root: state-narrowing
  gated on resolved_district_id, ignoring the signal's own state. Dual/triple-path smell — fixed:
  `_build_district_context` (modal source) + a serve-time override of an LLM proposal's all_districts
  scope + `brief_assembler.build_campaign_initiation_context` (other consumer). Targeting geography is now
  deterministic from the signal, not an LLM choice. Verified live: #7 → FL, 47 districts (was 1903).
  ruff/mypy clean, 7 ci2 tests pass. Committed.
- Scout-MCP-deferral fix worker fired (worker/scout-mcp-deferral, Sonnet, worktree) — Lead does live verify.
- OPEN (UI polish, Campaigns-UI work): the initiation modal shows a BLACK overlay with no info while the
  context loads (no skeleton/spinner) until data arrives — Jon flagged. Lower priority.

## 2026-06-05 (cont.10) — Scout tool-deferral FIXED + VERIFIED LIVE; marketing pipeline fully unblocked

- Merged `worker/scout-mcp-deferral` (51bc2875): root cause = claude-code MCP_CONNECTION_NONBLOCKING
  (default on) → async MCP handshake → deferred tool catalog if first turn fires before connect. Fix:
  MCP_CONNECTION_NONBLOCKING=false (blocking) + --no-session-persistence (valid flag, confirmed) + scout
  prompt fallback. 22 tests pass, ruff/mypy clean.
- **VERIFIED LIVE:** legislative scout run 05e94c4b invoked legiscan.search ×43 + get_bill ×18 (+ territory/
  reason-code lookups) — tools fully connected, ZERO "deferred" narration. emitted 0 = nothing NEW qualified
  this cycle (legitimate; already had 20 legislative signals). Top-of-funnel blocker RESOLVED.
- **Marketing pipeline now alive end-to-end:** scouts invoke tools → qualify (fit-gated) → Gate-1 (selects
  fit-passers) → campaign. Fresh auto-proposals need new qualifying content (+ optional keys to widen).
- Open scout coverage (config, not bugs): SAM_API_KEY (procurement), STARBRIDGE_API_KEY (starbridge),
  board_minutes browser User-Agent (403s). Plus: Campaigns-UI (blank-state/layout/modal-loading) vs #7,
  provider-routing audit (terminal opus), CRM conversation (roadmap).

## 2026-06-05 (cont.11) — Clean-run test: SAM key wrong + scout-failure-nukes-run bug

- Clean full marketing.main run (5e445a83) FAILED: 7 scouts succeeded + 3 qualified signals, but
  scout_procurement HUNG 900s (ClaudeCodeTimeoutError) → failed the WHOLE run. Cause: the SAM key
  (SAM- prefix) is the WRONG type — procurement attempted the real SAM.gov call which auth-failed +
  LLM-retry-looped to the 900s CLI timeout. (Before the key, procurement stubbed instantly.)
- SAM key answer for Jon: needs a free **api.data.gov** key (https://api.data.gov/signup/, ~40-char
  unprefixed) — also powers Federal Register + Grants.gov. The SAM- key is a SAM.gov system credential,
  wrong for the api.sam.gov opportunities endpoint.
- ACTIONS: blanked SAM_API_KEY in .env (backed up .env first; procurement stubs fast again) + restarted
  app + triggered clean re-run 6b28ca77 (monitored).
- Resilience fix MERGED (8d67c6b, 27 tests): scout nodes continue_on_failure → one flaky scout no longer
  fails the run; gate fan-in treats tolerated failures as done. NEEDS re-seed (seed_marketing_pipeline)
  + app restart to apply continue_on_failure to the LIVE marketing.main def — do AFTER the current re-run
  finishes (don't restart the app mid-run).

## 2026-06-05 (cont.12) — Clean run reached Gate-1 (5 qualified!); defensive bundle merged; Gate-1→campaign gap

- Clean re-run 6b28ca77 (SAM blanked): procurement SUCCEEDED (no hang), scouts → 5 qualified signals,
  gate_1 SUSPENDED + Slack card. The auto path works scout→qualify→Gate-1. ✅
- Approved Gate-1 (resume) → run resumed → `content_brief_assembler` FAILED: "requires exactly one
  uninitiated candidate for pipeline run; found 0". ROOT: the pipeline Gate-1 (signal_brief) approval
  resumes but never promotes signals→candidate; promotion only lives in the MANUAL /api/signal-queue/{id}
  /approve path (cluster_or_create_candidate). Divergent gate paths — same class as Group-A PIPE-1.
  Brief: `briefs/gate1-approval-promotes-candidate.md` (funnel both paths through one promotion fn,
  candidate linked to pipeline_run_id). This is the LAST gap to a fully-auto campaign.
- Defensive fix bundle MERGED (Worker #1, migration 0065 applied): agents 1/2/172 repointed to claude-code
  + fallback; graph_extractor/workflow_executor/spawn_subagent route via resolve_adapter; codex symlinked.
  753 tests pass. Graph extractor empty-key failure FIXED (obs 224 moved NULL→pending, attempting via
  claude-code — no more silent empty-key bypass). Full graph population = the 238-obs backfill follow-on
  (async retries need an in-app event loop; standalone invocation can't complete them).
- Manual approve→campaign path works (#7 banked). Per-family rulesets/qualifier all live.

## 2026-06-05 (cont.13) — Gate-1 fix merged; signal-cluster model confirmed; operator-selection brief for terminal opus

- Gate-1→candidate promotion MERGED (0719a34): shared promote_signal_to_candidate unifies manual + pipeline
  paths. Live test on run 6b28ca77's 5 real signals → promoted, but 5 DIFFERENT districts → 5 candidates,
  while content_brief_assembler wants "exactly one per run". Surfaced a DESIGN fork → Jon decided.
- **Signal-cluster model (confirmed for Jon):** many signals → one campaign via campaign_candidate_signals
  (primary + corroborating). cluster_or_create_candidate auto-clusters same district+family within a window;
  unrelated → separate campaigns. Campaign card shows "N signals"; initiation modal shows the Signal Cluster.
  Inbox currently shows signals FLAT (grouping = a new enhancement).
- **Jon's locked decision:** Gate-1 = OPERATOR PICKS at the gate (interim; "many per cluster" is the model),
  HYBRID: system visually flags the STRONGEST cluster (border + glow) as suggested; related signals grouped
  in the inbox. content_brief_assembler must handle the operator's selection, not rigid "exactly one".
- Brief written: `briefs/gate1-operator-selection-suggested-cluster.md` → **for terminal opus to delegate**.
- **Clean slate:** cleared all campaign candidates (#7, 9-13), reverted 7 signals approved→qualified
  (187 qualified now). Backed up `.backups/campaigns-backup-cleanslate-2026-06-05.sql`.
- App restarted with ALL fixes live (new SAM key, daily cadence, resilience, gate-1 promotion) + pipeline
  re-seeded (continue_on_failure on scouts).

## Campaign UI roadmap (Jon + Opus Lead own this — NOT terminal opus)

Full brief: `briefs/gate1-operator-selection-suggested-cluster.md`. Interim usability now (get it usable);
full visual redesign is LATER (after the whole-app debug pass — lower priority). Pieces:
1. **DONE (terminal, merged + Lead-verified):** Gate-1 cluster grouping + suggested-strongest (amber
   border/glow/⚡ chip) + operator-selected promotion + multi-candidate assembler.
2. **Interim usability (Jon+Lead, next):** (a) initiation OVERLAY → reuse Jira modal styling
   (`.jira-modal-backdrop`/`.jira-modal`), remove the gradient mask ONLY on the pop-up — KEEP it on
   light-bg containers (Jon likes it there); (b) campaign detail: fix visual hierarchy, invisible buttons
   (same color as bg), text padding, white-text-on-light-bg contrast; (c) add a **"Signals" tab** showing
   the attached cluster (primary + corroborating).
3. **Future (roadmap, post-redesign):** campaign lineage / related-campaigns + "start from / clone (reuse
   assets)" — foundation EXISTS (`predecessor_id` + predecessor-asset fetch); needs UI surfacing + a clone
   action.
Verify against real campaign #15 (Grosse Pointe Schools Leadership Transition). Browser-smoke; Jon reviews colors.

## Terminal-opus delegation queue (post review) — PRIORITY ORDER (Jon 2026-06-05)

Worker #1 (defensive bundle) DONE+merged. Split (Jon 2026-06-05):
- **Campaign UI** (`gate1-operator-selection-suggested-cluster.md`) → **Jon + Opus Lead handle this — NOT
  terminal opus.**
- **Terminal opus** owns: Cost page (Phases 1–3) then Memory UI, in this order:
1. **Cost page — Phases 1–3 ONLY** (Lead APPROVED the plan w/ right-sizing): P1 cost_events foundation ∥
   Phase R routing-control-surface → P2 visibility dashboard → P3 routing opportunities. DEFER P4 cloud-infra
   / P5 forecast / P6 alerts+budgets until after 1–3 land + Jon decides. UX guard: make the synthetic-API-
   cost-vs-flat-subscription framing unmistakable in the hero.
3. **Memory UI** — lower priority (Jon: "more for me"), last.
Plus standalone: graph-extraction backfill (238 obs); rewrite cost-prereq into "seed overrides" after Phase R.
Lead spot-checks each per-phase brief just-in-time before its worker spawns; browser-smoke every merge.

## 2026-06-05 (cont.14) — Terminal opus shipped Gate-1 operator-selection UI (its current task); Lead verified

- Terminal opus was mid-task on `gate1-operator-selection-suggested-cluster.md` (its current task) and
  finished it — merged 3 commits CLEANLY on top of Lead's main (400385a/62f2c16/da822fa, layered on
  40283ea; no conflict). 31 tests pass. (So the Campaign-UI Jon+Lead "owned" — the gate-1 cluster part —
  is done; the INTERIM-USABILITY part below is still Jon+Lead's.)
- **Lead VISUALLY verified** (Claude_Preview screenshot, current code): Approval Queue → Gate-1 card shows
  7 cluster cards; Grosse Pointe (MI) has the amber border + "⚡ Strongest signal" chip (score 65%, 2
  stacked signals), primary+corroborating grouped; others plain at 60%, not suggested. Amber doesn't clash.
  Terminal's open question (pixels) = ANSWERED, looks good.
- Lead false-alarm corrected: my "/api/approvals/38 → 0 clusters" was a wrong JSON key in my probe
  (clusters live at pipe4Context.context.clusters → 7 intact). Run b6821d3e's 8 qualified signals intact.
  NO coordination damage; terminal built cleanly on main. (Two Leads on one repo+DB is a theoretical risk
  but caused no damage here.)
- STILL Jon+Lead's (interim usability, separate from terminal's cluster work): the dark campaign INITIATION
  overlay (→ reuse Jira lightbox colors) + Campaigns-page alignment/padding/margins. Captured in the brief.
- Terminal's next task = the COST PAGE (Phases 1-3) per the prompt Jon is sending it.

## 2026-06-05 (cont.15) — MARKETING PIPELINE FULLY WORKING END-TO-END (verified live)

- Drove the full chain live on run b6821d3e: approved the SUGGESTED Grosse Pointe cluster via
  POST /api/approvals/38/decision {decision:approved, selectedClusterKeys:["5314|general_growth"]} →
  run resumed → content_brief_assembler SUCCEEDED ("Proposed campaign initiation for candidate 15:
  Grosse Pointe Schools Leadership Transition") → run status=succeeded. Campaign #15 created
  (human_gate_1/in_inbox, proposed, awaiting initiation).
- **Net: scout→qualify→Gate-1(cluster+suggested)→operator approves cluster→campaign proposed→brief
  assembled→run completes ALL WORK.** The content_brief_assembler "found 0/found 5" failures are gone
  (terminal's Worker B multi-candidate fix + my gate-1 promotion + the operator-selection together).
- Real campaign #15 ("Grosse Pointe Schools Leadership Transition") is on the Campaigns page → ready for
  the Campaign-UI interim smoke test (dark overlay → Jira colors + alignment).
- App on :8000 restarted to current code (all merges live).

## Backlog (noted, NOT priority — Jon flagged)

- **Writing Studio: deleting folders does nothing** (Jon, 2026-06-05) → empty folders accumulate and
  create a mess. The delete-folder action is a no-op (404/unwired or doesn't persist). Investigate the
  WS folder delete path (frontend action → backend route) + ensure empty folders can actually be removed.
- **Initiation modal: black-overlay-while-loading** (no skeleton/spinner before context loads) — UI polish.

- **Memory page visualization** (2026-06-05): the Memory page needs a good way to VISUALIZE the
  memory store (drawers/observations/graph). Currently not working/empty. Design task — deferred,
  not a priority now. Revisit during the UI pass.

## 2026-06-05 (cont.2) — Both agent briefs landed, verified + merged

- **Run-metrics** (`worker/agent-run-metrics` 7b30e37 → merged): /api/stats/agent-metrics now aggregates
  agent_runs. Live-verified vs DB: board_minutes 74 runs/74 succ/$2.71; overview total_runs=854.
  Browser-verified the RUN HEALTH card renders (RUNS 74 / SUCCESS 100% / AVG COST $0.04 / LAST RUN 1h).
  Lead caught + fixed: (a) 4 ruff errors in the new test (agent ran ruff on stats.py only) — ccbff44;
  (b) avg_duration unit bug — endpoint emitted seconds but formatDuration() expects ms → '89.76ms';
  fixed to *1000 → card now '2m' (95bb869, browser-verified).
- **Builder blueprint** (`worker/agent-builder-blueprint-fields` b6a00ed → merged): propose schema +
  _commit_agent (CREATE + lossless partial UPDATE) + system prompt now author all blueprint fields
  (urgency_tiers as OBJECT). Tests assert CREATE round-trip + lossless partial-update on main (4 pass).
- Both used own worktree + own test DB (artemis_test_metrics / artemis_test_builder) — no contamination,
  no shared-DB contention. Disjoint files → clean merges. ruff + mypy clean; app boots (363 routes).
- Agent surface now: blueprint shows, roster auto-loads, run-health real, builder can author blueprints.
  Remaining: lifecycle_status still empty in DB (builder can now set it going forward); test-isolation
  chore still queued; calendar click-confirm still pending Jon.

## 2026-06-05 (cont.) — Agent profile: 3 bugs, 2 FIXED+browser-verified, 1 briefed

Browser-verified via Claude_Preview (serverId-driven eval/screenshot) — NOT code-reasoning alone.
My first guess (enriched id/agentId match) was real but NOT the cause of the empty blueprint.
Actual causes:
- **Blueprint empty (FIXED, commit on main):** buildAgentProfile sourced blueprint from `config`
  (= `draft||enriched||agent`); getAgentDraft() runs every render and builds a draft that OMITS all
  blueprint fields → config.blueprint = undefined → "Not specified". Persona rendered because it reads
  `enriched ?? agent` directly. Fix: source read-only blueprint from `runtime = enriched ?? agent`.
- **Roster load-hang (FIXED, same commit):** renderOperationsView never triggered refreshAgentsFromApi
  on mount → agents view hung on "Loading" until a rail-click handler fetched. Fix: kick the fetch on
  first mount (guarded) for the agents view. (This is the operations-view half of the original IL1
  symptom; Group D fixed the home.js boot-view half.)
- **Runs/health = 0 (BRIEFED, not a display bug):** `/api/stats/agent-metrics` (stats.py:153) is a
  literal stub returning empty — never wired to agent_runs (which has 74–108 runs/agent). Brief:
  `briefs/agent-run-metrics-endpoint.md`.
- Verified live (no manual click): roster auto-loads; Board Minutes Scout blueprint shows Cadence
  "Every 1 day" + Inputs + Urgency; only genuinely-null lifecycle_status stays "Not specified".
- Builder write-gap brief still stands: `briefs/agent-builder-blueprint-fields.md`.

## 2026-06-05 — Agent profile "Not specified" = display bug (data safe) + builder write-gap

- Symptom: agent profile shows Operating Blueprint all "Not specified" + system prompt not surfaced.
- **Data verified intact (DB + API wire):** Legislative Scout has system_prompt(2144), cadence(86400),
  urgency_tiers(object), failure_modes, inputs_required, db_tables_touched, implementation_notes(1685).
  17–18/20 agents similarly populated. Only lifecycle_status genuinely null.
- **Root cause #1 (display):** operations-shell.js fetched/matched enriched detail by NUMERIC `id`
  (`selectedAgent.id`, line 1848–1849) but `/api/agents/{id}` resolves only the string `agentId`; the
  L829 guard `(_enrichedAgent.agentId||id)===agent.id` then failed → enriched discarded → profile fell
  back to thin cached object → "Not specified" everywhere. FIXED: load+match by agentId (id fallbacks).
  node --check OK. Awaiting Jon hard-refresh visual confirm.
- **Root cause #2 (builder write-gap, the requested audit):** PROPOSE_AGENT schema (agent_builder.py)
  + `_commit_agent` (engine.py) only handle 8 core fields — builder cannot author/edit any blueprint
  field (cadence/urgency/failure_modes/inputs/db_tables/impl_notes/lifecycle/persona/output_contract).
  Existing agents got blueprints from seeding, not the builder. Brief written:
  `briefs/agent-builder-blueprint-fields.md` (schema + commit + **prompt** + proposal-model + live verify).
- **Process note:** Explore subagent's final verdict ("fields are NULL, pure write-path gap") was WRONG
  — it inferred from builder code without checking DB/wire. Verified against data; it's a display bug.
  Reinforces: verify the EFFECT, never trust an unchecked inference.

## 2026-06-05 — Group D merged (Personal/OKR/meetings/calendar/load) — live-verified data-safe

- Merged `1e35e85` onto main (D commit `4465dce`, worker/solidity-personal). Conflict-free —
  D's files (calendar.py, meetings.py, okr.py, api.js, status.js, home.js) are disjoint from A/B/C.
- **No migrations / no models / no schema change** touched → OKR data-loss structurally impossible.
- **LIVE-verified against artemis_os (live DB):** OKR objectives read returns Jon's real data;
  row counts intact 4 obj / 20 KR / 30 activity BEFORE and AFTER; KR PATCH no-op returned 200 +
  count unchanged; activity-log POST persisted (30→31) then test row reverted (→30). O1 ✅ data-safe.
- C2 `/api/people/search` returns real contacts live ✅; `/api/todos` responds ✅; IL1 boot-order
  logic reviewed (explicit/persisted route now wins; savedProject is fallback) ✅; F2 guard added.
- **One item NOT fully live-exercised:** C1 calendar create/edit/RSVP needs a real Google OAuth
  round-trip — routes registered + GCalClient-backed + no destructive DB SQL (502s on failure,
  can't corrupt). Flagged to Jon to click-confirm next time he's in Calendar.
- Test-isolation chore brief written: `briefs/test-isolation-shared-db.md` (fire after solidity tail).

## 2026-06-05 — Solidity Groups A/B/C merged (with contamination caveat)

- Merged onto `main` (safety tag `pre-solidity-merge` = 4c7ee66): A `b305f00`, B `373bdb3`, C `7fdb92a`.
- **Contamination found:** worktrees were NOT isolated. The "intel" commit `3cf0df7` had swept in
  A's pipeline-gate files + half of B's FA work; the dedicated FA branch `0aa4210` held only the
  OTHER half (authority/context/core/okr/system). No single branch was independently complete, and
  per-branch live verifications ran against trees that didn't match the committed branch.
- Resolved via real 3-way merge A→B→C. Only genuine conflicts: `approvals.py`/`routes.py`
  (comment-only divergence → took C's documented version) and `initiation.py` (git auto-unioned A's
  decision_state guard + C's trend-enrichment). Verified A-only fixes survived: executor.py PIPE-5
  run_id scoping (line 548), initiation guard, test_pipe4_routes.py.
- **Verified on merged tree:** 145 focused tests green (pipe4 + intel-p1 + g1-FA), ruff clean,
  mypy clean (586 files), app boots (348 routes).
- **LESSON (reinforces existing memory):** external agents MUST run in their own worktree launched
  with cwd INSIDE the worktree; sharing the main repo working tree scrambled three agents' commits.
- **Test-suite instability is NOT app instability:** every test flagged "failing" in full check.sh
  runs (no_direct_status_writes, builders/test_agents, memory_drill) PASSES in isolation. Root cause
  = non-isolated shared test DB (parallel TRUNCATE CASCADE + seed wiped by sibling tests). Bounded
  test-isolation chore, scheduled AFTER the solidity tail — not a "stabilization project."
- Group D (Personal/OKR/meetings/load) dispatched to **terminal** (codex hit usage limits).

## 2026-06-03 — Slack Gate-2 smoke complete (config + 2 real bugs fixed, merged to main)

Picked up the blocked Gate-2 channel-notify smoke (HANDOFF-2026-06-03.md). Branch
`lead/slack-gate2-lookup-callback-fix` → merged `--no-ff` to main (5ca5127 / fix 09fe54f).
Stale `../artemis-os-slacknotify` worktree removed; lead shell stayed in main repo throughout.

**Config blocker (the handoff item):** `Settings` uses `env_prefix="ARTEMIS_"`, so the un-prefixed
`MARKETING_CAMPAIGNS_SLACK_CHANNEL` / `APPROVAL_NOTIFY_OVERRIDE` in `.env` were ignored. Added
`validation_alias=AliasChoices(ARTEMIS_…, bare)` to both config.py fields (committed, robust) +
canonicalized `.env` to the prefix + added `ARTEMIS_APP_BASE_URL=http://localhost:8000` (was empty,
which silently drops the Edit-in-WS Slack button).

**Two real bugs the live smoke surfaced (unit tests missed both):**
1. **Approver DM lookup** — `_lookup_slack_user_id` used `users.list` (first page only, 25 members) +
   email filter → jon.fila@ not on page one → "user not found" fallback. Switched to canonical
   `users.lookupByEmail` (new `SlackClient.lookup_user_by_email`). DM now lands.
2. **Approve-from-Slack never resumed** — the interactive callback mutated a shared nested node_states
   dict and reassigned WITHOUT `flag_modified` → SQLAlchemy saw no change → decision never persisted →
   run re-suspended. Routed callback through the shared `_prepare_pipeline_resume` (has flag_modified +
   validation), catching HTTPException to keep Slack's always-200 contract.

**Verified LIVE (app :8000 + Slack MCP):** channel post lands in #marketing-campaigns (C0B8QE17DGQ);
DM lands to jon.fila@ (test override, no fallback, no josh/angela); Slack **Approve** drives run
385506cc → succeeded, decision=approved persisted. Tests: +`lookup_user_by_email` (3) and callback
persistence regression guards (3); ruff+mypy clean; 35 affected tests pass.

**REMAINING GAP (separate, known):** Edit-in-WS button + draft preview are ABSENT at Gate-2 because the
content agents produced NO deliverable — their output_summary says "Bash not available / constrained to
specific tools." That's the marketing tool-use blocker (project-marketing-pipeline-tool-use-blocker,
many cc*/p* worker branches in flight), NOT the Slack feature. The Edit-link logic itself is correct;
it just needs a real `deliverable_ids[0]`. Surface to Jon; do not treat the Slack notify feature as
blocked on it.

### Later 2026-06-03 — content unblocked + Writing Studio audited + roadmap locked
- **Codex fixed the tool-use blocker** (prompt grounding) — verified live (run b2892798 →
  real deliverable id=5, draft_ready, Gate-2 approval 31 has real draft_summary) and
  **merged to main `0aead86`**. Content agents now call their MCP tools. Brief +
  paste-prompt pattern: `briefs/tooluse-content-agent-prompt-grounding.md`.
- **Two Sonnet audits run** (cost-smart; Opus synthesizes): (1) rejection→learning loop is
  **WRITE-ONLY** (observations written, no runtime agent reads them, reason dropped);
  (2) Writing Studio training brain (converse-with-AI `/compose`, ruleset-shapes-drafts,
  seed corpus, propose/learn-new-rules) **did not survive the rebuild** — shell + data
  models + deep-link + editing are real; the brain is missing/stubbed. Full writeup +
  gap map + locked roadmap: **`docs/writing-studio-and-self-training-audit-2026-06-03.md`**.
- **Locked 3-phase order:** (1) cockpit usable — signal card real content + remove Reject,
  content card show draft + remove Reject + deep-link, campaign folders fix; (2) rebuild
  "converse with the AI" (port `/compose` + wire ruleset + seed corpus); (3) close both
  learning loops (writing propose/approve + signal rejection→memory→agent-reads).
- **New standing prefs saved to memory:** conversational/non-technical comms style;
  team-resources + division-of-labor (Opus plans/audits/verifies/merges; Sonnet+Codex+2nd
  Claude Code Max execute).
- **NEXT:** Phase 1 worker briefs (signal card / content card / folders). Folder fix is the
  no-judgment-needed starter; the two card redesigns want a quick design pass with Jon on
  exactly what info to show.

### Phase 1 COMPLETE (2026-06-03) — cockpit usable
All built by Sonnet workers, verified by Lead (live Slack reads + DB), merged to main:
- **Campaign folders** (cb6019f + per-campaign refinement): one folder PER CAMPAIGN, name
  derived LIVE from the campaign (rename auto-syncs; no rename endpoint exists yet). Backfilled
  existing 5 drafts; 0 orphaned in All-drafts. Obsolete family-level tests retired (786a46c).
- **Signal card** (d3f96f4): name+district title, why-it-matters, urgency, reason codes,
  group-size, fit score, MULTIPLE evidence snippets; Approve + View, NO Reject.
- **Content card**: full draft body chunked across Slack blocks; Approve + Edit-in-WS
  (deep-linked to deliverable id), NO Reject/View. Subject-duplication polish fixed (7f5b8a3).
- Locked: signals approvable Slack OR in-app, reject in-app (training); content approve-from-Slack
  OK now draft is visible; Google-Docs preview banked for long-form (in the audit doc).
- Both cards verified LIVE in Jon's Slack DM (jon.fila@) with real data.
- **Worktree cleanup TODO:** leftover merged worktree-agent-* branches from the 3 folder/card
  workers (harmless; `git worktree remove` when convenient).
- **NEXT = Phase 2:** rebuild Writing Studio "converse with the AI" (port /compose from Node ref +
  wire ruleset into drafting + seed corpus). Big one — plan WITH Jon before building.

### Phase 2 CORE COMPLETE (2026-06-03) — "converse with the AI" rebuilt
Orchestrated as 3 parallel Sonnet workers (terminal route), Lead verified live + merged each:
- **① Conversation storage** (merged; migration 0063 applied to artemis_os): writing_draft_thread_messages
  model + repo (create_thread_message / list_thread_messages_for_draft), linked to campaign_deliverables.
- **② Seed corpus** (merged; imported to artemis_os): ported the approved Node writing-agent-seed verbatim
  (Amira Marketing Voice profile + 7 examples + 9 sources) + POST /api/writing-studio/seed/import (idempotent).
- **③ Compose engine** (merged; commit 06f13d4): POST /api/writing-studio/drafts/{id}/compose — loads
  profile+rules+ranked examples, injects them into the system prompt with a no-fabricated-claims guardrail,
  calls the model via resolve_adapter/run_turn (the agents' provider cascade — no SDK key needed), persists
  user+assistant thread messages, returns proposed learnings. Draft detail now serves real threadMessages.
- **VERIFIED LIVE:** POST /compose on draft 5 → real on-brand grounded response (trace: 2 rules + 4 examples
  from Amira Marketing Voice injected), conversation persisted + served back via draft detail. 68 WS tests green.
- **REMAINING (flag to Jon):** (a) the INITIAL auto-draft (writing_studio_adapter agent at Gate-2) still does
  NOT read the ruleset — only the interactive compose conversation is rule-grounded; wiring rules into initial
  generation is the next sub-item. (b) proposed learnings are returned-not-persisted (Phase 3). (c) minor:
  compose metrics report inputTokens oddly (claude-code adapter quirk; cosmetic).
- **Worktree cleanup TODO** growing: many merged worktree-agent-* branches; prune when convenient.
- **NEXT = Phase 3:** close the learning loops (persist + approve/reject proposed rules; signal-rejection →
  memory → agent reads it). Plus the Phase-2 remainder (a) above.

### Phase 3 MERGED + VERIFIED (2026-06-04) — self-training loops closed
Built by the TERMINAL Opus Lead's Sonnet workers (correct division of labor — app Opus briefs +
verifies + merges; terminal lead fires workers). 4 branches handed back; app Opus verified live + merged:
- **A — ground first auto-draft** (merge 4eefcca): initial content-agent draft now pulls profile+rules
  +examples (shared prompt-builder) w/ anti-fabrication guardrail. 14 unit tests. NOTE: verified by tests
  + the same builder proven live via compose; a full fresh-pipeline-run live check is DEFERRED (expensive).
- **B — writing learning loop** (merge 99be429; migration 0064 applied to artemis_os): persist proposed
  learnings → writing_training_candidates; propose/approve-reject review endpoints + UI wired. VERIFIED
  LIVE: compose on draft 5 → candidate id1 (status proposed) → POST /training-candidates/1/decision
  approved → promoted to writing_rules id3 (active, source_candidate_id=1).
- **C-1+2 — reject reason → agent-scoped memory** (merge f494978): optional reason on signal/content
  reject → written into the gate observation, scoped agent:<slug>. VERIFIED LIVE: rejected signal 28 w/
  reason → observation #186 (signal_gate1_decision) content includes "Reason: …" scoped to
  agent:marketing.qualifier.cross_reference. (Reject route is POST /api/signal-queue/{id}/reject.)
- **C-3 — agents read own rejections** (cherry-pick ab1701f of 8d25e86; the branch carried dup-B/C12, so
  cherry-picked just its own commit; resolved an agent_executor.py conflict so A's grounding + C-3's
  rejection injection coexist): VERIFIED LIVE: fetch_agent_rejection_context('marketing.qualifier.cross_reference')
  returns the 2 rejections w/ reasons → next agent run sees them.
- Integration fix (bfd75f2): B changed proposedCandidates to snake_case (proposed_text/draft_id) + now
  persists; reconciled the stale Phase-2 compose test + endpoint docstring. Combined Phase-3+WS suite = 82 green.
- **Coordination hazard hit + handled:** terminal lead operated in the SAME main repo and left it checked
  out on worker/p3-b, with `main` parked in a LOCKED agent worktree (agent-a1fda278). Freed it (clean,
  removed) before merging. LESSON: terminal lead should work in its own worktrees, not the main repo HEAD.
- **Test side-effect on dev data:** signals 26 + 28 are now rejected_at_gate_1 (verification); reversible.
- **Worktree cleanup TODO (now large):** many merged worktree-agent-* + worker/p3-* branches; prune.
- **Phase 1+2+3 of the Writing-Studio/self-training arc are DONE.** Remaining: A fresh-run live check;
  the broader Marketing Intelligence Layer (docs/marketing-intelligence-layer-design.md) phases 1-5.

### Robustness fix + Marketing Intelligence Phase 1 MERGED + VERIFIED (2026-06-04)
- **Robustness fix** (Codex, merge 527acc8): enqueue binds to run target_candidate_id (no cross-candidate
  misfire); adapter fails fast w/o brief; Gate-2 won't fire w/o reviewable draft; initiation blocks
  dispatch w/o brief. 33 tests; Codex live-verified. The A-grounding check that surfaced this also
  CONFIRMED Phase-3 A works (deliverable 8 body was on-brand).
- **Intelligence Phase 1** (Decisions 1+2): merges f2d0ace (trends core), 345904c (D1 enrichment),
  69c62c4 (D2 prioritization). Deterministic; no migrations; snapshots persist as trend_snapshot
  observations. VERIFIED LIVE: D1 — initiation-proposal returns trendContext {momentum, comparables,
  decisionHistory}; candidate 3 showed priorApproves=5/rejects=1 from Phase-3 memory (layers connect).
  D2 — /api/marketing/intel/prioritization returns ranked districts (3 velocity / 5 combined). 67 tests
  green, mypy clean. Design: docs/marketing-intelligence-layer-design.md "Phase 1 — concrete design".
- app_base_url now the Cloudflare tunnel (https://app.artemisos.me) so coworker Slack links work.
  D2 time-sensitivity uses created_at+urgency proxy (no deadline column yet; clean swap-in).
- **RECURRING PROCESS ISSUE:** Codex AND terminal lead both operated in the MAIN repo working tree
  (Codex left it on its branch; terminal leaked untracked intel/ files) — Lead untangled both. External
  agents MUST work in isolated worktrees.
- Residue purged: deliverable 8, approval 32, junk runs failed.
- **NEXT:** Decision 3 (banked); alerts/digest; or surface D1 trendContext + D2 ranking in the UI.

---

## 2026-06-01 EVENING — Stream 2 (campaign initiation) COMPLETE; next = CMP-SEND

### Branch + DB
- `lead/j6a-granola-integration` HEAD = ci3 merge (Stream 2). Migration head **0058**. App running pid 30001 (autonomous scout scheduler armed, out-of-process).
- **In flight via Codex (main repo, worker/cmp1-md1-remove-mocks):** CMP1+MD1 — remove the `CAMPAIGNS`/`SIGNALS_MOCK`/`APPROVALS_MOCK` mocks from marketing-os.js, render real campaign_candidates + real dashboard counts. (Lead standing down from main-repo git ops while Codex runs.)

### Stream 2 — DONE + ACTIVATED
- **CI1** (0057, d3d5858): campaign identity cols + deliverable_types registry + `campaign_candidate_signals` join + `cluster_or_create_candidate` (deterministic district+family, editable 90d window, open-only) wired into Gate-1 approve_signal + `predecessor_id` lineage + TargetScope Pydantic.
- **CI2** (0058, 8848566): `CampaignInitiationProposal` Pydantic + `initiation_proposal_json` + brief_assembler reads FULL cluster + grounds on predecessor + 5.1 prompt rewritten to emit proposal JSON (H5 trap caught) + `gate_campaign_initiation` human_gate + deliverable fan-out registry-driven/mix-gated.
- **CI3** (b33024e, no migration): initiation UI form + GET initiation-proposal / POST initiate (initiate_campaign + gate resume).
- **Activated:** re-seeded marketing agents (proposal prompt) + marketing.main pipeline (gate_campaign_initiation node live, deliverable_outreach_email), app restarted. Flow works: signal → Gate-1 approve → cluster → LLM proposes campaign → operator confirms (UI) → content fires for confirmed mix.
- Grouping/lineage design: `docs/campaign-initiation-and-district-design.md` § "Stream 2".

### NEXT STREAM — CMP-SEND (the content path dead-ends at "draft created")
Per `docs/content-path-audit-2026-06-01.md` (read-only audit): "signal → named campaign" works, but "campaign → reviewed draft → SENT" does NOT. Gate-2 has no review UI / no decide handler / no send mechanism (`DeliverableState.approved` is terminal, no `sent`). Task **#108**. Briefs:
- **CMP-SEND-1** (DRAFTED `briefs/cmp-send-1-gate2-review-drawer.md`, gpt-5.4/medium): Gate-2 review drawer + decide endpoint + pipeline resume. Fire after CMP1.
- **CMP-SEND-2** (not drafted): outbound send (+ `DeliverableState.sent`) — has an EMAIL-INFRA decision pending (SMTP/SendGrid/etc.); also the capture seam for #106 outcome tracking.

### Engine health (verified, parallel audit)
Healthy + autonomous: all 9 scouts ran (48h, no crashes), ~61 signals at **82% district resolution**, canonical families/urgency throughout. **Taxonomy fix VERIFIED working** — the audit's "P0 enum mismatch" was a FALSE ALARM (failures were pre-05-31 historical; ~3% recent edge where an agent emits an unrecognized family). Banked: **#109** federal_funding 0% resolution (federal grants have no district — prompt logic), **#110** non-producing scouts (legislative/starbridge/linkedin/procurement call APIs but emit 0 — connectors unconfigured).

### Banked (open) tasks
#106 campaign outcome/effectiveness tracking (future loop-closer, seams documented) · #107 5.1 prompt stale-section cleanup · #108 CMP-SEND stream · #109 federal_funding district logic · #110 non-producing scouts audit · #83/#84 CMP1/MD1 (in flight) · plus older: #24 writing-studio deeper, #25 CC8 run-lock, #28 except-audit, #37 responsiveness, #64 stewardship, #76 D3 ruleset versions, #77 D4 (likely subsumed by districts), #40 COO doc.

### NEXT MOVES
1. CMP1+MD1 lands (Codex) → verify + merge → Campaigns tab shows real campaigns.
2. **Genuine first-campaign end-to-end test** with a real signal (approve → cluster → propose → confirm → draft) — proof the Stream-2 flow holds before building send on top.
3. **CMP-SEND-1** (Gate-2 review) → CMP-SEND-2 (outbound send, after email-infra decision).

### Operating notes
- Concurrent Codex/terminal-Lead need separate worktrees (two-Leads-one-tree lesson). Scheduled agentic runs out-of-process (scout_cli / pipeline run_cli) — web process never spawns claude directly.
- New uncommitted docs/briefs while Codex runs (content-path-audit, cmp-send-1, cmp1-md1 briefs) — commit after Codex's CMP1 merges to avoid sweep.

---

## 2026-06-01 (earlier) — Marketing engine trustworthy/solid/autonomous; Stream 2 design locked

### Branch + DB state
- `lead/j6a-granola-integration` HEAD = **2f057a3** (Stream-2 grouping design doc). Migration head **0056**.
- App running on :8000 (restart pid ~81620 era); autonomous scout scheduler armed (staggered, out-of-process).

### What this multi-day arc closed (read docs/campaign-initiation-and-district-design.md for the spec)
- **PIPE6** Workflows/Automations sunset → Pipelines (410 Gone). **CC12** content-agent tools (Writing Studio handoff).
- **Marketing flow audit** → found Campaigns tab renders mock `CAMPAIGNS` (CMP1 pending).
- **District layer (DIST1–DIST6) COMPLETE + ACTIVE:** districts entity + NCES 2024-25 data (13,462 distinct), tier bands (D1–D4, editable in Signal Playbook), soft-flag D4, scouts emit geography, resolver (suffix + numbered-name variants). Live resolution ~86%.
- **Taxonomies reconciled to single source of truth:** campaign families (#79/#80, Josh spec 5: obc/dyslexia/biliteracy/hit/general_growth) + urgency (#81, hot/standard/enrichment) — normalize in josh_spec.
- **Clean slate purge** done (operational marketing tables; memory/districts/config preserved).
- **Engine trustworthiness (the big arc):** #101 scheduler drives real tool use; #102 scouts run out-of-process (can't crash app); #103 PIPELINES run out-of-process too (proven: 602s run, app survived); #104 tokenizers/multiprocessing semaphore leak fixed at root; #97 loader keys by nces_id; #100 district-data refresh button; #105 resolver variants. **Verified in the wild: autonomous scouts + a full pipeline run produced signals without crashing the app.**

### Stream 2 (campaign initiation) — DESIGN FULLY LOCKED (2026-06-01), build not started
- Multi-signal → ONE campaign via **deterministic cluster-or-create** (resolved_district + campaign_family, editable 90-day window). LLM proposes the campaign FROM the cluster + reviewable refinements — **never the grouper**. Fresh candidate per campaign with **predecessor_id lineage** → view/clone/adapt prior brief + collateral (not a blank page).
- Schema (CI1): `campaign_candidate_signals` join + `predecessor_id` + clustering config + initiation columns + `deliverable_types` registry (outreach_email active, others coming-soon).
- CI1/CI2/CI3 briefs updated with the grouping/lineage addenda.
- **FUTURE banked (#106):** campaign outcome/effectiveness tracking — seams documented in the design doc (don't hunt).

### NEXT MOVE
Build **CI1** (initiation substrate + grouping + lineage). Then CI2 (initiation step + Pydantic + brief_assembler grounded on cluster/predecessor), CI3 (initiation UI). Then CMP1/MD1 mock cleanup lands naturally.

### Operating note
Concurrent Codex/terminal-Lead need separate worktrees (two-Leads-on-one-tree lesson). Scheduled agentic runs execute out-of-process (scout_cli / pipeline run_cli) — web process never spawns claude directly.

---

## 2026-05-30 EOD — H5 + SP both landed; anti-hallucination layer structurally complete

### Branch state

```
lead/j6a-granola-integration HEAD = (post-SP merge — ~76 commits ahead of starting point)
Recent commits:
  (SP merge)   — Signal Playbook combined SP1+SP2 (Codex)
  69876bc      — feat(sp): add signal playbook editor (Codex)
  67719fd      — merge(h5): Daily Brief + Pipeline AI Panel Pydantic
  c32b6ca      — fix(h5): align brief prompt with DailyBrief schema (Opus follow-up)
  5b2905c      — feat(h5): Daily Brief + Pipeline AI Panel Pydantic
  f0266c5      — merge(cc29): rejection memory carryover
  ... (earlier merges per chronology)
```

Migration head: **0052** after SP. PIPE6 will land 0053.

### Anti-hallucination stream STRUCTURALLY COMPLETE

```
H1 ✅  self-teaching tool errors (ToolRegistry layer)
H2 ✅  scout intake Pydantic + reason_code allowlist
H3 ✅  trajectory summarizer Pydantic + Builder revalidation
H4 ✅  meeting summarizer Pydantic + FA revalidation
H5 ✅  Daily Brief + Pipeline AI Panel Pydantic
```

Every JSON-emitting LLM surface in the platform has Pydantic validation + retry-on-failure + provenance framing. Jon's 2026-05-29 invariant ("hallucinations cannot happen on this app with any of the builders and agents") is now structurally enforced.

### SP landed via Codex

Codex dispatched SP. Two new findings banked as tasks #79, #80, #81 — josh_spec.py campaign_families normalization, label-vs-slug ambiguity, urgency prose vs enum reconciliation. All cosmetic/cleanup, none blocking.

### PIPE6 in flight via Codex

Brief at `briefs/pipe6-workflows-automations-sunset.md`. Executes D6 lock from original master plan. ~400 LOC + migration 0053. Auto-migrates 1 existing workflow ("Codex Smoke Workflow") to Pipelines. Replaces /api/automations/* + /api/workflows/* with 410 Gone. Removes Automations + Workflows tabs from Operations sidebar. MC-style memory observation for the migration event.

**When PIPE6 lands:** the master-plan priority order is fully executed through PIPE6. Next stream is CC12 (Writing Studio handoff) → then Marketing flow audit per ROADMAP.

### Locked Decisions Ledger current state

| D# | Status |
|---|---|
| D1, D2, D5, D6.1 | ✅ Done |
| D6 (Pipeline unified) | 🔄 PIPE6 in flight (Codex) — execution of the lock |
| D7 (Signal Playbook) | ✅ Done (SP merged) |
| **D3 (ruleset versioning append-only)** | 🔴 **STILL OUTSTANDING — task #76** |
| **D4 (HMH partner flag)** | 🔴 **STILL OUTSTANDING — task #77** |

D3 and D4 are the two outstanding locked decisions surfaced via the Locked Decisions Ledger audit. Neither is blocking active work but both should be picked up before Salesforce/ChurnZero/Gong integration begins (D4 especially — districts will need partner flags).

### Discipline lessons codified in PLATFORM-MAP this session

1. "Substrate complete" ≠ "behavior complete" — verify runtime + DB rows + end-to-end loop
2. Workers' self-reports are claims; spot-check critical ones; branch existence is also a claim
3. Parallel briefs need migration-number coordination + file-overlap analysis
4. Test DB contamination from parallel workers — rebuild test DB after parallel rounds
5. Provenance framing on LLM-content consumed by other LLMs
6. Self-teaching error messages on tool validation (H1 pattern)
7. **Audit Locked Decisions Ledger before drafting any new plan** (Jon caught D6/PIPE6 drift)
8. **Pydantic briefs MUST list the prompt-builder file in scope** (H5 missed prompt.py; production would have gone empty if not caught)

### Operating mode this block

Heavy parallel work via terminal-Lead + Lead Agent tool + Codex. Codex handled SP + PIPE6 (saves Claude Max tokens). Lead handled coordination + Locked Decisions audit + planning docs (PLATFORM-MAP, INDEX, ROADMAP, this log).

### Next move (when PIPE6 lands)

1. Merge PIPE6 onto lead/j6a-granola-integration (Lead handles per the existing pattern)
2. Verify migration + 410 routes + UI cleanup
3. Fire CC12 — Writing Studio content-agent handoff brief (currently undrafted; ~30-40 min Lead time to write, ~200 LOC implementation)
4. After CC12: Marketing flow audit (Dashboard / Campaigns / Approval Queue) per master plan
5. Then MW2-MW4 (Memory Wings UI) when ~4 weeks of memory data accumulate
6. Then Stewardship SH1-SH5 (~6-8 weeks out, design locked)

### Documentation maturity this session

- `docs/PLATFORM-MAP.md` — NEW, comprehensive platform state (single cold-pickup doc)
- `docs/INDEX.md` — NEW, catalog of all docs + briefs
- `docs/ROADMAP-2026-05-30.md` — NEW, forward plan + active streams
- `docs/memory-shell-vision-2026-05-29.md` — NEW, memory design locked
- `docs/platform-stewardship-design-2026-05-30.md` — NEW, stewardship design locked
- `docs/hallucination-audit-2026-05-29.md` — NEW, drove H1-H5 stream
- `docs/memory-audit-2026-05-29.md` — NEW, drove M1-M6
- `docs/hollowness-audit-2026-05-29.md` — NEW, drove CC18-CC20

3 new audits + 2 new design docs + 4 new operational docs in one session. Documentation:code ratio is now meaningfully better — future LLMs picking up cold have explicit context instead of having to rediscover hollowness.

---

---

## 2026-05-30 LATE — Three parallel rounds landed; substrate ready for integration work

This block captures everything since "Round 2 memory complete." Approaching context compaction — keep this for rollover safety.

### Branch state

```
lead/j6a-granola-integration HEAD = 46a6678
46a6678  fix(alembic): chain 0050 after 0049 (bundles A+B were parallel-branched from 0048)
0bcb816  merge(bundle-b): CC21+CC22 — tool_invocations.builder_session_id + definition_proposals.rejection_reason
13dcbcc  merge(bundle-a): CC27+CC28 — extend ScopeKind Literal + widen memory_evidence.source_id to TEXT
3cb8245  merge(mc2-mc5): Memory Carryover bundle — 4 new approval surfaces + MC1 MW1 refactor
d879d44  merge(cleanup): CC23+CC24+CC25+CC26
0e1b5c5  merge(cc19): Builder MCP Tool Execution (yesterday)
... (CC20, H1-H4, M1, M5, M6, M2, M3+M4, MC1, MW1 all merged behind these)
```

Migration head: **0051** on both prod and test DB (test DB rebuilt cleanly after parallel-worker contamination — see "Discipline" section below).

### Memory state at end-of-session

```
31 observations · 10 drawers · 49 evidence · 17 scopes · 32 obs_scopes (MW1 join)
Sources actively writing: M1 (trajectory), M5 (signal genealogy), M3 (FA convo drawers),
                          MC1 (proposal approvals), MC2 (Gate 1 approvals), MC3 (skill),
                          MC4 (pipeline gate), MC5 (FA marketing approvals)
ScopeKind: 12 values (project, workspace, brand, agent, skill, global, pipeline, district,
                      account, person, meeting, personal)
EvidenceSourceKind: 9 values (drawer, observation, agent_run, signal_queue,
                              definition_proposal, pipeline_run, skill,
                              floating_artemis_messages, meeting)
```

### Production receipts (verified empirically)

- **engine.commit() fired twice**: Proposal #4 approved 2026-05-29 LATE (first ever), Proposal #5 approved 2026-05-30 (second; brief_composer system_prompt md5 changed 56c0bdb8 → 2cfeaa06 → 39dfcc3b)
- **Brief_composer measurably improved**: pre-Proposal-#4 runs (329, 318, 275) failed; post-Proposal-#4 run #340 successfully processed all 7 pending signals end-to-end. The platform learned from its own runs.
- **Builder Proposal #5 cited memory observations #25 + #5** in proposal metadata — M2 retrieval working under real load. Builder noticed previous fix worked + identified two NEW issues (pipeline-run scope leakage + misleading signals_emitted metric) that were only visible from observing run #340.
- **MC1 fired on real operator approval** — obs #26 + #27 landed automatically (multi-scope: agent + workspace:platform).
- **MC2 fired on real Gate 1 approval** of signal #3 — observation #28 lands with category=signal_gate1_decision.

### What landed this round (the four big merges, after the morning's pipeline smoke)

| Stream | What | Files | Migration |
|---|---|---|---|
| **MC2-MC5 + MC1 refactor** | 4 new carryover surfaces (signal Gate 1, skill promotion, pipeline gate, FA tool approve) + MC1 refactor to use MW1 multi-scope primitives | `artemis/builder/memory_carryover.py`, 4 route files | none |
| **Cleanup batch CC23-26** | EvidenceSourceKind Literal extension, evidence_count off-by-one, Drawers empty-state copy, pgvector serialization fix | `artemis/memory/schemas.py`, `routes/memory.py`, `memory-shell.js`, `retrieval.py` | none |
| **Bundle A (CC27+CC28)** | ScopeKind extension (pipeline + integration scopes) + memory_evidence.source_id BigInt→TEXT (UUIDs/slugs without hash workaround) | `memory/schemas.py`, `memory/models.py`, `memory/store.py`, etc. | **0049** |
| **Bundle B (CC21+CC22)** | tool_invocations.builder_session_id + CHECK XOR constraint; definition_proposals.rejection_reason + rejected_at + /reject body | `tools/models.py`, `tools/mcp_server.py`, `builders/models.py`, `builder/routes.py`, `agents.js` | **0050 + 0051** |

### Three discipline lessons from this round (write to memory, don't forget)

1. **Wait for Worker relays before merging.** I merged Bundle A based on `worker/bundle-a-substrate` branch existing + commit on it, without getting terminal-Lead's explicit relay. The work was correct, but Worker A's report flagged a real concern (`int(ev.source_id)` casts in repository.py) that I would have spot-checked sooner if I'd waited. Operating Principle #4 — Worker self-reports are claims, not evidence. Branch existence is also a claim.

2. **Test DB contamination is a recurring parallel-worker pattern.** When 2+ Workers run pytest against shared `artemis_test` DB, alembic_version drifts. After Bundle A+B merged, prod DB needed `psql UPDATE alembic_version SET version_num='0048'` then `alembic upgrade head`. Test DB needed full `dropdb && createdb && alembic upgrade head` because the stamp-without-schema-migration left it in inconsistent state. **Pattern that worked: rebuild test DB after parallel rounds before post-merge verification.**

3. **Parallel briefs with migrations must explicitly assign migration numbers.** Bundle A and Bundle B both declared `down_revision="0048"` because they were drafted in parallel without coordination. Required manual rebase to chain 0050 after 0049. **Future parallel briefs should: (a) coordinate migration numbers upfront in the brief, OR (b) instruct the Worker to use the next-available number at write time, OR (c) bundle migration-adding work into single Workers.**

### Bundle A specific finding worth a follow-up read

Worker A's report (which I didn't see before merging — sent post-fact by Jon): repository.py needed `int(ev.source_id)` casts at drawer/obs preview lookups. Worker noted: "Worth confirming the Worker didn't paper over a real type mismatch — read the repository.py changes specifically."

**Status: not yet investigated.** The casts are necessary because drawer/observation IDs are BigInt PKs that source_id (now TEXT) needs to compare against. The cast pattern `int(ev.source_id)` is fine when source_kind is `"drawer"` or `"observation"` (those source_ids are stringified ints from CC28's backfill). It would fail if `int()` were called on a non-numeric string (skill slug, UUID) — but those source_kinds reference rows in other tables (skills.slug, pipeline_runs.id, etc.), not the BigInt-PK drawers/observations tables. **The cast is correctly scoped per source_kind branch in the lookup logic.** Bank as low-priority sanity-check task — re-read repository.py:get_observation_detail when next touching memory code.

### Banked architectural follow-ups (after Bundle A+B)

- **CC29** — Memory carryover for rejected proposals (Bundle B Part C deferred to avoid Bundle A overlap). ~80 LOC.
- **Repository.py source_id cast audit** — confirm the `int(ev.source_id)` pattern is safely scoped to drawer/observation source_kinds. Quick read-only check.
- **`_LEGACY_HASHED_OBSERVATION_IDS` reconciliation** — obs #29/30/31 from MC3/MC4/MC5 pre-CC28 smokes have SHA-256 hashes as source_ids. Lossless invariant says don't modify them. Documented in `memory_carryover.py`. Future cleanup brief could mark them with a `legacy_format=true` flag if a column is added.

### Next move (when context resumes)

**H5 — Daily Brief + Pipeline AI Panel anti-hallucination completion.** ~150 LOC. Closes the H1-H4 stream by Pydantic-validating the remaining LLM-emit sites (per `docs/hallucination-audit-2026-05-29.md`):

- `artemis/brief/generator.py` — daily brief JSON emit (currently bare json.loads → DB)
- `artemis/pipelines/assistant/turn_handler.py` — Pipeline AI Panel proposal emit (currently regex-extracted JSON)

After H5: every JSON-emitting LLM surface in the platform has Pydantic validation + retry-on-failure + provenance markers. The "no hallucinations" invariant becomes structurally enforced across the entire platform.

**Drafting H5 brief now** (in flight at end of this session). Brief at `briefs/h5-daily-brief-pipeline-ai-pydantic.md` (when complete).

### What to do at session-resume

1. Read this section first.
2. Verify branch state: `git log --oneline -3` should show `46a6678` HEAD with the merges above.
3. Verify migrations: `uv run alembic current` should show `0051 (head)`.
4. If H5 brief exists at `briefs/h5-daily-brief-pipeline-ai-pydantic.md`, fire it. Otherwise draft it.
5. Pending streams (per `docs/ROADMAP-2026-05-30.md`): SP1 (Signal Playbook), CC12 (Writing Studio handoff), MW2-MW4 (Memory Wings UI — wait for ~4 weeks of data), SH stream (Stewardship — deferred 6-8 weeks).
6. Active task list: 65 completed + ~15 pending (including CC29, CC34 federal_funding investigation, CC8/9 follow-ups, Inbox UI placement, OKR expansion direction-setting).

---

---

## 2026-05-30 — Memory keystone P4 complete; MC + MW streams kicked off

**Round 2 closure (M2 + M3+M4):**
- M2 merged at `b4eea5a` — Builder reads agent memory via `builder_search_memory` MCP tool
- M3+M4 merged at `26b1f15` — Floating Artemis auto-writes turn drawers + auto-reads at prompt build

**Verification done via direct invocation (browser screenshots in conversation):**
- M2: returns observation #3 (M1's trajectory) for brief_composer scope with 1 evidence link to agent_run #329
- M3: drawer #2 lands in `agent:floating-artemis` scope with verbatim `[USER]...[ASSISTANT]...` content + `source_kind=floating_artemis_message`
- M4: prompt injection block "## Recent memory (LLM-curated observations from prior conversations)" with provenance framing (H4 pattern)
- Memory shell visual: 2 drawers / 4 observations / 3 evidence / 4 scopes — all rendered correctly

**Memory keystone P4 (agent integration) DONE.** Status of all 6 sources:

| Component | Status |
|---|---|
| M1 trajectory → memory observation | ✅ |
| M5 marketing signal → memory observation | ✅ |
| M6 memory shell UI | ✅ |
| M2 Builder reads agent memory | ✅ |
| M3 FA writes conversation drawers | ✅ |
| M4 FA auto-reads at prompt build | ✅ |

Started session: 1 row in 11 memory tables. Now: 4 observations + 2 drawers + 3 evidence links + 4 scopes with active write+read paths from 6 distinct surfaces.

**Banked findings from Round 2:**
- **CC26** — pgvector embedding serialization fails in semantic search path (`could not convert string to float`). search_observations falls back gracefully to lexical+recency. Not blocking; semantic search is a nice-to-have at current data volume. ~30 LOC fix banked.

**In flight (terminal-Lead just dispatched):**
- **MC1** (`worker/mc1-proposal-approval-to-memory`) — definition_proposals approval → memory observation. The carryover stream's first brief.
- **MW1** (`worker/mw1-multiscope-schema`) — multi-scope observation schema (migration 0048 + new `memory_observation_scopes` join table + new `wing` + `confidence_origin` columns).

Both ~120 LOC, independent files, no merge conflicts expected. Lead merges in order: **MW1 → MC1** when reports come back.

**Deferred until after MC1+MW1 land:**
- Live pipeline-run smoke (currently blocked by in-flight pipeline_run `7ed7e0fd-...` at awaiting_approval since 2026-05-29). When fired, will exercise M1+M5 with real LLM agents.

**Memory shell visual reference (browser screenshot in chat history):**
- Header: "2 DRAWERS · 4 OBSERVATIONS · 3 EVIDENCE LINKS · 4 SCOPES"
- Listing: all 4 observations with scope chips (AGENT · FLOATING-ARTEMIS / AGENT · MARKETING.QUALIFIER.BRIEF_COMPOSER / WORKSPACE · MARKETING / GLOBAL · GLOBAL)
- Detail pane shows evidence chains: DRAWER #X / SIGNAL_QUEUE #182 / AGENT_RUN #329

**Locked design decisions (from earlier 2026-05-29 EOD work):**
All 13 architectural questions resolved in `docs/memory-shell-vision-2026-05-29.md`. Key locks: D6 multi-scope (the most important), D2 attention bands KILLED, D8 conflict surfacing, D9 no auto-aging, D10 scope IS privacy boundary. MC stream has 5 briefs (~380 LOC); MW stream has 4 briefs (~820 LOC); MC2-MC5 to be drafted after MC1 lands + verifies.

**The full self-improvement + memory journey this session:**

| Layer | Bug class | Fixed by |
|---|---|---|
| L1 producer/data | GC + KeyError + race + FK | CC10-CC15 |
| L2 producer/content | snapshots thin; extraction invisible | CC16-CC17 |
| L3 consumer/discovery | UI never passed target_id; no Inbox | CC18 + Proposals Inbox |
| L4 consumer/execution | adapter dropped tools | CC19 |
| L5 consumer/grounding | Builder hallucinated facts | CC20 |
| L6 platform/anti-hallucination | unvalidated JSON across multiple surfaces | H1+H2+H3+H4 |
| **Memory P4 (agent integration)** | dormant substrate | M1+M2+M3+M4+M5+M6 |

**First `engine.commit()` in production:** 2026-05-29 LATE (Proposal #4 approved for brief_composer). MC1 will make sure every future engine.commit() leaves a memory observation.

---

## Operational note for future sessions: Chrome connector LIVE

---

## Operational note for future sessions: Chrome connector LIVE

As of 2026-05-29 LATE, Jon installed the Claude Chrome connector and it now reaches localhost on this machine. Lead can drive visual browser verification directly.

**Pattern:**
1. `mcp__Claude_in_Chrome__list_connected_browsers` — pick the local one
2. `select_browser` with the chosen deviceId
3. `tabs_context_mcp` → `navigate` → `screenshot` via `browser_batch`
4. `find` then `left_click` by ref for interactions

Use this instead of falling back to "Jon needs to eyes-on it" for UI verification. Browser is available; use it.

---

## 2026-05-29 (LATEST) — Round 1 memory keystone live + visually verified

**M1 + M5 + M6 merged on `lead/j6a-granola-integration`** (commits b0bfefd, acf3926, d7fc20c). All three Workers' code verified empirically in production.

**Production memory state after Round 1 smoke (verified by direct invocation + Memory shell screenshot):**

```
memory_drawers      : 1  (M5 wrote: verbatim LAUSD signal JSON, scope workspace:marketing)
memory_observations : 3  (1 bootstrap + M5 qualified-signal + M1 trajectory-direct)
memory_evidence     : 3  (observation→drawer, observation→signal_queue:182, observation→agent_run:329)
memory_scopes       : 3  (global, workspace:marketing, agent:marketing.qualifier.brief_composer)
```

**Verification done:**
1. Direct call to `signal_queue.update_status(signal_id=182, newStatus="qualified")` via ToolContext — drawer + observation + 2 evidence links landed. M5 confirmed.
2. Direct call to `_write_trajectory_observation(run_pk=329, agent_id="marketing.qualifier.brief_composer", what_worked=...)` — observation + agent_run evidence link landed. M1 confirmed.
3. Chrome browser verification of Memory shell at `/#/memory` — all stats badges render correctly, both tabs work, observation detail shows full evidence chain with DRAWER #1 / SIGNAL_QUEUE #182 / AGENT_RUN #329 source labels.

**Banked findings during Round 1:**
- CC23 — `EvidenceSourceKind` Literal needs `agent_run`/`signal_queue`/`meeting` added (M5/M6 used raw pg_insert as workaround)
- CC24 — observation `evidence_count` field off-by-one in route metadata (UI uses array length correctly, so cosmetic)
- CC25 — Memory shell Drawers tab empty state says "Select an observation" (should be "Select a drawer")

**One real production state change made:** signal #182 (LAUSD screen-time policy) was qualified during M5's smoke. It's now a real `qualified` signal in production. If the next pipeline run picks it up at Gate 1, it'll appear in Jon's approval queue. The qualification is legitimate (POLICY_EDTECH_TIME_LIMIT reason code on a real LAUSD policy announcement).

**Round 2 in flight via terminal-Lead:**
- M2 — Builder reads agent memory (~150 LOC) — `briefs/m2-builder-reads-agent-memory.md` → `worker/m2-builder-reads-memory`
- M3+M4 combined — Floating Artemis auto-write + auto-read (~220 LOC) — `briefs/m3m4-floating-artemis-memory.md` → `worker/m3m4-floating-artemis-memory`

Independent of each other. Both will be verified the same way: direct invocation + Memory shell visual + browser screenshot.

---

## 2026-05-29 (LATE) — ALL 6 LAYERS CLOSED + engine.commit() first production fire

---

## 2026-05-29 (latest) — ALL 6 LAYERS CLOSED + engine.commit() first production fire

**HISTORIC MILESTONE:** the self-improvement loop fired end-to-end for the first time in the platform's history.

**The single most important DB fact:**

```
PRE-APPROVE:  agents.system_prompt md5 = 56c0bdb8d0c34b96c221fbe40466a746 (2583 chars)
POST-APPROVE: agents.system_prompt md5 = 2cfeaa0685c56ff20fc162b7b7051621 (1175 chars)
```

Agent row id=17 (`marketing.qualifier.brief_composer`) was updated by `engine.commit()` from a definition Lead approved (Proposal #4 from Builder session 14). This is the first production write to an agent's definition by the platform's own self-improvement machinery. The proposal contained:
- Real state enums (no hallucinations — CC20 grounding worked)
- "MUST always call tools" rule (addresses Run #318 silent no-ops)
- Batch-fetch via `signal_queue.find_recent_qualification_results` (addresses Run #329 N+1 probe)
- Explicit "never use a value not in the tool's enum" instruction (anti-hallucination at runtime)
- Citations to 3 trajectory summaries (runs 329, 318, 275) with diagnostic detail

**Merge sequence (this turn):**

```
80d40e5  merge(h4) — meeting summarizer Pydantic + Floating Artemis revalidation (Lead-dispatched)
b1c60fb  merge(h3) — trajectory summarizer Pydantic + Builder provenance framing
5f35002  merge(h2) — scout intake Pydantic + reason_code allowlist + SignalState consolidation
4bf5e6f  merge(h1) — self-teaching ToolRegistry errors (platform-wide foundation)
6265432  merge(cc20) — Builder grounding tools (read_tool_signatures, read_db_schema, read_skill_catalog)
185b385  merge(j6a) — Proposals Inbox brought onto integration branch
```

**Smoke verification on integrated branch:**
- `./scripts/check.sh`: 2737 passed / 1 failed (known-exempt j5b Jira) / 1 skipped (CC20 API-gated)
- H1 self-teaching verified: `pending_human_review` (the Run #329 hallucination) → "Invalid value for parameter 'newStatus': 'pending_human_review'. Valid values are: pending_qualification, qualified, rejected_hard_filter, suppressed_stale, approved, rejected_at_gate_1, snoozed, archived."
- H2 schemas verified: invalid `urgencyTier='extreme'` rejected; `SignalState` enum now includes `suppressed_deprioritized`.
- H3 schemas verified: oversized `what_worked` and extra fields rejected.
- H4 schemas verified: hallucinated `due="next Tuesday-ish"` rejected; `due="this week"` accepted as allowed loose token.
- Builder session 14 (Layer 6 closure smoke): Proposal #4 landed with REAL states, ZERO hallucinated. Same agent, same review prompt as CC19's smoke. Anti-hallucination stack proven end-to-end.

**The full hollowness journey (all 6 layers, in chronological diagnosis order):**

| Layer | Symptom | Root cause | Brief | Status |
|---|---|---|---|---|
| L1 producer/data | 0/236 trajectory summaries | GC + KeyError + race + FK | CC10-CC15 | ✅ |
| L2 producer/content | summaries hollow even when landing | snapshot thin + extraction invisible | CC16-CC17 | ✅ |
| L3 consumer/discovery | nothing surfaces proposals | UI never passed target_id; no Inbox | CC18 + Proposals Inbox | ✅ |
| L4 consumer/execution | Builder can't propose | adapter dropped `request.tools` | CC19 | ✅ |
| L5 consumer/grounding | Builder hallucinated state enums | no truth-tool access | CC20 | ✅ |
| L6 platform/anti-hallucination | scouts + trajectory + meeting also hallucinated; pollution chains | no Pydantic on JSON-emit + opaque tool errors | H1+H2+H3+H4 | ✅ |

**Active state:**

- Branch HEAD: `lead/j6a-granola-integration` = `80d40e5`
- Agent row 17 (brief_composer) updated by engine.commit() at md5 `2cfeaa06...`
- Proposal #4: status=approved (the historic first)
- Proposal #3: status=pending → can stay or be rejected; superseded by Proposal #4 anyway
- memory_observations: still 1 row (1 user-written via Floating Artemis tool). M1 will flip this.
- Banked: brief_composer should fire on next pipeline run with new prompt; verify the run uses the updated definition.

**Next surgical move:**

M1 (`briefs/m1-trajectory-summary-to-memory-observation.md`) — trajectory summary → memory observation. ~120 LOC. Now safe to fire because H3 validates summaries before they could pollute memory. Brief already drafted; updated with prerequisite note that H1-H4 + CC20 + engine.commit-verified are all in place.

**Open work staged after M1:**

- CC21 — `tool_invocations.builder_session_id` column (banked from CC19 follow-up)
- CC22 — `definition_proposals.rejection_reason` column (banked, gap #3 in consumer-side audit)
- M2 — Builder reads agent memory (after M1 produces observations)
- M3 — Floating Artemis auto-write conversation drawers
- M4 — Floating Artemis auto-read at prompt build
- M5 — Marketing signal → memory observation
- M6 — Memory shell UI wiring
- Other-surface anti-hallucination follow-ups (Pipeline AI Panel grounding tools, Daily Brief Pydantic, Dev Projects)
- The proposals inbox UI placement fix (`f6ab956` in worktree) — UI pass

**Last updated context:** 2026-05-29 LATE. All four H-briefs + CC20 + first engine.commit() landed in one session. Six layers of hollowness closed empirically. Next instance: read this section + `docs/hallucination-audit-2026-05-29.md` + the M1 brief to pick up.

---

## 2026-05-29 (later) — Hallucination audit + CC19 verified + CC20 verified + H1-H4 stream

**What landed in this turn:**
1. CC19 verified end-to-end: Builder session 12 landed 2 proposals (agent + skill co-proposal). Self-improvement Layer 4 closed.
2. **Both proposals had to be rejected.** Builder hallucinated state names (`disqualified`, `needs_enrichment`) that don't exist. Actual states: `pending_qualification`, `qualified`, `suppressed_stale`, `rejected_hard_filter`, `archived`, `held_pending_corroboration`. Same class of bug as Run #329's `pending_human_review` — runtime hallucination.
3. CC20 brief written + fired in parallel by Jon while Lead audited. Worker merged at `6265432`. Added 3 grounding tools (read_tool_signatures, read_db_schema, read_skill_catalog) + multi-source enum extractor + system prompt mandate. Worker surfaced `suppressed_deprioritized` as legacy DB state not in enum.
4. **CC20 verified live**: session 13 landed Proposal #3 with REAL states (pending_qualification, qualified, suppressed_stale, rejected_hard_filter). Builder grounded against truth this time. Layer 5 closed.
5. Jon's directive: "hallucinations cannot happen on this app with any of the builders and agents" — platform-wide invariant, not Builder-specific.
6. **Full hallucination audit landed:** `docs/hallucination-audit-2026-05-29.md`. 10 LLM call sites enumerated. 3 HIGH risk (scout runner, trajectory summarizer, meeting summarizer — two of which pollute downstream LLMs). 5 MEDIUM. 2 LOW. Four architectural patterns proposed: A (self-teaching tool errors), B (Pydantic on JSON-emit), C (grounding tools), D (pollution-chain isolation).
7. **Anti-hallucination brief stream drafted (H1-H4):**
   - `briefs/h1-self-teaching-tool-errors.md` — ~120 LOC. Platform-wide tool-error self-teaching format. Foundation brief.
   - `briefs/h2-scout-intake-pydantic.md` — ~150 LOC. Pydantic on scout emission + reason_code allowlist enforcement.
   - `briefs/h3-trajectory-summarizer-pydantic.md` — ~150 LOC. Pydantic on trajectory summary + retry + Builder provenance framing.
   - `briefs/h4-meeting-summarizer-pydantic.md` — ~150 LOC. Mirror of H3 for meeting → Floating Artemis pollution.
8. Jon firing H1+H2+H3 in parallel via terminal-Lead RIGHT NOW. H4 awaits decision on whether to fire 4-in-parallel or stage after H1-H3.

**Active state:**

- Branch HEAD: `lead/j6a-granola-integration` = `6265432` (CC20 merged)
- Proposal #3 (kind=agent, target_id=17, status=pending, builder_session_id=13) — CORRECT this time, awaiting approval after H1-H4 all land. Task #51 tracks this.
- Two rejected proposals (#1 + #2) preserved as evidence of the hallucination class
- The `suppressed_deprioritized` DB drift is on H2's plate to resolve (legacy state cleanup)

**Decision log (in this turn):**
- Both proposals rejected because Builder hallucinated state enums (not failure of mechanism — failure of grounding)
- Anti-hallucination is a PLATFORM-WIDE invariant per Jon, not Builder-specific
- H1-H4 sequence proposed (H1 foundation → H2/H3/H4 specific surfaces) — Jon chose parallel fire
- Approve Proposal #3 deferred until H1-H4 all land (task #51 banked)
- M1 (memory: trajectory → observation) BLOCKED by H3 — must fire after H3 so memory writes from validated content

**Five layers of self-improvement hollowness, now all diagnosed:**
| Layer | Bug | Closed by |
|---|---|---|
| L1 producer/data | Summaries failed via GC + KeyError + race + FK | CC10-CC15 ✅ |
| L2 producer/content | Snapshots thin; extraction invisible | CC16-CC17 ✅ |
| L3 consumer/discovery | UI never passed target_id; no Inbox | CC18 + Inbox ✅ |
| L4 consumer/execution | Adapter dropped tools | CC19 ✅ |
| L5 consumer/grounding | Builder hallucinated facts when proposing | CC20 ✅ |
| **L6 platform/persistence-amplification** | **Other LLM surfaces also hallucinate; pollution amplifies through chains** | **H1+H2+H3+H4 (firing now)** |

**The chain of empirical findings (this session, in order):**
1. Memory audit → 1 row in 11 tables → P4 unstarted
2. Hollowness audit → definition_proposals=0 → L4 root cause = adapter dropping tools
3. CC19 → L4 fixed, proposals land
4. Smoke verification → Builder hallucinated state names → L5 root cause = no grounding tools
5. CC20 → L5 fixed, Builder grounds against truth
6. Hallucination audit → realized L5 fix was per-surface; platform-wide grounding/validation needed → H1-H4

**The patterns Lead has internalized from this session:**
- "Substrate complete" claims are never sufficient — verify runtime + DB row count + end-to-end loop fires
- Workers' self-reports are claims, not evidence — re-verify spot-check critical claims
- LLM-emitted JSON without Pydantic = pollution vector
- Surfaces that feed other LLMs are amplification vectors — worst-shape risk
- Self-teaching error messages are the cheapest unlock (single-turn recovery vs silent failure)

**Last updated context:** 2026-05-29 mid-evening. H1+H2+H3 fired in parallel right now. H4 drafted, awaiting fire decision. Proposal #3 deferred for engine.commit smoke after H1-H4 land. Next instance: read this section + `docs/hallucination-audit-2026-05-29.md` + the 4 H-briefs to pick up.

---

## 2026-05-29 — Hollowness audit + Layer 4 diagnosis + CC19 + Option B locked

**What landed:**
1. `docs/memory-audit-2026-05-29.md` — memory keystone audit. 11 tables, 1 row total. Substrate complete through P3, P4 (agent integration) unstarted. M1-M6 brief sequence proposed.
2. `docs/hollowness-audit-2026-05-29.md` — wider taxonomic pass across all surfaces. Classification on 5 dimensions. Found 3rd parallel hollowness: `definition_proposals=0` despite CC10-CC18 + 35 summaries + 10 builder_sessions.
3. **Manual self-improvement consumer smoke (Lead, via HTTP API):** Created Builder session 11 targeting `marketing.qualifier.brief_composer` (4 trajectory summaries). Builder LLM produced substantive diagnostic + drafted complete revised definition. **Could not call `propose()`** — its own response: *"The `propose` tool isn't wired into this session's tool catalog."*
4. **Layer 4 root cause:** `ClaudeCodeAdapter.complete()` at adapter.py:100-162 silently drops `request.tools`. Marketing pipeline works because it uses `.run_with_tools()` (different adapter method, wired via MCP/CC1-CC2). Builder uses `.complete()` which has never been tool-capable. Same fingerprint as every other hollowness layer.
5. **Provider capability matrix established:** anthropic/gemini/openai/openrouter all support tools in `.complete()` natively. codex/lm-studio are text-only fallbacks. claude-code is the structural special case (tool-capable only via MCP path).
6. **Option B chosen** (over Option A=anthropic-first cascade for Builder, Option C=hybrid). Rationale: preserves subscription-only as personal-instance Artemis ships to more employees; MCP infrastructure becomes universal tool path for Builder + Floating Artemis + Pipeline AI Panel + future surfaces.
7. **CC19 brief written:** `briefs/cc19-builder-mcp-tool-execution.md`. ~600 LOC cap. Adds 5 Builder tools to MCP server with `builder_session_id` scoping. Modifies `ClaudeCodeAdapter.complete()` to route through MCP when `request.tools` non-empty. Builder integration is minimal (contextvar + short-circuit). After CC19, `definition_proposals` gets its first row.

**The four hollowness layers in self-improvement** (all now diagnosed; CC19 closes the last):
- Layer 1 (producer/data): trajectory summaries broken by GC footgun + KeyError + race + FK violation. Fixed CC10-CC14.
- Layer 2 (producer/content): snapshot too thin + extraction visibility bug. Fixed CC16-CC17.
- Layer 3 (consumer/discovery): UI never passed target_id + no Inbox surface. Fixed CC18 + Proposals Inbox.
- Layer 4 (consumer/execution): Builder LLM declares tools but adapter drops them. **CC19 fixes (in flight).**

**The memory hollowness (separate parallel layer):** 11 tables, 1 row. Trajectory summaries bypass memory. Pipeline signals bypass memory. Floating Artemis amnesiac. Audit doc has M1-M6 sequence; **M1 is next surgical brief after CC19** (trajectory summary → memory observation, ~80 LOC).

**Wider hollowness findings (from docs/hollowness-audit-2026-05-29.md):**
- 🟢 Healthy: marketing pipeline (290 runs), OKR Studio personal scope (4 obj/20 KR/30 activity), Floating Artemis sessions (102), CC17 tool_invocations (358).
- 🟡 Shallow: Writing Studio (1 profile/7 examples/9 sources — sparse), meetings (1 summary/0 raw_inputs), only 1 pipeline (marketing.main).
- 🟠 Dormant: skills (1 skill / **0 agent_skills** — no agent has any skill linked), automations (0 rows after 1056 LOC), gcal_events_cache (0 despite active integration), dev_projects (1 project / 0 messages), personal_todos (0).
- 🔴 Hollow: self-improvement consumer side (Layer 4 — CC19 fixes), memory writes from agents (M1-M6 sequence).

**Meta-patterns Lead observed about previous Opus session's "substrate complete" claims:**
- (a) substrate ≠ behavior — declaration of "shipped" was made on code merged, not runtime exercised
- (b) tests passed without exercising integration loops
- (c) UI scaffolding outran integration backbone
- The CC10-CC18 discipline (verify runtime + DB row count + end-to-end loop) is the antidote — keep applying it.

**Provider switching reference (Jon asked):** anthropic/gemini/openai/openrouter swap seamlessly for tool-using surfaces (all support `request.tools` natively in `.complete()`). claude-code requires MCP path (`.run_with_tools()` today; after CC19, also via `.complete()` when tools present). codex + lm-studio are text-only — switching to them on a tool-using surface silently loses tools (CC19 adds warning log to catch this future-hollowness pattern).

**Branch state (corrected from earlier confusion):**
- `lead/j6a-granola-integration` HEAD = `064c301`. Does **NOT** contain the Inbox feature.
- `worker/proposals-inbox` contains Inbox commits (c8e0179, 5b74958) — NOT yet merged into j6a.
- `f6ab956` (Inbox placement fix from Worker) lives in a worktree. Per Jon's direction, **deferred to a future UI pass**.
- Terminal-Lead instructed to merge `worker/proposals-inbox` → `lead/j6a-granola-integration` next so CC19 + M1 can build on the Inbox foundation.

**Open questions for next exchange:**
- After CC19 merges + Lead verifies first-ever proposal lands, fire M1 (memory: trajectory summary → memory observation).
- The placement fix (`f6ab956`) sits in worktree pending UI pass.
- OKR Studio personal scope is healthy; expansion to Marketing team is queued.

**Last updated context:** 2026-05-29 session was triggered by Jon's "do the memory audit, then broader hollowness audit." Net result: 2 new audit docs, 1 critical brief (CC19), Option B decision logged, provider capability matrix established. Next instance: read this section + the two audit docs + `briefs/cc19-builder-mcp-tool-execution.md` to pick up.

---

This is the continuity file. If a Lead session rolls over or gets closed, the successor session reads this FIRST, then `ARTEMIS-OS-MASTER-PLAN.md`, then `HANDOFF.md`, then `STREAMS-2026-05-26.md` (when it exists), then catches up via `git log --oneline -20 lead/j6a-granola-integration`. Last 5-10 exchanges are at the bottom so a successor has the conversational thread.

---

## Lead operating principles (read FIRST, before anything else)

These are non-negotiable. Codified after the previous Opus session declared "near completion" but missed three layers of hollowness underneath. The pattern that produced that miss: assuming substrate completion implies behavioral completion. Don't repeat it.

1. **Never assume — check directly.** If a doc says "X is done," verify X works at runtime, not just at code-merged level. "Pipeline executor walks 22 nodes" ≠ "scouts produce real signals." Query the DB. Run a smoke. Trace from claim to evidence.

2. **Don't trust single signals.** A populated `system_prompt` field doesn't mean the LLM sees it. A rich seed loader doesn't mean the DB rows are loaded. Sample-of-one isn't proof; check the full chain.

3. **When you find a gap, ask "what's the elegant solution," not "what's the smallest patch."** The regional_news adapter brief from earlier in this session was a small patch on the symptom. The tool-execution architecture is the elegant solution. Bias toward "what's the right shape?" over "what's the quickest fix?"

4. **Worker self-reports of "done" are claims, not evidence.** Reports must include the acceptance assertions (cURL outputs, psql row counts, test pass lines, browser smoke snapshots). Files-in-tree doesn't equal committed. Committed doesn't equal merged. Merged doesn't equal working in the UI.

5. **Suspicion is a useful tool. Use it on substrate before declaring milestones.** If something feels too clean — a 22-node DAG that completes in <1 second with zero signals — that's a signal to investigate, not a signal to celebrate.

6. **Push back on Jon when his framing implies a check you haven't done yet.** He explicitly values pushback over agreement. If he says "I think X is broken" — verify directly, don't take his word and don't dismiss his instinct either. He's usually right but for different reasons than he thinks.

7. **Propose, don't dictate. Then commit.** Lay out the trade-offs. Recommend. Wait for Jon's call. Once made, execute decisively. Don't second-guess after green light.

8. **Don't set LOC caps that fight a brief's own completeness criteria.** Repeated miss this session: F3 brief said "≥12 urgency_tiers" (only 9 scouts have them), P3 brief said "≥20 tools AND ≤950 LOC" (contradictory — 24 tools can't fit). Workers correctly resolved toward completeness and flagged it. When a brief enumerates N deliverables, either size the cap to N×per-item cost or drop the hard cap and say "ship the enumerated set, report the diff stat." A LOC cap is a calibration signal, never a contract that overrides correctness.

---

## Active state

### ✅ PHASE BH CLOSED — 2026-05-27 (full chain, visually verified)

The marketing pipeline runs end-to-end on the Claude Code subscription, zero API cost: scout fetches live news → emits signal via MCP tool → qualifier applies Josh's §4 rules (qualify / hard-filter / suppress-stale) + writes a rich brief → Gate 1 suspends holding qualified signals → **the approval card renders real content** ("4 qualified", reason codes LEADER_TRANSITION_FORMAL + POLICY_EDTECH_TIME_LIMIT, the LAUSD screen-time brief + evidence). Lead confirmed in-browser (screenshot captured this turn).

**Merge chain on lead/j6a-granola-integration:** F1 `4c8fdd4` → F2 `7ad56b0` → F3 `40cdf0b` → (P1 `e9356db`, P4 `6769fe5`, P2 `40fa7b9`) → F5 `9c885e9` → F6 → CC1 `22cca3c` → CC2 `cd87142` → CC-fix `a0d8880` → CC4 `6cf7ae8` → CC5 `a6524c3`.

**The six hollowness layers, all caught only by running it, each smaller than the last:** (1) data loading, (2) runtime injection, (3) tool execution, (4) invocation task, (5) provider tool-use (the deep one — subscription MCP path), (6) qualifier tools + gate-card DB read. Never once accepted "it looks done."

**Next:** Signal Playbook (SP1→SP2, D7) → PIPE6. Banked cleanups: stale pre-CC5 thin approval cards, ~8 locked worker worktrees, cost-dashboard reads claude total_cost_usd not token sums, per-node editable pipeline instructions, scout_runner legacy-path deprecation. **NEW BANKED 2026-05-28:** the Agent Builder is functionally domain-agnostic (verified by reading `agent_builder.py` — zero marketing refs; works on generic agents/builder_sessions/definition_proposals tables; skill-suggestion mechanism is generic), BUT `artemis/builder/routes.py` imports `_auth`/`_errors` from `artemis/marketing/routes/` (those files are GENERIC shared helpers per their own docstrings, just misplaced). Move them to `artemis/routes/_shared/` + update importers so the platform-not-marketing-only architecture is visible in the code structure — trivial cleanup, worth doing before non-marketing domains build.

### Robustness + API-key findings (2026-05-27, Jon asked "is it solid + are agents missing keys")

**Is it solid? Logic yes, dispatch NO (fix = CC7).** Robustness check ran pipeline #2 → it FAILED: "Orphaned queued run (executor never started)", 6 scout signals emitted but stuck pending (run never executed). Root cause: all 3 dispatch sites in `routes.py` do bare `asyncio.create_task(_execute_pipeline_run(run_id))` — the task ref is discarded, so the event loop's weak ref lets the GC collect it before it runs (documented Python footgun). Intermittent: run #1 survived, run #2 GC'd. The pipeline LOGIC is solid (run #1 full chain; run #2 proved stale-dedup does NOT death-spiral repeat runs — scouts still emitted 6 new). **CC7** (`briefs/cc7-pipeline-dispatch-durability.md`) fixes it: retain task refs + re-dispatch orphaned queued runs on sweep. HIGH priority — solidity gate before SP1 + before cron use.

**Are agents missing API keys? YES (4 scouts blocked).** Tool inventory: working free (no key) = news_api (Google News RSS → regional_news/federal_funding/leadership_transition), state_doe (RSS), board_minutes (BoardDocs). Blocked stubs: legislative→LEGISCAN_API_KEY (free key), starbridge→STARBRIDGE_API_KEY (paid/proprietary), federal_funding-partial→grants.gov+federal_register (FREE public APIs, just unimplemented), procurement→portal scraping, linkedin→scraping. **CC6** (`briefs/cc6-free-api-scout-sources.md`) claims the free wins (grants.gov + federal_register real now, legiscan client ready-pending-free-key). starbridge (paid), linkedin/procurement (scraping) = separate later efforts.

**Parallel plan:** CC7 (dispatch fix, high-pri) + CC6 (free-API sources) fire in parallel (independent files: routes/scheduler vs tools). After CC7 confirms solidity (Lead smoke: 3 back-to-back runs all execute) → SP1.

**RESOLVED 2026-05-27:** CC6 merged (`e43923f`), CC7 merged (`4141f37`). **CC7 smoke PASSED — 3 back-to-back runs all dispatched + executed, no GC-orphan. Dispatch durability is solid (platform-wide, not marketing-specific).** CC6 full-pipeline smoke in progress (confirming federal_funding/legislative emit from grants.gov/Federal Register). b3_consolidation event-loop flakes banked as test-infra cleanup.

**Strategic doc written (Jon's platform-thinking ask):** `docs/pipeline-authoring-principles.md` — distills the 6 Phase BH hollowness lessons + the durability guarantees/boundaries into authoring PRINCIPLES + an "is my pipeline solid?" checklist, structured to ground the AI Pipeline Builder so it builds solid pipelines by conversation WITHOUT engineers. CC7's fix is platform-wide (generic execution engine), so every future pipeline inherits durable dispatch. **Banked: bake `pipeline-authoring-principles.md` into the AI Pipeline Builder's system prompt/grounding** (so the Builder enforces P1-P8 + runs the checklist before presenting a pipeline as ready). Durability layers: guaranteed now = durable dispatch + orphan re-dispatch + per-node isolation + wall-clock bound; NOT yet = crash-resume of in-flight runs, auto per-node retries, strong idempotency (future "durability hardening" effort, not needed pre-production-load).



**Phase:** SCOUT HALF CLOSED ✅ — subscription MCP tool-use WORKS. CC1 (`22cca3c`) + CC2 (`cd87142`) + smoke-fix (`a0d8880`) merged. CC3 real smoke: scouts fetched live news + emitted **23 real signals** via `claude -p --mcp-config`, zero API cost. The 5-layer-deep provider blocker is solved; the MCP tool pattern works for any agent. BUT the eyes-on-glass downstream check found the chain is **HALF-closed**: all 23 signals stuck at `pending_qualification`, content/gates/deliverables SKIPPED, `campaign_briefs=0` — the original acceptance "Gate 1 shows real content" is UNMET. Root cause: qualifier/content agents DECLARE tools (`signal_queue.update_status`, `signal_briefs.write`, etc.) but P3 never IMPLEMENTED them (scout tools only); the MCP server silently drops unknown tool names so the qualifier can't transition signals. Fix = **CC4** (`briefs/cc4-qualifier-content-tools.md`): implement the qualifier/content tools (mostly real vs existing tables: signal_queue, campaign_briefs/brief_snapshots, rulesets; districts.get stubbed). Then the full chain flows. CC4 is the genuine Phase BH close. NOTE: "22 signals = done" nearly slipped past us — only the downstream node-state check caught the half-close (never-assume, again). Cost caveat: the claude-code adapter records only last-turn tokens + DROPS claude's total_cost_usd — don't trust agent_runs token sums for cost (fix in C-cost-dashboard). FK-import bug (MCP process must import pipelines.models) caught+fixed by the smoke (a0d8880) — was masked by CC1's test conftest.

(superseded) **Phase:** PHASE 2 code-complete; F6 merged. **5TH HOLLOWNESS LAYER found + blocks loop-close: provider tool-use gap.** F6 made scouts TRY to act (no longer conversational), but they report "I don't have access to the tools" because the claude-code provider adapter can't do tool-use and the cascade never leaves it (+ empty API keys). This is a PROVIDER FORK decision for Jon (see Open Questions) — Option A (anthropic key + switch marketing agents to provider=anthropic) vs Option B (build tool-use into claude-code adapter). Lead recs A now, B banked. Loop closes the moment a tool-capable provider runs the scouts. ALL the plumbing below the provider is verified working (run_agent builds the 8-tool registry, 0 dropped; run_turn forwards specs).

(superseded) **Phase:** PHASE 2 code-complete; CLOSING SMOKE REVEALED A 4TH HOLLOWNESS LAYER. Real-LLM pipeline run `967e4933` succeeded but emitted ZERO signals. Root cause: scouts respond conversationally ("I have the X Scout spec. What's your ask?") because `agent_executor` invokes `run_agent` with no user_message → falls back to `agent.goal` (a descriptive sentence, not an imperative task). Agents are never told to ACT. Tools wired, prompts rich, but no "execute now" trigger. ALSO found: regional_news missing signal_queue.write (P1 gap — other 8 scouts have it). Fix = `briefs/f6-agent-invocation-task.md` (imperative invocation task in agent_executor + regional_news tools + re-seed). Cost data captured: ~$0.11/run for 9 scouts at single-turn behavior (will rise to ~$0.30-0.50 once tool-use rounds fire; far under the $50 cap). F6 is the true loop-closer for Phase BH.

(prev) **Phase:** PHASE 2 NEARLY COMPLETE. F1/F2/F3/P1/P4/P2/F5 all merged + verified. Only **P3 (tool implementations)** remains — fired to terminal-Lead. After P3 merges + post-merge re-seed + real-LLM pipeline smoke, Phase BH is DONE and PIPE6 is next. check.sh currently green except the single known j5b Jira flake (m5b FK-isolation failure does NOT reproduce in full-suite runs — banked anyway). Latest verified state: all 9 scouts' reason_codes_emitted match Josh's spec exactly; F2 system-prompt injection confirmed rich (6654 chars for regional_news); P2 tool loop proven e2e (scout LLM → signal_queue row).

(prev) **Phase:** PHASE 1 COMPLETE. F1 (`4c8fdd4`), F2 (`7ad56b0`), F3 (`40cdf0b`) all merged to `lead/j6a-granola-integration` and verified directly. F4 design landed + signed off. Combined F2+F3 smoke proves the runtime layer is no longer hollow: `marketing.scout.regional_news` system prompt is now 6654 chars (was ~616) with all 7 expected sections present. **Layers 1 (data) + 2 (runtime) of the three-layer hollowness are fixed. Layer 3 (tool execution) remains — that's Phase 2.** IMPORTANT: scouts still won't emit signals to signal_queue until Phase 2 lands tool execution; the LLM now sees rich instructions but has no `signal_queue.write` tool to act on them. Next: P1/P2/P3/P4.

**Key discovery from F4 design pass:** the tool-execution infrastructure mostly already exists. `artemis/agent/loop.py` has the full tool-use loop. `artemis/agent/tools.py` has `ToolRegistry`. The bridge from `agent.tools` (DB column) to actual tool implementations is the only missing piece. P2 is therefore ~500 LOC, not 1-2 weeks. Total Phase 2 effort revised down: ~2-3 days wall clock with parallelism.

**Working theory of where Artemis OS is:** Substrate is complete (PIPE1-5, executor, live-view, approval cards, run history, connectors, Builder, blueprints UI display). The hollow-shell problem Jon was sensing is real and verified — 3 layers stacked:

1. **Data layer:** seed parsers fail for 6+ fields. Only ~30% of blueprint depth loads to DB.
2. **Runtime layer:** `run_agent()` only injects ~30% of what IS loaded into the LLM call. Persona voice, urgency tiers, failure modes — all ignored.
3. **Tool layer:** tool resolution is stubbed (`tool resolution is not yet implemented. Running with no tools.`). Scouts can't write `signal_queue` rows because the `signal_queue.write` tool doesn't execute.

Plus a fourth issue: Josh's spec is duplicated across 4 places (`decisions/campaign-signal-spec-v1.md`, byte-identical copy in `docs/marketing-ops-v1/`, re-encoded Python list in `seeds/reason_codes.py`, partial inline copies in each agent blueprint). Single source of truth doesn't exist yet.

**Full picture:** `docs/blueprint-audit-2026-05-26.md`.

---

## Position in master plan

The work in this session (Phase 1+2 blueprint/runtime/tool-execution rebuild) is a **corrective insertion** into the master plan, not a planned phase. The previous Opus session ended believing scout adapters were the next gap; this session discovered the hollowness layers underneath. So our work is between "substrate complete" and "real scout adapters" in the original sequence.

**The original next-priority list from HANDOFF.md (2026-05-23):**
1. Real scout adapters → **subsumed by our Phase 1+2** (blueprint rebuild + runtime injection + tool execution closes this for real)
2. **PIPE6 — Workflows + Automations sunset + auto-migrate to Pipelines** ← next-next after our Phase 2 lands
3. Real Slack OAuth + Gate 1 DM delivery
4. Comprehensive UI polish pass
5. Test infrastructure pass
6. Smaller backlog (Agents tab loading race, M3 transition wire-up, JSONB MutableDict audit, AI button bubble-catch, trajectory_summarizer JSON bug)

**Master plan section that needs updating** (Jon's call to edit):
- `docs/ARTEMIS-OS-MASTER-PLAN.md` "Where we are" → Operations slab subsection should note the hollowness discovery + that the Phase 1+2 rebuild closes the gap before PIPE6 fires.
- D6 lock is already documented. PIPE6 implementation is already on the priority list. No new decisions required.

**Sidebar confirmed (from browser smoke):** Operations tabs visible today include Automations (6), Skills (3), Pipelines, Agents (9), Workflows (4), Memory (3). PIPE6 will delete Automations + Workflows after migrating their rows to Pipelines.

---

## Decisions made this session

| Date | Decision | Why |
|---|---|---|
| 2026-05-26 | Canonical Josh spec = `decisions/campaign-signal-spec-v1.md`. Kill the duplicate. | Jon's call. One file. |
| 2026-05-26 | Blueprint inline reason-code tables are pre-Josh mockup content. Don't preserve them. | Jon confirmed they're stale. |
| 2026-05-26 | Pattern B: Josh's spec content is injected into LLM call at runtime via a parser, not re-encoded in seeds. Blueprints become voice/focus docs; Josh's spec carries operational data. | Jon: "if B is better long-term let's get it done." |
| 2026-05-26 | Add a "Primary scouts" column to Josh's spec reason code table so the spec drives the scout↔code routing. | Cleanest single-source pattern; one Josh edit reflows everywhere. |
| 2026-05-26 | 4-stream parallelism cap. Sequential dependencies honored (F1 → F2/F3; F2 → P2). No worker self-merges. | Mess-avoidance. Jon's explicit concern. |
| 2026-05-26 | Workers must include self-tests in their report-backs (cURL + psql + check.sh). I do the browser smoke from this session. | terminal-Lead can't browser-smoke. Pattern keeps Jon out of manual walkthroughs. |
| 2026-05-26 | Functionality > polish. Workers do not polish prose/styling/error messages unless functionality requires it. | Jon: "right now i want to get functionality working the looks of it we can fix after." |
| 2026-05-26 | Phase 3 (Builder editing surface) deferred. Maybe killed. Revisit after Phase 2 lands. | Editing surfaces for fields that don't do anything at runtime are decorative. |
| 2026-05-26 | This session's `briefs/scout-adapter-regional-news.md` is shelved. Premature given the tool-execution direction. Will be rewritten as `news_api.search` tool implementation under Phase 2 P3. | Adapter approach becomes unnecessary once tool execution is real. |
| 2026-05-26 | Cost cap v1 policy: keep existing per-run cap, raise default to $50 (env-configurable via `ARTEMIS_SCOUT_COST_CAP_USD`), no per-call cap, INFO log per scout run, separate cost-dashboard UI stream (C-cost-dashboard) queued post-Phase 1. | Jon's "let it work before you kneecap it" instinct + need for empirical cost data before tightening. |
| 2026-05-26 | Lead operating principles codified at top of LEAD-SESSION-LOG. Successor sessions inherit "never assume, check directly, propose elegant solutions" guidance. | Previous Opus session declared near-completion while substrate was hollow underneath. Don't repeat. |
| 2026-05-26 | Master plan updated with Phase BH (corrective insertion). PIPE6 confirmed as next-next after our Phase 2 lands. | Protect against future sessions thinking they're "almost done." |
| 2026-05-26 | Lead doc suite committed (`e80a4e4`) for durability after terminal-Lead's `git checkout` clobbered the uncommitted master-plan edit. | Continuity docs must survive session loss / careless git ops. Don't leave them uncommitted. |
| 2026-05-26 | `reason_codes_emitted` column = derived cache of Josh's spec (Option A). Seed sources it from the spec; CASE override-preservation removed; no per-agent override. F5 brief implements. | Honors "Josh's spec = single source." Fixes the stale-value + double-injection bug found in the live DB. Operators edit the spec, not the agent row. |
| 2026-05-26 | Banked: m5b `test_reason_code_system_injection` (pre-existing FK-isolation, legacy scout_runner path) + j5b Jira flake. | Legacy path superseded by tool execution; don't sink time. j5b already exempt. |
| 2026-05-26 | scout_runner (`artemis/marketing/scout_runner.py`) is the LEGACY path; tool execution (P2/P3) supersedes it. Flagged for eventual deprecation. | Two scout-execution paths shouldn't coexist long-term. Revisit after Phase 2. |

---

## Plan (committed)

**Phase 1 — Foundation, ~2 days, 4 streams**

| Stream | Worker | Owner files | Depends on |
|---|---|---|---|
| F1 — Josh-spec parser | Codex | `artemis/marketing/josh_spec.py` (new), `decisions/campaign-signal-spec-v1.md` (add column), `artemis/marketing/seeds/reason_codes.py` (rewrite to read parser), DELETE `docs/marketing-ops-v1/Campaign Signal Spec v1.md` | — |
| F2 — Runtime injection | Sonnet (isolated worktree) | `artemis/builders/executor.py` | F1 merged |
| F3 — Seed parser repairs | Codex | `artemis/marketing/seeds/marketing_agents.py` (regex fixes for urgency/failure/notes) | F1 merged (different files but seed ordering matters) |
| F4 — Tool-exec architecture brief | Lead (me, this session) | New: `docs/tool-execution-architecture.md` | — |

**Phase 2 — Blueprint rebuild + tool execution, ~3-4 days, 4 streams**

| Stream | Worker | Owner files | Depends on |
|---|---|---|---|
| P1 — Scout blueprint rebuild | Sonnet | `docs/marketing-ops-v1/agents/scout/*.md` | F1 merged |
| P2 — Tool registry + LLM integration | Claude Code | `artemis/builders/executor.py`, new tool registry files | F2 merged (file conflict on executor.py) |
| P3 — Core tool implementations | Claude Code | `artemis/tools/*.py` (new), maybe `artemis/marketing/*.py` for signal_queue.write etc. | F4 design brief landed |
| P4 — Qualifier + content blueprint rebuild | Sonnet | `docs/marketing-ops-v1/agents/{qualifier,content}/*.md` | F1 merged |

**Phase 3 (deferred):** Builder editing surface for blueprint fields. Decision after Phase 2 lands.

---

## Open questions waiting on Jon

- **PROVIDER FORK (blocks Phase BH loop-close).** The 5th hollowness layer: the `claude-code` provider adapter (`artemis/providers/claude_code/adapter.py`) cannot do tool-use — it flattens everything to a `claude --print` text prompt and ignores `request.tools`. Verified: the adapter has zero tool handling; the cascade (`resolve_adapter`) tries `provider` first and only falls through on auth/availability errors, so claude-code (always available) is never bypassed; both ANTHROPIC_API_KEY and OPENAI_API_KEY in `.env` are empty (len 0). Marketing agents are seeded `provider=claude-code`. Net: scouts now actively TRY to use tools (F6 worked) but report "I don't have access to the tools this task requires" because the provider can't emit tool calls. This conflicts with the committed invariant "Provider cascade is CLI-first; agents default to claude-code (no API key)" — that invariant predates tool execution.
  - **Option A (pragmatic unblock):** Jon adds a real ANTHROPIC_API_KEY to `.env` + switch marketing agents to `provider=anthropic` (seed change + re-seed). Loop closes immediately, no code. Costs API $ (~$0.11–0.50/run, trivial at demo scale). Abandons "free CLI" for marketing agents.
  - **Option B (architecturally aligned, bigger):** teach the claude-code adapter tool-use — expose artemis tools as an MCP server claude-code connects to (claude-code CLI supports MCP natively), or use the claude-code SDK tool mechanism. Keeps the free model. Real provider engineering, ~1-2 weeks.
  - **RESOLVED 2026-05-27: Jon has NO anthropic key and won't add API cost (the whole point is relying on the paid CLI subscription). Option A is OFF. Must do Option B. Requirement: keep a path to add an API key LATER.**

### Provider tool-use — research findings (claude-code-guide agent, 2026-05-27)

The authoritative answer on doing custom tool-use via the Claude Code subscription (no API key):

1. `claude --print` headless CAN do tool use, but ONLY claude-code's BUILT-IN tools (Read/Write/Edit/Bash/Grep/WebSearch/WebFetch). No custom-tool registration in headless `-p`.
2. **`claude -p --mcp-config <json>` is the ONLY subscription-compatible path to custom tools.** Claude connects to an MCP server and calls its tools autonomously during the headless run (it runs its own agent loop). This works with subscription auth.
3. The **Claude Agent SDK (Python)** supports custom in-process tools BUT requires `ANTHROPIC_API_KEY` — it does NOT authenticate via the CLI subscription. So it's the "add key later" path, not the subscription path.
4. MCP per-run context (DB session, agent_id, run_id, permission allowlist): MCP server is a separate process. Options: env/args at launch, shared DB, or HTTP callback into the app.

**Implication / locked direction:** The subscription path = an MCP server that re-exposes the artemis tool registry (P2/P3), launched per-run by the claude-code adapter via `--mcp-config`, with run context passed at launch. claude-code runs its own tool loop (artemis's `run_turn` is bypassed for claude-code-provider agents). The "add API key later" path is ALREADY built: the AnthropicAdapter forwards tools through artemis's `run_turn` loop correctly — it just needs a key. Both coexist via the provider cascade. This is the F4-equivalent design for the subscription era; needs its own design doc + brief. Real scope (~1-2 weeks). This is now the critical path to close Phase BH — the loop cannot close without it given the no-API-key constraint.

  - Note: terminal-Lead left a plain uvicorn running on :8000 (merged code) from its smoke. May conflict with Lead's preview server.

## Resolved this turn

- F4 Q1-Q5 all green. Q4 (cost cap) elaborated: default raised to $50, env-configurable, no per-call cap, observability INFO log added, cost-dashboard UI as follow-up stream `C-cost-dashboard` (queued post-Phase 1).
- Master plan `docs/ARTEMIS-OS-MASTER-PLAN.md` updated with Phase BH section under "Where we are" → Operations slab. Future Lead sessions inherit the corrective-insertion context.
- **F1 merged** as commit `4c8fdd4` (2026-05-26 12:14 EST). Verified directly: 17 reason codes in DB, primary_scouts column in spec, duplicate deleted, parser smoke OK, qualifier rules + state nuances all parse, 8 tests pass, 297 LOC (under 300 cap), the 1 check.sh failure is the pre-existing j5b Jira flake unrelated to F1. Codex made one independent judgment call — skipped DB storage of primary_scouts, runtime reads parser only — which is the right call. Phase 1 wave 2 (F2 + F3) is unblocked.

---

## Jon's working style — captured

- **Conversational before implementing.** Wants to talk through approach before code lands. Stated explicitly. Honor this.
- **Provides goals; leans on Lead for planning.** Don't ask him to design; ask him for the goal.
- **Values pushback over agreement.** Said "push back wherever this doesn't land." Don't be sycophantic.
- **Functionality > polish.** "Looks of it we can fix after."
- **Allergic to mess from parallel agents.** Wants clear coordination + Workers that self-test.
- **Has previously had agents claim done without commits.** Calibration concern; codified as "Worker 'done' = git log shows commit hash, not files-in-tree."
- **Lost previous Opus session by accident.** Hence this log.
- **Capacity:** 2 Claude Max + 2 Codex accounts. Real parallel throughput.
- **No remote pushes ever.** Local-only repo discipline.

---

## Where Jon needs to manually intervene

- Pasting Lead-authored prompts into the right place (terminal-Lead vs Codex CLI vs Workers). I label each brief explicitly with `**Paste-into:**`.
- Pasting Worker report-backs into this Lead chat for analysis.
- Confirming merges are clean (terminal-Lead does the merge; Jon sees the result).
- Anything that requires UI judgment that I can't see (rare — I can browser-smoke from this session via preview_* MCP tools).

---

## Conversational thread summary (most recent first)

**Turn N+10 (current):** CC4 merged (lead `6cf7ae8`). Real run: qualifier applied Josh's §4 rules — 22 qualified, 5 rejected_hard_filter, 3 suppressed_stale — wrote rich briefs to `signal_queue.qualification_json.brief`, gate_1 suspended `awaiting_approval` holding them. **Data chain CLOSED** (qualification intelligence real, not just transitions). Lead verified directly (rich brief in DB confirmed). Remaining gap: Gate-1 CARD renders thin (signal_count:0, brief_preview:null) because `_build_pipe4_context` reads structured keys from node_states, but claude-code agents return only text + commit via tools → briefs are in the DB but not on the card. Wrote CC5 (read-side fix: `_build_pipe4_context` reads qualified signals + briefs from signal_queue; backfill existing gate approval to verify without a 13-min run). Encodes the MCP-era principle: agent effects live in the DB, downstream context reads from DB not node_states. CC5 is the final visual close. Sixth layer caught — each smaller than the last (provider → qualifier tools → card rendering), converging.

**Turn N+9:** CC1 verified by Lead (6 tests, per-agent scoping proven: regional_news↔news_api, legislative↔legiscan, both share signal_queue_write) → merged. CC2 returned; terminal-Lead caught the worker's "3 pre-existing failures" misreport (3rd worker misreport this session) — they were worktree-`.env` artifacts (passed 40/40 on clean lead in main repo) → CC2 merged. CC3 real smoke: **23 real signals emitted via subscription MCP tool-use, zero API cost** — scout half CLOSED. terminal-Lead caught+fixed an FK-import bug the smoke exposed (a0d8880). Lead's eyes-on-glass downstream check found the chain HALF-closed: signals stuck pending_qualification, content/gates skipped, campaign_briefs=0 — qualifier/content agents declare tools P3 never implemented. Wrote CC4 brief to implement them (signal_queue.get/update_status/find_*, signal_briefs.write, ruleset_storage.*, districts.get stub) → closes the full chain. Honest recalibration to Jon: scout half is a huge win (hard part solved), but "Gate 1 real content" acceptance unmet until CC4. Flag: provider corrections (--max-turns doesn't exist → wall-clock timeout; --strict-mcp-config added; hyphenated flags) made by Lead in CC2 brief + design doc, committed by terminal-Lead (eb92281). Two-agents-one-repo coordination hazard noted.

**Turn N+8:** Jon confirmed: no API key, subscription-only is a hard constraint (a "design limitation"), so Option B is mandatory; requirement = keep an add-key-later path. Jon asked the key design question: "how do we ensure claude-code calls the right tool for the right agent?" Lead answered (4 layers: per-run MCP scoping to agent.tools + built-ins disabled + per-tool enforcement + per-run process isolation) and wrote the full design doc `docs/claude-code-mcp-tool-execution.md` (F4-equivalent). Design: claude-code becomes the agent runtime (runs its own loop), an artemis MCP server (`python -m artemis.tools.mcp_server`, stdio, per-agent scoped, reuses P2/P3 registry + ToolContext from launch args) is the tool provider; adapter launches `claude -p --mcp-config --allowedTools mcp__artemis__* --disallowedTools <builtins> --max-turns`; run_turn bypassed for claude-code tool runs; the add-API-key-later path (anthropic + run_turn) already works. 5 open questions for Jon's sign-off (txn commit granularity, concurrency, max-turns cap, MCP lifetime, failure surfacing). Streams CC1 (server) → CC2 (adapter) → CC3 (Lead smoke). Awaiting sign-off before build.

**Turn N+7:** Closing smoke ran (run `967e4933`) — succeeded but ZERO signals. Lead diagnosed the 4th hollowness layer: scouts respond conversationally because agent_executor invokes run_agent with no imperative task (falls back to agent.goal). Wrote `briefs/f6-agent-invocation-task.md` (imperative invocation task by role + regional_news tool fix + re-seed; headline acceptance = real run produces ≥1 signal). Captured first cost datapoint (~$0.11/run for 9 scouts single-turn). Jon then said: document everything + lock Signal Playbook + slot it. Lead wrote `docs/signal-playbook-design.md` (Option B locked: table canonical, markdown = one-way export, structured CRUD UX, under Marketing, v1 = reason codes), added D7 to master plan, slotted Signal Playbook AFTER Phase BH and BEFORE PIPE6 in STREAMS roadmap. About to commit all docs.

**Turn N+6:** F5 verified green (all 9 scouts' codes match Josh's spec, non-scouts empty; check.sh green except j5b). Jon asked 3 questions — Lead answered from live DB evidence: (a) blueprints = 16 separate markdown files, seed logic + personas = 1 Python file, 1 DB row each; (b) Josh's codes ARE centralized in `decisions/campaign-signal-spec-v1.md` — agent `reason_codes_emitted` is now a derived cache (edit spec → re-seed → all agents update; never edit individual agents); (c) blueprints + personas applied (brief_assembler 0→1540, writing_studio_adapter 0→1589 chars; runtime composes regional_news to 6654 chars) — caveat: verified fields populated + flowing, NOT a literal screenshot-vs-blueprint diff (offered that audit as a separate pass if Jon wants certainty). P3 reported: Worker shipped full catalog (24 tools, 9 real/15 stubs, 35 tests pass, check.sh clean, live Google News RSS fetched 25 real items) but overran LOC cap 1629 vs 950. Lead approved the merge — the cap contradicted criterion #2 (≥20 tools), my brief's fault; Worker correctly chose completeness. Told Jon to have terminal-Lead merge + post-merge smoke, then Lead does the end-to-end browser proof. **Calibration lesson logged: stop setting LOC caps that fight a brief's own completeness criteria.**

**Turn N+5:** P2/P1/P4 all merged (HEAD `40fa7b9`). Lead verified P2 e2e green (9/9 tool tests against test DB — scout LLM → real signal_queue row). terminal-Lead made + owned a `git checkout HEAD -- .` mistake that clobbered the uncommitted master-plan Phase BH edit; Lead recovered it from context (only that one tracked file was hit; untracked session docs survived). Lead found terminal-Lead's Test A diagnosis half-right: fresh-seed gives empty reason_codes_emitted (correct) BUT live dev DB had STALE codes preserved by the seed CASE logic — causing double-injection (agent_executor stale codes + F2 spec codes). Jon delegated all 3 decisions to Lead. Lead executed: (1) **committed the doc suite** `e80a4e4` for durability; (2) **Test A → Option A** — wrote `briefs/f5-reason-codes-from-spec.md` (seed sources reason_codes_emitted from Josh's spec, removes CASE override-preservation, updates test); (3) **banked Test B (m5b legacy FK-isolation) + Test C (j5b Jira flake)**. Also wrote `briefs/p3-tool-implementations.md` (the full tool catalog, against P2's actual code shapes). F5 + P3 ready to fire in parallel (different files).

**Turn N+4:** Jon said "do your thing" (full autonomy). Lead exercised judgment on Phase 2 sequencing: discovered P3 (tool implementations) imports from P2's registry.py + context.py, so P3 can't fire until P2 lands. Revised wave: fire P2 + P1 + P4 in parallel (no file collision — P2 owns artemis/tools/ + executor.py bridge, P1 owns scout blueprints, P4 owns qualifier/content blueprints), HOLD P3 until P2 merges. Also moved the signal_queue.write reference tool INTO P2 so P2 proves end-to-end (pipeline → real signal), making P3 pure pattern-following. Wrote 3 briefs: p2-tool-bridge.md, p1-scout-blueprint-rebuild.md, p4-qualifier-content-blueprint-rebuild.md. Key coordination decision: P1/P4 must NOT re-seed the shared dev DB (two isolated worktrees re-seeding from divergent blueprint copies would clobber); they verify via load_marketing_agent_rows() (no DB write); Lead runs the re-seed ONCE after both merge. P3 brief deferred — will draft against P2's actual merged code, not the design doc, in case the Worker deviated. Handed P2/P1/P4 to Jon to fire.

**Turn N+3:** F2 + F3 both reported and merged. F2 (terminal-Lead → Sonnet Worker, merged `7ad56b0`, 227 LOC, 8 tests). F3 (Codex direct, `40cdf0b`, 110 LOC, 11 tests). Lead verified both directly: git log shows clean F1→F2→F3 chain; `_build_system_prompt` exists + wired at executor.py:270; DB shows 9 scouts with urgency_tiers + 16 with failure_modes + 16 with notes. **Combined smoke: regional_news system prompt now 6654 chars with all 7 sections (persona, reason codes, state nuances, urgency, failure modes, context) — runtime hollowness fixed.** Codex flagged that the brief's "≥12 urgency_tiers" bar was wrong — only 9 scouts define urgency tiers and it correctly refused to fabricate them for qualifier/content agents. Lead acknowledged: that's the operating principle working (don't fake data to hit a number); brief calibration was off, not the data. Phase 1 done. Next: P1 (scout blueprints), P2 (tool registry+wiring), P3 (tool impls), P4 (qualifier+content blueprints).

**Turn N+2:** F1 fired to Codex; report came back green. Lead verified directly (operating principle #1) via git log + psql + parser smoke. Commit `4c8fdd4` is on lead branch, 17 reason codes in DB, primary_scouts column in spec, duplicate deleted, 8 parser tests pass, 297 LOC under cap. The 1 check.sh failure (j5b Jira) is pre-existing, in HANDOFF as known exempt. Codex made one independent judgment call — skipped DB storage of primary_scouts, runtime reads parser only — which is the correct call. F2 (runtime injection — Sonnet Worker via terminal-Lead, isolated worktree) and F3 (seed parser repairs — Codex direct) briefs written and ready to fire in parallel. Lead handed both to Jon.

**Turn N+1:** Jon green-lit "lets do it" + signed off all 5 F4 questions, with Q4 elaborated to "(a) raise default to $50, env-configurable, observability log + follow-up cost dashboard." Lead produced four artifacts: STREAMS coordination doc, F1 brief, F4 design brief, master plan Phase BH entry. Cost dashboard added as new stream C-cost-dashboard (low priority, post-Phase 1). Operating principles section codified at top of session log so successors inherit "never assume, check directly."

**Turn N (preceding):** Jon asked for a session-log file to capture context against rollover. This file is the answer.

**Turn N-1:** Jon: "yes on the rules" + flagged that terminal-Lead can't browser-smoke. Asked Lead to handle all prompt-routing + report analysis + further planning. Lead laid out the routing convention (4 places: Lead session, terminal-Lead, Codex, Workers) and the post-merge browser-smoke ownership pattern.

**Turn N-2:** Jon: "lets do it but we need to make sure we dont get messy with all these parallel agents working, they also need to test their work so i dont have to manually walk everything." Lead locked in 8 coordination rules: sequential deps honored, single merger, file ownership per stream, explicit acceptance tests per Worker, no remote pushes, functionality > polish, hard LOC caps, single coordination doc.

**Turn N-3:** Jon: "the blueprints were also filled with mockup reason codes so we probably need to do a rebuilt of those... lets build it how we want." Lead pivoted plan: Josh's spec becomes runtime-injected, blueprints become voice/focus docs, broken seed regexes for reason codes get removed entirely instead of fixed. Five-phase stream plan, ~7-8 day wall clock.

**Turn N-4:** Jon: "do both" + asked whether Josh's signal doc was already a single source. Lead investigated — found 4-place duplication: `decisions/campaign-signal-spec-v1.md`, byte-identical copy in `docs/marketing-ops-v1/`, re-encoded Python list, inline blueprint copies. Wrote `docs/blueprint-audit-2026-05-26.md` capturing the full hollowness picture.

**Turn N-5:** Jon described scouts live under Agents tab, app should be AI-agent-maintainable, blueprints likely incomplete. Lead pivoted from the regional_news adapter brief to a deeper audit — found the seed loader parses 12+ fields but DB shows ~30% population (15/16 scouts have empty reason_codes_emitted; 16/16 have NULL urgency_tiers, failure_modes, implementation_notes, lifecycle_status). Identified `run_agent()` only injects 3 of the rich fields into the LLM call. Identified `tool resolution is not yet implemented` as the load-bearing structural gap.

**Turn N-6 (start of session):** Jon told Lead he's a fresh instance picking up cold after the previous Opus session closed accidentally. Asked for familiarization with the project, conversational mode before implementing.

---

## Files Lead has created or substantially edited this session

- `briefs/scout-adapter-regional-news.md` (now shelved per Pattern B decision; preserved for reference)
- `docs/blueprint-audit-2026-05-26.md` (the audit findings)
- `docs/LEAD-SESSION-LOG.md` (this file)
- `docs/STREAMS-2026-05-26.md` (operational coordination)
- `briefs/f1-josh-spec-parser.md` (paste-ready for Codex)
- `docs/tool-execution-architecture.md` (F4 design brief, awaiting Jon's sign-off)

## Files Lead has read deeply this session

- `docs/ARTEMIS-OS-MASTER-PLAN.md`
- `docs/HANDOFF.md`
- `decisions/campaign-signal-spec-v1.md`
- `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`
- `artemis/marketing/seeds/marketing_agents.py`
- `artemis/marketing/seeds/reason_codes.py`
- `artemis/marketing/scout_runner.py`
- `artemis/marketing/scout_sources/{__init__.py, base.py, _stub_base.py, regional_news.py}`
- `artemis/marketing/scout_intake.py`
- `artemis/pipelines/node_executors/agent_executor.py`
- `artemis/pipelines/routes.py` (run endpoint)
- `artemis/builders/executor.py` (run_agent)
- `artemis/builders/models.py` (Agent ORM)
- `artemis/scouts/regional_news/{scout,client,mapping}.py`
- `public/js/features/operations-shell.js` (Operating Blueprint render)
- `briefs/m5b-scout-execution-path.md`

## DB queries Lead has run

- Census of all 16 marketing agents — field population per agent (system_prompt length, tools count, reason_codes count, persona, urgency_tiers, failure_modes, implementation_notes, lifecycle_status).

## Browser smoke checks Lead has done

- Pipelines surface → Run on Marketing Pipeline → verified executor walks scouts, overlay component wires correctly (history link → `#/pipeline-run-history`, Cancel button text + disabled logic, `skipped` in TERMINAL_STATUSES per `39f65ba` patch).

---

## Update protocol

After every meaningful exchange (decision made, brief written, Worker report received, blocker discovered):

1. Update **Active state** at the top.
2. Add a row to **Decisions made this session** if a decision landed.
3. Update **Plan** if sequencing changed.
4. Add to **Open questions waiting on Jon** if a blocker appeared.
5. Prepend a new **Conversational thread summary** turn at the top of that section.
6. Update **Files Lead has created/edited/read** if new files touched.

Keep it terse. This file is a continuity tool, not a chronicle.
