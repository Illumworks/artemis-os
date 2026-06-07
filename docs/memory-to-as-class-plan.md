# Plan: Memory keystone → A/S-class (the bigger 3)

**Status:** PLAN (2026-06-05). Quick wins #1 (feedback loop) + #4 (decay scheduler) are DONE +
verified live. This plans the remaining three gaps from `docs/memory-system-assessment-2026-06-04.md`.
A cleanup workstream from the stress test will be appended below.

Principle: build the **ruler before adjusting the dials** — the eval harness (#2) comes first so #3
and any retrieval change can be validated, not guessed.

---

## #2 — Retrieval eval / tuning harness (BUILD FIRST — it's the enabler)

**Why:** the fusion weights (FTS .30 / semantic .40 / recency .15 / score .15 in
`config/memory-retrieval.yaml`) are hand-tuned constants copied from the Node reference. There's no
way to know if a change helps or hurts recall. Without a ruler, every other retrieval improvement is
guesswork and regressions are invisible.

**Approach:**
- **Labeled eval set** = (query → relevant observation ids). Two sources, combined:
  (a) a small hand-curated **golden set** (10–20 queries with known-correct hits), and
  (b) a **synthetic set** — generate queries from stored observations via Haiku ("what would someone
  ask to retrieve this?"), held out from the observations themselves to avoid circularity.
- **Metrics:** recall@k (k=5/10), MRR, nDCG over `search_observations`.
- **Runner:** a `artemis/memory/eval/` module + a pytest entry + a CLI (`python -m artemis.memory.eval`)
  that runs the set and prints metrics. Add to CI as a non-blocking quality gate (report, don't fail
  on small deltas).
- **Then tune:** a small grid/sweep over the YAML weights against the metrics; lock the best config.

**Scope/files:** new `artemis/memory/eval/` (eval-set builder, metrics, runner); reads
`search_observations`; uses Haiku for synthetic queries. **Effort:** medium. **Risk:** synthetic
circularity → mitigate with the hand-curated golden set + held-out splits.

## #3 — Semantic conflict detection (data integrity at scale)

**Why:** `conflict_detector.py` is prefix/temporal/relation **string heuristics** only. Semantically
contradictory but lexically different facts ("$50k budget" vs "fifty thousand allocated" vs "we set
aside $75,000") silently coexist and corrupt the observation layer as volume grows.

**Approach:**
- On write (extend `write_observation_with_conflict_check`, `consolidator.py:305`): embed the new
  observation, find in-scope observations above a **cosine-similarity threshold** (candidate
  agreements OR contradictions — similarity alone can't tell them apart).
- For those few high-similarity candidates only, run a cheap **LLM adjudication** (Haiku):
  "do these contradict? agree? unrelated?" — gated by the threshold so cost stays tiny.
- Contradictions → `memory_conflicts` (existing table) for operator review, feeding the existing
  confidence-delta auto-resolve path.

**Scope/files:** extend `conflict_detector.py` with a semantic+LLM detector; reuse embeddings, the
`memory_conflicts` table, and the resolution flow. **Effort:** medium-large. **Risk:** LLM cost
(gate by similarity threshold), false positives (tune threshold + keep the auto-resolve evidence
guard).

## #5 — Writing-Studio ↔ keystone connection (the original P0 motivation; most product-visible)

**Why:** the memory keystone was *originally motivated* by brand-voice recall for the Writing Studio,
but the Studio uses separate `writing_rules`/`examples`/`profiles` tables with **zero `artemis.memory`
imports**. Connecting them fulfills the founding vision and makes brand voice benefit from semantic
retrieval, consolidation, the feedback loop (sticky brand examples), and decay.

**Approach:**
- **Write path:** when a writing rule/example is approved (the Phase-3 propose→approve loop) or a
  draft is composed/edited, also write a brand-voice observation/drawer to the keystone in
  `brand:<profile_slug>` scope (lossless, evidence-linked to the rule/draft).
- **Read path:** `compose_engine` retrieves relevant brand-voice memories from the keystone
  (semantic recall of approved phrasings/examples) to **augment** the current rules-table lookup —
  keep the rules-table path as a fallback so draft quality can't regress.
- Validate the grounding change against #2's eval (and a before/after on draft quality).

**Scope/files:** bridge `artemis/marketing/writing_studio/` ↔ `artemis/memory/`: write hook on
approve/compose; read augmentation in `compose_engine.py`. **Effort:** large (integration + a
compose-grounding behavior change). **Risk:** changing grounding can affect draft quality → ship
behind augment-not-replace + a fallback, and verify with #2.

---

## How-the-bot-learns verdict (audit 2026-06-05) + natural-use roadmap

**Question:** does the bot learn only from approved proposed-rules/skills, or also from *natural use*
like ChatGPT/Claude memory?

**Verdict:** Artemis is **better than consumer AI at STRUCTURED, event-driven learning** (gate
approve/reject → memory, trajectory summaries, trend snapshots — all evidence-linked, lossless,
multi-scope; a more sophisticated substrate than ChatGPT/Claude), but **worse at AMBIENT natural-use
learning** — the thing that makes ChatGPT "feel like it knows you."

**Big finding — two of the blockers were just fixed in the cleanup batch (merged 2026-06-05):**
- **C3 (consolidation dormant) → FIXED.** Consolidation called the raw Anthropic SDK (empty key here)
  and silently no-op'd, so Floating-Artemis chat turns piled up as raw drawers and were NEVER
  distilled into durable observations. Now routed through the claude-code provider → the
  conversation→memory pipeline is alive.
- **C1 (secondary-scope retrieval broken) → FIXED.** Many learning writes (MC2-4 gate/rejection, the
  Phase-3 agent self-correction) tag observations on a *secondary* agent scope that retrieval never
  queried — so those learning loops were SILENTLY DEAD. Now retrievable. The rejection-learning loop
  we shipped actually fires now.

**Remaining gaps to "learns naturally, ≥ ChatGPT/Claude" (ranked):**
- **NL1 — Salient-fact / preference EXTRACTION from conversation (MISSING, high).** Even with C3,
  consolidation is compression/dedup, NOT extraction. ChatGPT auto-extracts "user prefers X / works
  at Y" from chat. Build a fact/preference extractor over FA turns (could auto-fire the existing
  `set_pref` tool on detected preferences). Low-risk to auto-learn (operator prefs/workflow), unlike
  brand/claims which stay gated.
- **NL2 — Edit-as-training (E7 in the WS plan, high, strategic).** Plain edits teach nothing today;
  diff AI-vs-human → human-gated proposed learnings. Makes "every edit trains the bot" real.
- **NL3 — C4 decay teeth** (needs #2 eval) + **#3 semantic conflict** (update/contradict old memories)
  + **#5 Studio↔keystone** (brand-voice recall). Covered above.
- **Correct-to-stay-gated:** brand voice + published claims (NL2 keeps human approval — right call).
  **Safe-to-auto-learn:** operator preferences / workflow patterns (NL1).

## Recommended sequencing

1. **#2 eval harness first** — the measurement infrastructure that makes #3 and #5 safe to ship.
2. **Then #3 (integrity) or #5 (strategic), Jon's call.** My lean: **#5 next** for visible value
   (completes the founding vision, most commercial), with #2's eval guarding the grounding change;
   then #3 to keep the layer clean as volume grows. If data-integrity worry is higher, flip #3/#5.

Each is its own briefed effort (terminal lead / Codex build → app Opus verifies + merges). #2 and #3
are mostly memory-internal (parallelizable); #5 touches the Writing Studio (sequence after #2).

## Cleanup workstream (from the 2026-06-05 stress test)

The stress test confirmed the foundation is solid (retrieval cluster-ranking, the feedback loop
0→10 exact, lossless supersession A→B→C, graceful degradation all PASS). But it found things that
are *supposed* to work and don't — "make what we built actually work," several quick:

**REAL BUGS (fix-what's-broken):**
- **C1 — Multi-scope retrieval is broken (HIGH).** MW1 writes the `memory_observation_scopes` join
  table, but `search_observations` filters on `memory_observations.scope_kind/scope_id` directly and
  never queries the join table — so an observation written with `additional_scopes=[B]` CANNOT be
  retrieved by a search on scope B. Secondary scopes are silently dead. Affects Phase-3 gate/rejection
  + trend observations that rely on secondary scopes. Fix: join `memory_observation_scopes` in the
  retrieval SQL. `artemis/memory/retrieval.py` (~319–419).
- **C2 — Conflict prefix too wide (MEDIUM, quick).** `_ATTRIBUTE_KEY_PREFIX_TOKENS = min(8,…)` means
  "$50k vs $75k" on the same topic isn't flagged (the differing number sits inside the 8-token
  prefix). Natural patterns put the value at token 5–7. Tighten to ~4–5. `conflict_detector.py:72`.
  (Folds into #3.)

**DORMANT CAPABILITY (important):**
- **C3 — Consolidation is effectively OFF + fails silently.** The consolidator calls Haiku via the
  raw Anthropic SDK, which needs `ANTHROPIC_API_KEY` — but this app is subscription/claude-code-CLI
  based and that key is empty, so consolidation catches the auth error and silently returns `[]`. So
  the whole consolidation layer is dormant in prod. Fix: route consolidation through the existing
  provider abstraction (`resolve_adapter`/the claude-code path the agents + compose engine use — no
  SDK key needed), AND stop swallowing failures (log ERROR + a metric). `consolidator.py`.

**DESIGN-MATCH GAP (ties to the just-shipped #4):**
- **C4 — Decay is wired but toothless.** `obs.score` only feeds the "score" channel (0.15 × 0.40 ≈
  6% of the final fused score), so even fully-decayed observations drop only ~10% in ranking — a
  stale-but-semantically-relevant memory never actually "forgets." So #4 (decay scheduler) runs but
  doesn't meaningfully suppress retrieval. Fix options (validate via #2's eval): re-weight so decay
  bites, and/or add a hard archive threshold (drop very-decayed observations out of the active pool).
  Also revisit the aggressive `discovery` decay (hits 0.5 in ~10 days). `retrieval.py` weights +
  `maintenance.py`.

**MINOR:**
- **C5 — Empty/whitespace query returns 5 semantic results** (embedding of "" matches recent rows).
  Add the same `query.strip()` guard the FTS path already has, to the semantic path. `retrieval.py`.
- **C6 — Non-standard `category` strings silently accepted** + decayed at the 0.95 default with no
  warning (mistyped categories vanish into the "other" bucket). Validate/log unknown categories.

(Note: the "3 flaky consolidator tests" did NOT reproduce — all 275 memory tests passed cleanly.)

## Re-sequenced recommendation (incorporating stress findings)

1. **Quick cleanup batch first** — C1 (multi-scope retrieval), C2 (conflict prefix), C3
   (consolidation via provider + no silent swallow), C5/C6 (guards). These make what we already built
   actually function. Mostly small; C1 + C3 are the high-value ones.
2. **#2 eval harness** — the ruler. Also the safe way to do **C4** (re-weight decay so it bites) and
   any retrieval tuning.
3. **#3 semantic conflict detection** — absorbs C2; the real fix for "catches contradictions."
4. **#5 Writing-Studio↔keystone** — new capability; the founding vision; sequence after #2.
