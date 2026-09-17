#!/bin/bash
# Champions digest — weekly delivery. This is the one that reaches people.
#
# It deliberately does NOT ingest first. If the daily job has been failing, the
# right outcome is a stale-but-honest digest rather than a fresh pull at send
# time that hides a broken daily job -- the Run log tab and `artemis ops` are
# where that is meant to surface.
#
# A failed send is reported to Jon by DM. This job runs unattended and nobody
# reads champions-weekly.err.log; a silent failure would look exactly like a
# quiet week, and the recipients would have no way to tell the difference.
set -uo pipefail
cd /Users/artemis/Artemis/artemis-os || exit 1
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$STAMP] weekly digest"

OUTPUT="$(uv run python -m artemis.champions --deliver --days 7 "$@" 2>&1)"
STATUS=$?
echo "$OUTPUT"

if [ $STATUS -ne 0 ]; then
    echo "[$STAMP] FAILED (exit $STATUS) — notifying"
    TAIL="$(echo "$OUTPUT" | tail -12)" uv run python - <<'PY' || echo "notification also failed"
import asyncio, os, httpx
from artemis.db import SessionLocal, engine
from artemis.champions.deliver import _kai_token

JON = "U09F3EPJXSQ"

async def main() -> None:
    async with SessionLocal() as session:
        token, _ = await _kai_token(session)
    detail = os.environ.get("TAIL", "")[:2500]
    text = (
        ":rotating_light: *The Champions weekly digest did not send.*\n"
        "Nobody received it, and the recipients cannot tell this from a quiet week.\n"
        f"```{detail}```\n"
        "Re-run by hand: `./scripts/champions_weekly.sh --slack C0B26KNKGCT`"
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
fi

exit $STATUS
