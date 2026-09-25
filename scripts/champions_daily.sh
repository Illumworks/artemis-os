#!/bin/bash
# Champions digest — daily refresh. Sends NOTHING.
#
# The weekly digest renders from the database, so without this it would report
# whatever was last pulled. Each step is separate and ordered: an item has to be
# stored before it can be classified, classified before its pod matters, and all
# of that before the sheet is worth rebuilding. A step that fails leaves the
# earlier work committed rather than rolling the whole night back.
set -uo pipefail
cd /Users/artemis/Artemis/artemis-os || exit 1
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

FAILURES=""
note_failure() { FAILURES="${FAILURES}${1}"$'\n'; log "WARN: $1"; }

# A failed refresh is reported, for the reason 2026-09-25 demonstrated: the
# Vanilla API host moved that morning and every call began returning 302. This
# script logs a warning and carries on, which is right -- a broken ingest should
# not stop the sheet rebuilding -- but nobody reads champions-daily.out.log, so
# the first sign would have been Monday's digest quietly missing a week of posts.
notify_failures() {
    [ -z "$FAILURES" ] && return 0
    FAILURES="$FAILURES" uv run python - <<'PY' || log "notification also failed"
import asyncio, os, httpx
from artemis.db import SessionLocal, engine
from artemis.champions.deliver import _kai_token

JON = "U09F3EPJXSQ"

async def main() -> None:
    async with SessionLocal() as session:
        token, _ = await _kai_token(session)
    text = (
        ":warning: *The Champions daily refresh had a failing step.*\n"
        "The digest still sends on Monday, but it will be missing whatever this "
        "step would have added.\n"
        f"```{os.environ.get('FAILURES', '')[:2000]}```"
    )
    async with httpx.AsyncClient(timeout=30) as http:
        await http.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": JON, "text": text},
        )
    await engine.dispose()

asyncio.run(main())
PY
}

log "ingest + classify"
uv run python -m artemis.champions            || note_failure "ingest/classify failed (exit $?)"
log "pod join"
uv run python -m artemis.champions --resolve-pods || note_failure "pod join failed (exit $?)"
log "amira replies"
uv run python -m artemis.champions --replies  || note_failure "replies failed (exit $?)"
log "sheet rebuild"
uv run python -m artemis.champions --sheet    || note_failure "sheet rebuild failed (exit $?)"
notify_failures
log "done"
