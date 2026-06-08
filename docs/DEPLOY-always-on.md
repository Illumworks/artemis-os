# Deploy — always-on / reboot-resilient (Mac mini)

Goal (Jon, traveling): the app must stay up unattended and **auto-recover after a reboot/power outage**.

## Current state (2026-06-08)
- **App** (`:8000`): running via a detached `nohup` process with CF Access login ON (verified: SPA 200,
  direct `/api/me` → 401). The `nohup` **survives this session ending**, but is NOT agent-managed → it does
  NOT come back on its own after a reboot/crash. ← the gap.
- **Tunnel** (`app.artemisos.me` → localhost:8000): managed by the `me.artemisos.tunnel` LaunchAgent
  (auto-restarts). ✅
- **Power**: `pmset autorestart=1` (starts after power failure) ✅, `sleep=0` (won't sleep) ✅. Already set.

## The blocker (root cause, confirmed)
The repo lives under **`~/Desktop/`**. macOS privacy (TCC) **blocks launchd-spawned third-party binaries**
(the venv Python) **from reading files under Desktop** — confirmed: a launchd agent running the venv uvicorn
dies with `PermissionError: Operation not permitted: .venv/pyvenv.cfg`. (Apple binaries like `/bin/bash` are
exempt, which is why a plain `ls` test passed — and why the app has only ever run from a shell with inherited
Desktop access, never as a service.) So **the app can't auto-start as a background service while it's in
Desktop.**

## Fix A — grant Full Disk Access (fast; gets reboot-resilience now)  ← recommended before travel
The app agent (`~/Library/LaunchAgents/me.artemisos.app.plist`) is already configured correctly (venv
uvicorn + WorkingDirectory + CF env baked in). It just needs the Python interpreter to be allowed to read
Desktop:
1. **System Settings → Privacy & Security → Full Disk Access → "+"**.
2. Press **Cmd+Shift+G**, paste: `/opt/homebrew/opt/python@3.11/bin/` and add **`python3.11`**.
   (Also add `/opt/homebrew/bin/uv` for good measure.) Toggle them **ON**.
3. Tell Lead → Lead loads the agent (`launchctl bootstrap gui/$UID …/me.artemisos.app.plist`) and verifies
   it serves :8000 with CF on + restarts on crash (KeepAlive). Then it survives reboots.
- Tradeoff: FDA is a broad grant on that Python. Acceptable for a dedicated always-on box. Re-grant if Python
  is upgraded to a new minor (e.g. 3.12).

## Fix B — move the repo off Desktop (cleaner, permanent; do later)
Move the repo to e.g. `/Users/artemis/artemis-os` (NOT under Desktop/Documents/Downloads). Then launchd can
read it with no FDA needed. More robust long-term, but requires re-pointing git worktrees + paths — a
deliberate migration, not a rushed one. Recommended eventually; the tunnel points at `localhost:8000` so it's
unaffected by the move.

## Until fixed
A reboot/power outage will restart the Mac (autorestart=1) but the **app will NOT auto-recover** (the agent
fails on Desktop). So either do **Fix A before travel**, or accept that a power event means the app stays
down until someone restarts it. The `nohup` keeps it up as long as the Mac doesn't reboot.
