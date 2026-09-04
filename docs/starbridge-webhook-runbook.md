# Starbridge webhook — runbook

What this is, and every place the URL is written down, so a hostname change is a
checklist rather than an archaeology exercise.

Live since 2026-09-04. First backfill: 1,021 signals across five bridges.

## The shape of it

Starbridge POSTs a bridge row the moment its columns finish processing, instead
of a scout polling every four hours. The delivery is Ed25519-signed, verified
against a public key, and routed to one of two stores depending on what the
signal is.

```
Starbridge bridge (webhook column)
    │  POST, Ed25519-signed over {webhook-id}.{webhook-timestamp}.{raw body}
    ▼
Cloudflare Access — BYPASS policy on this one path
    ▼
https://app.artemisos.me/api/starbridge/webhook
    ▼
artemis/routes/starbridge.py      verify signature, 5-min replay window, idempotency
    ▼
artemis/starbridge/router.py      resolve bridge type, dedupe, classify
    ├── procurement / funding / approved-vendor  →  signal_queue (pending_qualification)
    ├── meeting / leader transition              →  memory_observations
    └── unclassifiable                           →  nowhere, counted
```

## THE URL IS WRITTEN DOWN IN FIVE PLACES

`https://app.artemisos.me/api/starbridge/webhook`. If the hostname, the scheme or
the path ever changes, all five need updating. Four of them are outside this
repo, which is the whole reason this file exists.

| # | Where | How to change it | In this repo? |
|---|---|---|---|
| 1 | **Starbridge webhook columns** — one per bridge | Dashboard → open bridge → the webhook column ("Callie Ping") → edit URL | no |
| 2 | **Cloudflare Access application** "Starbridge Webhook" | Zero Trust → Access → Applications → Destinations → path | no |
| 3 | **Cloudflare tunnel** `me.artemisos.tunnel` | the tunnel config maps the hostname to `127.0.0.1:8000` | no |
| 4 | **DNS** for `app.artemisos.me` | Cloudflare DNS | no |
| 5 | **The route prefix** | `artemis/routes/starbridge.py`, `APIRouter(prefix="/api/starbridge")` | yes |

**Which bridges carry a webhook column** (as of 2026-09-04) — each is a separate
edit, and there is no bulk update:

- RFPs - State & State DOE
- Amira RFP Search
- Intervention Search (delete?)
- K-8 After School Curriculum RFPs
- Amira Learning Board Minutes Mentions

To re-check that list at any time, ask the API rather than the UI:

```bash
uv run python -c "
import asyncio, os, httpx, json, artemis
from artemis.scouts.starbridge.client import StarbridgeClient
async def m():
    c = StarbridgeClient(api_key=os.getenv('STARBRIDGE_API_KEY',''))
    h={'Authorization': f'Bearer {os.getenv(\"STARBRIDGE_API_KEY\",\"\")}'}
    async with httpx.AsyncClient(timeout=90, base_url='https://dashboard.starbridge.ai') as cl:
        for b in await c.list_bridges():
            r = await cl.get(f'/api/external/bridge/{b.bridge_id}/column/metadata', headers=h)
            if r.status_code == 200 and 'webhook' in json.dumps(r.json()).lower():
                print(f'{b.filter_type:8} {b.name}')
asyncio.run(m())"
```

## Credentials

Two, both in `.env`, neither in git.

- `STARBRIDGE_API_KEY` — bearer token for the REST API. Dashboard → Settings →
  API Keys. Used by the scout and by bridge-type resolution, **not** by the
  webhook path.
- `STARBRIDGE_WEBHOOK_PUBLIC_KEY` — Ed25519 verification key, `whpk_` prefixed.
  Dashboard → Settings → Webhook Keys. It is a *public* key: it can verify
  signatures and never create them, so it is not a secret.

**If the signing key is regenerated in Starbridge, every delivery fails with a
signature mismatch** until this is updated. That is the loudest failure mode
here, and the log says exactly that.

Empty key = the endpoint returns 503 and refuses everything. Fail-closed is
deliberate: an endpoint that accepts unsigned bodies when a key is missing is an
open write path into the signal queue.

## Verifying it end to end

```bash
# 1. the path is open and the app is behind it (expect 401 — signature refused)
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://app.artemisos.me/api/starbridge/webhook \
  -H 'Content-Type: application/json' -H 'webhook-id: probe' \
  -H "webhook-timestamp: $(date +%s)" -H 'webhook-signature: v1a,AAAA' -d '{}'

# 2. the REST of the app is still protected (expect 302)
curl -s -o /dev/null -w '%{http_code}\n' https://app.artemisos.me/

# 3. deliveries are landing
grep 'starbridge webhook' ~/Library/Logs/artemisos/app.err.log | tail -20
```

| Symptom | Cause |
|---|---|
| `302` on the webhook path | Cloudflare Access bypass is gone or its path no longer matches |
| `401` on a real delivery | signing key regenerated in Starbridge, or the raw body was re-serialised before verifying |
| `503` | `STARBRIDGE_WEBHOOK_PUBLIC_KEY` is empty |
| `405` on localhost | the app is running code from before the route existed — restart it |
| Starbridge says "Delivered", nothing in our log | **it did not arrive.** Starbridge records success at the row level for deliveries that never reached us; this happened on the first attempt and cost an hour. Trust the log and the database, never that column. |

**A restart is not optional after changing this code.** The route, the profile
cache and the settings are all read at import. The first webhook attempt failed
precisely because the running process predated the endpoint.

## Two design decisions worth not re-litigating

**Dedupe runs before classification.** A duplicate is a duplicate whether or not
this particular copy classifies. Kansas arrived as "...Public School Districts
RFP" from one bridge and "...Public School Districts" from another; only the
first carries the word that marks it procurement, so classifying first dropped
the second as unclassified instead of recognising it. Headlines are compared with
`normalize_headline`, which strips procurement-vehicle words (RFP, RFI, Request
for Proposal, Invitation to Bid) — a closed list, not a similarity score, because
a threshold high enough to catch this would merge two districts' separate
screener RFPs.

**The bridge's own `filterType` decides which reason codes are reachable.** The
webhook body does not carry it, and guessing from the row's words put 21
board-minute rows into Josh's campaign queue — "Charleston County SD Allocates
$2.2M for Amira" is a customer saying they already bought, not a procurement
trigger — and tagged "Pasadena Unified SD RFP for Security Patrols" as a literacy
RFP. The type is resolved from the API and cached per process; a lookup failure
degrades to the keyword heuristic rather than dropping the delivery.
