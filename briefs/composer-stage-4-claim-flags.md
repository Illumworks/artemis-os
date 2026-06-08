# Build brief — Composer Stage 4: claim flags (conservative detection + orange flag + grow the register)

**Agent:** terminal (design-coupled — the flag must be UNOBTRUSIVE; Lead reviews the feel). **Branch:**
`worker/composer-claim-flags` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_claimflags`). **Do NOT merge — report.** Read first: `docs/AGENT-WORKING-PRINCIPLES.md`,
`docs/COMPOSER-REBUILD-PLAN.md` (Stage 4), `docs/mockups/composer-v5-prototype.html` (the approved claim-flag
look + interaction — match it), and Stage-1/2's `public/js/features/composer-v5.js` (the editor + selection
toolbar you extend).

## The point of this stage + the GOVERNING CONSTRAINT (Jon)
Flag claims in the document that aren't backed by the approved Claims Register — but **QUIET by default, NOT
annoying.** Amira is conservative (only data-backed claims, no boasting), and the register now holds ~88
approved claims (verbatim from published content). So the flag must light up ONLY on genuinely strong claims
that AREN'T already approved — never nag about ordinary descriptive copy or about register gaps. A calm,
on-brand draft should show ~zero flags. Two ways to grow the register feed this (both in scope).

## Existing surface (build on it; do NOT fork)
- **Claims Register backend (merged):** `Claim` model + `/api/writing-studio/claims` (list?status=approved,
  create, approve, retire). Repo `list_claims(session, profile_id, status="approved")`. ~88 approved claims
  seeded. Tiers 1–4 (Tier 4 = high-stakes quantified/exclusivity).
- **Editor + toolbar (merged):** `composer-v5.js` ProseMirror editor (Stage 1) + the floating selection
  toolbar (Stage 2). The mockup shows the claim flag = **orange DOUBLE-underline**, hover peek, click →
  popover (Approve / Edit / Find source). I prototyped that exact look — match it.

## Deliverable 1 — detection backend (CONSERVATIVE, DETERMINISTIC, tunable)
New endpoint `POST /api/writing-studio/drafts/{id}/claim-scan` (or take raw text) → returns flagged spans:
`[{start, end, text, reason, nearestApproved?: [{id, phrasing, similarity}]}]`.
Logic (deterministic — no LLM in this stage):
1. **Candidate detection — only "strong claim" language.** Scan the draft for spans matching strong-claim
   patterns ONLY: (a) **quantified** — percentages, "Nx", score/percentile points, durations tied to
   outcomes ("X weeks of growth", "in 20 minutes"), counts ("5 million"); (b) **superlative/exclusivity** —
   only, first, best, #1, most, leading, proven, guaranteed, unmatched, "industry-leading"; (c)
   **comparative/category** — "more than", "outperforms", "vs.", "compared to". Ordinary descriptive
   sentences are NOT candidates. Bias toward NOT flagging when unsure (false-negatives are cheaper than
   nagging — Jon).
2. **Suppress candidates already approved.** For each candidate span, compare (normalized: lowercase, strip
   punctuation/whitespace) against the profile's APPROVED claims (`list_claims(status="approved")`). If the
   span is highly similar to an approved claim (deterministic token-set similarity ≥ a tunable threshold,
   start ~0.6–0.7), SUPPRESS it (it's approved language) — no flag. Otherwise it's a strong claim NOT in the
   register → flag it. Include the top 1–2 nearest approved claims in `nearestApproved` for popover context.
3. **Tunable + quiet:** expose the threshold + which pattern classes are active as constants so we can dial
   sensitivity. Default conservative. Pure/deterministic; fast; runs server-side.

## Deliverable 2 — the flag UI in the composer (match the mockup)
- Decorate flagged spans in the PM document with the **orange double-underline** (ProseMirror decorations —
  NOT contenteditable hacks). Re-scan on draft load + debounced after edits (cheap; deterministic).
- **Hover** → lightweight peek ("Claim not in Register — click to resolve"). **Click** → floating popover
  (the prototype's look): the reason + `nearestApproved` (so the user sees "is this one of ours?") + actions
  **Approve / Edit / Find source**.
- **Approve** → adds the flagged text to the register as an APPROVED claim (POST claims → approve, or create
  with status approved) → the flag clears + the claim is banked (so it never flags again). 1-click, natural.
- Calm by default: just the underline at rest; the entrance/hover restraint from the prototype (one soft
  glow on first detect, hover emphasis — no persistent animation).

## Deliverable 3 — "＋ Add to Claims Register" from the highlight toolbar (Jon's natural add-path)
Add an action to the Stage-2 selection toolbar: select any passage → **＋ Add to Claims Register** → adds it
as an APPROVED claim (human-initiated; minimal friction — optional quick category, default uncategorized).
This + Approve-from-flag are the two natural ways the register grows through normal writing.

## Acceptance (verify the EFFECT — show it; this is the feel-critical stage)
- A draft sentence that makes a strong claim NOT in the register (e.g. an invented "Amira improves scores 99%")
  → **flagged** (orange). A sentence that closely matches an APPROVED claim (e.g. one of the seeded verbatim
  claims) → **NOT flagged** (suppressed). Ordinary descriptive copy ("Our tutor listens as students read")
  → **NOT flagged.** Paste the scan output proving all three.
- Click a flag → Approve → GET claims shows the new approved claim + a re-scan no longer flags that span.
- Highlight → ＋ Add to Claims Register → the claim is created (approved); GET claims shows it.
- **Quiet check:** run the scan on a real, on-brand existing draft and confirm few/zero flags (not a noisy
  mess). Report the flag count on a normal draft — that's the headline acceptance.
- Mock the LLM if any (none expected — deterministic). `./scripts/check.sh` for touched Python (note
  PRE-EXISTING failures separately). No console errors on scan/flag/approve.

## OUT OF SCOPE (later)
LLM-based claim detection (a smarter pass — possible later option, not now); a full Claims-Register
management page (the Memory-page surface — separate); comments/pagination/Google Doc. Don't touch the
templates work.

## Constraints
Lossless (Approve/Add create claims; never delete; retire is the only removal). CONSERVATIVE default — match
the brand (quiet > nagging); make sensitivity tunable. Deterministic detection (no LLM). Reuse the claims
backend + the PM editor + the Stage-2 toolbar + the prototype's flag look — don't fork. Likely no migration.
Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA + worktree + the scan output (flagged
vs suppressed vs ignored), the approve→re-scan-clears proof, the highlight-add proof, and the flag-count on a
normal draft. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews (feel +
code) + verifies + merges.
