# Roadmap design — memory/database backup + restore (Drive-backed)

**Status:** ROADMAP capture (Jon, 2026-06-08, on behalf of Angela). Not a build-now task — captured so it
isn't lost. Becomes important once the app is live and the database is accumulating real data.

## The ask (Angela)
Once Artemis is running and building up a real database (memory drawers/observations, the Claims Register,
Writing Studio memory, signals, campaigns, etc.), we want a **regular, automated backup** so that **if
something happens, we can re-import and restore it**. **Storage: Google Drive** (the org has effectively
unlimited space there). This is a safety net for the crown-jewel data — especially the lossless memory
keystone, which is irreplaceable.

## Design sketch (to flesh out when we build it)
- **What to back up:** a full Postgres dump (`pg_dump` of `artemis_os`) — simplest + complete (captures
  memory, claims, writing rules/examples, signals, campaigns, deliverables, everything). Optionally a second
  "memory-only" logical export (the keystone tables) for fast partial restore. Compress (`.dump`/`.sql.gz`).
- **Cadence:** scheduled + regular (e.g. nightly), configurable. Runs unattended.
- **Where it runs:** ops/automation surface — a natural job for the planned **health/ops agent + scheduled
  tasks** (ties to `docs/artemis-agent-architecture-and-governance.md`). Until that exists, a cron/scheduled
  task can do it.
- **Storage:** upload the dump to a dedicated **Google Drive** folder (we have the Drive connector +
  unlimited space). Timestamped filenames (`artemis_os_YYYY-MM-DD.dump`).
- **Retention:** keep a rolling window (e.g. last 30 daily + 12 monthly); prune older. (Space is ample, but
  unbounded growth is still messy — keep it tidy, and per the lossless rule, prefer pruning OLD full dumps,
  never the live data.)
- **Restore:** a documented, **tested** re-import path (`pg_restore` / `psql`) — download the dump from
  Drive → restore into a fresh `artemis_os`. A restore drill should be run so we KNOW it works before we
  ever need it (an untested backup is not a backup).
- **Integrity:** verify each dump (non-zero size, restorable to a scratch DB) before declaring it good; log
  success/failure to the ops channel.
- **Security:** the dump contains ALL data — store it in an access-controlled Drive folder; consider
  encrypting the dump at rest (the Drive folder's sharing must be locked down). Do NOT email it or put it
  anywhere broadly shared.

## Why it's roadmap, not now
There's little data to lose yet, and the backup job is most naturally owned by the health/ops agent we've
already designed. Build it around the time we cut over to live use (a "pre-launch" must-have). Flagging now
so it's a committed requirement, not an afterthought.
