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

log "ingest + classify"
uv run python -m artemis.champions            || log "WARN: ingest/classify returned $?"
log "pod join"
uv run python -m artemis.champions --resolve-pods || log "WARN: pod join returned $?"
log "amira replies"
uv run python -m artemis.champions --replies  || log "WARN: replies returned $?"
log "sheet rebuild"
uv run python -m artemis.champions --sheet    || log "WARN: sheet rebuild returned $?"
log "done"
