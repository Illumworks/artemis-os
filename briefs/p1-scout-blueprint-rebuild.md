# P1 — Scout Blueprint Rebuild (9 files)

**Paste-into:** terminal-Lead. It spawns a Sonnet Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/p1-scout-blueprints`
**Browser smoke owner:** Lead (this session), post-merge (re-seed + verify)
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** 600 (across 9 markdown files — this is content, measured as net line delta). Hard stop at 750.

---

## Why this brief exists

The 9 scout blueprints under `docs/marketing-ops-v1/agents/scout/` contain **stale pre-Josh reason-code tables** baked in before Josh's canonical spec existed. Now that F1 made Josh's spec the single source and F2 injects reason codes + state nuances into the LLM prompt at runtime, the inline copies in the blueprints are (a) wrong and (b) duplicative drift risk. This brief cleans the blueprints to be **voice/focus documents** — what the scout is, how it talks, what it uniquely watches, its cadence, urgency discipline, failure modes, tools, and implementation notes. Operational reason-code data lives in Josh's spec, not here.

**Functionality-first framing:** the priority is removing wrong/duplicative content and ensuring the field-extracting sections parse cleanly into the seed loader. Voice polish is secondary — keep it light.

---

## Scope — for EACH of the 9 scout blueprints

Files: `docs/marketing-ops-v1/agents/scout/1.1` through `1.9` (`*.md`).

### Remove (functional — stale/duplicative)

1. **The `## Reason codes emitted` table.** Reason codes now come from Josh's spec via the "Primary scouts" column (F1) and are injected at runtime (F2). Replace the whole section with a one-line pointer:
   ```
   ## Reason codes emitted

   Sourced at runtime from `decisions/campaign-signal-spec-v1.md` (codes where this scout appears in the "Primary scouts" column). Do not hardcode codes here.
   ```
2. **Any `## State nuances` / "State nuances to watch (from spec §5)" inline quotes.** These are injected at runtime from Josh's spec §5 (F2). Remove the inline copy. If the section adds scout-specific nuance NOT in Josh's spec, keep only that delta and note it's scout-specific.

### Keep + ensure parse-clean (functional — feeds the seed loader, post-F3)

Make sure each blueprint has these sections with content the F3-repaired seed parser can extract:

- `## Purpose` — what this scout uniquely catches.
- `## Cadence` — keep the "every N hours / daily" phrasing so `_cadence_seconds` parses it.
- `## Inputs` — env keys + resources.
- `## Urgency tiers` — use the format the F3 parser handles (either `- **hot** — desc` bullets OR the "Reserve hot for:" prose+bullets form). Verify against `_urgency_tiers_v2`.
- `## Failure modes` — `- Name → description` bullets (the existing fallback regex handles this).
- `## Tools required` — the code-fence list of `namespace.method(...)` calls. These become the agent's `tools` array. **Critical:** these must match the tool names P2/P3 will implement (`signal_queue.write`, `news_api.search`, `state_doe.fetch`, `board_minutes.fetch`, `pdf_extractor.extract`, `memory_layer.upsert`, `territory_config.get_priority_states`, `reason_codes.get_allowlist`, etc.). Align tool names to the catalog in `docs/tool-execution-architecture.md` "Initial tool catalog". If a blueprint references a tool not in that catalog, either map it to the closest catalog tool or flag it in your report.
- `## Implementation notes for Codex` (or `## Implementation notes`) — keep concise, factual.

### Light voice pass (secondary — keep minimal)

- Ensure the opening paragraph matches the persona voice in `artemis/marketing/seeds/marketing_agents.py` `_PERSONAS` for this agent. Don't rewrite extensively — just make sure tone is consistent. This is polish; spend little time here.

### Do NOT

- Do NOT invent reason codes (they're in Josh's spec).
- Do NOT add `**Status:**` headers unless you're confident (lifecycle_status is nullable; fine to leave absent).
- Do NOT run a DB re-seed (isolated worktree + shared dev DB = race with P4). Instead verify via the loader's parse function (see acceptance).

---

## Files owned by this stream

- EDIT: `docs/marketing-ops-v1/agents/scout/1.1-starbridge-researcher.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.3-linkedin-observer.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.4-legislative-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.5-federal-funding-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.6-state-doe-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.7-procurement-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.8-board-minutes-scout.md`
- EDIT: `docs/marketing-ops-v1/agents/scout/1.9-leadership-transition-scout.md`

**Do not touch:** qualifier/content blueprints (P4), any Python, Josh's spec, the seed loader.

---

## Acceptance criteria (Worker must demonstrate each)

1. **All 9 blueprints parse cleanly via the loader (NO DB write):**
   ```bash
   uv run python -c "
   from artemis.marketing.seeds.marketing_agents import load_marketing_agent_rows
   rows = load_marketing_agent_rows()
   scouts = [r for r in rows if r['agent_id'].startswith('marketing.scout.')]
   for r in scouts:
       print(r['agent_id'], 'sys_prompt:', len(r['system_prompt'] or ''), 'tools:', len(r['tools']), 'urgency:', bool(r['urgency_tiers']), 'failure:', bool(r['failure_modes']), 'notes:', bool(r['implementation_notes']))
   "
   ```
   **Paste the output.** All 9 should show non-zero system_prompt, non-empty tools, and urgency/failure/notes = True.
2. **No stale reason-code tables remain:** `grep -rl "Reason codes emitted" docs/marketing-ops-v1/agents/scout/` should show all 9, and `grep -A3 "Reason codes emitted" docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md` should show the runtime-pointer text, NOT a code table. **Paste both.**
3. **Tool names align with catalog:** list every distinct tool name across all 9 blueprints' `## Tools required` sections, and confirm each maps to a tool in `docs/tool-execution-architecture.md`. **Paste the list + any mismatches.**
4. `./scripts/check.sh` passes (no Python changed, should be clean modulo known flakes). **Paste summary.**
5. `git diff --stat` net line delta ≤ 600 (750 hard stop). **Paste it.**
6. `git log --oneline -1` on `worker/p1-scout-blueprints`. **Paste it.**

---

## Hard constraints

- LOC cap: 600 net delta (750 hard stop).
- Functionality first: removing stale content + parse-clean sections matter more than prose polish.
- Do NOT re-seed the DB. Lead runs the re-seed once after P1 + P4 both merge.
- Do NOT touch qualifier/content blueprints or any Python.
- Local-only git. Worker commits on `worker/p1-scout-blueprints`; terminal-Lead merges after Lead approves.

---

## Report-back format (Worker pastes verbatim, filled in)

```
P1 — Scout Blueprint Rebuild report

1. Commit hash / branch / worktree
2. LOC diff stats (net line delta)
3. Loader parse output (acceptance #1)
4. Stale-table removal proof (acceptance #2)
5. Tool-name alignment list + mismatches (acceptance #3)
6. check.sh summary
7. Anything surprising — especially tool-name mismatches or blueprints that were already clean
```

---

**End of brief. Sonnet Worker: operating principle — never assume. Verify each blueprint parses via the loader before claiming done. Do not invent reason codes; they live in Josh's spec now.**
