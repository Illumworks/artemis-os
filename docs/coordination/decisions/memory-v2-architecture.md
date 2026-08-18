# Artemis Memory v2 — Architecture (canonical record)

**Status:** approved 2026-05-18 by Jon. Authored by Opus Lead after a multi-system audit + design conversation. Updated as tiers ship.

**Principle Jon committed to:** memory is **necessary**, not optional. The system must be **lossless by structural guarantee** (not by careful coding), **measurably accurate** (benchmarked, not assumed), and **maintainable by AI** (no human-only ergonomic crutches).

This document is the canonical record. If anything in COORDINATION.md or a brief contradicts this doc, this doc wins. Every memory-touching slice must reference its section here.

---

## 1. Why memory is the foundation

Artemis is operations chief for a single person + (later) a team. Operations chief without memory is a fancy autocomplete. Every capability we build — proactive Slack drafts, post-meeting follow-up, campaign continuity, OKR cadence, sub-agent coaching — depends on what Artemis remembers about Jon, the projects, the people, the prior decisions, the corrections.

If memory is lossy or unreliable, every layer above it inherits the unreliability. We've already lost data twice in one day (test suite truncated live tables). That cannot happen again, and no future bug — test, migration, app code, or AI-maintainer mistake — can be allowed to silently destroy facts.

This is why memory has its own six-tier roadmap (M1–M6), separate from the phase J/K/L work.

---

## 2. Competitive landscape (2026-05-18 audit)

| System | Storage philosophy | Distinctive strength | Distinctive weakness |
|---|---|---|---|
| **Mem (mem.ai)** | Flat embedded notes, auto-linking | Surfaces related notes as you type — very low friction capture | No graph; provenance shallow; consumer cloud lock-in |
| **Hindsight (LLM-memory line)** | Hierarchical episodic memory + summarization tier | Tiered retrieval, survives long conversations | Heavy on summaries — lossy by design |
| **Letta / MemGPT** | Virtual context window + memory tools | Agent-native; OS-style memory hierarchy | Postgres-only, no rich evidence chain |
| **MemPalace** ([github.com/mempalace/mempalace](https://github.com/mempalace/mempalace)) | Verbatim storage in wings/rooms/drawers + temporal ER graph | **Verbatim invariant** (never paraphrases), 96.6% R@5 on LongMemEval, 29 MCP tools, per-agent isolated wings | Single-process SQLite, no offsite durability, no shipped backup story |
| **Memory Palace (mnemonic technique)** | Spatial metaphor — rooms, anchors, loci | Powerful theory for human recall | No production system has shipped it with measurable wins; mostly inspirational |
| **Artemis v1 (today)** | Observations + evidence + entities + relations + Haiku consolidation | Explicit provenance, graph, Postgres, hybrid retrieval | Lossy consolidation, no durability, single node, unbenchmarked |

**Conclusion from the audit:** nobody has built a system with all the right primitives. They each pick a subset. Artemis v2 combines all of them, with **cryptographic durability as the actual moat**.

---

## 3. Architectural invariants

These are non-negotiable. Every memory operation must preserve them. Each tier ships with tests that verify the invariants haven't been weakened.

### 3.1 Verbatim canonical (the lossless invariant)

Every memory-write source — user turn, tool result, sub-agent observation, agent-run side-effect — lands in a single append-only table called `raw_inputs` first. That table is the **canonical source of truth**. Every derived structure (observations, entities, relations, consolidations, summaries) links back to one or more `raw_inputs` rows via foreign key.

**Even if every derived table is truncated by a bug, the system is fully reconstructable from `raw_inputs`.**

`raw_inputs` is:
- Append-only — no UPDATE, no DELETE, ever (enforced by trigger and code review)
- Hash-chained — each row includes `prev_hash` + `this_hash` so tampering is detectable
- Local-first — backed up nightly to local disk, archived to local cold storage after 90 days
- Replayable — every system state is reconstructable by walking the chain in order

### 3.2 Hash chain integrity (the tamper-evident invariant)

Each `raw_inputs` row computes `this_hash = SHA-256(canonical_form(row_with_prev_hash))`. A single SQL function (`verify_chain`) walks the chain and reports the first break, if any.

If memory was tampered with — by a bug, a migration, or a malicious actor — we know. Other systems don't have this.

Canonical serialization is documented exactly in `artemis/memory/hashchain.py`. Sorted JSON keys, no whitespace, deterministic across Python versions.

### 3.3 Additive consolidation (the never-overwrite invariant)

Haiku-driven consolidation produces **summaries that supplement raw, never replace it.** Old observations are not deleted when consolidated — the summary points to them. Consolidations are themselves observations with a different `source_kind`.

This is the explicit rejection of Hindsight/Letta-style summary-overwrite. We pay storage cost; we keep the truth.

### 3.4 Separated concerns (the no-bleed invariant)

Three different kinds of memory live in three different tables:
- **Facts** — what is true about the world (entities, relations, observations)
- **Preferences** — what Jon likes / dislikes / wants / how he wants Artemis to behave
- **Personality / voice** — Artemis's own observed dialogue patterns (her voice corpus)

These are stored separately so fact retrieval doesn't pollute with preference data, and so personality drift can be tracked independently of fact accuracy.

### 3.5 Per-scope isolation (the namespace invariant)

Every memory operation requires `(scope_kind, scope_id)`. Sub-agents get isolated namespaces by default. Cross-namespace retrieval is explicit. This is the MemPalace-inspired pattern but enforced at the schema level — not a convention.

### 3.6 Benchmarked accuracy (the measurable invariant)

We do not claim "rock solid" without numbers. The system has:
- Continuous LongMemEval benchmark — public, comparable to MemPalace's 96.6% R@5 reference
- Custom Artemis-specific recall benchmark — real Jon conversations + questions
- Latency targets enforced via tests: p50 < 50ms, p99 < 200ms on retrieval

Regressions on either are blockers, not warnings.

---

## 4. Layered architecture

```
LAYER 0 — VERBATIM CANONICAL (write-once, hash-chained, locally replicated)
  raw_inputs              every source event, append-only
                          prev_hash + this_hash chain
                          90-day active retention
                          archived to ~/.artemis/archive/{year}/{month}/*.jsonl.gz after 90 days
                          nightly pg_dump → ~/.artemis/backups/{date}.pg_dump.gz (30-day rolling)
                          row stays in DB as placeholder when archived (payload nulled, hash preserved)

LAYER 1 — STRUCTURED FACTS (derived; never deleted)
  observations            facts derived from raw_inputs
                          FK back to raw_input_id
                          confidence score 0.0–1.0 per observation
                          source_weight (user > agent > tool inference)
  entities                people / projects / places / things
                          valid_from / valid_until (temporal validity windows)
                          confidence score
  relations               edges between entities
                          valid_from / valid_until
                          confidence score
  conflicts               when two observations disagree about the same entity/relation
                          flagged for review, both retained

LAYER 2 — CONSOLIDATIONS (additive; never destructive)
  consolidations          Haiku-rolled summaries of clusters of observations
                          FK list back to the observations they summarize
                          their own observation row (so they're recoverable)
  epoch_summaries         daily / weekly / monthly rollups for long-session context compression
                          additive — original observations remain canonical

LAYER 3 — PREFERENCES + PERSONALITY (separated from facts)
  preferences             "Jon prefers terse responses, no corporate language" — operating preferences
                          consulted on style decisions, NOT on factual retrieval
  personality_corpus      Artemis's own dialogue patterns + observed voice
                          drift tracked over time

LAYER 4 — EPISODIC SEQUENCING
  episodes                coherent units of activity (a meeting, a session, a thread)
  episode_links           turn N → conversation M → session P → project Q
                          enables "what was happening Thursday at 3pm" queries
```

---

## 5. Indexing & retrieval

- **pgvector HNSW** indexes per-scope (sharded so growth in one project doesn't slow others)
- **Postgres FTS** via tsvector on observation text
- **Native joins** on `entity_mentions` for graph queries
- **Reciprocal-rank fusion** across all three signals (vector + FTS + graph) — the published-best hybrid retrieval pattern
- Optional **Haiku reranker** on top-20 results to boost precision at the long tail (M5)

Time-decay scoring on retrieval is preserved from v1: older observations weighted lower unless they explicitly anchor to a still-valid entity. This is different from MemPalace's temporal-proximity boost (proximity to query time) — both signals are useful; ours captures relationship freshness, theirs captures topical recency. M5 adds the proximity signal alongside decay.

---

## 6. Durability stack

```
TIER A — LIVE (active, 90-day window)
  Postgres primary on localhost:5432, artemis_os database
  pgvector + tsvector + B-tree indexes
  Hash-chain integrity check runs on every commit (DB trigger)

TIER B — LOCAL BACKUP (rolling 30-day, on-disk)
  ~/.artemis/backups/{YYYY-MM-DD-HHMMSS}.pg_dump.gz
  Nightly cron via launchd (me.artemisos.memory-backup.plist) at 04:00
  pg_dump -Fc | gzip
  Files older than 30 days pruned automatically
  Each backup verified readable (pg_restore --list) before retention

TIER C — LOCAL COLD ARCHIVE (forever, on-disk)
  ~/.artemis/archive/{year}/{month}/raw_inputs-{date}.jsonl.gz
  Nightly archive job via launchd (me.artemisos.memory-archive.plist) at 03:00
  Rows older than 90 days have payload moved to disk, row remains as placeholder
  Append-only structure — once written, never modified
  Rehydrate on demand via scripts/memory_rehydrate.py
  Local disks are TB-scale; no projected size pressure for years
```

**Cloud / off-site replication is intentionally deferred.** Jon's call 2026-05-18: local storage is sufficient at TB scale. If/when off-site becomes necessary, the additive change is straightforward (the archive directory rsyncs cleanly to any backend).

**Restore drill is part of the system, not an afterthought.** `scripts/memory_restore.py` and `docs/MEMORY-DURABILITY.md` document the exact process. Monthly drill is in the operator checklist.

---

## 7. Access surfaces

- **MCP tool suite** (M4) — 15-20 tools for memory ops, exposed via MCP protocol so any agent (including external Claude Code) can use the memory store
  - `memory.search`, `memory.add_observation`, `memory.get_entity`, `memory.list_episodes`, etc.
  - Subset of MemPalace's 29 — the high-leverage ops
- **REST API** for the app — internal, used by floating-Artemis chat orchestration
- **Floating-Artemis memory inspector** — existing right-tray that shows the observations Artemis read on this turn; gets richer in M4 with provenance drill-down
- **Per-agent isolated namespaces** (M4) — each sub-agent gets its own scope by default

---

## 8. Moat — where Artemis genuinely leads after v2 ships

1. **Cryptographic provenance** — hash-chained `raw_inputs`. Tamper-evident. No public system has this.
2. **Explicit evidence chain** — every fact links to its source(s). When a memory is wrong, we trace exactly which input caused it.
3. **Additive consolidation** — summaries supplement raw, never overwrite. Lossless by structural guarantee.
4. **Active correction loop** (M6) — when Jon says "no, that's wrong," the correction is both a new observation AND a confidence-reduction signal on the contradicted fact.
5. **Cross-modal observations** (M6) — text, image OCR, audio transcript with shared embedding space.
6. **Memory rehearsal** (M6) — spaced-repetition surfacing of decaying observations. Artemis periodically confirms "is this still true?" rather than letting facts go stale silently.
7. **Benchmarked accuracy** — we publish our LongMemEval numbers and don't claim "rock solid" without them.

---

## 9. Six-tier roadmap

Each tier ships with: schema migration(s), repository changes, tests that verify the invariants of that tier and all earlier tiers, and an addendum to this document.

| Tier | Scope | LOC est. | Dependencies | Status |
|---|---|---|---|---|
| **M1** | Lossless foundation — raw_inputs, hash chain, archive, backup, restore drill, verbatim invariant | ~800 | None — base tier | **Brief written, ready for Worker (2026-05-18)** |
| **M2** | Accuracy primitives — validity windows on entities + relations, confidence scores, source weighting, conflicts table | ~500 | M1 | Queued |
| **M3** | Scale — Postgres logical replication to a local read replica, per-scope HNSW indexes, async write pipeline | ~600 | M1, M2 | Queued |
| **M4** | Reach — MCP tool surface (15-20 tools), per-agent isolated namespaces, auto-save hooks, episodes | ~700 | M1, M2 | Queued |
| **M5** | Quality — LongMemEval benchmark harness, Haiku reranker, custom Artemis recall benchmark | ~400 | M1, M2, M4 | Queued |
| **M6** | Active correction + cross-modal + memory rehearsal | ~600 | M1–M5 | Queued |

**Total: ~3600 LOC across six tiers.** Worth ~6-8 Worker slices over the coming weeks.

---

## 10. Open questions and answered questions

### Answered 2026-05-18

- **Retention horizon** → 90 days active in Postgres, archive to local disk after that. Configurable.
- **Tamper evidence** → Hash chain (simpler than Merkle tree, sufficient for personal/team scale, AI-verifiable via single SQL function).
- **Cloud durability** → Deferred. Local disk only for now. Architecture allows additive cloud sync later.
- **Verbatim vs summary** → Verbatim is canonical, summaries supplement. Inverse of Hindsight's philosophy.
- **Per-agent isolation** → Required at the schema level via `scope_kind` + `scope_id`. Not a convention.
- **Benchmarking** → Required. LongMemEval R@5 as the published reference. Custom recall benchmark as the in-house reference.

### Still open (queued for resolution as tiers approach)

- **M3** — read replica on the same machine vs second machine? (Currently single-machine; consideration is whether to host the replica on a small NAS or similar to survive disk failure on the primary.)
- **M4** — exact list of which 15-20 MCP tools to expose. Need to pick from MemPalace's 29 + Artemis-specific ops.
- **M5** — license / source of LongMemEval dataset. Public is acceptable; need to confirm.
- **M6** — image OCR + audio transcription pipeline: subprocess `whisper.cpp` / `tesseract` locally vs hit an API. Probably local to stay consistent with the no-cloud principle.

---

## 11. How to keep this document alive

Every memory-touching slice must:
1. Reference the section here it depends on (e.g., "implements §3.1 verbatim invariant")
2. Update section 9 with the actual shipped LOC + status when merged
3. Add an entry to section 10 if any open question was resolved or a new one surfaced
4. Note any tier-renumbering or scope changes in the changelog at the bottom

When context is tight in a future session, the next AI maintainer reads this doc + the most recent COORDINATION.md entry and has the full architectural picture in <5 minutes.

---

## Changelog

- **2026-05-18 (Opus Lead)** — Doc authored after Jon + Lead's memory-system design conversation. M1 brief written into COORDINATION.md. Six-tier roadmap committed.
