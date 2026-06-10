# Session State / Resume Point — 2026-06-10

**Read this first in a fresh session.** Captures where we are after a very long working session, written
right before relocating the repo off `~/Desktop` (which re-roots the Claude session). Pairs with the
auto-loaded memory index (`MEMORY.md`). Opus Lead = the planning/verify/merge agent (Jon's "me").

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
- **RADAR (not now): mockup cleanup.** The current campaigns + their docs are mostly MOCKUPS. Leave ONE
  campaign as the **Friday presentation mockup**; clean up the rest (campaigns + docs) afterward. The
  incoming-signal marketing history should move from Artemis to Callie (handled lossless in slice 1, migrated
  at Callie build).
- Parked: marketing-pipeline bug fixes (snooze/reject/qualifier); the QA test drafts (#39/#40 — harmless
  "ignore" markers in live).

## Working discipline (also in memory — verify against current code)
Lossless (no deletes; supersession only). Verify the EFFECT in a real browser before "done" (the toolbar saga
proved synthetic tests give false confidence — Claude_in_Chrome on a worktree preview is the way). Test DB
vars: alembic uses `ARTEMIS_DB_URL`, pytest/conftest uses `ARTEMIS_TEST_DB_URL`; per-agent test DBs. Lead
merges via FF after verifying; workers do-NOT-merge-report in isolated worktrees. `dotenv override=False`
invariant in `artemis/__init__.py`.
