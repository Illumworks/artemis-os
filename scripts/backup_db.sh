#!/bin/bash
#
# Nightly Postgres backup for Artemis OS.
#
# There was no database backup of any kind before 2026-09-08. The git remote
# backs up the CODE; it has never held a single row. Everything the system has
# learned -- signals, memory observations, the enablement catalog, Argus dossiers,
# months of scout output -- existed only on one Mac mini's disk.
#
# Two things this script refuses to do, both learned the hard way elsewhere in
# this repo:
#
#   1. It never reports success for work it did not do. A dump is only a backup
#      once pg_restore can read it back, so every dump is verified with
#      `pg_restore --list` before the old ones are rotated out. A zero-byte or
#      truncated file exits non-zero and keeps yesterday's copy.
#
#   2. It pins pg_dump to the SERVER's major version. The pg_dump on PATH is
#      16.14 while the server is 17.10, and pg_dump refuses to dump a newer
#      server, so a script that just calls `pg_dump` fails every single night.
#
set -uo pipefail

BACKUP_DIR="${ARTEMIS_BACKUP_DIR:-$HOME/artemis-backups}"
KEEP_DAYS="${ARTEMIS_BACKUP_KEEP_DAYS:-14}"
LOG_DIR="$HOME/Library/Logs/artemisos"
LOG="$LOG_DIR/backup.log"
PGDUMP="/opt/homebrew/opt/postgresql@17/bin/pg_dump"
PGRESTORE="/opt/homebrew/opt/postgresql@17/bin/pg_restore"

DB_NAME="${ARTEMIS_BACKUP_DB:-artemis_os}"
DB_USER="${ARTEMIS_BACKUP_USER:-artemis}"
DB_HOST="${ARTEMIS_BACKUP_HOST:-localhost}"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
fail() { log "FAILED: $*"; exit 1; }

[ -x "$PGDUMP" ] || fail "pg_dump not found at $PGDUMP (server is 17.x; the one on PATH is 16.x and cannot dump it)"

STAMP="$(date '+%Y%m%d-%H%M%S')"
OUT="$BACKUP_DIR/artemis_os-$STAMP.dump"

log "starting dump of $DB_NAME -> $OUT"
if ! "$PGDUMP" -Fc -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f "$OUT" 2>>"$LOG"; then
    rm -f "$OUT"
    fail "pg_dump returned non-zero; no backup written this run"
fi

# A file existing is not a backup. Prove it can be read back.
if ! "$PGRESTORE" --list "$OUT" > /dev/null 2>>"$LOG"; then
    rm -f "$OUT"
    fail "dump was written but pg_restore could not read it; removed and kept the previous backups"
fi

SIZE_BYTES=$(stat -f%z "$OUT" 2>/dev/null || echo 0)
# The database is ~127MB, so a healthy compressed dump is tens of MB. Anything
# under a megabyte means something emptied or the dump stopped early, and that
# must NOT quietly rotate away a good backup from yesterday.
if [ "$SIZE_BYTES" -lt 1000000 ]; then
    rm -f "$OUT"
    fail "dump was only $SIZE_BYTES bytes, far below expectation; removed and kept the previous backups"
fi

TABLES=$("$PGRESTORE" --list "$OUT" 2>/dev/null | grep -c "TABLE DATA" || echo 0)
log "OK: $(du -h "$OUT" | cut -f1), $TABLES tables with data"

# Rotate only AFTER a verified good dump exists.
DELETED=$(find "$BACKUP_DIR" -name 'artemis_os-*.dump' -type f -mtime "+$KEEP_DAYS" -print -delete | wc -l | tr -d ' ')
[ "$DELETED" -gt 0 ] && log "rotated out $DELETED backup(s) older than $KEEP_DAYS days"

REMAINING=$(find "$BACKUP_DIR" -name 'artemis_os-*.dump' -type f | wc -l | tr -d ' ')
log "done. $REMAINING backup(s) on disk in $BACKUP_DIR"
exit 0
