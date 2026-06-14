# Worker Brief — Memory M1: semantic conflict detection (accuracy) + harness validation

**Owner:** Codex (backend/memory). **Lead:** Artemis (Opus) reviews + **merges (accuracy-sensitive —
wrong supersession silently loses the right answer).** **Isolation:** own worktree
(`worker/memory-m1-semantic-conflict`), own test DB (name contains `artemis_test`); commit before reporting;
do-NOT-merge.
**Status:** READY. Second Memory upgrade (M2 ✅ → M1 → M3). Every change is **measured against the M2 harness.**

## Why
Conflict detection already exists and is wired: `conflict_detector.detect_conflicts` runs inside the
consolidator → supersedes the contradicted obs (`superseded_by`) + writes a `memory_conflicts` row
(`resolution=NULL`) for review; retrieval filters `superseded_by IS NULL`. **But it's rule-based only** —
`incompatible_values` / `incompatible_temporal` / `incompatible_relational` (exact attribute/temporal/relational
matches). It misses **semantic** contradictions: paraphrased or implied conflicts that don't share an exact
attribute key. Un-retired stale/contradicted memories then compete with the correct current one in retrieval —
a likely contributor to the baseline **R@1 = 0.375**.

## What to build
1. **Semantic conflict detector**, extending the existing pipeline (don't fork it). For a new observation vs its
   same-(scope, entity) candidate set, detect contradictions the rules miss using embedding similarity to
   shortlist + an **LLM/NLI contradiction judge** on the shortlist. Feed results through the **existing**
   supersede + `memory_conflicts` machinery — same lossless path.
2. **PRECISION-FIRST — this is the cardinal rule.** A false-positive that supersedes a *valid* memory silently
   removes the right answer from retrieval. So:
   - Only **auto-supersede on high-confidence** contradiction.
   - **Borderline/low-confidence → write a `memory_conflicts` row (`resolution=NULL`) for review, do NOT
     auto-retire.** Bias toward leaving both active over wrongly retiring one.
   - Lossless holds: supersession only (never delete); a wrongly-superseded obs stays recoverable; raw_inputs
     hashchain untouched.
3. **Validate + diagnose against the M2 harness (the point of having built it):**
   - Re-run the baseline; report the **R@1 / MRR / latency / token-cost delta** from M1 — honestly, even if
     small.
   - **Categorize the baseline misses** (conflict-driven vs near-duplicate-ranking vs genuine recall gap) so we
     know what actually drives the remaining R@1 gap and where the *next* lever is. This is a required
     deliverable — don't just ship the detector.
   - Re-test the deferred **M2 weight tuning** (`confirmed_bias`) on the larger QA set; recommend adopting it
     only if the gain holds (don't auto-change prod weights — Lead decides).

## Constraints
- Precision over recall on conflict detection; conservative auto-action + review queue for the rest.
- Reuse `detect_conflicts`'s call site in the consolidator, the `superseded_by` mechanism, and the
  `memory_conflicts` review table. No new parallel system.
- Keep latency/token-cost in check — the semantic judge runs at ingest/consolidation (not in the read path);
  shortlist with embeddings before any LLM call. Report added cost.

## Ship gate (Lead verifies)
- Semantic detector catches real paraphrased/implied contradictions the rule detectors miss (test cases),
  routed through supersede (high-confidence) or the review queue (borderline).
- **R@1/MRR before-vs-after on the harness**, plus the **miss-category breakdown** that tells us the next lever.
- **Precision check:** no valid (non-contradictory) memory wrongly superseded — show the false-positive rate is
  ~0 on a labeled check set. Lossless intact (hashchain valid; superseded rows recoverable).
