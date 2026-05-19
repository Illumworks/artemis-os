# macOS TCC + launchd — Why the Artemis LaunchAgent Fails (exit 78)

**Status:** Workaround in place (`scripts/start-app.sh`). Root-cause fix deferred until someone wants to sit with Console.app open for an hour.

**Tracking:** task #13 on terminal-Lead's queue, referenced in `HANDOFF.md` "Session-end save 2026-05-18 LATE evening."

---

## What is TCC

**TCC = Transparency, Consent, and Control.** It's macOS's privacy framework — the thing that pops up "App X wants to access Y" prompts and gates apps from reaching protected resources. Introduced in OS X 10.7, expanded heavily in 10.14 Mojave (Files & Folders, Full Disk Access), and tightened again in every release through Sequoia.

Each binary on macOS has a "TCC identity" — derived from its bundle ID, code signature, or absolute path. The OS keeps a database (`~/Library/Application Support/com.apple.TCC/TCC.db` for user-scope, `/Library/Application Support/com.apple.TCC/TCC.db` for system-scope) of what each identity is allowed to do.

**Protected resources that require TCC grants:**
- Full Disk Access (read anything anywhere, including `~/Library/Mail`, other apps' containers)
- Files & Folders (Desktop, Documents, Downloads, network volumes)
- Automation (one app controlling another via AppleScript / Apple Events)
- Accessibility (synthesizing input events)
- Screen Recording, Microphone, Camera, Contacts, Calendar
- Developer tools (debugging other processes)

A binary without the right TCC grant gets a silent denial — system calls return ENOPERM or the API simply returns empty data with no error.

## Why this breaks LaunchAgents

When you run a command in your Terminal:
- The shell process inherits Terminal.app's TCC grants
- `uv run uvicorn ...` runs as a child of your shell, also inheriting Terminal.app's grants
- Anything those processes need from disk → Terminal.app's grant satisfies it

When `launchd` invokes the **same command** via a `LaunchAgent` plist:
- The spawned process inherits **launchd's** TCC identity, **not your shell's**
- launchd's TCC grants are essentially nothing — it's a system service, not a user-facing app
- The spawned binary (`uv`, then `uvicorn`, then `artemis.main`) needs grants in its own right
- Each binary in the chain is a separate TCC identity check

**Result:** the binary gets blocked from reading something it needs (often `.env`, the project directory, `~/.artemis/`, or the homebrew Cellar that hosts the runtime libs) and exits with EX_CONFIG (78) — sometimes before any of its own code runs, sometimes before launchd can even write to the stdout/stderr log paths in the plist.

That's why our LaunchAgent diagnostic was useless: **launchd refused to spawn the binary at all**, so the binary's own `echo "boot"` line never executed, and the log files stayed empty. Exit 78 from launchd's perspective is "configuration / prerequisite check failed before exec."

## What we tried

| Attempt | Outcome |
|---|---|
| `launchctl bootout` + `bootstrap` cycle | Exit 78, no logs |
| Debug wrapper plist with `echo "boot" > /tmp/boot.log` before `uv` | The echo never ran — proves launchd never reached exec |
| `launchctl print gui/501/me.artemisos.app` | "Could not find service" (had been booted out) |
| `@reboot` crontab line as bypass | Exit 144 — crontab is setuid root, blocked by macOS TCC unless Claude Code's Terminal has Full Disk Access |
| Manual `nohup uv run uvicorn ...` from shell | ✅ Works — shell has the TCC grants the LaunchAgent doesn't |

## Current workaround

`scripts/start-app.sh` (committed `eede147`):
- Idempotent — bails if port 8000 already has a listener
- Uses `nohup` + `disown` so the process survives shell exit
- Writes to `.app.log` and `.app.err.log` in the repo root
- Run manually after each reboot

This is acceptable for a single-user dev box that doesn't reboot often. Not acceptable for a deployed system.

## How to actually fix it (when someone wants to spend the time)

Three paths, in order of likely success:

### Path 1: Grant Full Disk Access to the right binary

1. Open System Settings → Privacy & Security → Full Disk Access
2. Add `/Users/artemis/.local/bin/uv` (resolve via `readlink -f $(which uv)`)
3. Also add `/opt/homebrew/bin/uv` if uv is installed there too
4. Possibly also add Terminal.app and Claude Code if they aren't already granted
5. Reload the LaunchAgent: `launchctl bootstrap gui/501 ~/Library/LaunchAgents/me.artemisos.app.plist`
6. Check `launchctl print gui/501/me.artemisos.app` for `state = running` and `last exit code = 0`

This is the canonical fix. ~10 minutes of clicking.

### Path 2: Use absolute paths and a self-contained runtime

If Path 1 doesn't stick, the issue is likely a binary in the chain that's still hitting a TCC gate. Solutions:

- Build a frozen executable (`pyinstaller` or `briefcase`) and grant TCC to that single binary
- Use `/usr/bin/python3` (Apple's bundled Python, which has system-level TCC grants) and a venv whose absolute paths don't traverse homebrew

This is heavier — ~half-day to make clean.

### Path 3: Skip launchd, use launchctl with Aqua session

Run as a user agent without `KeepAlive`, but instead via a login item triggered by `loginwindow`. Login items inherit the user session's TCC context. `osascript -e 'tell application "System Events" to make new login item'` is one way; macOS Settings → General → Login Items is the GUI.

Less elegant than launchd but bypasses the TCC inheritance problem entirely.

## Diagnostics that work (for future debugging)

```bash
# Is launchd refusing to spawn? Check the last exit code.
launchctl print gui/501/me.artemisos.app | grep -E "last exit|state|reason"

# Did TCC log a denial? (works on Sequoia+)
log show --predicate 'subsystem == "com.apple.TCC"' --info --last 1h | grep -i "artemis\|uv\|uvicorn"

# Check what TCC identity launchd is invoking
launchctl print gui/501/me.artemisos.app | grep -i "path\|cwd"

# Manually try the exact command launchd would run, with launchd's empty env
env -i HOME=/Users/artemis PATH=/usr/bin:/bin /path/to/uv run uvicorn artemis.main:app
```

**The `log show` query is the single most useful tool** — TCC denials appear there even when log files in the plist are empty.

## Why this matters for the project

- We're shipping a local-first app meant to run continuously on Jon's Mac mini
- Manual `start-app.sh` invocation is a footgun — one missed restart and Focus goes dark
- The TCC tightening trend means this problem gets harder, not easier, with each macOS release
- A proper fix should land before we tell anyone else to run Artemis on their machine

## Out of scope

- Cross-platform deployment (Linux is easier; we'd use systemd)
- Containerization (Docker on Mac has its own TCC headaches around volume mounts)
- Cloud-hosted Artemis (different problem entirely)

## References

- Apple's TCC docs: <https://support.apple.com/guide/security/controlling-app-access-to-files-secc01781f47/web>
- launchd manual: `man launchd.plist`
- The original failure: see `HANDOFF.md` 2026-05-18 LATE session, "One real problem I couldn't crack — the LaunchAgent"
- Workaround commit: `eede147 ops(workaround): scripts/start-app.sh`
