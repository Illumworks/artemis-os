# Memory Durability

> "The operator should be able to recover from disaster in five minutes following a single document."

This document is that document.

---

## Three-layer durability model

The memory system runs three concentric tiers of protection.

| Tier | Location | Updated | Retained |
|------|----------|---------|----------|
| **Live** | `artemis_os` Postgres | Real-time | Lossless — rows are never deleted |
| **Nightly backup** | `~/.artemis/backups/` | Daily via launchd | 7 days (configurable) |
| **Cold archive** | `~/.artemis/cold-archive/` | Monthly via `memory_archive_cold.py` | Indefinitely |

**Lossless rule (from the memory keystone architecture):** `memory_drawers` and `memory_observations` are never `DELETE`-d from the live database. Observations leave active retrieval via `superseded_by` — a forward pointer to the newer row — not deletion. This means the live database is itself a complete audit trail.

**Backup safety model:** The backup script uses a create-verify-THEN-prune order:
1. Dump to a new timestamped file.
2. Verify the new file with `pg_restore --list`.
3. Only after verification succeeds, prune files older than `--keep-days`.
4. Refuse to prune if the live DB has 0 rows across critical tables (anomaly guard).
5. Never delete the last remaining backup.

---

## How the observation supersession chain works

Every memory observation has an optional `superseded_by` column that points to a newer row. When consolidation produces a better synthesis of the evidence, it writes a new observation and sets the old one's `superseded_by` to the new ID. The old row remains permanently.

```sql
-- Two observations: obs #1 superseded by obs #2
SELECT id, content, superseded_by FROM memory_observations ORDER BY id;

  id |          content          | superseded_by
-----+---------------------------+---------------
   1 | "Jon prefers Slack DMs."  |             2   ← retired; still here
   2 | "Jon prefers Slack DMs;   |          NULL   ← active; this is what retrieval reads
       verified 2026-05-18."     |
```

`memory_verify_chain.py` walks every non-null `superseded_by` link and checks for:
- **Dangling FKs**: a `superseded_by` pointing to a non-existent row.
- **Cycles**: a → b → a (would cause infinite loops in chain walkers).

The chain is not a cryptographic hash chain; the integrity guarantee is structural (referential integrity + no cycles), not cryptographic.

---

## Restore drill — the five-minute procedure

Use this when you need to verify that a specific backup is restorable. This creates a temporary `artemis_restore` database and drops it when you're done.

```bash
# 1. Find the backup you want to test
ls -lh ~/.artemis/backups/

# 2. Restore to a throwaway DB
uv run python -m scripts.memory_restore \
    ~/.artemis/backups/artemis_os_20260518T020000Z.pgdump.gz \
    --target artemis_restore \
    --drop-before-restore

# 3. Verify the chain on the restored DB
uv run python -m scripts.memory_verify_chain --db artemis_restore

# 4. Spot-check row counts
psql -h localhost -U artemis -d artemis_restore \
    -c "SELECT COUNT(*) FROM memory_observations;"

# 5. Drop the restore DB when satisfied
psql -h localhost -U artemis -d postgres \
    -c "DROP DATABASE IF EXISTS artemis_restore;"
```

Expected total time: 2–4 minutes for a 50 MB backup on local Postgres.

---

## Monthly drill — automated

`scripts/memory_drill.py` runs the full dance (backup → verify → restore → chain check → row count comparison → cleanup) and writes a structured JSON report.

**Install the launchd job (runs on the 1st of each month at 02:00):**

```bash
# 1. Create the log directory
mkdir -p ~/.artemis/logs ~/.artemis/drill-reports

# 2. Copy the plist
cp launchd/me.artemisos.memory-drill.plist ~/Library/LaunchAgents/

# 3. Load it
launchctl load ~/Library/LaunchAgents/me.artemisos.memory-drill.plist

# 4. Trigger a test run immediately
launchctl start me.artemisos.memory-drill

# 5. Read the report
cat ~/.artemis/drill-reports/$(date +%Y-%m-%d).json | python -m json.tool
```

**Where reports live:** `~/.artemis/drill-reports/YYYY-MM-DD.json`

**What a passing report looks like:**
```json
{
  "pass": true,
  "run_at": "2026-06-01T02:03:47.123456+00:00",
  "steps": [
    {"name": "backup",              "ok": true,  "duration_ms": 1200, "detail": "wrote artemis_os_20260601T020347Z.pgdump.gz"},
    {"name": "verify_backup",       "ok": true,  "duration_ms":  340, "detail": "214 TOC entries"},
    {"name": "live_row_counts",     "ok": true,  "duration_ms":   80, "detail": "total across critical tables: 1847"},
    {"name": "restore_to_drill_db", "ok": true,  "duration_ms": 3100, "detail": "restored to artemis_drill, 214 TOC entries"},
    {"name": "verify_chain",        "ok": true,  "duration_ms":  420, "detail": "ok — 0 links checked, 1847 active observations"},
    {"name": "row_count_comparison","ok": true,  "duration_ms":  190, "detail": "all 7 tables match"},
    {"name": "cleanup_drill_db",    "ok": true,  "duration_ms":  110, "detail": "dropped artemis_drill"}
  ],
  "notes": []
}
```

**What a failing report looks like** (chain broken):
```json
{
  "pass": false,
  "steps": [
    ...
    {"name": "verify_chain", "ok": false, "detail": "Chain broken: 1 issue(s). First: {'id': 42, 'superseded_by': 99, 'reason': 'superseded_by=99 does not exist'}"}
  ],
  "notes": ["broken chain: id=42 reason=superseded_by=99 does not exist"]
}
```

A non-zero exit code from `memory_drill.py` means a step failed. Wire this to a Slack alert in your on-call rotation.

---

## What to do if you suspect corruption

**1. Run the chain verifier first:**
```bash
uv run python -m scripts.memory_verify_chain --db artemis_os --json
```

If `"ok": true` with no broken links, the logical structure is intact. The issue may be in data quality, not corruption.

**2. If the chain is broken:**
```bash
# Find the last known-good backup
ls -lt ~/.artemis/backups/

# Restore it to a side database and re-verify
uv run python -m scripts.memory_restore \
    ~/.artemis/backups/<last-good>.pgdump.gz \
    --target artemis_recovery \
    --drop-before-restore

uv run python -m scripts.memory_verify_chain --db artemis_recovery
```

**3. Swap the live DB (drastic — only if the live DB is unrecoverable):**
```bash
# Stop the artemis server first
# Then in psql as a superuser:
psql -U postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'artemis_os';"
psql -U postgres -c "ALTER DATABASE artemis_os RENAME TO artemis_os_corrupted_$(date +%Y%m%d);"
psql -U postgres -c "ALTER DATABASE artemis_recovery RENAME TO artemis_os;"
# Restart the server
```

Document the swap in `docs/WORKLOG.md` with the date, backup used, and reason.

---

## Environment variables that affect memory durability

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARTEMIS_DB_URL` | `postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os` | SQLAlchemy async URL for the live DB. Used by all scripts for connection params. |
| `ARTEMIS_HOME` | `~/.artemis` | Root data directory. Backups go to `$ARTEMIS_HOME/backups/`, drill reports to `$ARTEMIS_HOME/drill-reports/`, logs to `$ARTEMIS_HOME/logs/`. |

The launchd plist sets both variables explicitly. If you change the DB host or credentials, update the plist and reload it.

---

## For AI maintainers

This section is addressed to the next AI (Claude, Codex, or successor) maintaining this system.

**Lines you must not cross:**

1. **Never `DELETE FROM memory_drawers` or `DELETE FROM memory_observations` on the production database.** These tables are the evidence floor. Observations are retired via `superseded_by`, never deleted. If you find a migration that does this, refuse and escalate.

2. **Never run `alembic downgrade` on `artemis_os` without taking a verified backup first.** The sequence is: `memory_backup.py` → confirm the new `.pgdump.gz` is readable → then downgrade. No exceptions.

3. **Never prune backups programmatically unless the new backup has been verified.** The `memory_backup.py` script enforces this. If you're editing that script, preserve the create-verify-THEN-prune order.

4. **Never restore to `artemis_os` (the live database) without the two-layer guard** (`--force` + `--i-understand-this-overwrites-live-data`). The `memory_restore.py` script enforces this. If you're editing that script, preserve both flags as required conditions.

5. **The `artemis_drill` database is ephemeral.** It is created and dropped by the monthly drill. Never treat it as persistent or use it for anything else.

6. **If the monthly drill has been failing for more than one cycle,** that is a P1 incident. Do not silently note it — surface it to the human operator immediately.

7. **Off-site backup sync is explicitly out of scope (local-only, per Jon 2026-05-18).** Do not add cloud sync, S3 upload, or any remote transfer to these scripts without a new explicit decision.

8. **Encrypted backups are deferred to a future hardening pass.** Do not add encryption unless a new decision authorizes it.
