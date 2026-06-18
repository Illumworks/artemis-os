#!/bin/bash
# Artemis watchdog — silent self-heal of HANGS; alert ONLY on unrecoverable failure.
#
# launchd KeepAlive already auto-restarts the app on a crash/exit. This covers the
# OTHER failure mode: the process is alive but UNRESPONSIVE (a hang). Runs every
# ~2 min via launchd (me.artemisos.watchdog). On a failed /healthz it force-restarts
# with `-k` (plain kickstart is a no-op on a running service). It stays SILENT for
# routine self-heals and alerts Jon ONLY when a restart fails to recover the app
# (i.e. break-glass is actually needed).
set -u

HEALTH_URL="http://127.0.0.1:8000/healthz"
SERVICE="gui/$(id -u)/me.artemisos.app"
STATE_DIR="$HOME/.artemis-watchdog"
ALERTED_FLAG="$STATE_DIR/alerted"          # set while in an alerted outage (no re-spam)
CONF="$(cd "$(dirname "$0")" && pwd)/watchdog.conf"   # optional: PHONE=... or WEBHOOK_URL=...
LOG="$HOME/Library/Logs/artemisos/watchdog.log"
mkdir -p "$STATE_DIR" "$(dirname "$LOG")"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
check() { curl -fsS --max-time 10 -o /dev/null "$HEALTH_URL" 2>/dev/null; }

alert() {
  [ -f "$ALERTED_FLAG" ] && return   # already alerted for this ongoing outage
  local msg="$1"
  [ -f "$CONF" ] && . "$CONF"
  local sent=0
  if [ -n "${PHONE:-}" ]; then
    osascript -e "tell application \"Messages\" to send \"$msg\" to participant \"$PHONE\"" 2>>"$LOG" \
      && sent=1 || log "iMessage send failed"
  fi
  if [ -n "${WEBHOOK_URL:-}" ]; then
    curl -fsS -X POST -H 'Content-type: application/json' \
      --data "{\"text\":\"$msg\"}" "$WEBHOOK_URL" >/dev/null 2>>"$LOG" && sent=1 || log "webhook send failed"
  fi
  [ "$sent" -eq 0 ] && log "ALERT (no channel configured/sent in watchdog.conf): $msg"
  touch "$ALERTED_FLAG"
}

# Healthy → clear any outage state and exit silently.
if check; then
  rm -f "$ALERTED_FLAG"
  exit 0
fi

# Unhealthy → silent self-heal: force-restart and re-check.
log "healthz FAILED — self-healing with -k restart"
launchctl kickstart -k "$SERVICE" >/dev/null 2>&1
sleep 25
if check; then
  log "self-healed after restart (silent)"
  rm -f "$ALERTED_FLAG"
  exit 0
fi

# Restart did NOT recover it → break-glass needed → alert (once).
log "STILL DOWN after -k restart — alerting (break-glass needed)"
alert "Artemis is DOWN and auto-restart did not recover it. Break-glass: Tailscale SSH to the mini, run claude, read SESSION-STATE.md + fix. (artemis watchdog)"
exit 1
