# Memory V2 Architecture

Captured from Jon + Lead's 2026-05-18 design conversation.

## Why V2

The 2026-05-17 test-suite truncation bug wiped real OKR + integration data twice.
The conftest guard (`artemis_test` DB isolation) is the immediate fix. The architectural
fix is M1: make memory data structurally lossless so a future bug cannot silently destroy facts.

Design discipline (applies to all M-phases): **invariants over conventions.**
The system should be lossless by structural guarantee, not by careful coding.

---

## Six-phase plan

### M1 — Lossless foundation (this slice)

**Target invariants:**
1. Every memory-write source lands in `raw_inputs` first (verbatim, append-only).
2. `raw_inputs` rows form a SHA-256 hash chain — tamper-evident.
3. Cold archive moves payloads of rows >90 days old to `~/.artemis/archive/` (JSONL.gz).
   Rows stay as placeholders; hash chain remains continuous.
4. Nightly `pg_dump` writes to `~/.artemis/backups/` with 30-day rolling retention.

**Key files:** `artemis/memory/raw_inputs.py`, `hashchain.py`, `archive.py`, `backup.py`,
`alembic/versions/0016_memory_raw_inputs.py`, scripts, docs.

**Dependencies:** none (foundation tier).

---

### M2 — Validity windows + confidence + conflicts

**Target invariants:**
- Every observation and entity has `valid_from` / `valid_until` (already partially present).
- Confidence scores are first-class (not just `score` + `source_quality`).
- Conflict detection: two observations with incompatible claims in the same scope trigger
  a conflict record that surfaces to the operator for resolution.

**Key files:** new `memory_conflicts` table, updated consolidator, updated retrieval ranking.

**Dependencies:** M1 (needs raw_input_id provenance for conflict resolution).

---

### M3 — Replication + per-scope HNSW

**Target invariants:**
- Postgres logical replication to a read replica (or pgvector-optimized shard).
- Per-scope HNSW indexes so ANN search stays fast as the embedding table grows.

**Key files:** Alembic migration for HNSW index strategy, replication config.

**Dependencies:** M2 (stable schema before indexing strategy is locked).

---

### M4 — MCP tools + per-agent namespaces + auto-save

**Target invariants:**
- Memory read/write exposed as MCP tools (callable by external agents).
- Each agent run gets its own `scope_id` so cross-agent pollution is impossible.
- Auto-save: the agent loop persists every user turn + tool result to `raw_inputs`
  without the caller having to call `write_drawer` / `write_observation` explicitly.

**Key files:** `artemis/mcp/memory_tools.py`, updated `chat.py` auto-save hook.

**Dependencies:** M1 (raw_inputs is the auto-save target), M2 (validity windows on saves).

---

### M5 — Benchmark + reranker

**Target invariants:**
- LongMemEval benchmark suite wired into CI (or a local runner).
- A reranker model (cross-encoder) re-scores the top-K ANN results before prompt injection.

**Key files:** `artemis/memory/benchmark/`, `artemis/memory/reranker.py`.

**Dependencies:** M3 (stable HNSW indexes), M4 (auto-save generating real evaluation data).

---

### M6 — Cross-modal + active correction + rehearsal

**Target invariants:**
- Observations can reference images, audio transcripts, calendar events (cross-modal payloads).
- Active correction: Artemis detects when a new observation contradicts an existing one and
  proposes a supersession to the operator.
- Rehearsal: periodic re-retrieval of observations to prevent "forgetting" via score decay.

**Key files:** updated `MemoryObservation` model, new rehearsal scheduler.

**Dependencies:** M5 (benchmark to measure correction + rehearsal impact).

---

## Explicitly out of scope (deferred)

- **Cloud replication (R2/S3)**: explicitly local-only per Jon 2026-05-18.
  The cold archive and pg_dump are the durability story for V1.
- **Multi-user namespacing**: single-operator system; `owner_user_id` is a future hook.
- **Federated memory across instances**: single Artemis OS instance for now.
