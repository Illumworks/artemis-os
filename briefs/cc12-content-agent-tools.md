# CC12 — Content-agent tools (close the pipeline → Writing Studio handoff)

**Paste-into:** terminal-Lead OR Codex — well-specified backend work, no novel reasoning.
**Target branch:** `worker/cc12-content-agent-tools`
**Browser smoke owner:** Lead, post-merge — trigger a marketing pipeline run, verify the content-composer node produces a draft in `writing_studio` (the deliverable lifecycle completes through to a Studio draft).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (3 tool implementations + tests + seed update + small route additions if needed).
**Priority:** HIGH — closes the marketing pipeline → Writing Studio handoff. Identical pattern to CC4 (qualifier tools were missing; CC4 added them; full pipeline came alive). Per master plan priority order: Phase BH ✅ → Signal Playbook ✅ → PIPE6 (in flight) → **CC12** → Marketing flow audit.

---

## Why this exists

Per `docs/writing-studio-audit-2026-05-28.md` finding W1 (CRITICAL — same pattern as pre-CC4 qualifier):

> The three content agents declare tools that don't exist in the registry — silently dropped by the MCP server's per-agent scoping (so the agent runs with effectively no tools, just like the pre-CC4 qualifier).

| Agent | Declared tools | In registry? |
|---|---|---|
| `marketing.content.brief_assembler` | `campaign_brief.read`, `campaign_brief.write` | only `.write` is real; **`.read` missing** |
| `marketing.content.asset_selector` | `content_registry.list_approved_assets`, `claude.complete` | **both missing** (`claude.complete` is misdeclared — the LLM is implicit, not a tool) |
| `marketing.content.writing_studio_adapter` | `writing_studio.enqueue` | **missing** — the boundary tool that pushes drafts into the Studio |

Result today: content agents run, "succeed" via LLM chat, but emit no work-product. Drafts never reach the Studio.

**The substrate exists** (per W3 finding) — `artemis/marketing/writing_studio/invoke.py` already does the real work of creating deliverables + metadata + events. CC12 just wraps those existing implementations as MCP tools the content agents can actually call.

Same shape as CC4 (qualifier tools were the same kind of declared-but-not-implemented gap).

---

## Scope

### Part A — `writing_studio.enqueue` (the boundary tool)

The most important addition. Wraps `artemis/marketing/writing_studio/invoke.py` (existing module that creates the deliverable + bundles metadata + publishes events).

**Tool spec:**

```python
Tool(
    name="writing_studio.enqueue",
    description=(
        "Push a content draft into the Writing Studio for Angela/Julie/Olivia review. "
        "Creates a deliverable, attaches metadata bundle, fires the appropriate events. "
        "Returns the deliverable_id."
    ),
    input_schema={
        "type": "object",
        "required": ["campaign_brief_id", "draft_title", "draft_body", "voice_profile_slug"],
        "properties": {
            "campaign_brief_id": {"type": "integer", "description": "The campaign brief this draft is for."},
            "draft_title": {"type": "string", "minLength": 1, "maxLength": 200},
            "draft_body": {"type": "string", "minLength": 1, "maxLength": 20000},
            "voice_profile_slug": {
                "type": "string",
                "description": "Slug of the writing_profiles row to use (e.g., 'amira-marketing-voice').",
            },
            "asset_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional content_assets IDs referenced by the draft.",
            },
            "context_summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "Brief context for the reviewer (why this draft, what it should achieve).",
            },
        },
    },
)
```

**Implementation:** thin wrapper that calls `invoke.create_deliverable(...)` (or whichever entry function exists in `writing_studio/invoke.py` — verify the actual signature).

### Part B — `campaign_brief.read`

Wraps the existing `campaign_briefs` read path.

```python
Tool(
    name="campaign_brief.read",
    description="Read a campaign_brief by id. Returns full brief content + metadata.",
    input_schema={
        "type": "object",
        "required": ["brief_id"],
        "properties": {
            "brief_id": {"type": "integer"},
        },
    },
)
```

**Implementation:** `SELECT * FROM campaign_briefs WHERE id = :brief_id`. Returns JSON-serialized row. NOT_FOUND error if missing.

### Part C — `content_registry.list_approved_assets`

Wraps content_assets read with the approval filter.

```python
Tool(
    name="content_registry.list_approved_assets",
    description=(
        "List approved content assets available for inclusion in a draft. "
        "Filterable by campaign_family. Approved = status='approved' AND is_active=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "campaign_family": {"type": "string", "description": "Optional filter."},
            "limit": {"type": "integer", "default": 50, "maximum": 200},
        },
    },
)
```

**Implementation:** query `content_assets` with the existing approved/active filter. Use the existing repository function if one exists; create it if not.

### Part D — Drop `claude.complete` from asset_selector's tools

The agent declares `claude.complete` as a tool but the LLM is implicit (the adapter is the LLM). This is a definition error. Remove it from the agent's `tools` array.

**Files:** `artemis/marketing/seeds/marketing_agents.py` (the seed that creates `marketing.content.asset_selector`'s `tools` list).

After this brief: the agent's tools list contains only `content_registry.list_approved_assets` (now real, per Part C) — no phantom tool.

**Re-seed:** verify post-migration that the asset_selector agent row in `agents` table has the cleaned tools list. Either run a seed-update script OR add it to the migration.

### Part E — Tool registry registration

Add the 3 new tools to whatever module registers them with the tool registry. Look at how CC4's qualifier tools were registered (likely `artemis/tools/__init__.py` or similar) and follow the same pattern.

Verify `known_tool_names()` after this brief includes the 3 new tools.

### Part F — Tests

`artemis/marketing/tests/test_cc12_content_agent_tools.py`:

1. **`writing_studio.enqueue` creates a deliverable.** Invoke with fixture campaign_brief_id + valid draft. Verify `campaign_deliverables` row created + `writing_*` tables updated as appropriate.
2. **`writing_studio.enqueue` rejects invalid voice_profile_slug.** Self-teaching error (H1 pattern) listing valid slugs.
3. **`campaign_brief.read` returns the brief.** Fixture brief in DB. Tool returns matching JSON.
4. **`campaign_brief.read` NOT_FOUND on missing id.** Standard error shape.
5. **`content_registry.list_approved_assets` returns approved-only.** Fixture: 3 assets (2 approved, 1 unapproved). Tool returns 2 rows.
6. **`content_registry.list_approved_assets` filters by campaign_family.** Verify filter works.
7. **asset_selector agent's tools list no longer contains claude.complete.** Query `agents` table; verify cleanup.
8. **End-to-end integration:** run a content_composer agent run (mocked LLM that calls the 3 new tools). Verify the agent_run completes + a deliverable lands in `campaign_deliverables`.

### Part G — Optional: re-run a marketing pipeline post-merge to verify

Lead's post-merge browser smoke: trigger a fresh marketing pipeline run. The pipeline currently produces signals + qualified-signal briefs (verified earlier this session). With CC12, the content nodes should now ALSO produce drafts in the Writing Studio.

Worker doesn't have to run this — Lead handles in post-merge verification.

---

## Files owned

- NEW or EDIT: `artemis/tools/marketing/writing_studio.py` (writing_studio.enqueue — confirm path)
- NEW or EDIT: `artemis/tools/marketing/campaign_brief.py` (campaign_brief.read — confirm path)
- NEW or EDIT: `artemis/tools/marketing/content_registry.py` (list_approved_assets — confirm path)
- EDIT: `artemis/tools/__init__.py` (or wherever tools register — match CC4's pattern)
- EDIT: `artemis/marketing/seeds/marketing_agents.py` (remove claude.complete from asset_selector)
- POSSIBLE EDIT: `artemis/marketing/repository.py` (add `list_approved_content_assets` helper if not present)
- NEW: `artemis/marketing/tests/test_cc12_content_agent_tools.py`

---

## Acceptance criteria

1. **No schema changes.** Migration stays at 0053 (post-PIPE6). **Paste alembic current.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_cc12_content_agent_tools.py -v` — all 8 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation flakes). **Paste.**
4. `python -c "from artemis.tools.registry import known_tool_names; tools = known_tool_names(); print([t for t in tools if 'writing_studio' in t or 'campaign_brief' in t or 'content_registry' in t])"` — verify the 3 new tools appear. **Paste output.**
5. **asset_selector agent's tools list cleanup verified:** `psql -c "SELECT tools FROM agents WHERE agent_id = 'marketing.content.asset_selector';"` — verify `claude.complete` is GONE. **Paste.**
6. **Lossless invariant:** existing tools + agents not removed; only ADDED to the registry + claude.complete pruned from one agent's tools array.
7. `git diff --stat` + `git log --oneline -1` on `worker/cc12-content-agent-tools`. **Paste.**

---

## Hard constraints

- **Self-teaching errors (H1 pattern).** Invalid `voice_profile_slug` → error message lists valid slugs. Same for any other enum field.
- **Reuse existing `writing_studio/invoke.py`** as the engine for `enqueue`. Don't re-implement the deliverable creation.
- **No schema changes.** CC12 is pure tool wiring + seed cleanup.
- **Failure isolation in content_registry tool.** If query fails, return empty array + log warning (don't crash the agent run).
- **`writing_studio.enqueue` must respect existing state machine.** Deliverable lifecycle transitions go through the existing `DeliverableState` enum + `state_machine` module. Don't bypass.
- **Local-only git.** Worker commits on `worker/cc12-content-agent-tools`; merge after Lead approves.

---

## Coordination with PIPE6 (currently in flight)

PIPE6 touches: `artemis/automations/*`, `artemis/routes/builders/workflows.py`, `public/js/features/operations-shell.js`, migration 0053, `docs/SITE-MAP.md`, possibly `artemis/builder/memory_carryover.py`.

CC12 touches: `artemis/tools/marketing/*`, `artemis/tools/__init__.py`, `artemis/marketing/seeds/marketing_agents.py`, `artemis/marketing/repository.py`, tests.

**Zero file overlap.** CC12 can fire in parallel with PIPE6 if Jon wants more throughput, OR sequentially after PIPE6 lands. No migration collision (CC12 has no migration).

---

## What success looks like post-merge

A marketing pipeline run goes from `succeeded` at the content nodes today (no work-product) to `succeeded` with a real `campaign_deliverable` landing in the Writing Studio. Angela/Julie/Olivia (or the operator) sees a new draft to review. The full marketing pipeline → Writing Studio loop closes end-to-end.

Same shape as the loop closure CC4 delivered for qualifier → signal_briefs. Same pattern, different surface.

---

## Report-back format

```
CC12 — Content-agent tools report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially #8 end-to-end agent_run → deliverable)
4. Tool registry verification (3 new tools present)
5. asset_selector cleanup verification (claude.complete pruned)
6. check.sh summary
7. Anything surprising — especially around the writing_studio/invoke.py signature OR campaign_briefs schema OR content_assets approval filter shape
```

---

**Worker: CC12 is the W1 finding fix. Same pattern as CC4 (declared-but-not-implemented tools); same fix shape (wrap existing implementations as MCP tools). After this lands, the marketing pipeline → Writing Studio handoff closes end-to-end — the LAST gap from this session's audit work. Per master plan: after CC12, the Marketing flow audit (Dashboard / Campaigns / Approval Queue) becomes the next stream.**
