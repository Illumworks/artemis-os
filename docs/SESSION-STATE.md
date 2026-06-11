# Session State / Resume Point — 2026-06-10

**Read this first in a fresh session.** Captures where we are after a very long working session, written
right before relocating the repo off `~/Desktop` (which re-roots the Claude session). Pairs with the
auto-loaded memory index (`MEMORY.md`). Opus Lead = the planning/verify/merge agent (Jon's "me").

---

## ⏩ CURRENT STATE — 2026-06-10 (late session checkpoint, read FIRST)

**Move:** done. Repo lives at `/Users/artemis/Artemis/artemis-os`, git HEAD intact. App restart =
`launchctl kickstart -k gui/$(id -u)/me.artemisos.app` (NOT start-app.sh — dual-bind footgun). App serves the
working-tree `public/` live; backend code changes need a kickstart.

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
- **Callie C2 (Codex, in flight):** `briefs/callie-c2-multibot-routing.md` — multi-bot routing, dedicated
  `/events/callie`, per-app HMAC+token, **registry-driven so N named agents = a Slack app + an integrations
  row (no new code)**, agent-aware scope (Callie channels/DMs = marketing). Callie app INSTALLED+VERIFIED
  (bot `U0B9S32PTAM`); creds in gitignored `.env.callie` (Codex stores encrypted then deletes it).
  **On C2 deploy:** ONE `launchctl kickstart` makes C2 + QW1 + lint + slice-1 + C1 all live; then Jon
  repoints Callie's Slack Request URL to `…/events/callie` + Retry; Lead verifies live in `campaign signals`/
  `Marketing Campaigns`/her DM.
- **C3 (after C2):** Callie's domain tools (Writing Studio reads: Message Compass, claims register, Coherence
  Map; performance data; analyst posting), the **deliverable→editable-WS-draft body fix (QW2 folded here)**,
  the retired-history handoff to Callie. Plan: `docs/callie-build-plan.md`.
- **C4:** escalation Callie→Artemis + delegate-to-worker.
- **Marketing-routing (systemic):** Gate notifications must route to Callie's channel, never Artemis's DM
  (QW1 was the interim owner-DM suppression; C2/C3 is the proper home). Slice-1 only scoped the chat loop,
  NOT the pipeline posting path — that's why QW1 was needed separately.
- **App-Modes web-nav:** dropped (cosmetic; the split that matters is the Slack one).

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
