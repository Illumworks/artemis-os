# LegiScan API — integration & operating contract

**Status:** client built (`artemis/tools/legiscan.py`), **stub-until-key**. Goes live the moment
`LEGISCAN_API_KEY` is set. Powers the `marketing.scout.legislative` scout.
**License:** all LegiScan data is **Creative Commons Attribution 4.0 (CC BY 4.0)** — usable freely,
but **attribution to LegiScan is required**.
**Tier:** free Public API key — **30,000 queries/month**, resetting on the 1st.
**Registered:** 2026-06-01 (Amira Learning / Jon). Commercial + Internal-Use-Only.

> This doc is the operating contract we agreed to when registering for the key. The bullet rules
> below are LegiScan's; the **"Our posture"** notes are how the Artemis integration complies (or
> where it doesn't yet). Keep this in sync with `artemis/tools/legiscan.py`.

---

## What we use it for
Internal legislative-intelligence feed: the legislative scout searches state bills matching our
literacy/education keyword set (`literacy, reading, screening, screener, dyslexia, tutor,
education`) across our **6 priority states — FL, IN, MD, MO, IL, TX** — tracks bill status, and
retrieves bill metadata/text to emit qualified signals. Internal only; no republication.

## The two endpoints we call
- **`op=getSearch`** (`legiscan.search`) — lightweight refs under `searchresult`:
  `{bill_id, relevance, change_hash}` + a numeric `summary`. Bill text is NOT included here.
- **`op=getBill`** (`legiscan.get_bill`) — full bill record under `bill`; we project
  `{bill_id, bill_number, title, description, state, status_date, url, last_action}`.

---

## LegiScan operating rules → our compliance posture

### Query limits
- **30,000 queries/month, resets on the 1st.** → *Our posture:* well within budget. 6 states ×
  scheduled searches (the marketing pipeline trigger is every 4h) + getBill only on matches ≈ low
  thousands/month. **Headroom is large**; adding states is cheap.
- **Always check the `status` field for `"OK"` vs `"ERROR"` and act.** → ✅ **Compliant** —
  both tools bail to `[]`/`{}` and log a warning when `payload["status"] != "OK"`
  (`legiscan.py:154,185`).
- **Follow the per-op timing guidelines (Manual p.7); rate-limit.** → ✅ **Compliant** —
  `ScoutHttpClient(rate_limit=1.0)` enforces 1 request/second.
- **Recommend local caching of JSON to minimize replay spend.** → ⚠️ **Partial** — no JSON cache
  today. Acceptable at current volume; see "Known gap" below.

### Hashes — "Use the hashes. No. Really. Use them."
- **`change_hash` + `bill_id`** is returned by getBill/getSearch/getMasterList(Raw)/getSearchRaw.
  Store it; if the hash is unchanged, the bill data is unchanged → use cache, skip the query.
- → ⚠️ **KNOWN GAP (tracked).** `legiscan.search` already *returns* `change_hash`
  (`legiscan.py:68`), but we do **not** persist or compare it, so a re-run can re-`getBill` an
  unchanged bill. **Not urgent** at 6-state volume (nowhere near 30k), but it's LegiScan's most
  emphatic guideline. Fix when convenient: cache `(bill_id → change_hash)` and skip getBill when
  unchanged. Tracked as a follow-up (see bottom).

### Datasets / bulk (NOT used)
- Weekly Sunday-5am-ET ZIP datasets (getDatasetList/getDataset, `dataset_hash`), ~1000 queries for
  all 2010–2026 data; **failure to use `dataset_hash` → suspended access.** → *Our posture:* we do
  **not** use the bulk dataset path (we run targeted searches), so `dataset_hash` rules don't apply
  to us. **Do not** add a bulk-download loop without honoring `dataset_hash`.

### Texts & documents (NOT used yet)
- `getBillText` / `getAmendment` / `getSupplement`; ids live in getBill payloads; blobs are
  Base64; **don't download the same blob twice.** → *Our posture:* we read `getBill` metadata +
  the bill `url`, not full document blobs. If we ever pull `getBillText`, we must cache by
  `doc_id` and never re-download an unchanged blob.

### Housekeeping (hard rules — violation = suspended access)
- **No scraping the legiscan.com front-end.** → ✅ we use the API exclusively.
- **No creating multiple Public API service keys.** → ⚠️ **Operational rule for us:** maintain
  **exactly one** `LEGISCAN_API_KEY`. Do not register a second free key. If we outgrow 30k/month,
  upgrade to a paid Pull subscription — do **not** mint a second key.
- **CC BY 4.0 — must give LegiScan attribution.** → ⚠️ **Action:** signals derived from LegiScan
  carry the bill `url`; ensure "Source: LegiScan" attribution is retained wherever this data is
  surfaced (Gate-1 card, briefs, any external-facing artifact). Internal use still requires
  attribution under CC BY 4.0.
- **"Play nice! Respect the free public service."** → posture: conservative scheduling, status
  checks, rate-limiting, and (eventually) hash caching.

---

## Operating the key

**The working path is `.env`, NOT the Connectors panel.** `legiscan.py` reads
`os.getenv("LEGISCAN_API_KEY")` directly — it does not read the connector store. (As of
2026-06-01 the Connectors panel has no `legiscan` kind, and more broadly **no scout tool reads from
the connector resolver** — they all read env vars. The stub's "Set it in the Connectors panel"
message is inaccurate; see "Connectors gap" below.)

**Set the key** (the client is already built — no code change needed; it flips from stub to live):
```
# .env  (local-only; never commit secrets)
LEGISCAN_API_KEY=<the key from legiscan.com/legiscan>
```
Then **fully restart the app** so the scout subprocess picks up the new env. `--reload` does NOT
reliably re-read `.env` for already-scheduled scout subprocesses — stop and restart the process.
The legislative scout produces on its next scheduled run.

### Connectors gap (banked — task #113)
The Connectors panel stores encrypted credentials via `get_credentials_for_tool(...)`, but **no
tool in `artemis/tools/`/`artemis/scouts/` actually calls the resolver** — they read `os.getenv` or
are stubs (e.g. `starbridge`, which IS in the panel, reads nothing). So panel-entered keys never
reach the scout tools, while stub messages tell operators to use the panel. The proper fix is a
connectors-architecture decision: either (a) register `legiscan` in `CONNECTOR_KINDS` AND make the
tools resolve via `get_credentials_for_tool` (env fallback), or (b) accept env-only for scout keys
and correct every "Set in Connectors panel" stub message to say `.env`. Until then, **`.env` is the
canonical path** for all scout API keys (LegiScan, and once built: Starbridge, procurement).

**Verify it went live** (after a run):
```sql
-- recent legislative signals
SELECT id, headline, state, created_at FROM signal_queue
WHERE discovered_by = 'legislative' ORDER BY created_at DESC LIMIT 10;
-- tool calls should now succeed (success=t), not return the STUB string
SELECT tool_name, success, count(*) FROM tool_invocations
WHERE tool_name LIKE 'legiscan.%' AND created_at > now() - interval '1 day'
GROUP BY tool_name, success;
```
Before the key: `tool_invocations` shows `legiscan.search` with `success=f` returning the
"needs a free API key" stub (per the #110 connector audit). After: `success=t` with real results.

**Registration answers on file** (for re-registration / paid upgrade): Commercial · Internal Use
Only · 6 priority states (FL/IN/MD/MO/IL/TX) · vertical topic scope (low hundreds of bills) ·
custom Python client · CC BY 4.0 acknowledged · origin IP `108.18.96.219` (may be dynamic).

---

## Known gaps / follow-ups
1. **`change_hash` caching (efficiency + LegiScan's strongest guideline).** Persist
   `(bill_id → change_hash)` from `getSearch` and skip `getBill` when the hash is unchanged.
   Low urgency at current volume; do before expanding to many states.
2. **Attribution surfacing.** Confirm "Source: LegiScan" is retained on any external-facing use of
   bill-derived signals (CC BY 4.0 requirement).
3. **Origin IP.** `108.18.96.219` is the current egress IP; if dynamic, a strict Origin restriction
   could break access — provide a static IP/subnet if LegiScan enforces it.
