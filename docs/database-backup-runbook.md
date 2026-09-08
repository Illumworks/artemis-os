# Database backup — runbook

Live since 2026-09-08. Before that date there was **no database backup of any
kind** — not stale, not misconfigured, none had ever been taken.

The git remote backs up the code and has never held a single row. Everything the
system has learned (4,630 signals, 2,251 memory observations, the enablement
catalog, every Argus dossier, months of scout output) existed on one Mac mini's
disk. Losing that disk meant losing all of it.

## What runs

| | |
|---|---|
| Script | `scripts/backup_db.sh` |
| Schedule | `me.artemisos.backup` launchd job, nightly 03:30 |
| Destination | `~/artemis-backups/artemis_os-YYYYMMDD-HHMMSS.dump` |
| Format | pg_dump custom (`-Fc`), ~18MB compressed from a 127MB database |
| Retention | 14 days, rotated **only after** a verified good dump exists |
| Log | `~/Library/Logs/artemisos/backup.log` |

## Two things that would have made it silently useless

**pg_dump on PATH is the wrong major version.** It is 16.14; the server is 17.10,
and pg_dump refuses to dump a newer server. A script calling plain `pg_dump`
fails every night. The script pins
`/opt/homebrew/opt/postgresql@17/bin/pg_dump` and treats its absence as a hard
failure rather than falling back to the broken one.

**A file existing is not a backup.** Every dump is verified with `pg_restore
--list` and a 1MB size floor before anything rotates. A truncated or empty dump
is deleted and the previous backups are kept, so a bad night can never destroy a
good copy.

## Restoring

```bash
DUMP=$(ls -t ~/artemis-backups/artemis_os-*.dump | head -1)
createdb -O artemis artemis_restore_test
/opt/homebrew/opt/postgresql@17/bin/pg_restore \
    -d artemis_restore_test -U artemis --no-owner --no-privileges "$DUMP"
```

Then compare counts against live before trusting it:

```bash
for t in signal_queue memory_observations enablement_assets scout_runs; do
  echo "$t live=$(psql -tA -d artemis_os -c "SELECT count(*) FROM $t")" \
       "restored=$(psql -tA -d artemis_restore_test -c "SELECT count(*) FROM $t")"
done
```

Verified this way on 2026-09-08: all counts matched exactly. **Re-run this check
occasionally.** A backup nobody has restored is a hope, not a backup.

## Checking it is still happening

`uv run python -m artemis.ops` reports backup age under `BACKUP`, and raises a
`stuck` finding when the newest is over 48 hours old (two consecutive misses, so
a laptop asleep overnight does not cry wolf) or when none exists at all.

This check exists because a backup job that quietly stops is the same failure as
a scout that runs and emits nothing: everything reports fine, and the thing being
relied on is not happening.

## What is NOT covered

- **Off-machine copy.** Backups sit on the same disk as the database. That
  protects against a bad migration or a dropped table; it does **not** protect
  against disk failure or theft. An off-machine target (external volume, or an
  encrypted bucket) is the obvious next step and is not done.
- **`.env`.** Not in git and not in the dump. Credentials would need re-entering
  after a bare-metal restore.
- **`writing-samples/`.** ~97MB of PDFs, on disk only, catalogued in
  `docs/writing-samples-manifest.md`.

## A stale plist worth removing

`~/Library/LaunchAgents/com.artemis.server.plist` points at
`/Users/artemis/Desktop/Artemis/claudeck-artemis`, a path the repo left long ago.
It is not loaded. It is the job that might once have done this.
