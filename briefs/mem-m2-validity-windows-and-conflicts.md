# Memory M2 — Validity Windows + Confidence + Conflict Detection

**Owner:** Sonnet Worker (isolated worktree) — NOT Codex (architectural decisions inside)
**Branch:** `worker/mem-m2-validity-and-conflicts`
**LOC budget:** ~500 (full-diff insertions; cap at ~600 with headroom)
**Brief author:** Lead (Opus 4.7)
**Depends on:** Memory M1 (raw_inputs hash chain — already shipped). Standalone otherwise.
**Grounded in:** `decisions/memory-v2-architecture.md` (section "M2 — Validity windows + confidence + conflicts")

> **Naming note:** this brief is Memory phase M2. The marketing slab also has an M-series (M1-M5b). Do not confuse them. File slug uses `mem-m2-` prefix to disambiguate. Worker branch and commit messages should likewise prefix with `mem-`.

## Why this brief exists

Memory M1 made writes lossless — every observation lands in `raw_inputs` first, hash-chained, archived after 90 days. But the consolidated layer (`observations`, `entities`, `drawers`) still treats facts as monotonically true: when something changes about an entity, the old fact is overwritten or supplemented without a structural record of supersession. This is wrong for a long-horizon memory system. Facts have lifespans, confidence is a function of evidence quality, and contradictory claims should not silently win-by-write-order.

Memory M2 makes time + confidence + conflict first-class. After M2, the question "what was true on 2026-04-01?" is answerable; "how sure are we?" has a number; "did two sources contradict each other?" surfaces a `memory_conflict` row for the operator to resolve.

This is the layer that separates Artemis OS memory from "vector store with a chat history" — it's where we beat Mem/Hindsight/Letta/MemPalace on long-horizon truth maintenance.

## Scope

### In scope

1. **Schema additions** (Alembic migration):
   - `observations.valid_from` (timestamptz NOT NULL, default = `created_at`) — when this claim starts being true
   - `observations.valid_until` (timestamptz NULL) — when this claim stopped being true (NULL = still believed)
   - `observations.confidence` (float NOT NULL DEFAULT 0.5, CHECK between 0 and 1) — model's belief in the claim
   - `observations.supersedes` (BIGINT NULL FK → observations.id) — the prior observation this one replaces, if any
   - `observations.evidence_count` (int NOT NULL DEFAULT 1) — number of corroborating raw_inputs (incremented when a new raw_input restates the same claim)
   - Same four columns on `entities` (entity-level claims have lifespans too).
   - Indexes: `(scope_id, entity_key, valid_until)` partial index `WHERE valid_until IS NULL` — the "currently valid" hot path.

2. **`memory_conflicts` table**:
   - `id` BIGINT PK
   - `scope_id` TEXT (matches observations.scope_id)
   - `observation_a_id` BIGINT FK → observations
   - `observation_b_id` BIGINT FK → observations
   - `conflict_type` TEXT — enum: `incompatible_values`, `incompatible_temporal`, `incompatible_relational`
   - `detected_at` TIMESTAMPTZ
   - `resolution` TEXT NULL — enum: `a_wins`, `b_wins`, `both_valid_different_scope`, `manual_review_needed`, NULL when unresolved
   - `resolution_reason` TEXT NULL
   - `resolved_at` TIMESTAMPTZ NULL
   - `resolved_by` TEXT NULL (user email or `auto`)
   - UNIQUE on `(observation_a_id, observation_b_id)` after sorting the pair — prevents duplicate conflict rows.

3. **`artemis/memory/conflict_detector.py`** — pure logic. Function: `detect_conflicts(new_observation, existing_observations) -> list[ConflictCandidate]`. Three detectors:
   - `_detect_incompatible_values`: same `(scope_id, entity_key, attribute_key)`, different `value`, overlapping validity windows → conflict.
   - `_detect_incompatible_temporal`: same entity, claim A says "valid 2026-01 onwards", claim B says "ended 2025-12" → conflict.
   - `_detect_incompatible_relational`: A says `entity_X RELATES_TO entity_Y`, B says `entity_X NOT_RELATES_TO entity_Y` for overlapping windows → conflict.
   The detector is pure — given observations as Python objects, returns conflict candidates. No DB calls inside. Caller pre-fetches the comparison set.

4. **Consolidator update** — `artemis/memory/consolidator.py`:
   - Before inserting a new observation, call `conflict_detector.detect_conflicts(new_obs, candidates)`.
   - Pre-fetch candidates by `(scope_id, entity_key)` with non-terminated validity window.
   - For each conflict candidate: if auto-resolvable (confidence delta > 0.3 in favor of newer + same source quality OR newer has > 2× evidence_count), apply resolution automatically (set old observation's `valid_until = now`, set new observation's `supersedes = old.id`, log `memory_conflicts` row with `resolution = auto`). Otherwise: insert both observations, write a `memory_conflicts` row with `resolution = NULL` for the operator.

5. **Retrieval ranking update** — `artemis/memory/retrieval.py`:
   - Time-decay score now uses `valid_from`/`valid_until` instead of `created_at`. An observation valid from 2026-01 with `valid_until = NULL` ranks higher today than one valid-until 2025-03.
   - Final score multiplied by `confidence`. A 0.6-confidence observation ranks below a 0.9-confidence observation with otherwise equal time-decay + relevance.
   - `evidence_count` adds a log-scale boost: `score *= 1 + log10(evidence_count)` — three corroborating sources doesn't 3× the score, but it does decisively beat one source.

6. **Repository functions**:
   - `memory_repository.list_conflicts_unresolved(scope_id=None) -> list[Conflict]`
   - `memory_repository.resolve_conflict(conflict_id, resolution, reason, resolver) -> Conflict`
   - When `resolution = a_wins`: set `observation_b.valid_until = now`, set `observation_b.supersedes = observation_a.id`. (Mirror for `b_wins`.)
   - When `resolution = both_valid_different_scope`: no observation change; just close the conflict row.

7. **HTTP routes** (mounted in `artemis/routes/memory.py` if it exists; otherwise new file):
   - `GET /api/memory/conflicts?scope_id=...&status=unresolved` — list
   - `POST /api/memory/conflicts/{id}/resolve` — body: `{resolution, reason}` — applies and returns updated row
   - `GET /api/memory/observations/{id}/history` — returns the supersession chain (observation + all ancestors via `supersedes` link)

8. **Tests**:
   - Conflict detector: 3 detector functions × (positive case, negative case, edge case) = 9 tests.
   - Consolidator: insert two contradictory observations, assert one conflict row + supersession chain set correctly. Repeat with auto-resolution case (confidence delta).
   - Retrieval: insert three observations with same relevance but different `valid_until` and `confidence`; assert ranking order.
   - Migration up/down idempotent.
   - Backfill: existing observations without `valid_from`/`confidence` get sane defaults (`valid_from = created_at`, `confidence = 0.5`, `evidence_count = 1`); test asserts no row is corrupted.

### Out of scope

- Cross-scope conflict detection. Conflicts only fire within the same `scope_id` in M2. Cross-scope is M6.
- UI for the conflict resolution surface. The HTTP routes exist; the panel comes later.
- Active correction (Artemis proactively asks the operator to resolve). That's M6.
- Rehearsal. M6.
- Vector index strategy changes. That's M3 (Memory).

## Confidence semantics

Initial confidence is set at write time by the writer:
- **Direct user statement** (e.g., user said "my email is X") → 0.95
- **Tool result** (e.g., calendar API returned an event) → 0.90
- **LLM inference from observed text** (e.g., "user seems frustrated") → 0.50–0.70 depending on the inferring agent's rubric
- **LLM speculation without direct evidence** → 0.30–0.50

Document this in a table at the top of `consolidator.py`. The numeric values are tunable; the categories are stable.

Confidence updates on corroboration: when a new raw_input restates the same claim, `evidence_count += 1` and `confidence = min(0.99, current + (1 - current) * 0.3)` — asymptotic toward 1.0, never reaches it. Pure code path; testable.

## Invariants (structural)

1. **No observation is silently overwritten.** A change to an entity's attribute creates a new observation row with `supersedes` pointing at the old one. The old row stays, with `valid_until = now`.
2. **`valid_until > valid_from`** enforced by CHECK constraint.
3. **`confidence ∈ [0, 1]`** enforced by CHECK constraint.
4. **Conflict row is atomic with observation insert.** Same transaction.
5. **Sorting the pair in the UNIQUE constraint** prevents `(a, b)` and `(b, a)` from both existing — apply `LEAST(a, b), GREATEST(a, b)` in the index expression.
6. **Retrieval respects validity windows by default.** A query for "what's true now?" filters `WHERE valid_until IS NULL OR valid_until > now()`. A query for "what was true on date X?" filters `WHERE valid_from <= X AND (valid_until IS NULL OR valid_until > X)`. The default is "now"; explicit timestamp parameter overrides.

## Files expected

- `alembic/versions/<rev>_memory_m2_validity_and_conflicts.py` — schema + backfill. ~90 LOC.
- `artemis/memory/conflict_detector.py` — pure detectors + dataclasses. ~120 LOC.
- `artemis/memory/consolidator.py` — surgical edits to integrate conflict detection. ~50 LOC delta.
- `artemis/memory/retrieval.py` — surgical edits to ranking. ~30 LOC delta.
- `artemis/memory/repository.py` — list/resolve helpers. ~40 LOC delta.
- `artemis/routes/memory.py` — 3 routes (new file if not present, else additions). ~50 LOC.
- `artemis/memory/tests/test_conflict_detector.py` — 9 tests. ~80 LOC.
- `artemis/memory/tests/test_consolidator_conflicts.py` — integration. ~60 LOC.
- `artemis/memory/tests/test_retrieval_ranking_m2.py` — ranking tests. ~40 LOC.

Total: ~560 LOC. At the cap. Worker should aim for ≤ 600 and stop to ping Lead if growing.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`).
- dotenv `override=False` (`7ad1598`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call.
- `git diff --stat` for LOC self-reporting.
- The Memory M1 hash chain on `raw_inputs` is NOT touched in M2. If a test wants to seed an observation, it must also write a raw_input row to preserve the chain. Lift this from existing M1 test helpers; don't reinvent.

## What "done" looks like

1. Schema migration up/down clean three times.
2. Three conflict types detectable; all 9 detector tests pass.
3. Consolidator writes supersession chains atomically with observation inserts.
4. Auto-resolution fires when threshold met; manual conflict row exists otherwise.
5. Retrieval ranks current observations above superseded ones; high-confidence above low; high-evidence_count above low.
6. HTTP routes resolve a conflict end-to-end (POST resolves, GET returns updated row).
7. All tests pass.
8. `./scripts/check.sh` does not regress.
9. Full-diff insertions ≤ 600. Over → stop and ping Lead.

## Report Worker submits

1. `git diff --stat` output.
2. The three detector function signatures (paste).
3. The retrieval ranking formula (paste — the actual SQL or Python expression).
4. The confidence-update formula on corroboration (paste — assert it matches the spec above).
5. Test pass count.
6. Branch + worktree path.

---

**Lead notes (not for Worker):**
- This is the architectural spine for "long-horizon truth maintenance." After M2, every memory write either updates a claim cleanly (via supersession) or surfaces a conflict for resolution — no silent overwrites, ever.
- The auto-resolution thresholds (confidence delta > 0.3, evidence_count > 2× delta) are conservative starting points. We'll tune after the first 1000 conflicts have been logged.
- HTTP routes are minimal — list, resolve, history. The full conflict-resolution UI is later; this is the data layer the UI will consume.
- **NOT a Codex candidate.** The conflict detector logic, ranking formula, and consolidator integration all have judgment calls a paste-port can't make. Sonnet Worker via terminal-Lead.
