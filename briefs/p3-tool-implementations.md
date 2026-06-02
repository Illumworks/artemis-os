# P3 — Tool Implementations (the catalog)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/p3-tool-implementations`
**Browser smoke owner:** Lead (this session), post-merge — REAL pipeline run with live Google News RSS.
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** 800 (full-diff insertions including tests). Hard stop at 950.
**Design reference:** `docs/tool-execution-architecture.md` "Initial tool catalog". P2 (`40fa7b9`) already shipped the registry, context, and `signal_queue.write` reference tool — follow that pattern exactly.

---

## Why this brief exists

P2 built the bridge + `signal_queue.write` and proved the loop end-to-end with a mock LLM. P3 implements the remaining tools so scouts can actually *fetch* real data and emit real signals. After P3 merges, a real pipeline run hits live Google News RSS + state DoE RSS and produces real signals in `signal_queue` — the original "demo viability" goal from the very first handoff, achieved through the proper architecture instead of a per-scout adapter.

---

## The P2 pattern (follow exactly)

Every tool file mirrors `artemis/tools/signal_queue.py`:
1. Module docstring noting it's registered at import time.
2. A `_DEF = Tool(name=..., description=..., input_schema={...})`.
3. A `def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:` returning `(_DEF, _impl)` where `_impl(arguments: dict) -> str` is async.
4. `register_tool("<name>", _factory)` at module bottom.
5. The tool added to `artemis/tools/__init__.py`'s imports so the side-effect fires.

Tools return **strings** (success JSON or human-readable error). Validation/API failures return error strings (LLM can retry); only unexpected bugs raise. DB writes use `ctx.session` + `await ctx.session.flush()`.

---

## Scope — Tier 1: real tools that make scouts work (priority)

### `artemis/tools/territory_config.py`
- `territory_config.get_priority_states` → returns `json.dumps(list(parse_spec().territory_config.priority_states))`. No args.
- `territory_config.get_watch_keywords` → arg `campaignType` (optional); returns watch keywords from the campaign_type_mappings in Josh's spec. If no campaignType, return all.

### `artemis/tools/reason_codes.py`
- `reason_codes.get_allowlist` → returns the calling scout's allowed codes (from `reason_codes_for_scout(parse_spec(), ctx.agent_id.rsplit('.',1)[-1])`). Each code with its description + default_urgency. JSON.
- `reason_codes.lookup` → arg `code`; returns the full `ReasonCodeSpec` (description, what_scout_looks_for, default_urgency) for that code, or an error string if unknown.

### `artemis/tools/news.py`
- `news_api.search` → args: `query` (str, required), `state` (str, optional). Builds a Google News RSS URL: `https://news.google.com/rss/search?q=<url-encoded query>&hl=en-US&gl=US&ceid=US:en`. Fetches via `artemis/scouts/_http.py` ScoutHttpClient (timeout + UA). Parses the RSS with stdlib `xml.etree.ElementTree` (mirror the parser pattern in `artemis/scouts/state_doe/sources.py`). Returns up to 25 items as JSON: `[{title, link, published, source}]`. No API key required. On any error, return `json.dumps([])` (graceful empty) + log WARNING.
  - If `NEWS_API_KEY` env is set, you MAY additionally enrich via newsapi.org (reuse `artemis/scouts/regional_news/client.fetch_news_articles`), but this is optional — the Google News RSS path is the primary, key-free source.

### `artemis/tools/state_doe.py`
- `state_doe.fetch` → arg `state` (str, required). Reuse `artemis/scouts/state_doe/sources.fetch_doe_rss(state, http)`. Returns items as JSON. Graceful empty on error.

### Stubs that must exist for Tier-1 scouts to not error (return placeholder + WARNING)
- `artemis/tools/memory_layer.py` → `memory_layer.upsert_last_seen` returns `"ok-stub"` (the Memory-M2 table doesn't exist yet — design Q3 decision). Also `memory_layer.get`, `memory_layer.compute_similarity` as stubs returning empty/zero.
- `artemis/tools/contact_db.py` → `contact_db_stub.has_contact` returns `"true"` for v1 (design decision — stub returns True for priority districts).

---

## Scope — Tier 2: remaining catalog tools (real-where-cheap, stub otherwise)

### Real (reuse existing scout machinery)
- `artemis/tools/board_minutes.py` → `board_minutes.fetch`: reuse `artemis/scouts/board_minutes/client.fetch_boarddocs`. Returns empty list if the district isn't configured (graceful).
- `artemis/tools/pdf_extractor.py` → `pdf_extractor.extract`: reuse `artemis/scouts/_pdf.extract_text`. Arg `url`. Returns extracted text (truncated to a sane length, e.g. 5000 chars) or error string.

### Real DB writes
- `artemis/tools/unresolved_signals.py` → `unresolved_signals.write`: write a malformed-signal row to the `unresolved_signals` table IF it exists. If the table/model doesn't exist, make it a stub returning `"STUB: unresolved_signals table not present"` + WARNING. Check `artemis/marketing/models.py` first.
- `artemis/tools/campaign_brief.py` → `campaign_brief.write`: only `marketing.content.brief_assembler` may call it (permission check like signal_queue.write). Write to the campaign brief table if it exists; else stub. Check models first.

### Stubs (return `"STUB: <tool> not yet implemented. Set <ENV> in Connectors panel."` + WARNING)
- `artemis/tools/legiscan.py` → `legiscan.search`, `legiscan.get_bill` (needs LEGISCAN_API_KEY).
- `artemis/tools/starbridge.py` → `starbridge.search`, `starbridge.get_document`.
- `artemis/tools/grants_gov.py` → `grants_gov.search`.
- `artemis/tools/federal_register.py` → `federal_register.search`.
- `artemis/tools/procurement.py` → `procurement_portal.fetch`.
- `artemis/tools/linkedin.py` → `linkedin_scraper.fetch_posts`, `linkedin_scraper.check_profile_delta`.

Each stub still registers a real `Tool` definition (so the LLM sees the capability) but the `_impl` returns the stub string.

---

## Register everything

Update `artemis/tools/__init__.py` to import all new modules so their `register_tool` calls fire. After P3, `known_tool_names()` returns the full catalog.

---

## Tests

`artemis/tools/tests/test_<tool>.py` — one per module, ~30-50 LOC each:
- **territory_config / reason_codes:** assert correct values from Josh's spec.
- **news_api.search:** use `httpx.MockTransport` with a fixture Google News RSS XML (`artemis/tools/tests/fixtures/google_news_sample.xml` — hand-craft ~30 lines). Assert ≥1 item parsed with title+link. NO live HTTP in tests.
- **state_doe.fetch:** mock transport + fixture. Assert parse.
- **stubs:** assert each returns its placeholder string + that it's registered.
- **permission:** `campaign_brief.write` from a non-brief_assembler agent → PERMISSION_DENIED.
- **registry completeness:** assert `known_tool_names()` contains every catalog tool.

Run with `ARTEMIS_TEST_DB_URL="postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test"`.

---

## Files owned by this stream

- NEW: `artemis/tools/{territory_config,reason_codes,news,state_doe,memory_layer,contact_db,board_minutes,pdf_extractor,unresolved_signals,campaign_brief,legiscan,starbridge,grants_gov,federal_register,procurement,linkedin}.py`
- NEW: `artemis/tools/tests/test_*.py` + `artemis/tools/tests/fixtures/*.xml`
- EDIT: `artemis/tools/__init__.py` (add imports)

**Do not touch:** `artemis/tools/{registry,context,signal_queue}.py` (P2, sealed — import + follow pattern). `artemis/builders/executor.py` (P2's bridge already wires the registry). Blueprint markdown. The seed loader. Josh's spec. `artemis/agent/*`. `artemis/scouts/*` (reuse via import, don't modify).

---

## Acceptance criteria (Worker must demonstrate each)

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/ -q` — all pass. **Paste summary.**
2. **Registry completeness:** `uv run python -c "import artemis.tools; from artemis.tools.registry import known_tool_names; print(known_tool_names())"` shows the full catalog (≥20 tools). **Paste output.**
3. **Live Google News RSS smoke (real network, this is allowed here):**
   ```bash
   uv run python -c "
   import asyncio, json
   from artemis.tools.news import _factory
   from artemis.tools.context import ToolContext
   # ToolContext with a dummy session is fine — news.search doesn't use the DB
   class FakeSession: pass
   ctx = ToolContext(session=FakeSession(), agent_id='marketing.scout.regional_news', agent_db_id=1, agent_run_id='smoke', pipeline_run_id=None)
   _def, impl = _factory(ctx)
   print(impl.__name__)
   result = asyncio.run(impl({'query': 'Florida schools literacy curriculum board', 'state': 'FL'}))
   items = json.loads(result)
   print('items fetched:', len(items))
   print('first:', items[0] if items else 'NONE')
   "
   ```
   **Paste output.** Should fetch ≥1 real news item (network permitting). If network is blocked in the worktree, note it and rely on the fixture test instead.
4. `./scripts/check.sh` — passes modulo the known-exempt failures (j5b Jira; m5b FK-isolation if still present pre-F5). **Paste summary + remaining failures.**
5. `git diff --stat` ≤ 800 (950 hard stop). **Paste it.**
6. `git log --oneline -1` on `worker/p3-tool-implementations`. **Paste it.**

---

## Hard constraints

- LOC cap: 800 (950 hard stop). At cap, commit Tier 1 + as much Tier 2 as fits, ping back with what's done vs deferred.
- Tier 1 is the priority — if you must cut, cut Tier 2 stubs, never Tier 1.
- Follow P2's `signal_queue.py` pattern exactly. Do not invent a different tool shape.
- Reuse `artemis/scouts/*` machinery via import; do not modify those files.
- No live HTTP in unit tests (use MockTransport + fixtures). The ONE live call is acceptance #3, run manually.
- Local-only git. Worker commits on `worker/p3-tool-implementations`; terminal-Lead merges after Lead approves.

---

## Report-back format (Worker pastes verbatim, filled in)

```
P3 — Tool Implementations report

1. Commit hash / branch / worktree
2. LOC diff stats
3. Tools implemented (real) vs stubbed — two lists
4. Test pass summary
5. Registry completeness (known_tool_names output)
6. Live Google News RSS smoke (acceptance #3) — item count + first item
7. check.sh summary + remaining failures
8. Tier 2 cuts (if any) — what was deferred and why
9. Anything surprising — especially any catalog tool whose reuse-target (scouts/*, models) didn't exist as expected
```

---

**End of brief. Claude Code Worker: read `docs/tool-execution-architecture.md` + `artemis/tools/signal_queue.py` first. Operating principle — never assume a reuse target exists; if `artemis/scouts/board_minutes/client.py` or a model isn't shaped as this brief expects, STOP and report, don't improvise a different implementation.**
