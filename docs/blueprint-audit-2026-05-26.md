# Blueprint Audit — 2026-05-26

**Author:** Lead (Opus 4.7, second session after the first rolled)
**Trigger:** Jon's intuition that scouts/agents "felt like a hollow shell vs something concrete."
**Verdict:** Intuition was right. Three layers of hollowness stacked. The substrate is real. The blueprints are deep. The runtime ignores most of both.

This doc is the written record so the picture doesn't get lost when this session rolls over.

---

## What the agent blueprints contain

`docs/marketing-ops-v1/agents/` holds 16 markdown files (9 scout, 4 qualifier, 3 content, plus READMEs). Total ~4,900 lines. Each blueprint includes:

- Purpose
- Cadence
- Inputs (with env var keys)
- Outputs (signal shape)
- Urgency tiers (this-scout-specific)
- Tools required (`news_api.search(...)`, `signal_queue.write(...)`, etc.)
- Reason codes emitted (per-scout subset of Josh's 17)
- Prompt scaffolding (the actual LLM instructions)
- Failure modes
- DB tables touched
- Implementation notes

These match the screenshots Jon has of the marketing plan. Real spec, real depth.

---

## What the seed loader intends

`artemis/marketing/seeds/marketing_agents.py` is sophisticated. It parses 12+ fields per blueprint via section-extracting regex:

| Field on `agents` row | Source section | Status |
|---|---|---|
| `system_prompt` | `## Prompt scaffolding` | Works |
| `description` | `## Purpose` | Works |
| `tools` | `## Tools required` (code fence) | Works |
| `reason_codes_emitted` | `## Reason codes emitted` (table rows) | **Broken** for 15/16 |
| `cadence_seconds` | `## Cadence` (regex on "every N hours/daily") | Untested in audit |
| `urgency_tiers` | `## Urgency tiers` | **Broken** — requires `**name**` bold prefix the blueprints don't use |
| `failure_modes` | `## Failure modes` | **Broken** — same bold-prefix issue |
| `db_tables_touched` | `## DB tables touched` | Untested in audit |
| `implementation_notes` | `## Implementation notes for Codex` | **Empty in DB** for all 16 |
| `inputs_required` | `## Inputs` | Untested in audit |
| `lifecycle_status` | `**Status:**` header line | **Empty in DB** for all 16 |
| `persona` | Hardcoded inline dict in seed file | Works (16/16) |

Hardcoded persona for all 16 is solid — purpose + voice_notes strings written by Jon.

---

## What's actually in the DB right now

Query: `SELECT agent_id, length(system_prompt), jsonb_array_length(tools), jsonb_array_length(reason_codes_emitted), persona IS NOT NULL, urgency_tiers IS NOT NULL, failure_modes IS NOT NULL, implementation_notes IS NOT NULL, lifecycle_status FROM agents WHERE agent_id LIKE 'marketing.%';`

| agent_id | sys_prompt | tools | reason_codes | persona | urgency | failure | impl_notes | lifecycle |
|---|---|---|---|---|---|---|---|---|
| content.asset_selector | 966 | 2 | 0 | y | n | n | n | — |
| content.brief_assembler | **0** | **0** | 0 | y | n | n | n | — |
| content.writing_studio_adapter | **0** | 1 | 0 | y | n | n | n | — |
| qualifier.brief_composer | 2583 | 5 | 0 | y | n | n | n | — |
| qualifier.cross_reference | 558 | **0** | 0 | y | n | n | n | — |
| qualifier.ruleset_compiler | **0** | **0** | 0 | y | n | n | n | — |
| qualifier.ruleset_manager | 1968 | **0** | 0 | y | n | n | n | — |
| scout.board_minutes | 1169 | 6 | 0 | y | n | n | n | — |
| scout.federal_funding | 1341 | 7 | 0 | y | n | n | n | — |
| scout.leadership_transition | 1556 | 9 | 0 | y | n | n | n | — |
| scout.legislative | 2144 | 8 | **4** | y | n | n | n | — |
| scout.linkedin_observer | 1581 | 4 | 0 | y | n | n | n | — |
| scout.procurement | 1300 | 5 | 0 | y | n | n | n | — |
| scout.regional_news | 616 | 4 | 0 | y | n | n | n | — |
| scout.starbridge_researcher | 675 | 5 | 0 | y | n | n | n | — |
| scout.state_doe | 857 | 6 | 0 | y | n | n | n | — |

**Summary:**
- ✅ persona populated 16/16
- ⚠️ system_prompt populated 13/16 (3 totally empty)
- ⚠️ tools populated 12/16 (4 totally empty including ALL qualifier agents except brief_composer)
- ❌ reason_codes_emitted populated 1/16 (only legislative)
- ❌ urgency_tiers populated 0/16
- ❌ failure_modes populated 0/16
- ❌ implementation_notes populated 0/16
- ❌ lifecycle_status populated 0/16

Hollowness ≈ 70% by field.

---

## What `run_agent()` actually feeds the LLM

From `artemis/builders/executor.py` lines 170-225, the LLM call's `system` parameter is built from only three sources:

1. `agent.system_prompt` (if non-null)
2. `agent.goal` (formatted as `## Goal\n{goal}`)
3. `shared_context` dict (formatted as `## Context\n{k}: {v}` lines)

**Ignored at runtime even when populated:**
- `persona` (the voice you wrote — never reaches the LLM)
- `urgency_tiers`
- `failure_modes`
- `db_tables_touched`
- `implementation_notes`
- `output_contract`
- `inputs_required`

So even if the seed parsers were fixed today, ~70% of the deep blueprint data would still be dead weight in the DB.

---

## Tool execution

Hardcoded warning at `builders/executor.py:184`:

```
Agent '%s' has tools %r but tool resolution is not yet implemented. Running with no tools.
```

`run_turn()` gets called with `tools=None` on line 223. So tool declarations are decorative — the LLM never gets the tool list and tools never execute. This is the load-bearing structural gap. Scouts that declare `signal_queue.write` as a tool never write signals because the tool doesn't exist at runtime.

---

## Builder reach (the "is it limited?" question)

The Operating Blueprint surface lives in `public/js/features/operations-shell.js` line 2222+ as `renderOperatingBlueprint(agent)`. It renders:
- Cadence
- Lifecycle status
- Inputs required
- Urgency tiers
- Failure modes
- DB tables touched
- Implementation notes

But it's **read-only display**. The block uses `<details>`/`<summary>`/`<pre>` markup, no `<input>` or `<textarea>`. There's no PATCH route I've spotted that takes those fields. So Builder/Agent Card today cannot edit:

- urgency_tiers
- failure_modes
- implementation_notes
- inputs_required
- db_tables_touched
- lifecycle_status
- cadence_seconds

It can probably edit (need to confirm): system_prompt, tools, persona, model, provider, reason_codes_emitted.

So the answer to "is the Builder limited?" is: **yes, materially**. The agent row has ~22 fields; Builder reaches maybe 8-10 of them. The Operating Blueprint section is a viewing window into fields that only the seed loader writes.

---

## Josh's spec — single-source-of-truth status

Josh's `decisions/campaign-signal-spec-v1.md` (128 lines) is the canonical source for reason codes / territory config / qualifier rules / per-state nuance. **But it's currently duplicated and partially-copied across four places:**

1. `decisions/campaign-signal-spec-v1.md` — canonical
2. `docs/marketing-ops-v1/Campaign Signal Spec v1.md` — **byte-identical duplicate** (`diff -q` returns nothing). Drift risk: 100%.
3. `artemis/marketing/seeds/reason_codes.py` — 17 codes **re-encoded as a Python list** with a comment `# Verbatim from decisions/campaign-signal-spec-v1.md §2`. Not read from the spec; manually transcribed.
4. Each agent's blueprint markdown — each scout's blueprint contains its own table of the 4-6 reason codes relevant to that scout, plus quoted "State nuances (from spec §5)" snippets. These are **partial inline copies**.

If Josh updates one reason code description, four places need to update by hand. The single-source pattern doesn't exist yet. **Recommendation:** Pattern A from Jon's chat — build a `josh_spec_parser.py` that reads `decisions/campaign-signal-spec-v1.md` and is the single source for reason codes, territory config, qualifier rules (when M4 lands), and per-scout nuance snippets. Downstream consumers (`reason_codes.py` seed, `marketing_agents.py` seed) read from the parser instead of redefining.

---

## Three layers of hollowness — ordered fix list

1. **Data layer:** seed parsers fail for 6+ fields. Fix the regexes (`_urgency_tiers` and `_failure_modes` need fallback patterns for non-bolded bullets; `_reason_codes_emitted` works in theory but DB is empty for 15/16 — probably means seed wasn't re-run since loader was extended).
   - Effort: ~1 day Codex.

2. **Runtime layer:** `run_agent()` only injects system_prompt + goal + shared_context. Persona voice / urgency tiers / failure modes / implementation notes / output contract — all ignored.
   - Effort: ~1 day Sonnet. ~50 LOC delta in `executor.py`.

3. **Tool layer:** tool resolution is stubbed. The structural piece for AI-maintenance and for scouts emitting real signals.
   - Effort: 3-5 days with parallel help. Touches runtime + provider cascade + permission flow. Probably needs its own architecture brief before implementation.

4. **Builder reach:** Agent Card / Builder UI can't currently edit urgency_tiers / failure_modes / implementation_notes / inputs_required / lifecycle_status / cadence_seconds. Adding editor controls + a PATCH route.
   - Effort: ~2-3 days Sonnet, depending on how rich the editing experience needs to be (form-style vs Builder-conversational).

5. **Josh's spec single-source:** consolidate four-place duplication into one parser.
   - Effort: ~1 day Codex/Sonnet. Mechanical.

---

## Recommendation: order of operations with 4 parallel streams

Capacity: 2 Claude Max + 2 Codex = 4 concurrent worker streams.

**Day 1 (parallel):**
- Stream A (Codex): seed parser fixes — get urgency/failure/reason-codes loading from blueprints. Re-seed.
- Stream B (Sonnet): `run_agent()` injects deep fields into LLM system message. Persona voice + urgency tiers + failure modes + implementation notes.
- Stream E (Codex): Josh's spec single-source parser. Consolidate the four-place duplication.

End of Day 1: scouts go from hollow-LLM-chat to LLM-chat-with-voice-and-urgency-discipline-and-failure-mode-awareness. Reason codes properly enforced. Josh's spec is the single source.

**Day 2-5 (parallel):**
- Stream D (Claude Code, big stream): tool execution layer. The structural piece.
- Stream C (Sonnet): Builder/Agent Card editing surface for blueprint fields. So future edits don't require seeding from markdown.

End of Day 5: scouts emit real signals via `signal_queue.write` tool calls. Builder can edit any agent field conversationally. AI-maintenance loop is real.

After Day 5: regional_news adapter brief can be revisited — but the answer will probably be "scouts call `news_api.search` and `state_doe.fetch` tools, no per-scout adapter needed." Tool implementations replace adapter implementations.

---

## What this means for the `briefs/scout-adapter-regional-news.md` I drafted this morning

It's **wrong**, or at least premature. The `agent_executor` branch (Part A) becomes unnecessary once tool execution is real. The RSS-fetching code (Part B) probably becomes a `news_api.search` and `state_doe.fetch` tool implementation, not a per-scout adapter class. **Recommend shelving** until Stream D direction is decided.

---

## Files touched in this audit

- Read: `artemis/builders/models.py`, `artemis/builders/executor.py`, `artemis/marketing/seeds/marketing_agents.py`, `artemis/marketing/scout_runner.py`, `artemis/marketing/scout_sources/*.py`, `artemis/pipelines/node_executors/agent_executor.py`, `artemis/scouts/regional_news/*.py`, `decisions/campaign-signal-spec-v1.md`, `docs/marketing-ops-v1/Campaign Signal Spec v1.md`, `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`, `public/js/features/operations-shell.js`, `public/js/components/agent-modal.js`.
- DB queries: agent field-population census across all 16 marketing agents.
- No code written. No data mutated. Audit-only.
