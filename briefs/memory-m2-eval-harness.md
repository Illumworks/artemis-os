# Worker Brief — Memory M2: retrieval eval / tuning harness

**Owner:** Codex (backend/data). **Lead:** Artemis (Opus) reviews + merges. **Isolation:** own worktree
(`worker/memory-m2-eval-harness`), own DB for any scale-test data (name contains `artemis_test`); commit before
reporting; do-NOT-merge.
**Status:** READY. First of the Memory upgrades (M2 → then M1 → M3). The measurement foundation everything else
proves itself against.

## Why
`artemis/memory/retrieval.py:search_observations` fuses 5 channels (FTS, semantic, recency, score,
graph_expand) using **hand-tuned constant weights** (`RetrievalWeights` in `config/memory-retrieval.yaml`:
fts 0.30 / semantic 0.40 / recency 0.15 / score 0.15 / graph_proximity 0.12, plus `ScoreFeatureWeights`).
There is **no scoreboard** — we can't prove the keystone promise ("accurate, fast, snappy, token-cheap") or
tell whether a weight change helps, and we can't show it holds as the corpus grows 10× for the multi-team
expansion. M2 builds that measurement + tuning loop. (M1 accuracy + M3 scope-aware retrieval will then prove
they don't regress *against this harness*.)

## What to build
1. **Synthetic QA generation from stored observations.** For a sample of real observations, generate a natural
   query whose correct answer is that specific observation (LLM-generated query + the obs id as ground-truth
   target). Persist the QA set so runs are repeatable. Cover the channel mix (FTS-favorable keyword queries,
   semantic paraphrases, recency-sensitive, entity/graph queries).
2. **The eval harness** (a repeatable command/script). Over the QA set, call `search_observations` and compute:
   - **Recall@k** (k = 1/3/5/10) and **MRR** — is the ground-truth obs in the top-k.
   - **Latency** p50 / p95 per query.
   - **Token cost** — embedding calls/tokens + result-payload tokens per query (the "doesn't annihilate the
     context window / cost" claim).
   Emit a compact **scoreboard** (JSON + a short human summary).
3. **Baseline report** with the *current* weights — the number we're starting from.
4. **Weight tuning.** A mode to sweep `RetrievalWeights` (+ `ScoreFeatureWeights`) and report the
   recall/latency/cost frontier; recommend a weight set that beats the hand-tuned baseline on recall without
   hurting latency/cost. Do NOT silently change prod weights — recommend; Lead decides what to commit.
5. **Scale test (the 10× claim).** Generate/duplicate a synthetic corpus ~10× current volume (with realistic
   scope variety) in an isolated DB, re-run the harness, and report whether recall/latency/token-cost hold.
   **`log()`/note any sampling or caps** — no silent truncation.

## Constraints
- **Read-only against real memory** — never mutate, supersede, or delete stored observations/raw_inputs (the
  lossless invariant). Scale-test data lives in a separate eval DB, not prod memory.
- Reuse the existing embedding provider + `search_observations` as-is (don't fork retrieval). Deterministic
  where possible; fix seeds; record the corpus size + config used in every report.
- Don't print sensitive observation *content* in committed reports beyond short snippets needed to interpret a
  miss.

## Ship gate (Lead verifies)
- `uv run <harness>` produces a scoreboard: recall@k, MRR, p50/p95 latency, token cost — on the real corpus.
- A baseline-vs-tuned comparison showing at least one weight set that improves recall without regressing
  latency/cost (or a clear finding that the hand-tuned weights are already near-optimal).
- A 10× scale run showing how recall/latency/cost move with volume, with any caps/sampling stated.
- Zero mutations to real memory (verify counts/hashchain unchanged).
