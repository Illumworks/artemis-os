# CC4 — Qualifier + Content Tools (close the FULL chain)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc4-qualifier-content-tools`
**Browser smoke owner:** Lead (this session), post-merge — the FULL-chain proof (Gate 1 with real content).
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~500 (enumerated tool catalog; ship the listed tools complete + report the diff — do not cut to a number).
**Depends on:** CC1+CC2 merged (the MCP path works for scouts — proven, 23 signals). This extends the same path to the qualifier + content agents.

---

## Why this exists — the half-closed chain

CC3 proved the scout half: scouts emit real signals via subscription MCP tool-use (23 signals, `signal_queue` 2→24). BUT the chain stops at the qualifier:
- All signals stay `pending_qualification` — the qualifier "succeeds" but transitions ZERO to `qualified`.
- So the downstream gate sees no qualified signals → content team, both gates, and all deliverables SKIP. `campaign_briefs = 0`. **Gate 1 shows no real content — the original Phase BH acceptance is unmet.**

**Root cause:** the qualifier/content agents DECLARE the right tools in their blueprints, but P3 never IMPLEMENTED them (P3 built scout tools only). The MCP server silently drops unknown tool names, so the qualifier runs with no way to read pending signals or transition them.

Missing tools (declared in `agent.tools`, absent from the registry):
- `signal_queue.get`, `signal_queue.update_status`, `signal_queue.find_by_district_and_code`, `signal_queue.find_recent_qualification_results`
- `signal_briefs.write`, `signal_briefs.get_approval_history`
- `campaign_brief.read` (content.brief_assembler; `campaign_brief.write` already exists from P3)
- `ruleset_storage.get_active`, `.get_version`, `.write_new_version`, `.activate`, `.get_hit_rate`
- `districts.get`

This brief implements them, following the P2/P3 tool pattern exactly. Then the full chain flows.

---

## Scope — implement the missing tools (follow `artemis/tools/signal_queue.py` pattern)

### Real, against `signal_queue` (the transition tools — highest priority)
- **`signal_queue.get`** — arg `signalId`; return the signal row as JSON. Any marketing agent.
- **`signal_queue.update_status`** — args `signalId`, `newStatus`; transition the signal's `signal_status`. **Use the M3 state machine `transition()` if present** (`artemis/marketing/state_machine.py`) so the transition is validated + audited; else set the column directly with a TODO. **Permission: qualifier agents only** (`marketing.qualifier.*`). This is THE tool that unblocks the chain (pending_qualification → qualified).
- **`signal_queue.find_by_district_and_code`** — args `districtId`, `reasonCode`; return matching signals (for cross-reference/dedup).
- **`signal_queue.find_recent_qualification_results`** — recent signals with their qualification status (for ruleset_manager hit-rate). Reasonable query against signal_queue.

### Real, against the brief tables — RECONCILE FIRST
Two brief concepts exist: `qualifier.brief_composer` declares `signal_briefs.write`; `content.brief_assembler` declares `campaign_brief.read/write`; tables present are `campaign_briefs` + `brief_snapshots`. **Determine which table the Gate 1 / Approval Queue surface actually reads for its card content** (check `artemis/marketing/routes/campaign_ops.py`, the approval card population, and the pipeline gate config). Then:
- **`signal_briefs.write`** — brief_composer's Josh-readable brief that Gate 1 shows. Map it to the table the Gate reads (likely `campaign_briefs` or `brief_snapshots`). **Permission: brief_composer only.** Populate the fields the approval card's `brief_preview` needs.
- **`signal_briefs.get_approval_history`** — query `approvals` for prior decisions on similar briefs.
- **`campaign_brief.read`** — read `campaign_briefs` (content.brief_assembler reads the immutable brief).
- Reconcile with P3's existing `campaign_brief.write` (don't duplicate; if signal_briefs.write and campaign_brief.write target the same table, document the relationship — likely brief_composer writes the Gate-1 brief, brief_assembler writes the downstream immutable campaign brief; they may be different tables).

### Real, against `rulesets`
- **`ruleset_storage.get_active`**, **`.get_version`**, **`.write_new_version`**, **`.activate`**, **`.get_hit_rate`** — map to the `rulesets` table (+ `campaign_ruleset_versions` if present for versioning, per D3). write_new_version/activate are append-only (lossless). If hit-rate has no backing data yet, return a documented empty/zero result.

### Stub
- **`districts.get`** — no `districts` table exists (Q1 deferred). Stub: return a minimal `{"district_id": <arg>, "known": false}` + WARNING. Document it for the future roster import.

### Register + permissions
- Add all to `artemis/tools/__init__.py` imports.
- Permission checks (defense-in-depth, like signal_queue.write): `signal_queue.update_status` qualifier-only; `signal_briefs.write` brief_composer-only; `campaign_brief.write` brief_assembler-only.

---

## Tests
`artemis/tools/tests/test_qualifier_content_tools.py` (use `ARTEMIS_TEST_DB_URL`):
1. `signal_queue.update_status` from a qualifier agent transitions a signal pending_qualification → qualified (verify in fresh session + commit). From a non-qualifier → PERMISSION_DENIED.
2. `signal_queue.get` returns the row; `find_by_district_and_code` filters correctly.
3. `signal_briefs.write` writes to the Gate-read table with the fields the approval card needs; brief_composer-only.
4. `ruleset_storage.get_active` returns the active ruleset; `write_new_version` appends (lossless).
5. `districts.get` returns the stub shape.
6. Registry completeness: `known_tool_names()` now includes all the above.

---

## Files owned
- NEW: `artemis/tools/signal_queue_ops.py` (the get/update_status/find tools — or extend a new module; do NOT edit P3's `signal_queue.py` which holds `signal_queue.write`)
- NEW: `artemis/tools/signal_briefs.py`, `artemis/tools/campaign_brief_read.py` (or fold into one briefs module), `artemis/tools/ruleset_storage.py`, `artemis/tools/districts.py`
- NEW: `artemis/tools/tests/test_qualifier_content_tools.py`
- EDIT: `artemis/tools/__init__.py` (register new modules)

**Do not touch:** CC1's mcp_server.py, CC2's adapter, P2/P3 existing tool files (import/reuse), executor.py, run_turn, blueprints, the seed.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/test_qualifier_content_tools.py -v` — all pass. **Paste.**
2. Registry includes the new tools: `known_tool_names()` shows signal_queue.update_status, signal_briefs.write, ruleset_storage.*, etc. **Paste.**
3. **THE FULL-CHAIN PROOF (real run):** trigger `marketing.main`, wait for terminal, then show:
   - signals transitioned: `SELECT signal_status, count(*) FROM signal_queue GROUP BY signal_status` — some now `qualified` (not all stuck pending). **Paste.**
   - downstream NOT skipped: the run's node_states for content_* + gate_* are `succeeded`/`awaiting_approval`, NOT `skipped`. **Paste.**
   - briefs created: the Gate-read brief table count went up. **Paste before/after.**
   - **Gate 1 has real content:** an approval/gate row with a non-empty brief preview drawn from a real signal. **Paste it.**
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc4-qualifier-content-tools`. **Paste.**

**Note:** the real run spawns claude subprocesses + MCP servers (subscription, ~13 min for the full pipeline). The full-chain proof is the headline; unit tests prove the tools but only a real run proves the chain. Don't claim the chain closed without #3.

---

## Hard constraints
- Follow the P2/P3 tool pattern exactly (Tool def + factory + register_tool + ToolContext).
- Use the M3 `transition()` for status changes if it exists (validated + audited); flag if not.
- Reconcile the signal_briefs vs campaign_brief table mapping by reading the Gate/approval-card source — don't guess which table Gate 1 reads.
- Reuse existing models; add NO new tables/migrations (use signal_queue, campaign_briefs/brief_snapshots, rulesets as they are).
- Local-only git. Worker commits on `worker/cc4-qualifier-content-tools`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC4 — Qualifier + Content Tools report
1. Commit / branch / worktree
2. LOC diff stats
3. Tools implemented (real) vs stubbed
4. Brief-table reconciliation: which table does Gate 1 read, where signal_briefs.write / campaign_brief.write land
5. Test pass summary (acceptance #1-2)
6. FULL-CHAIN PROOF (acceptance #3): signal_status breakdown, downstream node states, brief count before/after, the Gate 1 card with real content
7. check.sh summary
8. Anything surprising — especially the brief-table mapping or M3 transition availability
```

---

**Claude Code Worker: read CC3's outcome above + the P2/P3 tool pattern. Operating principle: the brief-table reconciliation is the one real unknown — read the Gate/approval-card source to find which table it renders, don't guess. The full-chain proof (#3) is the bar; unit-test-green is not "the chain closed."**
