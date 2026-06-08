# Build brief — Claim flags: precision tuning + "Disregard" action

**Agent:** terminal or Codex (detector = backend/Python; Disregard = FE + small backend). **Branch:**
`worker/claim-precision-disregard` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_claimprec`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`. Supersedes the
narrow "first ordinal" task (task_df580609 — fold it in). Context: on a real imported draft the detector
flagged rhetorical/non-claims (a question "How do we truly understand…?" and soft "what matters most…")
while correctly suppressing genuine claims that are already approved. Two fixes.

## Fix 1 — detector precision (`artemis/marketing/writing_studio/claim_detector.py`)
The "quiet, don't be annoying" bar (Jon) means FALSE POSITIVES are the enemy. Tighten candidate detection so
these are NOT flagged:
- **Questions** — a sentence ending in "?" is rhetorical, not a claim. Exclude.
- **Soft/rhetorical superlatives** — "what matters most", "matters most", bare "most" without a quantified/
  comparative object. Require the superlative/exclusivity to attach to a real product/outcome claim
  ("the only … that …", "the first … to …", "#1 … in …", "industry-leading accuracy") — not motivational copy.
- **Ordinal "first"** — leading/positional "First …" (the original task_df580609) must not flag; only
  "Amira is the first … to/that …" market-claim form should.
Keep genuine **quantified** claims as candidates (numbers/%/score points/"N weeks of growth"/"hundreds of") —
those SHOULD flag when not already approved. Stay conservative + tunable (the existing SUPPRESS_THRESHOLD /
PATTERN_CLASSES). Add unit tests: the question + "what matters most" + ordinal "First paragraph…" are NOT
flagged; a real "Amira is the first reading agent proven to…" + an invented "improves scores 99%" ARE.

## Fix 2 — "Disregard" on the claim popup
Add a **Disregard** action to the claim-flag popover (currently Approve / Edit / Find source → add
**Disregard**). Disregard = "this isn't a claim / don't flag it here" — it dismisses the flag and **must not
come back on re-scan**.
- Backend: store dismissed spans per draft (e.g. `deliverable_metadata.dismissedClaims = [{anchoredText|
  span}]`) — additive, lossless. The claim-scan suppresses any candidate matching a dismissed entry (by
  normalized text). A small endpoint `POST /api/writing-studio/drafts/{id}/claim-dismiss {text|span}` (or
  fold into the existing scan/claims surface — your call, but don't fork).
- FE (`composer-v5.js`): the Disregard button → call the dismiss endpoint → remove the flag immediately + it
  stays gone on re-scan. (Approve still adds to the register; Disregard just hides it for this draft.)

## Acceptance (verify the EFFECT)
- Detector: paste a draft with a question, "what matters most", "First paragraph…", a real "Amira is the
  first … proven …", and an invented "improves scores by 99%". Only the last two flag. Paste the scan output.
- Disregard: click a flag → Disregard → it disappears AND a re-scan does NOT re-flag it (prove via re-scan).
  Lossless (dismissals stored, drafts/claims never deleted).
- `./scripts/check.sh` for touched Python (note PRE-EXISTING separately). Browser-eyeball the popover.

## Constraints
Conservative default (false-negatives cheaper than nagging). Lossless (dismissals additive; no deletes).
Reuse the existing detector + claims/scan surfaces; don't fork. Likely no migration (dismissals in draft
metadata). Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA + worktree + the
precision scan output + the disregard-survives-rescan proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
