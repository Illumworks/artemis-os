#!/bin/bash
# Artemis log rotation — keeps the always-on logs from growing without bound.
#
# Why this exists: nothing was rotating these. app.out.log reached 25MB (one line
# per request, forever) and app.err.log 3.4MB. That is the same only-ever-grows
# shape as the socket leak that caused the 2026-08-05 outage, just with a much
# longer fuse and disk as the ceiling instead of a port. Bound it and stop
# thinking about it.
#
# WHY COPY-TRUNCATE AND NOT `mv`: launchd opens StandardOutPath/StandardErrorPath
# once and holds that file descriptor for the life of the process. Renaming the
# file does NOT make launchd reopen it — the app would keep writing into the
# renamed file, so the "current" log would stay empty until the next restart and
# the rotated copy would grow forever. cloudflared behaves the same way. Copying
# the contents aside and then truncating IN PLACE keeps the descriptor valid.
# The tradeoff is a tiny window between cp and truncate where a line can be lost;
# that is fine for logs and is the standard `copytruncate` approach.
#
# Installed as me.artemisos.logrotate (daily). Safe to run by hand any time.
set -u

MAX_BYTES=$((20 * 1024 * 1024))   # rotate once a log exceeds 20MB
KEEP=2                            # generations kept, gzipped (.1.gz, .2.gz)

LOGS=(
  "$HOME/Library/Logs/artemisos/app.out.log"
  "$HOME/Library/Logs/artemisos/app.err.log"
  "$HOME/Library/Logs/artemisos/watchdog.log"
  "$HOME/.cloudflared/tunnel.log"
  "$HOME/.cloudflared/tunnel.err.log"
)

STAMP="$(date '+%F %T')"
rotated=0

for f in "${LOGS[@]}"; do
  [ -f "$f" ] || continue
  size=$(stat -f%z "$f" 2>/dev/null || echo 0)
  [ "$size" -le "$MAX_BYTES" ] && continue

  # Age out old generations (highest first so nothing is overwritten early).
  i="$KEEP"
  while [ "$i" -gt 1 ]; do
    [ -f "$f.$((i-1)).gz" ] && mv -f "$f.$((i-1)).gz" "$f.$i.gz"
    i=$((i-1))
  done

  # Copy aside, then truncate in place — see the note above about the held fd.
  if cp "$f" "$f.1" 2>/dev/null; then
    : > "$f"
    rm -f "$f.1.gz"
    gzip -f "$f.1" 2>/dev/null
    echo "$STAMP rotated $(basename "$f") ($size bytes -> 0)" >> "$HOME/Library/Logs/artemisos/logrotate.log"
    rotated=$((rotated+1))
  else
    echo "$STAMP FAILED to copy $f — left untouched" >> "$HOME/Library/Logs/artemisos/logrotate.log"
  fi
done

[ "$rotated" -eq 0 ] && echo "$STAMP nothing over $((MAX_BYTES/1024/1024))MB" >> "$HOME/Library/Logs/artemisos/logrotate.log"
exit 0
