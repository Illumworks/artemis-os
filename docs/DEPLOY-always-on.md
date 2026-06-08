# Deploy — always-on / reboot-resilient (Mac mini)

Goal (Jon, traveling): app stays up unattended + **auto-recovers after a reboot/power outage**.

## Status (2026-06-08)
- **App** (`:8000`): now managed by the **`me.artemisos.app` LaunchAgent** (venv uvicorn + CF Access login
  env baked in, `RunAtLoad` + `KeepAlive`). **Crash-recovery verified** (killed it → auto-restarted, still
  login-enforcing). ✅
- **Tunnel** (`app.artemisos.me` → localhost:8000): `me.artemisos.tunnel` LaunchAgent (auto-restart). ✅
- **Power**: `pmset autorestart=1` (start after power failure) ✅, `sleep=0` ✅.
- **Login verification**: ON (direct `/api/me` → 401; logged-in Google users via the tunnel → in). ✅
- **REMAINING — auto-login (Jon's step):** see below. Without it, an unattended reboot brings the Mac back
  but the LaunchAgents don't load until someone logs in.

## How the app agent was made to work (the Desktop/TCC saga)
The repo is under `~/Desktop/`, which macOS privacy (TCC) protects. Two things had to be fixed:
1. **Full Disk Access** granted (drag-drop) to the REAL Python binary the venv resolves to —
   `/opt/homebrew/Cellar/python@3.11/3.11.15/Frameworks/Python.framework/Versions/3.11/bin/python3.11`
   (NOT the `/opt/homebrew/opt/...` symlink — TCC checks the real path).
2. The agent's **log files moved off Desktop** → `~/Library/Logs/artemisos/` (launchd couldn't create
   stdout/stderr files inside the Desktop folder).

⚠️ **FDA is version-pinned to `3.11.15`.** Do NOT `brew upgrade python@3.11` — it would change the Cellar
path and break the grant (+ the venv). If Python must be upgraded, re-grant FDA to the new path. The
permanent fix that removes this fragility is moving the repo off Desktop (below).

## REMAINING — enable auto-login (for unattended power-outage recovery)  ← Jon, before travel
LaunchAgents load at user **login**. After a power outage the Mac auto-restarts, but the app/tunnel won't
start until the user session exists. So enable automatic login:
- **System Settings → Users & Groups → (Login window / Automatically log in as) → select `artemis`** (enter
  the password once). 
- Tradeoff: anyone with physical access boots into the session. Acceptable for a dedicated always-on office
  box; it's the standard choice for a headless server-style Mac.
- Once on, the full chain works: power outage → auto-restart → auto-login → agents load → app + tunnel up.

## Permanent fix (later, not rushed) — move the repo off Desktop
Move `~/Desktop/Artemis/artemis-os` → e.g. `/Users/artemis/artemis-os` (non-TCC). Removes the FDA fragility
entirely (no version-pinned grant). Requires re-pointing git worktrees + updating the agent paths + the
team's working-dir references — a deliberate, coordinated migration. Do when Jon's back. Tunnel (localhost
port) is unaffected.

## Files
- `~/Library/LaunchAgents/me.artemisos.app.plist` (app), `me.artemisos.tunnel.plist` (tunnel) — versioned
  copies in `deploy/`.
- `com.artemis.server` LaunchAgent = the OLD Node app (claudeck-artemis, :9009) — unrelated; ignore/disable.
