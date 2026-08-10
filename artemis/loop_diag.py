"""TEMPORARY event-loop freeze diagnostics.

Installed from the FastAPI lifespan to catch the intermittent app-unresponsiveness
bug (asyncpg connect-timeout / event-loop freeze). REMOVE once that's closed; see
``briefs/instability-asyncpg-connect-timeout.md``.

How it works:
- A tiny asyncio ``_heartbeat`` task stamps ``_last_beat`` every 0.25s.
- A daemon ``_watch`` *thread* (not a task — a task can't run while the loop is
  blocked) checks the heartbeat. If the loop hasn't ticked in > ``_STUCK_THRESHOLD``
  seconds, the loop is frozen *right now*, so it dumps every thread's stack via
  ``faulthandler`` — capturing whatever sync call is blocking the loop live.
- SIGUSR1 -> all-thread stack dump (C-level handler; works even mid-freeze).
- SIGUSR2 -> dump of pending asyncio tasks (only fires if the loop is alive).

All output goes to stderr (-> ``~/Library/Logs/artemisos/app.err.log`` under launchd).
"""

from __future__ import annotations

import asyncio
import faulthandler
import signal
import sys
import threading
import time

_last_beat = time.monotonic()
_STUCK_THRESHOLD = 1.5  # seconds the loop may go silent before we call it frozen


def _dump_tasks() -> None:
    try:
        tasks = [t for t in asyncio.all_tasks() if not t.done()]
    except RuntimeError:
        return
    print(f"=== DIAG asyncio pending tasks: {len(tasks)} ===", file=sys.stderr)
    for t in tasks:
        print(f"--- task {t.get_name()} ---", file=sys.stderr)
        t.print_stack(file=sys.stderr)
    sys.stderr.flush()


async def _heartbeat() -> None:
    global _last_beat
    while True:
        _last_beat = time.monotonic()
        await asyncio.sleep(0.25)


def _watch() -> None:
    reported = False
    while True:
        time.sleep(0.25)
        delta = time.monotonic() - _last_beat
        if delta > _STUCK_THRESHOLD:
            if not reported:
                stamp = time.strftime("%H:%M:%S")
                print(
                    f"\n=== DIAG event-loop STUCK {delta:.2f}s @ {stamp} ===",
                    file=sys.stderr,
                    flush=True,
                )
                faulthandler.dump_traceback(all_threads=True)
                sys.stderr.flush()
                reported = True
        else:
            reported = False


def _on_usr2(_signum: int, _frame: object) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_soon_threadsafe(_dump_tasks)


def install() -> None:
    """Wire up the freeze detectors. Call once, from the running event loop."""
    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    except (ValueError, OSError):
        pass  # not main thread / unsupported
    try:
        signal.signal(signal.SIGUSR2, _on_usr2)
    except (ValueError, OSError):
        pass
    asyncio.create_task(_heartbeat(), name="diag-heartbeat")
    threading.Thread(target=_watch, name="diag-loop-watch", daemon=True).start()
    print(
        "=== DIAG loop_diag installed (heartbeat + stuck-watch thread) ===",
        file=sys.stderr,
        flush=True,
    )
