# Artemis Maintenance Runbook (travel / break-glass)

Two layers protect Artemis while Jon is away.

## Layer 1 — Self-heal (automatic, NO action, NO notification)
- **launchd `KeepAlive`** auto-restarts the app if it CRASHES/exits.
- **Watchdog** (`me.artemisos.watchdog`, every 2 min) force-restarts the app if it HANGS (alive but
  unresponsive — the failure mode KeepAlive misses). It does this **silently**.
- So most outages recover within ~2 minutes on their own. **You only get an alert if the watchdog's restart
  FAILED** (true break-glass).

## Layer 2 — Break-glass (you, only when alerted) — chat with Claude to fix, app-independent
From your phone:
1. Open your SSH app (Termius / Blink) → connect to the **mini over Tailscale** (user: `artemis`).
2. `cd ~/Artemis/artemis-os`  ← the REAL repo (a session may open in an empty Desktop dir).
3. Run `claude` → a fresh Claude Code session (auto-loads memory). Say: *"Artemis is down — read
   docs/SESSION-STATE.md and help me fix it."* It has the full repo + memory, so it can diagnose + fix like
   our normal sessions — completely independent of the Artemis app being up.

### Common fixes (or just let the Claude session drive)
- **Restart (REQUIRES `-k`):** `launchctl kickstart -k gui/$(id -u)/me.artemisos.app`
  — plain `kickstart` is a NO-OP on a running service. **ALWAYS verify the pid changed:**
  `launchctl print gui/$(id -u)/me.artemisos.app | grep 'pid ='`
- **Health:** `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/healthz` (200 = up)
- **App logs:** `tail -60 ~/Library/Logs/artemisos/app.err.log`
- **Watchdog log:** `tail ~/Library/Logs/artemisos/watchdog.log`
- **Postgres up?** the known instability was an asyncpg connect-timeout — check the DB is running.
- **Recent change broke it?** `git log --oneline -8`; the Claude session can revert/fix a bad commit.

## Key facts
- Real repo: `~/Artemis/artemis-os`. Port `8000`, health at `/healthz`.
- launchd jobs: `me.artemisos.app` (app, KeepAlive) · `me.artemisos.tunnel` (cloudflared) ·
  `me.artemisos.watchdog` (this watchdog).
- Restart needs `-k` + verify the pid changed — `healthz=200` alone does NOT prove a restart.

## Watchdog alert channel (set once, before travel)
The watchdog alerts you ONLY when self-heal fails. Set the channel in `scripts/watchdog.conf` (local, NOT
committed):
- iMessage: `PHONE="+1XXXXXXXXXX"`  (Messages must be signed in on the mini)
- or Slack: `WEBHOOK_URL="https://hooks.slack.com/services/..."`
