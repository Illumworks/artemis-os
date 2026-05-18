# Memory V2 Architecture Decisions

> Decision record for the memory keystone design. Maintained alongside code.

---

## §1 — Goals

The memory system must be:
- **Lossless**: no evidence is ever destroyed
- **Durable**: recoverable from hardware failure or operator error
- **Auditable**: every observation has a traceable source

---

## §2 — Scope

Covers `memory_drawers`, `memory_observations`, `memory_evidence`, the graph layer (`memory_entities`, `memory_relations`), and the retrieval pipeline.

---

## §3 — Core constraints

### §3.1 — Verbatim canonical

`memory_drawers` rows are written once and never modified. Content is immutable evidence. The `content_hash` column enforces deduplication within a scope. Any system that writes to `memory_drawers` must never `UPDATE` or `DELETE` existing rows.

`memory_observations` are retired via `superseded_by`, never deleted. This preserves the full chain of inference over time.

---

## §4 — Retrieval model

Active observations are those where `superseded_by IS NULL`. Retrieval queries filter on this condition. Embeddings are stored separately in `memory_embeddings` for approximate vector search.

---

## §5 — Consolidation

The consolidator reads recent drawers, synthesizes new observations via Claude, and chains them to existing ones using `superseded_by`. Each consolidation run is idempotent given the same input hash.

---

## §6 — Durability stack

Three tiers:

1. **Live database** (`artemis_os`): Postgres with WAL enabled. Lossless by design — the keystone models never delete rows from drawer/observation tables.

2. **Nightly backup** (hot tier): `pg_dump` in custom format, gzip-compressed, written to `~/.artemis/backups/`. Retained for 7 days. Managed by `scripts/memory_backup.py`. Safety model: create → verify → prune (never the reverse).

3. **Cold archive**: Backups older than 30 days are moved to `~/.artemis/cold-archive/` by `scripts/memory_archive_cold.py`. Retained indefinitely.

**Monthly drill**: `scripts/memory_drill.py` runs the full backup-restore-verify cycle against a throwaway `artemis_drill` database. Reports are written to `~/.artemis/drill-reports/`. See `docs/MEMORY-DURABILITY.md` for the five-minute recovery procedure.

**Out of scope for V2**: off-site sync, encrypted backups, Postgres replication (WAL streaming). These are deferred to M3.

---

## §7 — Graph layer (B4)

Entities and relations are extracted from observations by Claude. The graph is scope-local (same name in two scopes = two entity rows). Relation predicates come from a controlled vocabulary; unknown predicates are logged to `memory_relation_rejections` for vocabulary expansion.

---

## §8 — Change log

| Date | Decision | Owner |
|------|----------|-------|
| 2026-05 | Initial architecture, lossless rule, three-tier durability | Memory M1 |
| 2026-05-18 | Durability scripts + monthly drill added | Lead/memory-drill-automation |
