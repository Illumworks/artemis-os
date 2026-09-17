#!/bin/bash
# Champions digest — weekly delivery. This is the one that reaches people.
#
# It deliberately does NOT ingest first. If the daily job has been failing, this
# should send a stale-but-honest digest rather than quietly doing a fresh pull
# at send time and hiding that the daily job is broken -- the Run log tab and
# `artemis ops` are where that shows up.
set -uo pipefail
cd /Users/artemis/Artemis/artemis-os || exit 1
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] weekly digest"
exec uv run python -m artemis.champions --deliver --days 7 "$@"
