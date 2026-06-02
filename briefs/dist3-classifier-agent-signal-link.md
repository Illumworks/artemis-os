# DIST3 — District Classifier agent + signal→district link (Stream 1)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. This one has real reasoning: fuzzy name-resolution logic (signal "LAUSD" → NCES "Los Angeles Unified") and a migration that repurposes `signal_queue.district_id`. The name-matching strategy is the judgment call — not fully mechanical. Use the flagship.
**Target branch:** `worker/dist3-classifier-agent`
**Fires:** AFTER DIST1 merges (needs `districts` table + `upsert_district`). Can fire after DIST2 or in parallel with DIST2 (DIST2 = UI/routes, DIST3 = agent + signal link; verify no file overlap on signal_criteria.py — DIST3 shouldn't touch it).
**Browser smoke owner:** Lead post-merge (trigger a pipeline run, verify a signal resolves to a district row).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400.
**Priority:** HIGH — connects signals to the district entity so qualification (DIST4) can consume tier.

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` D-1/D-2/D-6: a **dedicated District Classifier agent** resolves a signal's messy district name to the canonical NCES district (created in DIST1's `districts` table), and the signal gets linked to it. **No LLM guesses enrollment or tier** — the agent ONLY does name-resolution; enrollment comes from NCES data (DIST1's loader, populated by Lead's data step #89) and tier is DIST1's pure function.

Today `signal_queue.district_id` + `.state` are 100% NULL — scouts never populate them. This brief closes that (absorbs the old "scout geo-fix").

---

## Scope

### Part A — Migration: signal→district link

**Run `uv run alembic current` FIRST. Your `down_revision` = whatever that returns (expected 0054 or 0055 depending on DIST2's migration — DIST2 has none, so likely 0054). Your `revision` = next integer. Paste `alembic current` in your report so the Lead can confirm no collision.**

- `signal_queue.district_id` is currently `text` (NULL). Repurpose it OR add `resolved_district_id BIGINT` FK → `districts.id`. **Recommended:** add `resolved_district_id` (FK, nullable) and leave the legacy `district_id` text alone (lossless — the scout's raw string stays for provenance; the FK is the resolved link). Index it.
- This keeps the raw scraped name AND the resolved entity — useful for auditing bad matches.

### Part B — District Classifier agent

A dedicated agent (D-6) whose ONLY job is name-resolution. Seed it in `artemis/marketing/seeds/marketing_agents.py` following the existing agent-spec line format (e.g. `marketing.district.classifier|district/...md|haiku|...`). It is a low-stakes resolver → `haiku` tier is appropriate.

Agent capability (via a tool, H1-disciplined):
- Input: a district name string (+ state hint if the signal has one).
- Tool `district.resolve(name, state?)`: fuzzy-matches against the `districts` table (NCES-loaded). Returns the best match's `district_id` + confidence, OR a "no confident match" result. **Deterministic matching logic in the tool** (normalized string match, state filter, common-abbreviation expansion like "LAUSD"→"Los Angeles Unified"); the LLM only adjudicates ambiguous multi-candidate cases.
- On confident match: set `signal_queue.resolved_district_id`.
- On no match: leave NULL, log, do not fabricate a district. (If the district genuinely isn't in NCES data yet, that's a data gap, not a guess to paper over.)

**Provide a common-abbreviation map** (LAUSD, NYCDOE, CPS, MDCPS, etc.) as a small static dict — these are the high-frequency ones; the rest rely on normalized matching.

### Part C — Wire into the pipeline

The classifier runs **before qualification completes** (so DIST4 can read tier at Gate 1). Insert it as a node early in the marketing pipeline (`artemis/pipelines/seeds/marketing_pipeline.py`) — after scout/intake, before `qualifier_cross_reference`. OR wire it into scout-intake so every new signal gets resolved on arrival. **Recommended:** scout-intake hook (every signal resolved at write time) — simpler than a pipeline node and covers signals from all sources. Confirm the intake path in your report.

### Part D — Tests

`artemis/marketing/tests/test_dist3_classifier.py`:
1. `district.resolve` exact match → correct district_id, high confidence.
2. `district.resolve` abbreviation ("LAUSD") → Los Angeles Unified (seed it in fixture).
3. `district.resolve` with state hint disambiguates two same-named districts in different states.
4. `district.resolve` no-match → returns no-match result, does NOT create or fabricate a district.
5. Signal resolution sets `resolved_district_id` on confident match; leaves NULL on no-match.
6. Lossless: legacy `district_id` text string preserved alongside the resolved FK.

---

## Files owned

- NEW: `alembic/versions/00XX_*.py` (signal→district FK — number per `alembic current`)
- EDIT: `artemis/marketing/models.py` (+resolved_district_id on signal_queue) ⚠️ **see coordination note**
- NEW: `artemis/tools/district_resolve.py` (the resolver tool + abbreviation map)
- EDIT: `artemis/tools/__init__.py` (register tool)
- EDIT: `artemis/marketing/seeds/marketing_agents.py` (+classifier agent)
- NEW: agent markdown `marketing-ops-v1/.../district-classifier.md` (or wherever agent prompts live — match existing)
- EDIT: scout-intake path (`artemis/marketing/scout_intake.py` or equivalent) for the resolution hook
- NEW: `artemis/marketing/tests/test_dist3_classifier.py`

⚠️ **Coordination:** this edits `marketing/models.py` — the same file DIST1 owns. **DIST3 MUST fire AFTER DIST1 merges**, never in parallel with it. (DIST2 doesn't touch models.py, so DIST2 ∥ DIST3 is fine.)

---

## Acceptance criteria

1. `alembic current` pasted (confirms down_revision, no collision) + `uv run alembic upgrade head`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_dist3_classifier.py -v` — 6 pass. **Paste.**
3. Resolver tool registered: appears in `known_tool_names()`. **Paste.**
4. Classifier agent seeded: `psql -c "SELECT agent_id FROM agents WHERE agent_id LIKE 'marketing.district%';"`. **Paste.**
5. **Lossless:** legacy `district_id` text untouched; resolution is additive via the FK. **Confirm.**
6. **No fabricated districts** — no-match path creates nothing. **Confirm + show the test.**
7. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **The agent resolves names only — it NEVER sets enrollment or tier.** Those come from NCES data + DIST1's pure function. This is the hallucination firewall.
- **No-match ≠ guess.** Unmatched signals keep NULL resolved_district_id. Better an honest gap than a wrong district.
- **Lossless:** keep the raw scraped name (legacy text column) for provenance/audit.
- **Fires after DIST1** (models.py overlap). Paste `alembic current` to prove migration coordination.
- **Local-only git.**

---

## Report-back format

```
DIST3 — classifier agent report
1. Commit / branch
2. alembic current (down_revision proof) + migration number
3. LOC per file
4. Test pass count (esp. abbreviation #2 + no-match-no-fabrication #4)
5. Tool registry + agent seed verification
6. Where the resolution hook landed (intake vs pipeline node) + why
7. Lossless + no-fabrication confirmations
8. Surprises — esp. NCES name-matching edge cases, abbreviation coverage
```
