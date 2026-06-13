# Memory Keystone — Readiness for Multi-Team + Upgrade Sequence

**Status:** PLANNING (2026-06-13). Companion to `docs/os-multi-team-expansion.md` (can the memory absorb the
expansion?) and `docs/memory-system-assessment-2026-06-04.md` (the A/S-class gap analysis). Sequenced in the
roadmap **before P6** — see `docs/artemis-pa-build-plan.md`.

## The original goal (the bar we're measuring against)
A **lossless** memory system that **rivals/beats top-tier** agent-memory (Mem0, MemGPT, Zep, LangGraph), is
**accurate**, **fast/snappy** for an AI to use, and **does not annihilate context windows or token cost.**

## Where it stands (2026-06-13)
- **Lossless** ✅ — append-only, SHA-256 hash-chained `raw_inputs`, supersession-only (never DELETE), full
  provenance + evidence lineage.
- **Built multi-team already** ✅ — observations are **multi-scope** (district + campaign + **account** + person
  + workspace at once, via `memory_observation_scopes`). This was designed *specifically* for Salesforce /
  Gong / Churn-Zero integration — i.e. exactly the multi-team data the OS expansion brings.
- **Fast + token-efficient** ✅ at current scale — 5-channel indexed/fused retrieval (FTS + vector/pgvector
  HNSW + recency + composite + graph 1-hop) surfaces the relevant slice instead of dumping context.
- **Top-tier** 🔵 — **B-class on an A-/B+ substrate.** 2 of 5 A/S-class upgrades shipped (use-feedback loop;
  decay scheduler).

**Verdict on absorbing the OS multi-team expansion: the architecture is ready — no rebuild needed.** The
schema was made for cross-team scope. But company-wide *volume* makes the remaining upgrades more pressing,
and adds one new requirement.

## The upgrades to be A/S-class AND multi-team-ready (the memory work to schedule)
**M1 — Semantic conflict detection** *(was A/S gap #3; now the most important).* Today conflict detection is
string/prefix matching — "$50k" vs "fifty thousand allocated" won't be flagged. At company-wide volume,
contradictory/stale facts accumulate → directly threatens **accuracy**. Add cosine-threshold and/or an LLM
adjudication pass.

**M2 — Retrieval eval / tuning harness** *(A/S gap #2).* Fusion weights are hand-tuned constants with no
scoreboard. Need a recall@k benchmark (synthetic QA from stored observations) to *prove* recall + speed +
token cost hold as volume grows 10×. Without it the "fast/snappy/cheap" promise is unverifiable at scale.

**M3 — Scope/role-aware retrieval** *(NEW — surfaced by the multi-team analysis).* Data is scoped and retrieval
can query by scope, but multi-team needs retrieval to **enforce who-can-see-what** (an AE pulls *their*
territory/accounts, not the whole company's). This is relevance + privacy **and** token cost (don't load the
company's entire memory into context). **Same filtering layer the OS expansion needs** ("filter by
territory/account or it's spam") — one build, two uses.

**M4 — Connect Writing-Studio brand-voice to the keystone** *(A/S gap #1's remainder).* The original P0
motivation; `compose_engine` still uses separate `writing_studio_rules/examples` tables. Less about multi-team,
more product-completeness — can slot independently.

*(Already done: use-feedback loop (`hit_count`), decay scheduler — A/S gaps #1 & #4 of the original 5.)*

## Why this goes BEFORE P6 (self-evolution)
P6 (GEPA-style self-evolution) **learns from accumulated execution-trace history** — which needs *time and
volume* to be worth optimizing against. Meanwhile **M1–M3 are immediately valuable**: they gate the OS
multi-team expansion (M3 especially) and protect accuracy + token cost as data grows (M1, M2). So: let trace
history accrue while we do the memory upgrades, then P6 optimizes a memory layer that is already accurate,
tuned, and access-scoped.

## Suggested sequence
**M1 + M2 + M3 together** = "accurate, fast, token-cheap, access-scoped at company scale" — the gate to both
the OS multi-team expansion and the original A/S-class goal proven under real load. **M4** slots whenever
(product-completeness). Then **P6**.
