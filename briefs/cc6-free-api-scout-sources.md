# CC6 — Free Public-API Scout Sources (grants.gov, Federal Register, LegiScan)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc6-free-api-sources`
**Browser smoke owner:** Lead (this session), post-merge — pipeline run shows federal_funding/legislative emitting from real sources.
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~350 (3 tool implementations + tests).
**Depends on:** P3 (the tool registry + pattern). Safe + additive — replaces stub tool files, touches nothing in the proven pipeline/qualifier path.

---

## Why this exists

Phase BH closed the chain, but 4 scouts emit nothing because their source tools are stubs. Of those, THREE can be made real with **free public APIs** (no paid key) — this brief does them, so `federal_funding` and `legislative` scouts produce real signals. (The remaining stubs — `starbridge` (paid/proprietary), `linkedin` (scraping), `procurement` (scraping) — are separate, bigger efforts, NOT in this brief.)

Free sources:
- **grants.gov** — public Search2 API, no key. → `grants_gov.search`
- **federalregister.gov** — public API, no key. → `federal_register.search`
- **LegiScan** — free API but requires a (free) registered key. → implement the client; if `LEGISCAN_API_KEY` unset, return a clear "needs free key" stub message so Jon knows to register + drop it in Connectors.

---

## Scope — implement 3 tools (follow the P3 `artemis/tools/*.py` pattern exactly)

### `artemis/tools/grants_gov.py` — REAL, no key
- `grants_gov.search` → args: `keyword` (e.g. "literacy", "tutoring"), optional `rows` (default 25). Hit the grants.gov public search API (verify the current endpoint — likely `https://api.grants.gov/v1/api/search2`, POST JSON). Filter/return literacy-relevant opportunities: `{title, opportunityNumber, closeDate, agency, url}`. Graceful empty + WARNING on error. No key required.

### `artemis/tools/federal_register.py` — REAL, no key
- `federal_register.search` → args: `term` (e.g. "literacy education funding"), optional `per_page` (default 20). Hit `https://www.federalregister.gov/api/v1/documents.json` (public, no key). Return `{title, document_number, publication_date, agencies, html_url, abstract}`. Filter to literacy/education relevance. Graceful empty on error.

### `artemis/tools/legiscan.py` — client ready, free-key-gated
- `legiscan.search` / `legiscan.get_bill` → use the LegiScan API (`https://api.legiscan.com/?key=<KEY>&op=getSearch&...`). Read `LEGISCAN_API_KEY` via the connector resolver (like other tools) or env. **If unset, return** `"STUB: LegiScan needs a free API key — register at legiscan.com/legiscan and add LEGISCAN_API_KEY in the Connectors panel."` (so it's ready the moment Jon adds the free key). If set, do a real search filtered to literacy/screening/dyslexia bills in priority states.

Use `artemis/scouts/_http.py` (ScoutHttpClient) for HTTP. Register all in `artemis/tools/__init__.py`.

---

## Tests
`artemis/tools/tests/test_free_api_sources.py` (use `ARTEMIS_TEST_DB_URL`):
1. grants_gov.search — mock the API (httpx MockTransport + fixture JSON), assert it parses opportunities. NO live HTTP in unit tests.
2. federal_register.search — mock + fixture, assert parse.
3. legiscan.search with no key → returns the "needs free key" stub string. With a (mocked) key → parses bills.
4. All three registered in `known_tool_names()`.
5. Graceful empty on API error (mock a 500) for each.

---

## Files owned
- REWRITE (from stub): `artemis/tools/grants_gov.py`, `artemis/tools/federal_register.py`, `artemis/tools/legiscan.py`
- NEW: `artemis/tools/tests/test_free_api_sources.py` + `artemis/tools/tests/fixtures/*.json`
- EDIT: `artemis/tools/__init__.py` only if the imports changed

**Do not touch:** starbridge/linkedin/procurement stubs (separate efforts), the MCP server, adapter, qualifier tools, pipeline, blueprints, seed.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/test_free_api_sources.py -v` — all pass. **Paste.**
2. **Live smoke (real network, allowed here):** call grants_gov.search('literacy') and federal_register.search('literacy education') directly (like CC1's standalone style) → paste the count + first item from each. (These are free public APIs — should return real data.) legiscan: paste the no-key stub message (unless Jon's already added a key).
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1` on `worker/cc6-free-api-sources`. **Paste.**

---

## Hard constraints
- Verify the real API endpoints/shapes against current docs before coding (grants.gov Search2, federalregister.gov v1, legiscan op=getSearch) — report what you confirmed.
- grants.gov + federal_register must be REAL (no key). legiscan client real but gated on the free key.
- Follow the P3 tool pattern; reuse `_http.py`.
- Local-only git. Worker commits on `worker/cc6-free-api-sources`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC6 — Free API Scout Sources report
1. Commit / branch / worktree
2. LOC diff stats
3. API endpoints confirmed (grants.gov, federal register, legiscan)
4. Test pass summary
5. Live smoke: grants_gov + federal_register real results (count + first item); legiscan stub message
6. check.sh summary
7. Anything surprising — especially API shape differences vs assumptions
```

---

**Claude Code Worker: verify the real API endpoints against current docs first — don't assume the URL/shape. grants.gov + federal register are free/public (must work live); legiscan needs a free key (client ready, stub-until-key). Operating principle: the live smoke (#2) must show real data from the free APIs.**
