# Worker Brief — Memory: the R@1 ranking + recall lever (post-dedup)

**Owner:** terminal (Codex out of tokens) — direct or one Sonnet sub-agent, isolated worktree.
**Lead:** Artemis (Opus) reviews + **merges (prod retrieval path, accuracy-sensitive — do NOT self-merge).**
**Isolation:** own worktree (`worker/memory-ranking-recall`), own test DB (contains `artemis_test`); commit
before reporting; report the branch + **before/after R@1·R@10·MRR on the M2 harness**.
**Status:** READY. The actual R@1 lever — dedup (M1b) cleaned clutter but left R@1 at 0.375. Diagnosis pinned
the cause to **ranking + recall**, not duplication.

## Diagnose FIRST (same discipline that saved M1) — don't assume all 4 misses are fixable
Terminal's remaining misses: time-series ranking (#335), fuzzy near-dup that's too risky to auto-merge
(#514/#515), genuine recall gap (#715). Before building, classify each remaining miss as **genuine failure vs
QA-ground-truth artifact**:
- The momentum miss (#335) = "3 *newer* snapshots outranked the expected answer." If newer is *more* relevant,
  the harness ground-truth (an arbitrarily-chosen older snapshot) is the bug, not retrieval. **A query whose
  honest best answer is the latest snapshot should NOT count as a miss.** Fix the QA where it's mis-specified;
  only treat truly-buried-correct-answers as ranking failures. Report the genuine-vs-artifact split.

## Build (precision-first, non-destructive — all at RANK time, nothing collapsed in storage)
1. **Rank-time sibling grouping.** When the top-k contains near-identical siblings (high similarity, but NOT
   byte-identical so M1b left them), surface ONE representative (canonical / latest) per group so distinct
   answers fill the slots instead of one answer's twins hogging the top. Reversible, retrieval-only — do NOT
   supersede/merge in storage (that's the risky fuzzy-merge M1b deliberately refused). Only group genuinely
   near-identical; keep distinct results.
2. **Recency/canonical tiebreak for time series** so the latest snapshot of a series ranks above its older
   selves for "current X" queries — without dropping the history from the corpus.
3. **Recall gap (#715).** Diagnose why the right obs isn't even in the top-10 candidate pool (FTS terms?
   embedding coverage? query phrasing?). Fix if it's a general recall issue; if it's one hard idiosyncratic
   case, say so — don't over-engineer for n=1.

## Strengthen the measurement (so gains are real, not noise)
- **Expand the QA set** well beyond the current ~24 queries — terminal correctly refused to bless the
  `confirmed_bias` weight tuning on 24. With a larger set, re-test that tuning and **recommend** adopting it (or
  not). Don't auto-change prod weights — Lead decides.
- Run the M2 harness before/after on the **deduped** live-clone corpus.

## Ship gate (Lead verifies)
- **R@1 / MRR up, R@10 NOT regressed** on the (expanded) harness — with the genuine-vs-QA-artifact split shown
  so we trust the number.
- Rank-time grouping never hides a genuinely distinct relevant result (precision check); zero storage mutations
  from this slice (it's retrieval-only).
- A clear recommendation on the `confirmed_bias` weight set, validated on the larger QA set.
