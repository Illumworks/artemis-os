#!/bin/bash
# Artemis watchdog — silent self-heal of HANGS; alert ONLY on unrecoverable failure.
#
# launchd KeepAlive already auto-restarts the app on a crash/exit. This covers the
# OTHER failure mode: the process is alive but UNRESPONSIVE (a hang). Runs every
# ~2 min via launchd (me.artemisos.watchdog). On a failed /healthz it force-restarts
# with `-k` (plain kickstart is a no-op on a running service). It stays SILENT for
# routine self-heals and alerts Jon ONLY when a restart fails to recover the app
# (i.e. break-glass is actually needed).
#
# Alert config: scripts/watchdog.conf (gitignored — see scripts/watchdog.conf.template).
# Priority order: Slack bot token (chat.postMessage) > Slack incoming webhook > iMessage.
set -u

HEALTH_URL="http://127.0.0.1:8000/healthz"
SERVICE="gui/$(id -u)/me.artemisos.app"
STATE_DIR="$HOME/.artemis-watchdog"
ALERTED_FLAG="$STATE_DIR/alerted"          # set while in an alerted outage (no re-spam)
# watchdog.conf lives next to this script; see watchdog.conf.template for keys.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$SCRIPT_DIR/watchdog.conf"
LOG="$HOME/Library/Logs/artemisos/watchdog.log"
mkdir -p "$STATE_DIR" "$(dirname "$LOG")"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
check() { curl -fsS --max-time 10 -o /dev/null "$HEALTH_URL" 2>/dev/null; }

# _slack_post_message <token> <channel_or_user_id> <text>
# Posts via Slack chat.postMessage. The app and DB may be down; this is intentionally
# self-contained (just curl + the Slack API). Returns 0 on success, 1 on failure.
_slack_post_message() {
  local token="$1"
  local target="$2"
  local text="$3"
  # Escape backslashes and double-quotes inside the text for JSON embedding.
  local escaped
  escaped="$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  local http_code
  http_code="$(curl -fsS --max-time 15 \
    -X POST "https://slack.com/api/chat.postMessage" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data "{\"channel\":\"$target\",\"text\":\"$escaped\"}" \
    -o /dev/null -w "%{http_code}" 2>>"$LOG")" || {
      log "Slack chat.postMessage curl failed (network/timeout)"
      return 1
    }
  if [ "$http_code" = "200" ]; then
    log "Slack alert sent via chat.postMessage to $target"
    return 0
  else
    log "Slack chat.postMessage returned HTTP $http_code — check token/target in watchdog.conf"
    return 1
  fi
}

alert() {
  [ -f "$ALERTED_FLAG" ] && return   # already alerted for this ongoing outage
  local msg="$1"
  # Source config if present; failures here must never crash the watchdog.
  if [ -f "$CONF" ]; then
    # shellcheck source=/dev/null
    . "$CONF"
  fi
  local sent=0

  # --- Channel 1: Slack bot token (most reliable; does not require an incoming webhook) ---
  if [ -n "${SLACK_BOT_TOKEN:-}" ] && [ -n "${ALERT_SLACK_TARGET:-}" ]; then
    if _slack_post_message "$SLACK_BOT_TOKEN" "$ALERT_SLACK_TARGET" "$msg"; then
      sent=1
    else
      log "Slack bot alert failed — falling through to other channels"
    fi
  fi

  # --- Channel 2: Slack incoming webhook (legacy; kept as a fallback) ---
  if [ -n "${WEBHOOK_URL:-}" ]; then
    local escaped_msg
    escaped_msg="$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    if curl -fsS --max-time 15 -X POST -H 'Content-type: application/json' \
        --data "{\"text\":\"$escaped_msg\"}" "$WEBHOOK_URL" >/dev/null 2>>"$LOG"; then
      log "Slack webhook alert sent"
      sent=1
    else
      log "Slack webhook send failed"
    fi
  fi

  # --- Channel 3: iMessage (fallback; requires Messages.app signed-in on this Mac) ---
  if [ -n "${PHONE:-}" ]; then
    if osascript -e "tell application \"Messages\" to send \"$msg\" to participant \"$PHONE\"" 2>>"$LOG"; then
      sent=1
    else
      log "iMessage send failed"
    fi
  fi

  if [ "$sent" -eq 0 ]; then
    log "ALERT (no channel configured/sent in watchdog.conf): $msg"
    log "ALERT TEXT WAS: $msg"
  fi
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
