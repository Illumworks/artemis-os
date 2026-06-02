# P4 — Qualifier + Content Blueprint Rebuild (7 files)

**Paste-into:** terminal-Lead. It spawns a Sonnet Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/p4-qualifier-content-blueprints`
**Browser smoke owner:** Lead (this session), post-merge (re-seed + verify)
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** 600 (across 7 markdown files, net line delta). Hard stop at 750.

---

## Why this brief exists

The 4 qualifier + 3 content blueprints are the thinnest agents in the system. Audit (2026-05-26) found:
- `marketing.content.brief_assembler` — **0 chars system_prompt, 0 tools**
- `marketing.content.writing_studio_adapter` — **0 chars system_prompt**
- `marketing.qualifier.ruleset_compiler` — **0 chars system_prompt, 0 tools**
- `marketing.qualifier.cross_reference` — 558 chars, 0 tools
- `marketing.qualifier.ruleset_manager` — 0 tools

These agents do the qualification + content-assembly work AFTER scouts emit signals. If they're hollow, the back half of the pipeline is inert even once scouts produce signals. This brief fills them in.

**Functionality-first:** the priority is the 3 totally-empty system prompts and the missing tool declarations. Voice polish is secondary.

---

## Scope — for the 7 blueprints under `docs/marketing-ops-v1/agents/{qualifier,content}/`

Files:
- `qualifier/2.1-cross-reference-agent.md`
- `qualifier/2.2-ruleset-manager-agent.md`
- `qualifier/2.3-ruleset-compiler.md`
- `qualifier/2.4-brief-composer-agent.md`
- `content/5.1-campaign-brief-assembler.md`
- `content/5.2-asset-selector-agent.md`
- `content/5.3-writing-studio-adapter.md`

### Priority 1 — Fill the empty `## Prompt scaffolding` sections (functional)

The seed loader extracts `system_prompt` from the `## Prompt scaffolding` section. Three blueprints produce empty system_prompts — meaning their `## Prompt scaffolding` section is missing, empty, or mis-headed. Read each blueprint, find why, and write a real prompt scaffolding section:

- **`content/5.1-campaign-brief-assembler.md`** — its job (per the blueprint body + the marketing plan screenshots): take Cross-Reference qualified output, build the immutable campaign brief downstream content trusts. Validation-first, frozen-evidence, fails loudly on bad input. Write a prompt that captures this.
- **`content/5.3-writing-studio-adapter.md`** — carries approved campaign inputs across the Writing Studio boundary, one draft payload at a time. Quiet, mechanical, reports exact boundary failures. Write a prompt.
- **`qualifier/2.3-ruleset-compiler.md`** — turns approved ruleset YAML into executable validated runtime objects, security-minded, speaks in compiler errors. Write a prompt.

Use the existing blueprint body content + the persona in `marketing_agents.py` `_PERSONAS` as source material. Don't invent a different role than what's already described — formalize what's there into a real prompt.

### Priority 2 — Declare tools (functional)

Qualifier and content agents currently have empty/thin `## Tools required`. Give them the tools they actually need, aligned to the catalog in `docs/tool-execution-architecture.md`:

- **cross_reference** — reads signals + rulesets + territory config. Tools: `territory_config.get_priority_states`, `reason_codes.lookup`, plus DB-read tools (flag if the catalog lacks a needed read tool).
- **ruleset_manager** — evolves rules conversationally. Tools: ruleset read/propose (flag if catalog lacks these — they may be P3 stubs or future).
- **ruleset_compiler** — compiles YAML. Likely no external tools (pure transform). Note this.
- **brief_composer** — already has 5 tools; verify they align to the catalog.
- **brief_assembler** — writes the immutable brief. Tool: `campaign_brief.write` (per the design doc's permissions section — only this agent may call it). Flag if not in catalog (it's a P3 tool).
- **asset_selector** — picks asset bundle. Tools: asset read tools (flag if catalog lacks them).
- **writing_studio_adapter** — carries payload across boundary. Tool: `writing_studio.enqueue` or similar (flag — likely P3/future).

**Where a needed tool isn't in the catalog yet, declare it in the blueprint anyway and FLAG it in your report.** The P2 bridge silently drops unknown tools (logs a warning), so declaring a not-yet-implemented tool is safe — it just won't fire until P3 (or a later stream) implements it. This lets the blueprints declare intent ahead of implementation.

### Priority 3 — Ensure parse-clean structured sections (functional)

For each blueprint, ensure `## Urgency tiers` (if applicable — qualifier/content may legitimately not have these), `## Failure modes`, `## Implementation notes` are present and parse via the F3-repaired loader. Note: it's OK for qualifier/content agents to NOT have urgency tiers (they don't emit signals) — don't invent them.

### Priority 4 — Light voice consistency (secondary)

Match tone to `_PERSONAS`. Keep minimal.

### Do NOT

- Do NOT re-seed the DB (isolated worktree + shared dev DB = race with P1). Verify via loader parse only.
- Do NOT touch scout blueprints (P1).
- Do NOT add reason-code tables (qualifier reads codes from signals; content doesn't emit them).

---

## Files owned by this stream

- EDIT: the 7 files listed above under `docs/marketing-ops-v1/agents/{qualifier,content}/`

**Do not touch:** scout blueprints (P1), any Python, Josh's spec, the seed loader, tool implementations.

---

## Acceptance criteria (Worker must demonstrate each)

1. **All 7 blueprints parse cleanly via the loader (NO DB write):**
   ```bash
   uv run python -c "
   from artemis.marketing.seeds.marketing_agents import load_marketing_agent_rows
   rows = load_marketing_agent_rows()
   qc = [r for r in rows if r['agent_id'].startswith(('marketing.qualifier.','marketing.content.'))]
   for r in qc:
       print(r['agent_id'], 'sys_prompt:', len(r['system_prompt'] or ''), 'tools:', len(r['tools']), 'failure:', bool(r['failure_modes']), 'notes:', bool(r['implementation_notes']))
   "
   ```
   **Paste the output.** All 7 must show non-zero system_prompt (the 3 previously-empty ones especially). All should have ≥1 tool OR a documented reason for zero (e.g. ruleset_compiler pure-transform).
2. **The 3 previously-empty prompts now have real content:** show system_prompt length for brief_assembler, writing_studio_adapter, ruleset_compiler — all > 200 chars. **Paste.**
3. **Tool-name alignment:** list every distinct tool across the 7 blueprints, map each to the catalog in `docs/tool-execution-architecture.md`, and clearly flag the ones NOT yet in the catalog (these are intentional forward-declarations). **Paste the list + flags.**
4. `./scripts/check.sh` passes modulo known flakes. **Paste summary.**
5. `git diff --stat` net delta ≤ 600 (750 hard stop). **Paste it.**
6. `git log --oneline -1` on `worker/p4-qualifier-content-blueprints`. **Paste it.**

---

## Hard constraints

- LOC cap: 600 net delta (750 hard stop).
- Functionality first: the 3 empty prompts + tool declarations matter most.
- Do NOT re-seed the DB. Lead re-seeds once after P1 + P4 both merge.
- Do NOT invent urgency tiers for agents that don't emit signals.
- Local-only git. Worker commits on `worker/p4-qualifier-content-blueprints`; terminal-Lead merges after Lead approves.

---

## Report-back format (Worker pastes verbatim, filled in)

```
P4 — Qualifier + Content Blueprint Rebuild report

1. Commit hash / branch / worktree
2. LOC diff stats (net delta)
3. Loader parse output (acceptance #1)
4. Three-empty-prompts-now-filled proof (acceptance #2)
5. Tool-name alignment list + forward-declared (not-yet-in-catalog) flags (acceptance #3)
6. check.sh summary
7. Anything surprising
```

---

**End of brief. Sonnet Worker: operating principle — never assume. The 3 empty prompts need real content derived from the existing blueprint body + persona, not invented roles. Verify the loader parses all 7 before claiming done.**
