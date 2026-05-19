#!/bin/bash
# Manual app starter — workaround for me.artemisos.app LaunchAgent failing with exit 78 (EX_CONFIG).
# Run this after every reboot until task #13 (LaunchAgent diagnosis) is resolved.
#
# Cause: launchd refuses to spawn the bash command — almost certainly a macOS xpcproxy/TCC
# restriction unrelated to the app code itself. Repro: kickstart the agent → exit 78 with zero
# output written to .app.log or .app.err.log. Direct invocation works (this script does that).
#
# Why not @reboot crontab: cron requires Terminal/Claude Code to have Full Disk Access; on this
# Mac it doesn't, so `crontab` install hangs/fails.

set -euo pipefail

PROJECT_ROOT="/Users/artemis/Desktop/Artemis/artemis-os"
cd "$PROJECT_ROOT"

# If port 8000 already has a listener, bail — don't double-start
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 8000 already in use — app likely already running. Skipping."
  exit 0
fi

# Start uvicorn detached. nohup + disown so it survives this shell exiting.
nohup /opt/homebrew/bin/uv run uvicorn artemis.main:app --host 0.0.0.0 --port 8000 \
  >> "$PROJECT_ROOT/.app.log" \
  2>> "$PROJECT_ROOT/.app.err.log" \
  < /dev/null &
disown

sleep 3
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Artemis OS started on port 8000."
else
  echo "WARN: port 8000 still empty after 3s — check .app.err.log"
  exit 1
fi
