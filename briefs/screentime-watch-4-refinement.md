# Brief — Screen-Time Watch #4: data-quality refinement (topic precision + legislative source)

**Owner:** app-seat Lead (me) → Sonnet worker in an isolated worktree.
**Read first:** `docs/screentime-watch-plan.md` + the existing engine in
`artemis/screentime/` (Brief 1). **Why:** the first live run produced off-topic
noise (literacy/reading-retention items, not screen-time) and a false-favorable
from the "exempt" keyword; the `legislative` source returned 0. This brief makes
the output genuinely about screen-time so it's fit to show Angela.

**Context — what the first run showed:** 763 gathered → 10 stored "real moves" that
were all reading-retention / literacy ed-policy, NOT screen-time. `legislative:
ok:0` (the core bill source found nothing). `state_doe` was crippled by a missing
Playwright browser (now installed — chromium is available). Two precision bugs:
(1) generic ed-policy passes the "real moves" filter; (2) "exempt" fired the
carve-out → 🟢 favorable on a reading-retention exemption.

## Scope (all in `artemis/screentime/` — NO migration, NO shared-scout edits)

1. **Screen-time topic-relevance gate (the core fix)** — in `filters.py`:
   - Add a gate, applied BEFORE store/classify, that keeps ONLY findings genuinely
     about **instructional/student screen-time or device-time limits** (and
     evidence-based-tool exemptions to such limits). Drop generic ed-policy
     (literacy, reading retention, curriculum approvals, test scores, etc.).
   - Make it **config-driven + tunable** (like the stance config): a
     require-terms / exclude-terms set in config, overridable without a deploy.
     Default require-set centered on screen-time/device-time/"screen use in
     schools"; exclude-set for the literacy/reading-retention noise we saw.
   - Borderline items: optionally a cheap **tool-less LLM relevance check**
     (`complete_with_fallback(primary="codex", fallback="claude-code")`, `model`
     INSIDE the CompletionRequest) only for items that pass keyword pre-screen but
     are ambiguous — keep it cheap, don't classify everything with the LLM.
   - This gate runs ahead of stance classification, so off-topic items never reach
     the classifier (which fixes the exemption false-positive at the source).

2. **Harden the stance classifier** — in `classifier.py` (belt-and-suspenders):
   - A carve-out / "exempt(ion)" signal counts toward 🟢 favorable ONLY when the
     item is screen-time-relevant (which, post-gate, it always is — but assert it).
   - Keep the existing negation-awareness ("no exceptions").

3. **Investigate the `legislative` source returning 0** (the most important ask —
   screen-time *bills*):
   - Read `artemis/scouts/legislative/` to learn how it queries (LegiScan? API key?
     keyword config?) and how `screentime/scout_fanout.py` invokes it.
   - Determine WHY it returned 0: (a) missing API key (LegiScan etc.), (b) the
     screen-time keywords/states aren't reaching it, or (c) genuinely no matching
     bills in the window. **Report which.**
   - If it's a screentime-side config/wiring issue → fix it in
     `screentime/scout_fanout.py` (how WE call the scout). If it's a missing API
     key or a bug in the shared scout itself → **STOP and report** (do NOT edit the
     shared `artemis/scouts/legislative/` module — it's shared with the campaign
     pipeline; Jon/Lead will handle a key or a coordinated scout fix).

## Constraints
- ORG RULE: no dependency added/upgraded. No migration. Lazy provider imports.
- Stay in `artemis/screentime/` + additive `config.py`. Do NOT edit shared scouts,
  marketing/campaign, `main.py`, `scheduler.py`.
- Test DB `artemis_test_screentime`. Don't restart the app or run live scouts
  (Lead does the live re-run after merge).

## Verification (observe the EFFECT)
- Unit tests proving the topic gate **drops** the exact noise we saw
  (reading-retention, literacy-mandate, curriculum-approval items) and **keeps**
  real screen-time items (instructional screen-time limit; an evidence-based-tool
  exemption to a screen-time rule).
- A reading-retention "exempt" item no longer classifies 🟢 favorable (it's dropped
  by the gate before the classifier).
- Tunability: changing the topic config flips whether a borderline item passes.
- `import artemis.main` clean; all existing screentime tests still pass.
- A clear written finding on the `legislative` 0-result cause + what you changed
  (or why it needs Lead/Jon).

**Deliverable:** commit to a worktree branch (do NOT merge). Report: the topic-gate
design + config shape, the classifier hardening, the **legislative investigation
finding** (key vs config vs no-bills, and what you did/flagged), test results, and
`import artemis.main` confirmation.
