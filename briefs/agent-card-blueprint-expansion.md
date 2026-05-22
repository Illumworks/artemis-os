# Agent Card Blueprint Expansion — Surface Full Markdown Definition

**Owner:** Codex (paste-ready)
**Branch:** `codex/agent-card-blueprint-expansion`
**LOC budget:** ~180 (honest overrun OK to ~240)
**Depends on:** M5 seeded agents.

## Why

M5 seeded each agent with `system_prompt`, `tools`, `persona`, and basic model/provider config. But the markdown files in `docs/marketing-ops-v1/agents/` contain much more operational info that never reached the DB or UI:

- **Cadence** (e.g., "Scheduled poll every 4h")
- **Inputs** (including required env keys / connector requirements)
- **Failure modes** documentation
- **DB tables touched** (operational metadata)
- **Urgency tier definitions** (per-source rules for hot/standard/enrichment)
- **Status** (e.g., "Bench-test for 6–12 months" for Starbridge)
- **Implementation notes** for developers

The Agent Card currently shows persona + identity + linked skills + recent runs. Operators can't see the full blueprint without opening the markdown file. Bad for transparency, bad for debugging.

**Fix:** add columns or a JSONB `blueprint` field to the `agents` table. Seeder extracts these sections. Agent Card renders them in expandable sections.

## Scope

### Data model

Add to `agents` table (one migration):
- `cadence_seconds` INT NULL — scheduling cadence in seconds (e.g., 14400 for 4 hours)
- `lifecycle_status` TEXT — e.g., `bench_test`, `active`, `deprecated`
- `urgency_tiers` JSONB NULL — per-tier definitions extracted from markdown's "Urgency tiers" section
- `failure_modes` JSONB NULL — array of `{name, description}` extracted from "Failure modes"
- `db_tables_touched` JSONB NULL — array of strings
- `implementation_notes` TEXT NULL — extracted from "Implementation notes for Codex"
- `inputs_required` JSONB NULL — array of `{kind, key, description}` extracted from "Inputs" section (where things like `STARBRIDGE_API_KEY` live)

OR simpler: one `blueprint` JSONB column containing all the above as a structured dict. Trades discoverability (separate columns) for flexibility (one JSONB). Worker picks; my rec: separate columns for the structured ones (cadence_seconds, lifecycle_status), JSONB for the free-form/list ones (urgency_tiers, failure_modes, inputs_required).

### Seeder update

Update `artemis/marketing/seeds/marketing_agents.py` to extract these sections from each markdown. Parsing is regex-light: section headers (`## Cadence`, `## Inputs`, etc.) bracket the content. Trim whitespace, parse bullet lists where applicable.

### Agent Card UI

New "Operating Blueprint" section in the Agent Card detail panel (below the existing persona / identity sections). Collapsible. Renders:

- **Cadence:** "Every 4 hours" (humanized from cadence_seconds)
- **Lifecycle:** badge — `bench-test` / `active` / `deprecated`
- **Inputs required:**
  - `STARBRIDGE_API_KEY` — Starbridge API access (env var) → link to Connector if connectors brief landed
  - `Memory Layer` — read-only (no auth needed)
- **Urgency tiers:** 3 rows showing hot/standard/enrichment with their conditions
- **Failure modes:** bulleted list with each mode's description
- **DB tables touched:** chip row
- **Implementation notes:** monospace block

Read-only for v1 (operator can't edit blueprint fields via UI — those come from markdown). Future: editable via Agent-Builder.

### Tests

- Migration up/down clean
- M5 re-seed populates blueprint fields for at least 3 representative agents (scout, qualifier, content)
- Agent Card renders Operating Blueprint section
- Markdown sections not present in a given agent's file → corresponding fields are null; UI renders "Not specified" placeholder

## Out of scope

- Editable blueprint via UI. Read-only for v1.
- Inputs-to-Connectors automatic linking. The Connectors brief handles that separately.
- Markdown rendering with full formatting. Plaintext extraction is fine for v1.
- Historical blueprint versions. Single current state.

## Files expected

| File | LOC |
|---|---|
| `alembic/versions/<rev>_agents_blueprint.py` | ~40 |
| `artemis/builders/models.py` | ~10 delta |
| `artemis/builders/schemas.py` | ~20 delta |
| `artemis/marketing/seeds/marketing_agents.py` | ~80 delta (section parsing) |
| `public/js/features/operations-shell.js` (Agent Card section) | ~70 delta |
| `public/css/features/operations.css` | ~30 delta |
| Tests | ~50 |

**Total: ~300 LOC.** Cap 360. Section parsing is the bulk; render is straightforward.

## Invariants

- Re-seed must preserve existing blueprint fields if operator has edited via UI (future-proofing — for v1, fields are markdown-only, so this just means don't clobber on null markdown)
- conftest hard-fail on non-test DB
- node --check on modified JS
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, sample blueprint for 1 agent (paste JSON), screenshot of Agent Card with Operating Blueprint visible, test pass count, branch.
