# Worker Brief — Memory M1 (revised): near-duplicate consolidation = the real R@1 lever

**Owner:** terminal (Codex out of tokens) — direct or one Sonnet sub-agent in an isolated worktree.
**Lead:** Artemis (Opus) reviews + **merges (lossless/accuracy-sensitive — do NOT self-merge).** **Isolation:**
own worktree (`worker/memory-near-dup-consolidation`), own test DB (contains `artemis_test`, not the shared
one); commit before reporting; report the branch + **harness R@1 delta** + **proof no information was lost**.
**Status:** READY. Supersedes the conflict-detection framing of M1 — terminal's diagnosis (below) redirected it.

## Why (terminal's data-driven finding, confirmed by Lead)
The R@1=0.375 misses are **near-duplicate clustering, not conflicts** (5/6 misses; 0/6 conflicts). And the
rule-based conflict detector is **dead code** (`write_observation_with_conflict_check` has no prod callers; real
ingest goes through `apply_consolidation`). So semantic conflict detection would not move R@1. The right answer
is getting **buried under its own near-identical siblings.**

## The sharp line (do not cross — this protects lossless + real data)
- **TRUE duplicates → consolidate.** USER message + the ASSISTANT echo of the same content stored as two obs;
  repeated near-identical status recaps. These are clones — collapse them.
- **Time series → KEEP ALL, never collapse.** A new momentum snapshot every few hours for the same district is
  *distinct data over time*, NOT a duplicate. Collapsing it destroys history. That miss is a **ranking**
  problem (surface the canonical/latest snapshot), addressed separately — do NOT dedup it.
- **When unsure whether two obs are clones vs distinct → KEEP BOTH** (precision-first, same rule as conflicts).

## What to build
1. **Find the source of the clones first.** Investigate WHY echo pairs / repeated recaps become separate
   observations (the conversation-log ingest path). If ingest is creating an observation from the assistant's
   verbatim echo, **fix it at the source** so new clones stop being created — cheaper than perpetually
   de-duping. Report what you find.
2. **Near-duplicate consolidation** within a `(scope, entity)` cluster: shortlist by high embedding similarity +
   high content overlap (a *strict* threshold — clones, not merely "related"), then collapse via the **existing
   lossless machinery** (`apply_consolidation` / `supersede_observation` + `link_evidence`): keep one canonical
   obs, supersede the clones, link evidence. Run it (a) **as a backfill** over the existing clutter and (b)
   **inline in the real ingest/consolidation path** going forward.
3. **Lossless, enforced:** consolidation = supersede + evidence-link, never delete; raw_inputs hashchain
   untouched; every superseded clone recoverable. Distinct (non-clone) memories and all time-series points stay
   active.

## Measure against the M2 harness (required — this is why we built it)
- R@1 / R@3 / MRR / latency / token-cost **before vs after** on the live corpus (token cost should *drop* —
  fewer clones).
- **Prove nothing was lost:** observation count change is all accounted for as supersessions (recoverable);
  hashchain valid; no time-series point or distinct memory superseded.
- Re-categorize remaining misses → confirms whether the next lever is **series-ranking** (the snapshot miss) or
  the **recall gap** (the 1/6 "TX signal count" miss). Also re-test the deferred M2 weight tuning on the larger
  QA set; recommend (don't auto-change prod weights).

## Ship gate (Lead verifies)
- Harness shows R@1/MRR up and token-cost down, with the clone clusters collapsed.
- Precision: a labeled check shows **no distinct memory and no time-series point wrongly superseded** (~0
  false-collapse). Lossless intact.
- A clear statement of the remaining-miss categories so Lead can aim the next lever (ranking vs recall).

## Deferred (separate, smaller — correctness insurance, NOT the R@1 play)
Wire the existing rule-based conflict detector into the real ingest path (it's currently dead code) + add the
semantic contradiction judge behind it. Low priority; Lead will brief separately once this lands.
