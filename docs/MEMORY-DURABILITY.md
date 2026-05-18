# Memory Durability — Operator Guide

## Three-layer durability model

Artemis OS memory is lossless by structural guarantee across three tiers:

| Tier | What it is | Where | Retention |
|------|-----------|-------|-----------|
| **raw_inputs** (hot) | Every memory write source, verbatim | Postgres | 90 days active + placeholder row forever |
| **Cold archive** | Gzipped JSONL, one file per month per archiving run | `~/.artemis/archive/` | Indefinite |
| **pg_dump backup** | Full Postgres dump, custom format | `~/.artemis/backups/` | 30 days rolling |

Derived tables (`memory_observations`, `memory_drawers`, entities, relations) FK back
to `raw_inputs` via `raw_input_id`. Even if all derived tables are truncated by a bug,
the raw source survives in `raw_inputs` and the cold archive.

---

## How the hash chain works

Every row in `raw_inputs` includes:

- **`payload_hash`** — SHA-256 of the canonical JSON of the payload. Preserved when the
  row is archived (payload is NULLed; hash stays).
- **`prev_hash`** — the `this_hash` of the immediately preceding row (NULL on the first row).
- **`this_hash`** — SHA-256 of the canonical serialization of the entire row, including
  `prev_hash`.

This forms a tamper-evident chain: if any past row is modified, every subsequent row's
`this_hash` becomes invalid. A single walk of the table detects the first break.

**Canonical serialization recipe (frozen — never change without a migration):**

```
json.dumps({
    "actor": ...,
    "created_at_iso": "<ISO 8601 with UTC offset>",
    "payload": ...,
    "prev_hash": ...,
    "scope_id": ...,
    "scope_kind": ...,
    "source_id": ...,
    "source_kind": ...,
}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

SHA-256 of UTF-8 encoding of the above string.

---

## Verifying chain integrity

```bash
# Full global chain
python -m scripts.memory_verify_chain

# Scoped (checks hash correctness for those rows only)
python -m scripts.memory_verify_chain --scope-kind project --scope-id proj-123
```

Exit 0 = intact. Exit 1 = break detected (stderr shows first_break_id).

---

## How to restore from a backup

### Step 1 — Take or locate a backup

```bash
# Take a fresh backup now
python -m scripts.memory_backup

# List existing backups
ls -lht ~/.artemis/backups/
```

### Step 2 — Verify the backup is readable

```bash
pg_restore --list ~/.artemis/backups/<TIMESTAMP>.pg_dump | head -20
```

### Step 3 — Restore to a scratch database

```bash
python -m scripts.memory_restore ~/.artemis/backups/<TIMESTAMP>.pg_dump
# → restores to artemis_os_restore
```

### Step 4 — Verify the restored data

```bash
psql -h localhost -U artemis artemis_os_restore -c "SELECT count(*) FROM raw_inputs;"
psql -h localhost -U artemis artemis_os_restore -c "SELECT count(*) FROM memory_observations;"

# Optionally run chain verify on the restored DB
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os_restore \
    python -m scripts.memory_verify_chain
```

### Step 5 — Swap (only after verifying)

```bash
# Stop the app first
# Then:
psql -h localhost -U artemis postgres -c \
    "ALTER DATABASE artemis_os RENAME TO artemis_os_old;"
psql -h localhost -U artemis postgres -c \
    "ALTER DATABASE artemis_os_restore RENAME TO artemis_os;"
# Restart the app
# Once satisfied:
psql -h localhost -U artemis postgres -c "DROP DATABASE artemis_os_old;"
```

---

## Monthly drill checklist

Run this drill on the first Monday of each month. Document results.

- [ ] Take backup: `python -m scripts.memory_backup`
- [ ] Verify dump: `pg_restore --list ~/.artemis/backups/$(ls -t ~/.artemis/backups | head -1)`
- [ ] Restore to scratch: `python -m scripts.memory_restore ~/.artemis/backups/$(ls -t ~/.artemis/backups | head -1)`
- [ ] Verify chain on restore: `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os_restore python -m scripts.memory_verify_chain`
- [ ] Verify row counts match between artemis_os and artemis_os_restore
- [ ] Drop scratch: `dropdb --if-exists -h localhost -U artemis artemis_os_restore`
- [ ] Document any issues in this file or in `../claudeck-artemis/PROJECT_LOG.md`

---

## Configuration

All paths and parameters are configurable via environment variables (prefix: `ARTEMIS_`):

| Env var | Default | Description |
|---------|---------|-------------|
| `ARTEMIS_ARCHIVE_DIR` | `~/.artemis/archive` | Root for cold archive JSONL files |
| `ARTEMIS_BACKUP_DIR` | `~/.artemis/backups` | Root for pg_dump files |
| `ARTEMIS_BACKUP_PG_HOST` | `localhost` | Postgres host for pg_dump |
| `ARTEMIS_BACKUP_PG_PORT` | `5432` | Postgres port |
| `ARTEMIS_BACKUP_PG_USER` | `artemis` | Postgres user |
| `ARTEMIS_BACKUP_PG_DBNAME` | `artemis_os` | Database to back up |
| `ARTEMIS_BACKUP_RETAIN_DAYS` | `30` | Days to keep pg_dump files |
| `ARTEMIS_ARCHIVE_AGE_DAYS` | `90` | Archive rows older than this many days |

---

## Launchd timer setup (macOS)

Plists are in `launchd/` in the repo. To install:

```bash
# Create log dir
mkdir -p ~/.artemis/logs

# Install plists
cp launchd/me.artemisos.memory-archive.plist ~/Library/LaunchAgents/
cp launchd/me.artemisos.memory-backup.plist ~/Library/LaunchAgents/

# Load (runs at 3am and 4am daily)
launchctl load ~/Library/LaunchAgents/me.artemisos.memory-archive.plist
launchctl load ~/Library/LaunchAgents/me.artemisos.memory-backup.plist

# Verify loaded
launchctl list | grep artemisos

# To run manually (test):
launchctl start me.artemisos.memory-archive
launchctl start me.artemisos.memory-backup
```

To unload: `launchctl unload ~/Library/LaunchAgents/me.artemisos.memory-archive.plist`

Logs: `~/.artemis/logs/memory-archive.log` and `memory-backup.log`.

---

## What to do if you suspect data corruption

1. **Do not restart the app** — the corrupted state is in Postgres; restarting won't help.
2. Run `python -m scripts.memory_verify_chain` — note the `first_break_id`.
3. Check the archive: if rows are archived, `python -m scripts.memory_rehydrate --ids <id>` to restore payload.
4. If the chain break is in a derived table (observations, entities), not raw_inputs — the raw source
   is still intact. Re-derive from raw_inputs.
5. If raw_inputs itself is corrupted, restore from the latest pg_dump (Step 3 above).
6. Document the incident in `../claudeck-artemis/PROJECT_LOG.md`.
