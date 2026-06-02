# M5 — Marketing Agent Seed (16 agents as DB fixtures)

**Owner:** Sonnet Worker (isolated worktree)
**Branch:** `worker/m5-marketing-agent-seed`
**LOC budget:** ~300 (full-diff insertions; the 16 agent rows are mostly data, not behavior)
**Brief author:** Lead (Opus 4.7)
**Depends on:** existing `agents` table (already shipped via O1/O2/O3 — persona JSONB column at `artemis/builders/models.py:71`). Soft-depends on M1 (some agents reference reason codes).
**Grounded in:** `docs/marketing-ops-v1/agents/{scout,qualifier,content}/*.md` — 16 agent definition files.

## Why this brief exists

The marketing-ops-v1 spec defines 16 agents across three teams: 9 scouts, 4 qualifier, 3 content. They're the **first seed dataset** for Artemis OS — proof that the platform can host real agent teams, not lorem-ipsum demos. Today these 16 agents exist only as markdown files. M5 makes them first-class rows in the `agents` table so they show up in the Builder UI, run through the F1 execution loop, and can be edited via the Agent-Builder conversational surface (O1). They are NOT a separate "marketing agents" table; they live in the same `agents` table as every future agent the user builds.

This is the smallest brief that turns Artemis OS from "platform you could put agents in" into "platform with real agents in it."

## Scope

### In scope

1. **`artemis/marketing/seeds/marketing_agents.py`** — idempotent seed loader. Reads each of the 16 markdown files in `docs/marketing-ops-v1/agents/{scout,qualifier,content}/`, extracts the structured fields (purpose, prompt, tools, model, persona), upserts one row per agent into `agents` table. `ON CONFLICT (agent_id) DO UPDATE` so re-running pulls in markdown edits.

2. **One-shot CLI under `scripts/seed_marketing_agents.py`** — thin wrapper that calls the loader. Same pattern as M1's seed script. NOT in app startup — operator runs it explicitly after deploy.

3. **Agent rows populated** with these fields:
   - `agent_id` — slug derived from filename (e.g., `marketing.scout.starbridge_researcher`, `marketing.qualifier.cross_reference`, `marketing.content.writing_studio_adapter`)
   - `name` — human-readable from the markdown title (e.g., "Starbridge Researcher")
   - `description` — the file's "Purpose" section text
   - `goal` — one-line distillation from purpose
   - `system_prompt` — the file's "Prompt scaffolding" block, verbatim
   - `tools` — JSONB array of tool names declared in the "Tools required" section
   - `model` — Haiku for high-volume deterministic scouts (1.1, 1.2, 1.3, 1.5, 1.7, 1.8), Sonnet for qualitative work (2.1 qualifier rubrics, 5.2 asset selector), Haiku for adapters (5.3)
   - `provider` — `claude-code` (default cascade); fallback set via `fallback_provider`/`fallback_model`
   - `memory_policy` — `persistent` for scouts that need dedupe-via-memory; `session_scoped` for stateless qualifier/content agents
   - `permission_mode` — `auto` for scouts (no human-in-loop per signal); `ask` for content team
   - `persona` JSONB — `{name, purpose, voice_notes, ghostwrite, profile_image_path}` per O2/O3 schema. Voice notes lifted from agent file's "Implementation notes" or generated as a one-paragraph soul statement matching the role. `ghostwrite` defaults to `false`. `profile_image_path` left null for now.
   - `output_contract` JSONB — for scouts, references the `Signal` schema (`{"schema": "signal", "version": 1}`); for qualifier, references qualification result shape; for content, references the writing-studio-draft schema.

4. **Self-improvement hook** — each seeded agent has a `trajectory_summary` enabled flag in its row config so O1's self-improvement loop applies. (If this flag doesn't exist as a column, defer to a metadata JSONB key on the agent row and document it; do NOT add a new column in M5.)

5. **Tests** — load seed, assert 16 rows exist, assert agent_ids match expected slugs, assert idempotent re-run produces no duplicates, assert markdown-edit-then-reseed updates the row, assert one persona JSONB structure validates per O2/O3 schema (don't validate all 16 — just one representative).

### Out of scope

- The scout execution path (cadence runner, signal_queue writes). That's M5b.
- Tool definitions referenced in `tools` array — if a tool string doesn't have a registered handler yet, seed it anyway. Runtime resolution is a separate concern.
- Agent prompts mid-flight edits via the UI. That's O1's responsibility.
- Profile images. `profile_image_path` is null. UI shows initials fallback.
- Agent chains/DAGs linking the 16. Out of scope; the orchestration emerges from M5b (scouts run independently) and M4/M3 (qualifier path) and M7 (writing studio handoff). No DAG row needed.

## The 16 agents (canonical slug map)

| Slug | File | Default model | Memory policy |
|---|---|---|---|
| `marketing.scout.starbridge_researcher` | scout/1.1-starbridge-researcher.md | haiku | persistent |
| `marketing.scout.regional_news` | scout/1.2-regional-news-scout.md | haiku | persistent |
| `marketing.scout.linkedin_observer` | scout/1.3-linkedin-observer.md | haiku | persistent |
| `marketing.scout.legislative` | scout/1.4-legislative-scout.md | haiku | persistent |
| `marketing.scout.federal_funding` | scout/1.5-federal-funding-scout.md | haiku | persistent |
| `marketing.scout.state_doe` | scout/1.6-state-doe-scout.md | haiku | persistent |
| `marketing.scout.procurement` | scout/1.7-procurement-scout.md | haiku | persistent |
| `marketing.scout.board_minutes` | scout/1.8-board-minutes-scout.md | haiku | persistent |
| `marketing.scout.leadership_transition` | scout/1.9-leadership-transition-scout.md | haiku | persistent |
| `marketing.qualifier.cross_reference` | qualifier/2.1-cross-reference-agent.md | sonnet | session_scoped |
| `marketing.qualifier.ruleset_manager` | qualifier/2.2-ruleset-manager-agent.md | sonnet | session_scoped |
| `marketing.qualifier.ruleset_compiler` | qualifier/2.3-ruleset-compiler.md | haiku | session_scoped |
| `marketing.qualifier.brief_composer` | qualifier/2.4-brief-composer-agent.md | sonnet | session_scoped |
| `marketing.content.brief_assembler` | content/5.1-campaign-brief-assembler.md | haiku | session_scoped |
| `marketing.content.asset_selector` | content/5.2-asset-selector-agent.md | sonnet | session_scoped |
| `marketing.content.writing_studio_adapter` | content/5.3-writing-studio-adapter.md | haiku | session_scoped |

These slugs are stable. Any later reference to a marketing agent uses these strings. Do not rename.

## Persona generation (per agent)

For each agent, the `persona` JSONB is structured per O2/O3:

```json
{
  "name": "<human-readable agent name>",
  "purpose": "<one-sentence soul statement>",
  "voice_notes": "<2-3 sentence character / tone description>",
  "ghostwrite": false,
  "profile_image_path": null
}
```

- `purpose` — derived from the agent file's "Purpose" section, distilled to one sentence in the voice of the role. Example for 1.1: "I surface legislation and funding moves before anyone else does — by the time you hear about it from the news, I already filed the signal."
- `voice_notes` — the agent's character. Examples:
  - **Starbridge Researcher (1.1):** "Precise, data-driven, slightly impatient. Speaks in terms of bills passed, dollars allocated, deadlines counting down. Doesn't tolerate vague news; demands the document."
  - **Regional News Scout (1.2):** "Curious, conversational, ear-to-the-ground. The agent who reads the local paper before the local paper finishes printing it."
  - **Cross-Reference Agent (2.1):** "Methodical, three-step thinker. Will tell you both why a signal qualifies and why it might not — never just one."
  - **Brief Composer (2.4):** "A briefer, not a salesperson. Writes for Josh and Angela's morning coffee, not for the BDR pitch deck."
  - **Writing Studio Adapter (5.3):** "Quiet, mechanical, reliable. Speaks only when something breaks."

Worker generates these for all 16 — keep them under 60 words each, match the role's actual character per the markdown spec. Lead reviews before merge.

## Idempotency contract

- Re-running the seed must not duplicate rows. Use `INSERT ... ON CONFLICT (agent_id) DO UPDATE` with explicit column list (not `EXCLUDED.*` for everything — only update the fields the seed owns; do not clobber `owner_user_id` if a user has taken ownership later).
- The fields the seed DOES update on re-run: `name`, `description`, `goal`, `system_prompt`, `tools`, `model`, `provider`, `memory_policy`, `permission_mode`, `persona`, `output_contract`, `updated_at`.
- The fields the seed does NOT touch on re-run: `agent_id` (key), `owner_user_id`, `created_at`.
- Document this in a docstring at the top of `marketing_agents.py`.

## Invariants

1. **All 16 agent slugs match the table above exactly.** Tests assert this.
2. **No new migration in M5.** Schema is already there. If you find yourself writing one, stop and ping Lead.
3. **Markdown is source of truth.** Seed loader reads .md files, parses them, writes to DB. Editing a row in DB without updating markdown is allowed by the system but explicitly out-of-scope for M5; the team workflow is: edit markdown → run seed → DB updates.
4. **Persona JSONB validates against O2/O3 schema** (`{name, purpose, voice_notes, ghostwrite, profile_image_path}`). If O2/O3 ships a stricter Pydantic schema, conform to it. If unclear, use the shape above and flag for Lead.

## Files expected

- `artemis/marketing/seeds/marketing_agents.py` — loader module. ~150 LOC (most of which is the slug-to-config map + persona dict). Markdown parsing is regex-light: extract the "## Purpose", "## Prompt scaffolding", "## Tools required" sections by header name.
- `scripts/seed_marketing_agents.py` — CLI wrapper. ~30 LOC.
- `artemis/marketing/tests/test_marketing_agents_seed.py` — tests. ~80 LOC.

## Test plan

1. **Seed loader populates all 16 rows.** Assert count by `agent_id LIKE 'marketing.%'`.
2. **Slugs match.** Assert the set of `agent_id` values equals the canonical 16-slug list above.
3. **Idempotent re-run.** Run seed twice; assert row count still 16, no DB error.
4. **Edit-reseed updates row.** Modify one markdown file's description in-place via a test fixture (temp copy of .md), reseed, assert `description` column reflects the change, assert `created_at` unchanged.
5. **Persona shape valid for one representative agent.** Assert `persona` keys = `{"name", "purpose", "voice_notes", "ghostwrite", "profile_image_path"}`. Assert `purpose` non-empty.
6. **owner_user_id not clobbered.** Set `owner_user_id = 42` on one seeded row, reseed, assert `owner_user_id` still 42.
7. **`tools` array shape.** Assert it's a JSONB list (possibly empty for some agents), not a string.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`).
- dotenv `override=False` (`7ad1598`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call.
- `git diff --stat` for LOC self-reporting. No estimating.
- Markdown file paths must exist (they shipped at `0722803` and earlier). Don't invent paths; if a file is missing, ping Lead.

## What "done" looks like

1. 16 rows in `agents` table with the canonical slugs.
2. Each row's `system_prompt` matches the verbatim "Prompt scaffolding" block in its markdown file.
3. Each row's `persona` JSONB has all 5 keys, `purpose` and `voice_notes` written in role-appropriate voice.
4. Re-running the loader is idempotent and respects ownership.
5. Tests pass.
6. `./scripts/check.sh` does not regress (note pre-existing failures).
7. Full-diff insertions ≤ 330. Over budget → stop and ping Lead.

## Report Worker submits

1. `git diff --stat` output.
2. The 16 agent_ids actually written (paste).
3. One representative agent's full row dump as JSON (paste — Lead reviews the persona/prompt fidelity).
4. Test pass count.
5. Branch + worktree path.
6. Anything ambiguous in a markdown file that required interpretation — flag for Lead. Do not silently guess on prompts.

---

**Lead notes (not for Worker):**
- This brief is heavy on data, light on logic. Worker spends most time on the persona generation + markdown parsing, not on novel code.
- The slug naming `marketing.scout.starbridge_researcher` matches O1's expected slug pattern (`domain.subdomain.name`). Once these are seeded, the Agent-Builder can list them, the Builder UI shows them, and any future "build me another scout" conversation has 9 prior examples to learn from.
- M5b (scout execution path) lands separately. Until M5b, these agents exist as definitions only — they don't run on a cadence. That's intentional; we want the rows in place so the UI works first.
