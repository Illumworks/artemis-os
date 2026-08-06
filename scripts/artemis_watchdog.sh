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

# A SINGLE probe is not evidence of a hang. When the box has stuck sockets on
# port 8000, a share of fresh TCP connects never complete their handshake, so an
# otherwise-healthy app fails a lone probe several times an hour. Restarting on
# that is actively harmful: the -k SIGKILL strands more sockets on 8000, which
# raises the stall rate, which fails more probes — a self-reinforcing spiral that
# turns a degraded-but-serving app into a hard outage (2026-08-05: 11 restarts in
# 3h, ending in a false "STILL DOWN" alert). Require several CONSECUTIVE failures
# so we only act on a real hang.
# 8 tries x 3s gap = ~24s worst case, comfortably inside the 120s launchd interval.
# Sized empirically, not from independent-probability math: stalls arrive in bursts
# (an observed run needed all of the first 5 tries), so assuming independence
# understates the false-positive risk. Keep the margin wide.
PROBE_TRIES=8
PROBE_GAP=3

_probe() { curl -fsS --max-time 10 -o /dev/null "$HEALTH_URL" 2>/dev/null; }

# Healthy if ANY probe in the series succeeds. PROBES_USED is the free degradation
# signal: a healthy box answers on probe 1 every time, so a rising average means
# connects are starting to stall. This is what we alert on — see creep_check.
PROBES_USED=0
check() {
  local i
  for i in $(seq 1 "$PROBE_TRIES"); do
    PROBES_USED="$i"
    if _probe; then
      [ "$i" -gt 1 ] && log "healthy on probe $i/$PROBE_TRIES (earlier probes stalled — not a hang)"
      return 0
    fi
    [ "$i" -lt "$PROBE_TRIES" ] && sleep "$PROBE_GAP"
  done
  return 1
}

# --- Leading indicators -------------------------------------------------------
# The 2026-08-05 outage was a MONOTONIC LEAK: unreaped TIME_WAIT sockets on port
# 8000 built up for ~3-4 weeks with nothing watching, then crossed the threshold
# where TCP handshakes start failing. Up/down health checks cannot see that coming
# — right up to the cliff the app is "up" and answering in 1ms. So we also watch
# the two cheap trend signals that DO move early, and alert while there is still
# time to act. Both cost zero new connections to port 8000 (adding probe traffic
# would feed the very leak we are watching).
TW_WARN=1500            # port-8000 TIME_WAIT count that predicts stalls (~3400 = ~13% failures)
PROBE_AVG_WARN=2.0      # mean probes-to-first-success over the recent window
CREEP_WINDOW=30         # cycles kept (~1h at the 120s launchd interval)
PROBE_HIST="$STATE_DIR/probe_history"
CREEP_FLAG="$STATE_DIR/creep_alerted"

creep_check() {
  # Rolling history of probes-to-first-success.
  echo "$PROBES_USED" >> "$PROBE_HIST"
  tail -n "$CREEP_WINDOW" "$PROBE_HIST" > "$PROBE_HIST.tmp" 2>/dev/null && mv "$PROBE_HIST.tmp" "$PROBE_HIST"

  local n avg tw
  n=$(wc -l < "$PROBE_HIST" 2>/dev/null | tr -d ' ')
  [ -z "$n" ] && return 0
  avg=$(awk '{s+=$1} END {if (NR) printf "%.2f", s/NR; else print "0"}' "$PROBE_HIST")
  tw=$(netstat -an -p tcp 2>/dev/null | grep -c '\.8000.*TIME_WAIT')

  # Only judge the average once the window is reasonably full.
  local degraded=0
  [ "$n" -ge 10 ] && awk -v a="$avg" -v w="$PROBE_AVG_WARN" 'BEGIN{exit !(a>w)}' && degraded=1
  [ "${tw:-0}" -gt "$TW_WARN" ] && degraded=1

  if [ "$degraded" -eq 1 ]; then
    if [ ! -f "$CREEP_FLAG" ]; then
      touch "$CREEP_FLAG"   # claim before sending — see the note in alert()
      log "DEGRADING: port-8000 TIME_WAIT=$tw (warn>$TW_WARN), mean probes=$avg over $n cycles (warn>$PROBE_AVG_WARN)"
      alert "Artemis is DEGRADING (still serving, not down): $tw stuck sockets on port 8000, avg $avg probes to get a response. A share of connections are failing to handshake. Fix = reboot the mini to clear the socket table; restarting the app does NOT help. (artemis watchdog)"
    fi
  else
    rm -f "$CREEP_FLAG"
    log "creep ok: port-8000 TIME_WAIT=$tw, mean probes=$avg over $n cycles"
  fi
}

# --- Restart circuit breaker --------------------------------------------------
# If restarting were going to fix it, one restart would have. Repeated restarts
# mean the cause is NOT in the process (on 2026-08-05 it was kernel socket state,
# which survives restarts) and each forced restart made things worse. Past the
# budget we stop restarting and escalate to a human instead.
RESTART_LOG="$STATE_DIR/restarts"
RESTART_BUDGET=3        # restarts allowed per window
RESTART_WINDOW=3600     # seconds

restarts_recent() {
  [ -f "$RESTART_LOG" ] || { echo 0; return; }
  local now; now=$(date +%s)
  awk -v n="$now" -v w="$RESTART_WINDOW" '$1 > n-w' "$RESTART_LOG" | wc -l | tr -d ' '
}

record_restart() {
  date +%s >> "$RESTART_LOG"
  tail -n 50 "$RESTART_LOG" > "$RESTART_LOG.tmp" 2>/dev/null && mv "$RESTART_LOG.tmp" "$RESTART_LOG"
}

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

# alert <msg> — sends on the first channel that works. Deliberately does NOT own
# any dedupe flag: there are now two independent alert conditions (hard outage and
# slow degradation), and when they shared one flag a degradation alert would
# silence a genuine DOWN alert. Each caller claims its own flag, BEFORE calling
# here — sending can be slow, and launchd will happily start the next run while
# this one is still inside alert(), which duplicated alerts until flags moved first.
alert() {
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

  # --- Channel 3: iMessage (last-resort fallback; needs Messages.app signed in) ---
  # Only if nothing else got through: this channel is the slow, flaky one. osascript
  # into Messages can block for ~2 minutes (AppleEvent timeouts, or Messages simply
  # refusing the `send`) — long enough for launchd to start overlapping watchdog
  # runs. So it is both gated on `sent` and hard-bounded by a kill timer.
  if [ "$sent" -eq 0 ] && [ -n "${PHONE:-}" ]; then
    osascript -e "tell application \"Messages\" to send \"$msg\" to participant \"$PHONE\"" 2>>"$LOG" &
    local osa_pid=$!
    local waited=0
    while kill -0 "$osa_pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
      sleep 1; waited=$((waited+1))
    done
    if kill -0 "$osa_pid" 2>/dev/null; then
      kill -9 "$osa_pid" 2>/dev/null
      log "iMessage send timed out after ${waited}s — killed"
    elif wait "$osa_pid" 2>/dev/null; then
      sent=1
    else
      log "iMessage send failed"
    fi
  fi

  if [ "$sent" -eq 0 ]; then
    log "ALERT (no channel configured/sent in watchdog.conf): $msg"
    log "ALERT TEXT WAS: $msg"
  fi
}

# Healthy → clear outage state, but still look at the trend before exiting: "up"
# is not the same as "well", and the leak that caused the last outage was only
# ever visible in the trend.
if check; then
  rm -f "$ALERTED_FLAG"
  creep_check
  exit 0
fi

# Unhealthy. Before touching anything, check the restart budget — if we have
# already restarted repeatedly this hour, restarting is demonstrably not the fix.
RECENT=$(restarts_recent)
if [ "$RECENT" -ge "$RESTART_BUDGET" ]; then
  log "healthz FAILED but restart budget spent ($RECENT in last $((RESTART_WINDOW/60))m) — NOT restarting"
  [ -f "$ALERTED_FLAG" ] && exit 1
  touch "$ALERTED_FLAG"
  alert "Artemis is failing health checks and has already been restarted $RECENT times this hour, so restarting is not fixing it. Not restarting again (forced restarts can make this worse). Needs hands: Tailscale SSH to the mini, run claude, read SESSION-STATE.md. (artemis watchdog)"
  exit 1
fi

# Unhealthy after every probe → silent self-heal.
# Step 1: GRACEFUL. SIGTERM lets uvicorn close its listener and its open
# connections properly; launchd KeepAlive then respawns it. `-k` (SIGKILL) gives
# the process no chance to tear down, stranding its sockets on port 8000 in a
# TIME_WAIT state this box does not reap — the very thing that causes the stalls.
# So escalate to -k only if graceful didn't bring it back.
log "healthz FAILED ${PROBE_TRIES}x — self-healing with graceful SIGTERM (restarts this hour: $RECENT)"
record_restart
APP_PID="$(launchctl print "$SERVICE" 2>/dev/null | awk '/^\tpid = /{print $3}')"
if [ -n "${APP_PID:-}" ]; then
  kill -TERM "$APP_PID" 2>/dev/null
else
  log "could not resolve app pid — falling straight through to -k"
fi
sleep 25
if check; then
  log "self-healed after graceful restart (silent)"
  rm -f "$ALERTED_FLAG"
  exit 0
fi

# Step 2: escalate to a force restart.
log "still failing after SIGTERM — escalating to -k restart"
launchctl kickstart -k "$SERVICE" >/dev/null 2>&1
sleep 25
if check; then
  log "self-healed after -k restart (silent)"
  rm -f "$ALERTED_FLAG"
  exit 0
fi

# Restart did NOT recover it → break-glass needed → alert (once).
log "STILL DOWN after -k restart — alerting (break-glass needed)"
if [ ! -f "$ALERTED_FLAG" ]; then
  touch "$ALERTED_FLAG"
  alert "Artemis is DOWN and auto-restart did not recover it. Break-glass: Tailscale SSH to the mini, run claude, read SESSION-STATE.md + fix. (artemis watchdog)"
fi
exit 1
