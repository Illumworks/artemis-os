# Brief — Agent-builder can't author the blueprint fields (audit fix)

**Type:** real write-path gap (the agent-builder agent is blind to most of the agent schema).
**Model:** Codex or terminal Sonnet. **Own worktree**, branch `worker/agent-builder-blueprint-fields`,
launched with `cwd` INSIDE the worktree. Don't touch the main repo working tree. Branch off `main`.

## Problem (verified 2026-06-05)

The `agents` table + `AgentRead` API + the Operations profile view all support a rich blueprint:
`system_prompt`, `persona`, `output_contract`, `reason_codes_emitted`, `cadence_seconds`,
`lifecycle_status`, `urgency_tiers` (JSONB **object** `{tier: description}`), `failure_modes`
(JSONB array of `{name, description}`), `inputs_required` (JSONB array of `{key, kind, description}`),
`db_tables_touched` (JSONB array of strings), `implementation_notes` (text).

Existing agents were SEEDED with these. But the **agent-builder** (the agent/tool that authors+edits
agents) can only write 8 core fields:

- `PROPOSE_AGENT` tool input_schema (`artemis/builder/agent_builder.py` ~L177–240) exposes ONLY:
  name, agent_id, description, goal, system_prompt, tools, model, provider. No blueprint fields.
- `_commit_agent` (`artemis/builder/engine.py` ~L394–450) on CREATE constructs `Agent(...)` with only
  name/description/goal/system_prompt/tools/model/provider/max_iterations; on UPDATE it `setattr`s only
  that same 8-key set. So blueprint fields (and persona / output_contract / reason_codes_emitted /
  cadence / lifecycle / urgency / failure_modes / inputs_required / db_tables_touched /
  implementation_notes / max_iterations beyond default) can never be authored or edited via the builder.

Net: any agent created/edited through the builder is blueprint-less, and the builder cannot enrich an
existing thin agent.

## Fix

1. **Extend `PROPOSE_AGENT` input_schema** (agent_builder.py) to accept the full blueprint:
   persona (object), output_contract (object), reason_codes_emitted (array[str]), cadence_seconds (int),
   lifecycle_status (str), urgency_tiers (object), failure_modes (array of {name,description}),
   inputs_required (array of {key,kind,description}), db_tables_touched (array[str]),
   implementation_notes (str), max_iterations (int). Keep only `name` required; everything else optional
   so partial proposals still work. Match the EXACT shapes the DB columns / `AgentRead` use
   (urgency_tiers is an OBJECT, not an array — get this right or the profile renders "Not specified").

2. **Extend `_commit_agent`** (engine.py) CREATE + UPDATE to read/write every new field. UPDATE must
   only `setattr` keys actually present in the proposal (so a partial edit never wipes existing blueprint
   data — lossless). Confirm `model_validate`/serialization round-trips object-typed JSONB correctly.

3. **⚠ UPDATE THE BUILDER AGENT'S PROMPT/INSTRUCTIONS.** Adding schema fields is useless if the builder
   LLM never fills them (known failure mode — schema widened but prompt unchanged → fields stay empty).
   Find the agent-builder's system prompt / instruction template (search artemis/builder/ and the
   builder agent's definition) and teach it to author the blueprint fields when creating/editing an
   agent — what cadence/urgency tiers/failure modes/inputs/db tables/implementation notes mean and to
   populate them by default. Include the urgency_tiers OBJECT shape in the prompt's example.

4. **Any Pydantic proposal model** (definition_proposal / proposal schemas) between the tool and
   `_commit_agent` must also carry the new fields — trace the whole path so nothing silently drops them.

## Verify LIVE (assert the EFFECT, not HTTP 200)

- Drive the builder to CREATE a new agent with blueprint fields populated; read the DB row back and
  confirm cadence_seconds/urgency_tiers(object)/failure_modes/inputs_required/db_tables_touched/
  implementation_notes/persona all persisted with correct shapes.
- Drive the builder to EDIT an existing agent's goal ONLY; confirm its existing blueprint fields are
  preserved (not wiped).
- Confirm the new agent's profile (GET /api/agents/{agentId}) returns the blueprint populated.
  (Note: a separate display-bug fix in operations-shell.js — enriched load/match by agentId — already
  landed; the profile view should now render what the API returns.)

## Constraints
- Lossless: never wipe existing fields on partial update. No destructive migrations (columns already
  exist — no schema change needed). Org dep rule: nothing < 7 days old; commit uv.lock if regenerated.
- Tests + ruff + mypy clean. Do NOT merge — report branch + how to verify each effect.
- Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
