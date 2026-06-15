# Session State / Resume Point — 2026-06-10

**Read this first in a fresh session.** Captures where we are after a very long working session, written
right before relocating the repo off `~/Desktop` (which re-roots the Claude session). Pairs with the
auto-loaded memory index (`MEMORY.md`). Opus Lead = the planning/verify/merge agent (Jon's "me").

---

## ⏩ CURRENT STATE — 2026-06-14 (read FIRST)

**MEMORY UPGRADE PHASE (current active track — postdates WS backlog + proactivity).** Goal: Artemis recalls
the *right* memory when asked. Order this session: M1c (done) → **M1 (done)** → M3 (next, per Jon).
- **M1 (semantic conflict detection): MERGED `aa2043c` + LIVE 2026-06-14** (migration 0088 applied to live DB;
  kickstart, `healthz` 200). Embedding-shortlist (cos≥0.60, top-5) → LLM contradiction judge via the
  `resolve_adapter_async` cascade (claude-code CLI, no API key needed) → CONTRADICT≥0.85 auto-supersede,
  borderline → `memory_conflicts` row `resolution=NULL` for review. **MAJOR FINDING:** conflict detection was
  wired into `write_observation_with_conflict_check`, which has ZERO non-test callers — so BOTH the new semantic
  detector AND the pre-existing rule-based `detect_conflicts` were **dead code, never running in prod.** Fix
  (`aa2043c`): `_run_conflict_checks` (consolidator.py:298) now runs both detectors inside `apply_consolidation`
  (:499) — the function the live sweep actually calls. So contradiction-catching runs in prod for the FIRST
  time. See [[verify-actual-call-path]] (the trap that almost shipped twice — the worker re-wired into the same
  dead function on attempt 1; Lead caught it by re-tracing non-test callers).
  - **Verified before merge (didn't trust the report):** 5 live-path smoke tests drive the real sweep and assert
    the DB EFFECT (stale obs `superseded_by` set + `memory_conflicts` row + retrieval flip), plus precision
    (additive fact / temporal refinement → supersede nothing) and no-provider fail-safe (no supersede, no
    crash). 43 conflict/detector tests green. Real judge verified live (CMO-vs-sales → CONTRADICT 0.93).
    Migration 0088 chains 0087→0088 cleanly. 3 suite failures confirmed PRE-EXISTING on main (stale
    `test_c3_no_provider_path` patches a nonexistent sync `resolve_adapter`; 2 asyncpg timeout flakes).
  - **Precision-first holds:** auto-supersede only on high confidence; fail-safe to review queue on uncertainty/
    no-provider; lossless (supersession only). FP rate ~0 on labeled set.
  - **Harness honesty:** conflict detection only affects FUTURE writes, so a static-corpus R@1 before/after does
    NOT move — proof is the live smoke, not an R@1 delta. Miss-category breakdown (post-M1c corpus): 2 genuine
    recall gaps (trend_snapshot JSON payloads vs NL queries) + 2 near-dup ranking (the same #514/#515 Slack
    pair). M1 doesn't target those (they're ranking/representation, not conflicts) — a NL-summary embedding for
    trend_snapshot JSON is a candidate next lever.
  - **`confirmed_bias` weight tuning — STILL DEFERRED (Lead).** Worker re-validated on a 36-query set and
    recommends adopt (R@1 0.556→0.611, MRR 0.693→0.739, p95 down). Holding: measurement used a regenerated QA
    set; fold in as a small separate, independently-verified change (good to pair with M3).
- **M1b (dedup):** merged — near-duplicate consolidation, lossless (no deletes; supersession only).
- **M1c (ranking + recall): MERGED `cb7b04c` + LIVE 2026-06-14** (kickstart, `healthz` 200). Merged by Opus
  Lead in *this* session because the other Lead session was wedged ("can't connect" while this one was fine —
  stale worktree/process, not a real outage). What shipped:
  - `top_k` 50→150 (rescues genuine recall miss #715; latency flat — payload still limit-10).
  - **series-collapse** (`config/memory-retrieval.yaml: series_collapse: true`): at RANK time only, when the
    pool has multiple snapshots of one known time-series (`Momentum snapshot for <id>`), keep the LATEST and
    let distinct results fill freed slots. **Precision-safe by construction:** only recognized momentum-series
    are ever grouped; conversations/signals/arbitrary facts → `_series_key` None → never touched. `False`
    restores byte-identical pre-M1c behavior. **Zero storage mutations** (retrieval-only).
  - Harness `series_aware_credit`: a "current X" query now counts a hit if the result shares the target's
    series key — stops the QA from penalizing Artemis for correctly returning the *newest* snapshot. This is
    why the famous "R@1 = 0.375" was mostly a measurement artifact.
  - **Verified this session (didn't trust the report):** 49 retrieval tests green vs real `artemis_test`;
    storage-write grep clean; precision logic re-proven (distinct never dropped, non-series never grouped,
    off=byte-identical). Metrics: **R@1 0.375→0.542, R@10 0.833→0.917, MRR 0.479→0.630.**
  - **Lead decisions:** (1) KEPT series-collapse — it was already built+tested+safe, so ripping it out to match
    an earlier "skip the polish" guess would just discard good work. (2) `confirmed_bias` weight tuning
    **DEFERRED** — worker correctly left prod fusion weights untouched; don't change them on a 24-query set for
    ~+0.02 MRR. Revisit only with an expanded QA set. (3) Hard pair #514/#515 left open (n=2, not worth
    over-engineering).
- **M3 — identity-aware scope enforcement: MERGED `f4b09f1` + LIVE 2026-06-14.** The live personal-data
  exposure (marketing teammates could see the owner's personal tabs/memory) is CLOSED. **D8** (one app + RBAC,
  not two apps) + **D11** (floating Artemis for owner / floating Callie for marketing) + **F1** (future split,
  parked). See `docs/ARTEMIS-OS-MASTER-PLAN.md` D8/D11/F1, `briefs/memory-m3-identity-scope-enforcement.md`,
  [[project-single-app-rbac-decision]]. What shipped:
  - `artemis/identity/scope_policy.py` — single-source `allowed_scopes_for_email/_agent` + `ScopeAllowance`
    (FAIL-CLOSED: unknown/unresolved → deny, never all). Owner=`amiracentral@amiralearning.com`→all; marketing
    human→marketing-shared + own `personal:<uid>`; callie→marketing + agent:callie (NO personal/agent:artemis);
    artemis→all. Marketing teammates SHARE the marketing workspace (no teammate isolation v1).
  - Enforced at ALL read paths: HTTP memory API (`routes/memory.py`+`repository.py`, SQL-level), agent retrieval
    (`floating_artemis/memory.py:_enforce_agent_scope_set`), the `query_memory` TOOL + chat provenance
    (`tools/core.py`, `chat.py`), MCP paths. **Turn-boundary identity binding:** web turn entrypoints resolve
    the LIVE identity → trusted_agent_id (owner→artemis else→callie), overriding persisted/stale session
    metadata; default flipped from fail-OPEN "artemis" to fail-CLOSED "callie". D11 floating agent resolved
    SERVER-SIDE. **Agent builder owner-only** (`require_owner`, `marketing/routes/_auth.py`) — last vector.
  - **Verification (Lead, hard):** each worker pass caught a deeper hole — found the build wired into the right
    HTTP layer but NOT the agent tool path, then NOT the turn-identity binding, then the builder vector. Final:
    382 floating+M3 tests green; my own adversarial access-engine check passed; marketing-on-stale-artemis-
    session DENIED personal proven via the real path. **Lesson reinforced:** [[verify-actual-call-path]] +
    [[live-smokes-catch-real-bugs]] — test the door a real user uses, not the convenient one.
  - **Follow-up (defense-in-depth, non-urgent):** the builder's internal `_search_memory` doesn't scope-limit
    which agent_id the LLM queries — now MOOT for the leak (builder is owner-only) but a nice hardening later.

- **MEMORY UPGRADE PHASE — COMPLETE** (M1b dedup, M1c ranking/recall, M1 conflicts, M3 access — all merged +
  live).
- **PROACTIVITY ENGINE — ALREADY BUILT + WIRED + RUNNING (corrected 2026-06-14).** The roadmap memory said
  "next: proactivity" but it's done: `artemis/proactivity/commitments.py:send_commitment_followups` (real:
  due-soon/un-followed → routed Artemis-DM / Callie-channel Slack post) is registered on the scheduler
  (`scheduler.py`) which `main.py:126` starts at boot. Morning brief, OKR check-in, stale-review escalation,
  commitments follow-up, radar all wired. Migrations 0083/0086 applied; `p3-agency-writes` + radar merged.
  **NOT a build task.** Remaining = (1) prove it FIRES end-to-end live (a real commitment → real Slack
  follow-up; "wired ≠ firing" — the memory-phase lesson), (2) triage two UNMERGED refinement branches
  `worker/p2-proactivity-voice` + `worker/p3-tool-implementations` (merge if good / delete if stale). Lean
  brief for terminal: `briefs/proactivity-verify-and-triage.md`. Design ref (already implemented):
  `briefs/p2bc-commitments-engine.md` + `docs/p2-proactivity-build-plan.md`.

---

## ⏭️ NEXT UP — fire order for terminal (queued 2026-06-14)

Terminal's CURRENT task = `briefs/proactivity-verify-and-triage.md`. **After it finishes**, these are READY to
fire (Opus verifies + merges each). Grounded against code 2026-06-14 — only genuine gaps; reuse, don't rebuild.

**Agency-writes (P3) — accurate state:** the propose→confirm gate (`agency_gate.py`) + OKR writes + **Jira
writes** (`jira_tools.py`: add_comment/transition/assign/create) + **Calendar** are DONE/live. The ONLY gaps:
Gmail-send + Slack-send-as-Jon (two `NotImplementedError` executors), and they need Gmail/Calendar scopes
actually connected first.

**Fire order:**
1. ✅ **`briefs/fa-polish-confirm-path-badge-avatars.md`** — DONE + LIVE (`efe2ed6` + avatar repoint `a36c559`;
   migration 0089 applied; app restarted). Task 1 confirm-path registry FIXED (resume now builds from canonical
   `build_authorized_tool_registry` — gcal/gmail/slack/jira/granola present on confirm; un-gates #3). Task 2
   stuck FAB "2" badge FIXED (0089 adds `started_at > now()-interval '2h'` to the active-runs view; the 2 stale
   runs now excluded → badge clears). Task 3 avatars by server-resolved `metadata.agent_id`, now pointing at
   Jon's profile photos `/icons/Artemisprofile.jpg` (owner) + `/icons/callieprofile.jpg` (Callie) — both serve
   200; the app-logo `artemis.png` stays the brand mark. Callie-image dependency RESOLVED.
2. ✅ **`briefs/p3-google-multiaccount-and-reads.md`** — DONE + VERIFIED LIVE (merged `fafa334` 6/13; verified
   2026-06-14). 2 accounts connected (jon.fila@ = Calendar+Gmail+Docs; amiracentral@ = Docs only); 35 gcal
   events cached; live Gmail read works; Docs export uses marketing cred. 66 tests pass. ⚠️ **OPEN:**
   meeting→action-items→commitments is wired + unit-tested but NOT yet exercised on real data (no recent meeting
   had action items). Real test = the 6/15–6/16 meetings; summarizer runs every 2 min → should auto-create
   commitments. **Check Monday afternoon (2026-06-15/16).**
3. **`briefs/p3-encrypt-google-tokens.md`** (NEW — SECURITY) — Google `access_token`/`refresh_token` are stored
   PLAINTEXT (`google_docs/models.py:30-31`, since migration 0076; all other integration creds are encrypted).
   Encrypt at rest reusing the existing Fernet helper (`connectors/encryption.py`, `ARTEMIS_CONNECTOR_KEY`) +
   backfill the 2 live accounts; rolls in the `_GCAL_SCOPE` dead-constant cleanup. Adds a migration. Do this
   before/parallel to #4 — low effort, real exposure (a DB dump leaks live Gmail/Calendar access).
4. **`briefs/p3-agency-messaging-sends.md`** (EXISTING, READY) — Gmail-send + Slack-send-as-Jon executors on the
   proven gate. DEPENDS on #2 (Gmail scope, now done) AND on the confirm-path fix (done, item 1). Lead audits +
   live-tests (sends *as Jon*).

**Proactivity = verified LIVE (2026-06-14):** terminal's dry-run delivered a real follow-up DM to Jon
(checked=1/sent=1, routed personal_ops → Artemis token), synthetic seed cleaned up. Stale branches
`p2-proactivity-voice` + `p3-tool-implementations` deleted. Marketing→Callie channel path proven mocked; a live
one-shot is optional/available. **FA audit done** (read-only): engine healthy; notifications backend is a STUB
(5 endpoints 404; `background-sessions.js` fires create on every completion → silent fail) — "build or delete"
decision parked; `propose_edit` is a stub.

**P5 — Learning loop / skill capture: BACKEND DONE + LIVE 2026-06-14** (merged `ca3da2a`; migration 0091;
restarted). Brief `briefs/p5-learning-loop-skill-capture.md`. The loop: agent run → trajectory summary (was
live) → **skill distiller** (`artemis/builder/skill_distiller.py`, on-demand `POST /api/builder/agents/{id}/
distill-skills`, LLM via resolver, threshold ≥2-of-10 in `what_worked`, dedups) → human approves proposal →
**skill marked approved + assigned to the originating agent** → **injected into that agent's future runs**
(`executor.py:_inject_skills_into_prompt`, approved + tool-overlap + cap 3×~200tok) → **usage tracked**
(`usage_count`/`last_used_at`). Human-gated (only proposes), fail-closed, scoped to builder agents (not FA prompt) in v1.
  - **Lead caught the open seam:** the worker built all components but `_commit_skill` creates skills as
    `status=proposed` + UNASSIGNED, while injection needs approved + agent-assigned — so the loop didn't close.
    Fixed in `approve_proposal` (self-improvement proposals → set approved + assign from `citations.agent_id`)
    + added a true end-to-end closure test (the worker's tests hand-set state and masked the gap). Verified:
    15/15 P5 tests pass in isolation (1 recurring DB-contention timeout flake), loop proven closing through the
    real approve route. See [[verify-actual-call-path]].
  - **Frontend "Distill skills" button — DONE** (Haiku, merged `864e59e`): a per-agent button in the Proposals
    Inbox "New Summaries" section calls the distill endpoint and refreshes; new skill proposals appear under
    "skills with pending proposals." **P5 is now feature-complete end-to-end.** (Frontend served live — refresh
    the browser.)
  - **Skills-tab surfacing + semi-autonomous trigger — DONE + LIVE** (merged `2a7b87e`): the distill flow now
    lives in the **Skills tab** (Jon's expected home) — a "Discover skills from recent runs" button + pending
    skill proposals with Approve/Reject in the Proposed sub-tab (the Agents-sidebar inbox stays too). AND the
    distiller now **auto-fires fire-and-forget after every 5 successful runs per agent** (no migration; counts
    runs since the last self-improvement proposal), still human-gated. Auto-trigger logic tested (fires at
    5/10, not before, not on 6, fail-safe). Restarted.
- **P6 — self-evolution** (⚪ capstone; now UNBLOCKED by the memory upgrades — let execution traces accumulate
  first). Writing Studio backlog = ✅ all 5 done.

---

## ⏩ CURRENT STATE — 2026-06-11

**Move:** done. Repo lives at `/Users/artemis/Artemis/artemis-os`, git HEAD intact. App restart =
`launchctl kickstart -k gui/$(id -u)/me.artemisos.app` (NOT start-app.sh — dual-bind footgun). App serves the
working-tree `public/` live; backend code changes need a kickstart. NOTE: app binds `127.0.0.1` — health-check
`http://127.0.0.1:8000/healthz`, NOT `localhost` (resolves to ::1 first → false "down").

**P2 PROACTIVITY — merged + live (2026-06-11), pending Jon's live dry-run:**
- **Conversational Slack confirm** (no buttons): layer-3 yield posts proposal; reply "go"/"no" (haiku
  classifier, conservative→NEITHER falls through to normal turn) resolves via `confirmation_store` +
  `resume_after_confirm`. merged.
- **Voice:** brief + OKR check-in get an Artemis-voice LLM render pass (persona + voice_render), dry-witty-
  Jarvis, lint-clean, plain-text fallback so delivery never fails.
- **OKR attribution fix:** Jira scoped to `assignee = currentUser()` (Jon only); only self-logged OKR activity
  asserted as his work, everything else labeled `Context:`; no one-ticket→many-KR fan-out.
- **OKR reconcile loop (migration 0081):** check-in fires → TTL'd breadcrumb (KR snapshot + proposal, Monday
  TTL) → next personal-DM reply gets reconcile context injected → word-dump maps to specific KRs → proposes
  `update_okr_kr` (layer-3 → "go" applies). Clear is conversation-driven via `complete_okr_checkin` tool (NOT
  apply-clearing — multi-KR dumps survive). KR state shown in opener. DB at 0081 head.
- **OKR round-trip LIVE-VERIFIED 2026-06-12:** word-dump → `stage_okr_updates` (DB breadcrumb) → "go" →
  KR 7/9/11 `prog` written (62/78/72 from 30/50/60 baseline) + `okr_activity` rows with Jon's words; breadcrumb
  completed + cleared. Required two more subscription-path fixes: (a) gated tools served via the SUBPROCESS
  `mcp_server` (strips layer>2) + in-memory `confirmation_store` can't cross the process boundary → DB-backed
  breadcrumb staging (migration 0082 `staged_updates`); (b) the confirm classifier called AnthropicAdapter
  (NO API key) → crashed → defaulted NEITHER → "go" never applied + model falsely claimed "Applied" → replaced
  with a DETERMINISTIC keyword classifier. See [[fa-claude-code-adapter-strips-layer3]].
- **PENDING (closes the OKR flow):** `worker/p2-okr-done-bullets` — apply must append the accomplishment
  BULLET to the KR `done_bullets` (visible in OKR Studio), not just bump `prog`. Brief written; Jon caught that
  only the number moved, not the visible "what we did" text.
- **NEXT SEQUENCING (Jon's call 2026-06-12):** finish the **Writing Studio backlog** (`docs/writing-studio-
  backlog.md`, 5 items incl. "Ready for review → Callie pings Angela" orchestration bridge), THEN resume the
  **proactivity engine** (P2b commitments → P2c follow-ups). See [[roadmap-sequencing-ws-then-proactivity]].
- **Same-bug follow-up (background task):** the channel-relevance classifier has the identical no-API-key
  crash (defaults to silent) — latent, will bite when Callie next handles a non-mention channel message.

**Chapter 2 = personal assistant + Named agents. Build split: Codex=backend, terminal=FE, Opus=architect/
verify/merge. Workers run on `worker/<scope>` branches; Lead FF-merges to main.** (Hazard seen this session:
all agents share ONE working tree → branch confusion; my commits once landed on a worker branch. Watch for it.)

**DONE + merged to main:**
- **P1 Slack two-way (Artemis DM):** inbound hardened — bot-self echo filter, owner allowlist (Jon =
  `U09F3EPJXSQ`, in `integration_configs[slack].authed_user_id`), identity→handle_turn. LIVE-VERIFIED.
- **Personas:** Artemis v1.2.2 + Callie v1.1.3 adopted (`*-personality-profile.md`).
- **Output lint:** deterministic no-em-dash/no-emoji on named-agent outbound.
- **Slice-1:** Artemis DM personal-scoped (marketing surfaces/tools dropped; old marketing DM history retired
  lossless, tagged `callie_handoff_pending`). LIVE-VERIFIED ("Here. What do you need?").
- **Callie C1:** FA loop persona-parameterized by `agent_id` (`load_agent_profile`; Artemis byte-stable).
- **FE Friday polish** + **WS empty-state fix** (the "old Studio" = zero-draft fallback after the mock reset;
  now shows v5 "No drafts yet"). **QW1:** marketing Gate-2 approver-DM no longer hits Jon's personal DM
  (channel-only for the owner) — `human_gate_executor.py`.
- **Friday demo campaign:** 3 mock campaigns deleted; **real campaign #18 "Texas HB27…" built** from hot
  signal #624 (TX scope, brief, 4 deliverables w/ real content in `campaign_deliverables.metadata` + Slack
  cards). NOTE: editable WS draft *bodies* are empty (stub external adapter) — content not lost; real fix = C3.

**IN FLIGHT / NEXT:**
- **Callie C2 — DONE + LIVE-VERIFIED (2026-06-10, merged b6e8292).** Multi-bot routing, dedicated
  `/events/callie`, per-app HMAC+token, **registry-driven (agent #3 = a Slack app + an integrations row, no
  new code)**, agent-aware scope (Callie channels/DMs = marketing; Artemis DM still personal). Callie bot
  `U0B9S32PTAM`, creds stored encrypted (migration 0078 added `integrations.agent_id`). Live test passed:
  Callie replied in her DM + `campaign signals` (C0B9CHVC7KQ) + `Marketing Campaigns` (C0B8QE17DGQ), her own
  token, zero echo, Artemis untouched. The C2 restart also made QW1 + lint + slice-1 + C1 live.
  **Jon TODO:** remove the Artemis bot from the marketing channels now that Callie holds them.
- **C3a — DONE + live (merged worker/callie-c3a-analyst-toolset):** Callie's analyst tools —
  `get_message_compass`, `search_claims_register`, `get_campaign_performance` (layer 1, marketing-scoped),
  `post_analyst_message` (layer 3, posts to her channels via her own token, lint-clean). 57 tests green.
  **C3b/c/d — DONE + live + DB-verified (2026-06-10):** C3b marketing Gate cards post as Callie's bot;
  C3d deliverable body now in canonical `versions[0].content` (migration 0079 backfilled #18's 42-45 — they
  render); C3c ran live: 245 obs ingested into `agent:callie` memory scope, `callie_handoff_pending` cleared,
  Artemis scope untouched. 100 tests green; test DB repaired (artemis_test @ 0079). **Callie thread CLOSED.**
  Remaining LIVE checks for Jon: trigger a marketing gate → card as Callie; open #18 draft → body renders;
  ask Callie about retired history → recalls.
- **NEXT idea raised (Jon): hot-signal → `incoming signals` channel ticker** (detailed, NO buttons, view-only,
  C0B989DS5DZ). NOT done — it's queued `briefs/slack-signal-routing.md`. Dependency (signals-funnel redesign)
  has landed → buildable. **Needs reconcile for the Callie split:** raw ticker = pipeline/faceless;
  `campaign signals` = Callie's analyst lane; Artemis no longer posts (out of marketing channels).
- **Slack split COMPLETE:** Jon removed Artemis from the marketing channels; Artemis = personal DM only,
  Callie = marketing channels. **Future idea (Jon, 2026-06-10):** Artemis (overseer/delegate lane) helping
  manage Jon's contractor in the marketing DESIGN channel on his behalf — not yet scoped.
- **Still open (env):** the test DB (`artemis_test`) is in a dirty migration state (pre-existing, post-move) —
  Codex had to mock around it for C3a. Repair tracked separately; run `alembic upgrade head` vs the test DB.
- **C4:** escalation Callie→Artemis + delegate-to-worker.
- **Marketing-routing (systemic):** Gate notifications must route to Callie's channel, never Artemis's DM
  (QW1 was the interim owner-DM suppression; C2/C3 is the proper home). Slice-1 only scoped the chat loop,
  NOT the pipeline posting path — that's why QW1 was needed separately.
- **App-Modes web-nav:** dropped (cosmetic; the split that matters is the Slack one).
- **Callie/Artemis Slack polish — DONE + live (2026-06-11):** W1 Callie scoped marketing-only (per-agent
  surface allowlist; verified 8 surfaces, no Jira/Calendar/OKR leak — **add Jira+Calendar back here when scope
  expands**); W2 natural channel replies on `message.channels` + cheap haiku "should I respond?" gate (default
  silent) + @mention-asker only on cold-start / >5min re-engage (silent in active flow, never in DMs); W3
  `find_by_keyword` (signal+campaign search by bill number — verified HB27→#624+#18). Two stale-test failures
  fixed (g1_chat agent_id kwarg; j8 test's 2-col ON CONFLICT) — both test-only, prod `upsert_integration` was
  already 3-col. 356 tests green. **W2 live blocker found 2026-06-11:** campaign-signals (C0B9CHVC7KQ) +
  marketing-campaigns (C0B8QE17DGQ) are **PRIVATE** → Slack fires **`message.groups`**, not `message.channels`;
  Callie's app only subscribed to `message.channels`, so non-@mention private-channel messages never arrived
  (app_mention still worked). FIX (Jon, Slack-side, NO code): add **`message.groups`** to Callie's bot event
  subscriptions (groups:history already granted). Receiver already handles channel_type="group" via
  listen_channel_messages + channel allowlist. If reinstall prompted, re-verify her token. Then redo W2 step 1.

---

## The move that's happening (why this doc exists)
The repo is being relocated **`~/Desktop/Artemis` → `~/Artemis`** (whole workspace folder) to escape the
macOS Desktop/Full-Disk-Access fragility that wedged the shell mid-session. Repo becomes
`/Users/artemis/Artemis/artemis-os`. A migration script handles app-stop → move → carry Claude memory/history
to the new project key → fix the launchd plist → rebuild `.venv` → `git worktree repair` → reload → verify.
After it runs, Jon reopens Claude on `~/Artemis/artemis-os` and we resume from here.

---

## Where we are: Chapter 1 is DONE (marketing/content system) — all live
- **Composer v5** — feature-complete + hardened. The selection toolbar was rebuilt on ProseMirror's own
  update cycle (driven by `dispatchTransaction` + `view.hasFocus()` + `coordsAtPos`; whole paper editable so
  drags don't collapse; dismiss via outside-pointer). Also shipped: natural-tone chat, apply-to-document
  (fenced deliverable), picker fixes, claim precision + Disregard, sans-serif (no serif), equal 24/24 margins.
  ⚠️ The toolbar logic is hard-won — don't touch `updateSelectionState`/`positionNearSelection`/
  `showSelToolbar`/`hideSelToolbar`/`handleOutsidePointerDown` + the `.cv5-paper`/`.ProseMirror` padding
  without re-verifying in a real browser.
- **Composer Phase B** — claim Nearest-Approved click-to-replace; @mention autocomplete (`GET /api/users`);
  Slack-DM-on-mention (verified working live). Merged.
- **Campaign workspace pass** — real asset titles/types/status; Open + Remove-from-campaign; "Content Review
  Pending" lists all current reviews; "Approve for campaign" in the composer; two-way draft↔campaign linking
  (Add new asset / Link asset from the campaign; Attach-to-campaign from the composer). Merged.
- **Signals** — unified one-page worklist (ranked shortlist + collapsible inbox); funnel redesign (clickable
  cluster cards → "Start a campaign" → real campaign; converted-signal traceability; dismiss/reject captures
  a "why" for training); freeform clustering; hot cards use a full amber **border** (not a left bar).
- Earlier this arc: claims register (88 verbatim), Google Docs per-user OAuth, Track A identity (CF Access),
  always-on Mac (launchd + pmset + auto-login + cloudflared tunnel).

## Chapter 2 is NEXT: Artemis as a personal assistant (beat Hermes/OpenClaw)
Full plan: **`docs/artemis-pa-build-plan.md`** (+ competitive analysis of `agent-references/`). Bottom line:
Artemis's foundation (memory-graph + propose→confirm safety + domain connectors + in-app floating UI) is
already STRONGER than Hermes/OpenClaw; the engine is ~70% built. The gap is **proactivity (commitments +
cron) + Slack-as-a-two-way-channel + agency-writes.** Sequence:
- **P1 — Slack two-way** (chat with Artemis in her DM; FIRST). Jon creates the Slack app/tokens; we build the
  bridge.
- **P2 — Proactivity/commitments engine** (the differentiator) → **P3 agency-writes** → **P4 orchestration
  (then Callie reports up)** → **P5 learning loop/skill capture** → **P6 self-evolution (committed capstone,
  GEPA-style, gated by tests + LLM judge + human PR; foundations laid from P1).**
- Build split: **Codex = backend slices, terminal = FE, Opus = architect/verify/merge.** Per-agent test DBs;
  serialize heavy Codex runs.

## Agent architecture (design locked; build = Chapter 2)
`docs/agent-slack-architecture.md`. **Artemis** = top-level personal PA + overseer (1:1 Slack DM; personal +
ops + supervises sub-agents; marketing hidden in her personal app build). **Callie** (Calliope, muse of
eloquence) = marketing analyst sub-agent reporting to Artemis ("the analyst" vs the pipeline "ticker" — never
re-posts raw signals). **Named Agent Standard**: every Named agent gets persona+avatar, memory, proactivity,
agency-behind-the-gate, Slack presence, orchestration-awareness, defined domain, and is self-improving by
design — the agent builder must scaffold this. Workers stay faceless/ephemeral.
- Personas (repo root): `artemis-personality-profile.md` (existing v1.1, loaded by `floating_artemis/
  personality.py`) + `callie-personality-profile.md` (new v1.0 draft — **Jon is revising it**).
- Slack channels: `incoming signals` (C0B989DS5DZ, sales ticker) · `campaign signals` (C0B9CHVC7KQ, action) ·
  `Marketing Campaigns` (existing). The queued `briefs/slack-signal-routing.md` folds into Chapter 2.

## Open threads / pick up
- **Angela** is reviewing `docs/Angela-Writing-Memory-Review.docx` (38 proposed Writing-Studio voice rules,
  Approve/Reject per item). When she returns it: load the approved ones as active Writing-Studio rules, drop
  the rejected. Source proposals: `docs/angela-writing-memory-proposals.md`.
- **Personas — ADOPTED 2026-06-10.** `artemis-personality-profile.md` → v1.2.2 (partner-mode, autonomy
  0-3, principled challenger; salutation rule = no honorifics, "Jon" sparingly), `callie-personality-profile.md`
  → v1.1.3 (Mark dropped; "Compass" = the real Writing Studio **Message Compass**; Coherence Map kept; Callie
  reports ONLY to Artemis, may delegate to workers; analyst-not-ticker, owns `campaign signals` + `Marketing
  Campaigns`). Both committed + loader-verified. **Callie is still design-only (P4)** — only her persona exists.
  Output lint MERGED (67617d0, `briefs/lint-named-agent-output.md`): deterministic no-em-dash/no-emoji lint
  on named-agent outbound (Codex implemented, Opus Lead verified + merged); voice samples reframed
  calibration-only. NOTE: takes effect live only after the next app restart.
- **Chapter 2 P1 (Slack two-way)** — CORRECTION (2026-06-10 audit): the inbound plumbing was already
  SHIPPED by the J1/J9 series (built 2026-05-17/18, before state-capture tightened) — Events receiver +
  HMAC, dedupe, `route_inbound`→`handle_turn`→reply, Slack tools in FA registry, owner-mode credential UI,
  triage queue; a live Slack app is connected (active bot token, `bot_user_id U0AMNKUGXLP`). But it had
  NEVER carried a real conversation (172 inbound rows, all bot-self = an echo loop; `last_verified_at`
  null) and had no identity gating. **This session hardened the inbound boundary** (see
  `briefs/p1-slack-inbound-hardening.md`): bot-self echo filter, owner allowlist (fail-closed),
  identity→`handle_turn`. Built + tested (ruff/mypy clean, 240 FA tests green) **and LIVE-VERIFIED**:
  allowlist owner = Jon's personal `U09F3EPJXSQ` (NOT the Amira marketing bot `U0AMNKUGXLP` / amiracentral@
  `U0ADG7FQZJA`), written to `integration_configs[slack].authed_user_id`. First real Jon↔Artemis DM
  round-tripped 2026-06-10; echo loop confirmed gone (inbound held at 173, no bot-self re-ingest). Also
  required a Slack-side fix: App Home → Messages Tab → "Allow users to send messages" was OFF. **Next on
  this lane:** the personal DM still leads with *marketing* context (old session history) — that's the
  Artemis-personal vs Callie-marketing split (App Modes), future Chapter 2 work, not P1.
- **App Modes / Artemis-Callie Slack split (Chapter 2, in progress).** Re-scoped 2026-06-10: the real need is
  the *Slack agent-domain split*, not web nav. Documented in `docs/agent-slack-architecture.md` (tightened:
  Artemis DM = personal/ops/upgrades, **no unprompted marketing**; marketing only on explicit ask or a
  Callie-escalated decision; Callie owns marketing channels; remove Artemis from those channels once Callie
  is live). **Slice 1 brief READY:** `briefs/personal-scope-artemis-dm.md` (Codex) — personal surface scope
  for the DM, personal-scope prompt, and LOSSLESS retirement of the marketing-polluted DM history (tagged to
  hand to Callie later). Web nav hiding = shelved/cosmetic. Bringing Callie online = the next major build (P4).
- **Friday demo campaign — RESET DONE 2026-06-10.** The 3 mock campaigns (ids 14/15/16) + dependents were
  deleted (atomic; pipeline-run history preserved). A REAL campaign was created from hot signal #624
  ("Texas Personal Financial Literacy Course Requirement (HB27)") via the real Gate-1 promotion
  (`promote_signal_to_candidate`): **campaign_candidate id=18**, stage `human_gate_1`, `in_inbox`, family
  `general_growth`; signal 624 now `approved`. NOT yet run through the initiation wizard (scope/deliverables/
  proposal) — open choice: walk it live in the demo vs pre-build it. FE polish: `briefs/fe-friday-demo-polish.md`
  (terminal) removes the FE mock-campaign fallback + tidies composer/agent-monitor.
- **Callie C1 MERGED** (e41f2d9): FA loop persona-parameterized by `agent_id` (Artemis byte-stable; Callie
  persona loads). Next: C2 (multi-bot routing, dedicated `/events/callie` endpoint) — needs Callie's bot token
  (OAuth install of her app A0B9Q790Y9Y). Plan: `docs/callie-build-plan.md`.
- Parked: marketing-pipeline bug fixes (snooze/reject/qualifier); the QA test drafts (#39/#40 — harmless
  "ignore" markers in live).

## Working discipline (also in memory — verify against current code)
Lossless (no deletes; supersession only). Verify the EFFECT in a real browser before "done" (the toolbar saga
proved synthetic tests give false confidence — Claude_in_Chrome on a worktree preview is the way). Test DB
vars: alembic uses `ARTEMIS_DB_URL`, pytest/conftest uses `ARTEMIS_TEST_DB_URL`; per-agent test DBs. Lead
merges via FF after verifying; workers do-NOT-merge-report in isolated worktrees. `dotenv override=False`
invariant in `artemis/__init__.py`.
