# Cost Phase 4 — Cloud infra tab (Fly.io reference)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-4-cloud-infra-tab`
**Browser smoke owner:** Lead, post-merge — open Cost page → Cloud infra tab, verify projection card renders with editable machine config.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~180 (1 small endpoint + tab renderer + tests).
**Priority:** MEDIUM — answers "what would it cost to host this app in the cloud".
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** Phase 2 merged.

---

## Why this exists

The audit grounded that Cloudflare alone can't host the backend (no long-running container support for FastAPI + Postgres + APScheduler + pgvector). Fly.io fits the stack well, so we use it as the reference for monthly infra cost. Cloudflare can plug in for static CDN + R2 storage.

This tab is a **projection**, not a deployment. It tells Jon "if you deployed today on Fly.io with this config, monthly infra would be ~$X. Add your synthetic API LLM cost from the Spend tab for the full picture."

---

## Scope

### Part A — Backend: pricing constants + small endpoint

NEW: `artemis/costs/cloud_pricing.py`

```python
"""Fly.io + Cloudflare reference pricing (USD, as of 2026-06-06).

Per-component rates — UI lets the user pick a config size and we sum.
"""

FLY_MACHINES = {
    "shared-cpu-1x": {"vcpu": 1, "ram_gb": 0.256, "monthly_usd": 1.94},
    "shared-cpu-2x": {"vcpu": 2, "ram_gb": 0.512, "monthly_usd": 3.89},
    "shared-cpu-4x": {"vcpu": 4, "ram_gb": 1.0,   "monthly_usd": 7.78},
    "performance-1x": {"vcpu": 1, "ram_gb": 2.0,  "monthly_usd": 27.00},
    "performance-2x": {"vcpu": 2, "ram_gb": 4.0,  "monthly_usd": 45.00},
    "performance-4x": {"vcpu": 4, "ram_gb": 8.0,  "monthly_usd": 90.00},
    "performance-8x": {"vcpu": 8, "ram_gb": 16.0, "monthly_usd": 180.00},
}

FLY_POSTGRES = {
    "small": {"ram_gb": 4, "storage_gb": 4, "monthly_usd": 20.0},   # dev/small prod
    "medium": {"ram_gb": 8, "storage_gb": 10, "monthly_usd": 50.0},
    "large": {"ram_gb": 16, "storage_gb": 40, "monthly_usd": 120.0},
}

FLY_VOLUME_USD_PER_GB_MO = 0.15
FLY_BANDWIDTH_FREE_GB = 160  # included
FLY_BANDWIDTH_USD_PER_GB = 0.02

CLOUDFLARE_R2_USD_PER_GB_MO = 0.015
CLOUDFLARE_R2_FREE_GB = 10
CLOUDFLARE_HYPERDRIVE_FREE = True  # included for now
```

NEW: `GET /api/costs/cloud-projection`

Query params (all optional, with defaults that map to current single-Mac realistic deploy):
- `machine`: one of `FLY_MACHINES.keys()` (default `"performance-2x"`)
- `postgres`: one of `FLY_POSTGRES.keys()` (default `"small"`)
- `volume_gb`: int (default `10`)
- `r2_storage_gb`: int (default `0`)

Response:

```json
{
  "config": {
    "machine": "performance-2x",
    "postgres": "small",
    "volume_gb": 10,
    "r2_storage_gb": 0
  },
  "components": [
    {"name": "Compute (performance-2x, 4GB)", "monthly_usd": 45.00},
    {"name": "Postgres (small)", "monthly_usd": 20.00},
    {"name": "Volume (10 GB)", "monthly_usd": 1.50},
    {"name": "Bandwidth (first 160 GB free)", "monthly_usd": 0.00},
    {"name": "Cloudflare R2 (0 GB, 10 GB free)", "monthly_usd": 0.00},
    {"name": "Cloudflare CDN + Hyperdrive", "monthly_usd": 0.00}
  ],
  "total_monthly_usd": 66.50,
  "notes": [
    "Fly.io pricing as of 2026-06-06.",
    "Cloudflare can front static assets via Workers Pages + R2 for blobs.",
    "LLM API spend lives in the Spend tab; add both for total cloud monthly."
  ]
}
```

### Part B — Frontend tab

Replace the placeholder in `cost-shell.js` for the Cloud infra tab.

UI:

```
Cloud infra (Fly.io reference)

Configuration:
  Machine:    [Performance-2x (2 vCPU, 4 GB RAM) ▼]
  Postgres:   [Small (4 GB RAM, 4 GB storage) ▼]
  Volume:     [10] GB
  R2 storage: [0] GB

Components:
  Compute (performance-2x, 4 GB)       $45.00 / mo
  Postgres (small)                     $20.00 / mo
  Volume (10 GB)                        $1.50 / mo
  Bandwidth (first 160 GB free)         $0.00 / mo
  Cloudflare R2 (0 GB)                  $0.00 / mo
  Cloudflare CDN + Hyperdrive           $0.00 / mo

Infra total:                           $66.50 / mo
+ LLM API (this month):                $87.40 / mo (from Spend tab)
= Estimated monthly cloud cost:       $153.90 / mo

Notes:
  • Fly.io pricing as of 2026-06-06.
  • Cloudflare can front static assets via Workers Pages + R2 for blobs.
  • This is a reference — actual cost depends on real traffic and usage.
```

Changing any config control re-fetches the projection and updates the components + totals. No write surface; nothing persists to the DB.

### Part C — Tests

`artemis/routes/tests/test_cloud_projection.py` (new):

1. **Default config returns expected total.** Hit `/api/costs/cloud-projection` with no params. Verify total = sum of components for the default config.
2. **Custom machine changes total.** `?machine=performance-4x` → verify compute component matches `FLY_MACHINES["performance-4x"]["monthly_usd"]`.
3. **Bandwidth free tier respected.** Worker doesn't actually accept bandwidth as a param in this phase; verify bandwidth always shows 0 (within free tier).
4. **R2 free tier respected.** `?r2_storage_gb=5` → $0. `?r2_storage_gb=20` → $0.15 (10 GB beyond free × $0.015).

---

## Files owned

- NEW: `artemis/costs/cloud_pricing.py`
- EDIT: `artemis/routes/costs.py` (or new `artemis/routes/costs_cloud.py`) — add `/cloud-projection` endpoint
- EDIT: `public/js/features/cost-shell.js` (replace Cloud infra placeholder)
- EDIT: `public/css/panels/cost.css` (config controls, component rows)
- NEW: `artemis/routes/tests/test_cloud_projection.py`

---

## Acceptance criteria

1. **No schema changes.** **Paste.**
2. **Backend tests pass.** **Paste.**
3. `./scripts/check.sh` passes. **Paste.**
4. **Live smoke (Lead does post-merge):**
   - Open Cost page → Cloud infra tab.
   - Verify default config renders with total ~$66.50.
   - Change machine dropdown to "Performance-4x"; verify compute jumps to $90 and total updates.
   - Change Postgres dropdown to "Medium"; verify total updates again.
   - Verify the "LLM API + Infra = Estimated monthly cloud cost" line picks up the Spend tab's current value (you'll need to fetch Phase 2's `/summary` endpoint in parallel and reuse `totals.cost_usd`).
   - **Paste a screenshot of the populated tab.**
5. `git diff --stat`. **Paste.**

---

## Hard constraints

- **Read-only projection.** No deployments, no provisioning, no signup links.
- **Pricing is a snapshot** with the date in the response. If Fly raises rates we update the constants in a small follow-up.
- **No hidden costs** — every line item in the projection appears in the components array with a label.
- **The "+ LLM API" line reuses Phase 2's `/summary` endpoint** — don't duplicate the math. Single source of truth for LLM cost.
- **Notes section is mandatory** — surfaces the assumptions so Jon can sanity-check.
- **Configuration changes are client-side only** — no persistence. Refreshing the page resets to defaults. (If Jon later wants to pin a default config, that's a follow-up.)
- **Local-only git.** Worker on `worker/cost-phase-4-cloud-infra-tab`; Lead merges after smoke.
