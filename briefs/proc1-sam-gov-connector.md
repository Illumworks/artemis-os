# PROC1 — Build the real procurement connector against SAM.gov (replace the stub)

**Paste-into:** Codex.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. A real external-API
client mirroring an existing, well-established pattern (`legiscan.py`/`grants_gov.py`) + tests.
Not `mini` (real HTTP client + key gating + parsing), not `high` (it's pattern-following).
**Target branch:** `worker/proc1-sam-gov-connector`
**Fires:** now. **No migration.** Touches only `artemis/tools/procurement.py` + tests — no overlap
with marketing-os.js or the contacts/send work (CMP-SEND-2). Safe to run in parallel.
**Authoritative finding:** #110 connector audit — `procurement_portal.fetch` is a pure stub; the
`marketing.scout.procurement` agent calls it and emits 0 signals. SAM.gov key (`SAM_API_KEY`) is
now set in `.env`.
**LOC cap:** ~250.
**Priority:** MEDIUM — activates the only code-fixable silent scout.

---

## Why this exists
`artemis/tools/procurement.py` is a stub (`procurement_portal.fetch` returns a "not yet
implemented" string). The procurement scout therefore emits nothing. SAM.gov exposes a **free,
public Get Opportunities API** (api.data.gov key, now in `.env` as `SAM_API_KEY`) that lists
federal/contract opportunities — including K-12 literacy/curriculum/assessment RFPs. Build the real
client so the scout produces.

## The pattern to mirror (READ THESE FIRST)
- `artemis/tools/legiscan.py` — the gold standard: real client, **stub-until-key** (returns a
  clear stub string when `SAM_API_KEY` unset), `ScoutHttpClient` rate-limiting, status/HTTP-code
  checks, returns `[]` on any error, registered via `register_tool`.
- `artemis/tools/grants_gov.py` / `artemis/tools/federal_register.py` — keyless siblings; same
  shape (search → list of lightweight dicts the scout agent turns into signals).
- The scout agent (`marketing.scout.procurement`) consumes the tool output and calls
  `signal_queue.write` itself — the tool just returns clean JSON, it does NOT write signals.

## SAM.gov API specifics
- **Endpoint:** `https://api.sam.gov/opportunities/v2/search`
- **Auth:** query param `api_key=<SAM_API_KEY>` (the api.data.gov key). Read from
  `os.getenv("SAM_API_KEY", "")`; if empty, return the stub string (mirror legiscan's `_NEEDS_KEY`).
- **Key params:** `keyword` (or `title`), `postedFrom` / `postedTo` (MM/dd/yyyy — **required** by
  the API; default to a trailing window, e.g. last ~30 days), `limit` (cap ~25), `ptype` (procurement
  type; optional). Verify exact param names against current SAM.gov docs — they're strict about the
  date format and will 400 if `postedFrom/postedTo` are missing/malformed.
- **Response:** opportunities under `opportunitiesData` (a list). Each item has fields like
  `title`, `solicitationNumber`, `fullParentPathName` (agency), `postedDate`, `responseDeadLine`,
  `uiLink` (the public URL), `description` (sometimes a URL to fetch, sometimes text), `naicsCode`,
  `typeOfSetAside`. Project to a clean shape: `{title, solicitation_number, agency, posted_date,
  response_deadline, url, description, naics}`.
- **Rate limit:** api.data.gov keys are ~1000 req/hour. Use `ScoutHttpClient(rate_limit=...)` like
  the siblings; one search per scout run is plenty.

## Scope
### `procurement_portal.fetch` — real implementation
- Replace the stub `_impl` with a real call: `op=search` against the endpoint with `api_key`,
  `keyword` (from the tool's `query`/`keyword` arg), a trailing `postedFrom`/`postedTo` window, and
  `limit`. Default keyword behavior should support our domain (literacy/reading/curriculum/
  assessment/tutoring) — let the scout pass the keyword; default to a sensible literacy term if none.
- Parse `opportunitiesData` → list of projected dicts (above). Return `json.dumps(list)`.
- **Stub-until-key:** no `SAM_API_KEY` → return the clear stub string (already points to `.env`).
- **Resilience:** non-200 / API error JSON / network error → log a warning, return `json.dumps([])`
  (never raise — mirror legiscan). Check any SAM error envelope and bail to `[]`.
- Keep `pdf_extractor.extract` as-is (the scout uses it for attachments; out of scope here).

### Tests (`artemis/tools/tests/test_procurement.py` or extend the stubs test)
1. No `SAM_API_KEY` → returns the stub string (monkeypatch env unset).
2. Mocked 200 with a realistic `opportunitiesData` payload → returns the projected JSON list with
   the right fields. (Mock the HTTP layer — no live network in tests.)
3. Mocked non-200 → returns `[]`.
4. Mocked API error envelope / malformed JSON → returns `[]`.
5. Mocked empty `opportunitiesData` → returns `[]`.

## Files owned
- EDIT: `artemis/tools/procurement.py` (real `procurement_portal.fetch`)
- NEW/EDIT: `artemis/tools/tests/test_procurement.py` (or extend `test_stubs.py`)

## Acceptance criteria
1. `uv run pytest artemis/tools/tests/test_procurement.py -v` — all pass (offline/mocked). **Paste.**
2. **Live smoke (real key):** with `SAM_API_KEY` set, run the tool directly with a literacy keyword
   and **paste the real projected JSON** (a few opportunities). If SAM returns 0 for the keyword,
   try a broader term ("education") to prove the client works, and note it.
3. `./scripts/check.sh` (j5b Jira flake known-exempt) + `git diff --stat` + `git log --oneline -1`. **Paste.**
4. **COMMIT on `worker/proc1-sam-gov-connector`. Local git only, no push.** Commit message ends
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Hard constraints
- **Stub-until-key** — never crash when `SAM_API_KEY` is unset; return the stub string.
- **Never raise from the tool** — `[]` on any error (the scout must degrade gracefully).
- **No new dependencies** (org rule: nothing < 7 days old; use stdlib + the existing
  `ScoutHttpClient`/httpx already in the repo).
- **The tool returns data; it does NOT write signals** — the scout agent owns `signal_queue.write`.
- **Local-only git.**

## Report-back format
```
PROC1 — SAM.gov connector report
1. Commit / branch
2. LOC per file
3. Exact SAM.gov params used (esp. postedFrom/postedTo format) + endpoint
4. Test pass count
5. Live smoke: real projected JSON (paste a few opportunities) + the keyword used
6. check.sh summary
7. Surprises — esp. SAM.gov param strictness / response shape vs the brief
```
