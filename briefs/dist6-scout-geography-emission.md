# DIST6 — Scouts emit district geography (activates the district layer)

**Paste-into:** Codex OR terminal-Lead sub-worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. Small tool-schema change + scout prompt edits + tests. The prompt work needs care (it shapes live agent behavior) but is well-scoped.
**Target branch:** `worker/dist6-scout-geography`
**Browser/Lead smoke owner:** Lead — trigger a regional_news scout run post-merge, confirm new signals get districtId+stateCode AND auto-resolve to a districts FK.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (tool schema + 2-3 prompt files + tests).
**Priority:** HIGH — this is the activation key. The entire district layer (DIST1–DIST5 + 13,403 NCES districts) is built but DARK until scouts emit geography. This turns it on.

---

## Why this exists (the audit, 2026-05-31)

The district resolution chain is ALREADY fully wired:
- `signal_queue.write` accepts `districtId` (string) — `artemis/tools/signal_queue.py`
- `scout_intake` normalizes `districtId` → `district` and `stateCode` → `state_code`
- **DIST3's resolver already fires** in `signal_queue.py` (~lines 165–185): if `district` is present → `resolve_district()` → sets `resolved_district_id`. Verified live.

The ONLY gap: **scouts don't populate `districtId`/`stateCode` when they write.** All 202 existing signals have these NULL — yet their summaries clearly name districts ("Grosse Pointe Schools", "Lake Worth ISD", "Fort Bend ISD", "St. Louis Public Schools"). The geography is in the prose, never in the structured fields. So the resolver has no input and the whole layer stays dark (Gate 1 cards show "unresolved", D4 soft-flag never fires).

This is a **prompt + tool fix**, not an enrichment build. Same pattern as the H5/CC4 "substrate wired, prompt doesn't use it" findings.

Live scouts producing signals: `regional_news` (91), `federal_funding` (79), `leadership_transition` (31). regional_news + leadership_transition are district-centric; federal_funding is often state/federal-level (district legitimately optional).

---

## Scope

### Part A — Tool fix: `artemis/tools/signal_queue.py`

1. **Add `stateCode` to the input_schema `properties`** — it is currently MISSING from the tool schema even though `scout_intake` reads `payload.get("stateCode")`. Scouts have no documented way to pass state. Add:
   ```python
   "stateCode": {"type": "string", "description": "2-letter US state of the district (e.g. 'TX', 'FL'). Populate whenever known."},
   ```
2. **Strengthen the `districtId` description** to make clear it's the district NAME for resolution:
   ```python
   "districtId": {
       "type": "string",
       "description": (
           "The school district this signal is about, by NAME as commonly written "
           "(e.g. 'Fort Bend ISD', 'Grosse Pointe Schools', 'St. Louis Public Schools'). "
           "ALWAYS populate when the signal concerns a specific district — the system "
           "resolves it to the canonical NCES district and its size tier (D1–D4). "
           "Leave empty only for genuinely state/federal-level signals with no single district."
       ),
   },
   ```
   (Keep it optional in `required` — federal/state signals legitimately have no district. The push is via description + prompts, not a hard requirement that would break federal_funding.)

### Part B — Scout prompt fixes

Update the district-centric live-scout prompts to ALWAYS populate districtId + stateCode for district-specific signals, with a concrete example. Files (parsed by `marketing_agents.py` seed):
- `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`
- `docs/marketing-ops-v1/agents/scout/1.9-leadership-transition-scout.md`
- Also update `1.8-board-minutes-scout.md` and `1.6-state-doe-scout.md` for when they go live (board minutes are inherently district-scoped; state_doe is state-scoped → set stateCode, districtId usually empty).

Add to each a clear instruction block, e.g.:
> **Geography is mandatory for district signals.** When a signal concerns a specific district, you MUST set `districtId` to the district's name exactly as commonly written (e.g. "Fort Bend ISD") and `stateCode` to its 2-letter state. The platform resolves this to the district's size tier (D1–D4) — without it, the signal reaches the inbox with no size context and cannot be size-filtered. Extract the district name from the same source you used for the headline.

Include one concrete `signal_queue.write(...)` example showing districtId + stateCode populated.

**federal_funding (`1.5`)**: lighter touch — instruct to set `stateCode` when a funding item is state-specific and `districtId` only if a specific district is named; otherwise leave both empty (the resolver handles null gracefully). Do NOT force a district where none exists (no fabrication).

### Part C — Re-seed the agents

Scout prompts are loaded into the `agents` table via the seed. After editing the markdown, re-run the seed (or the documented re-seed path) so the live agent rows pick up the new prompts. Confirm in report: `psql -c "SELECT agent_id FROM agents WHERE agent_id LIKE 'marketing.scout.%';"` and spot-check that a re-seeded prompt contains the new geography instruction.

### Part D — Tests

`artemis/marketing/tests/test_dist6_scout_geography.py`:
1. `signal_queue.write` tool schema now exposes `stateCode` in properties.
2. A `signal_queue.write` call WITH districtId + stateCode → row has district_id + state set, AND (via the existing DIST3 hook against a seeded districts fixture) resolved_district_id gets set.
3. A `signal_queue.write` call WITHOUT districtId (federal-style) → row created, district_id NULL, resolved_district_id NULL, no error (no fabrication).
4. stateCode validation: invalid (e.g. 'XX' that isn't 2 letters / 'Texas') handled per existing scout_intake rules (it already validates 2-letter format).

### Part E — Lead post-merge verification (not the worker's job)

Lead triggers a real `regional_news` scout run and confirms NEW signals land with districtId + stateCode populated and resolved_district_id auto-set. This is the end-to-end proof the layer is live. (Worker just makes the unit tests pass.)

---

## Files owned

- EDIT: `artemis/tools/signal_queue.py` (add stateCode to schema, strengthen districtId desc)
- EDIT: `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.9-leadership-transition-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.8-board-minutes-scout.md`, `1.6-state-doe-scout.md`, `1.5-federal-funding-scout.md`
- EDIT: seed re-run path (confirm how seeds reload agent prompts)
- NEW: `artemis/marketing/tests/test_dist6_scout_geography.py`

**No migration.**

---

## Acceptance criteria

1. `signal_queue.write` schema exposes `stateCode`; `districtId` description strengthened. **Paste the schema diff.**
2. `pytest .../test_dist6_scout_geography.py -v` — 4 pass (use ARTEMIS_TEST_DB_URL for pytest). **Paste.**
3. Re-seed verified: a scout agent row's prompt contains the new geography instruction. **Paste the spot-check.**
4. **No-fabrication preserved:** federal-style write with no district → NULL, no error (test #3). **Confirm.**
5. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **No fabrication.** Scouts populate districtId ONLY when a real district is named. State/federal signals stay NULL. The resolver already handles NULL → no resolved district (honest gap).
- **districtId stays optional in `required`** — the push is via description + prompt, not a hard schema requirement (that would break federal_funding).
- **Don't touch the resolver or intake** — they already work. This brief only makes scouts SUPPLY the input.
- **Re-seed after prompt edits** or the live agents won't pick up the change (memory lesson: prompt files feed agents via the seed).
- **Local-only git.**

---

## Report-back format

```
DIST6 — scout geography emission report
1. Commit / branch
2. signal_queue.write schema diff (stateCode added, districtId desc)
3. Scout prompt files edited + the geography instruction added
4. Re-seed verification (agent row prompt contains new instruction)
5. Test pass count (esp. resolve-on-write #2 + no-fabrication #3)
6. check.sh summary
7. Surprises — esp. how the seed reloads prompts, any scout that can't cleanly name districts
```
