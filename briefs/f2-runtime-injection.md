# F2 — Runtime Injection: Rich System Prompt from Agent Row + Josh's Spec

**Paste-into:** terminal-Lead. It spawns a Sonnet Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/f2-runtime-injection`
**Browser smoke owner:** Lead (this session), post-merge
**Report back to me by:** Jon pastes terminal-Lead's relay (with Worker's full report) into Lead chat
**LOC cap:** 200 (full-diff insertions including tests). Hard stop at 250.

---

## Why this brief exists

Today `artemis/builders/executor.py:run_agent()` builds the LLM system prompt from only three sources:
1. `agent.system_prompt`
2. `agent.goal`
3. `shared_context` dict

But the `agents` row has rich blueprint data populated (or about-to-be-populated by F3): `persona`, `urgency_tiers`, `failure_modes`, `implementation_notes`, `db_tables_touched`, `inputs_required`. And F1 just landed Josh's spec parser, which exposes reason-code allowlists and state nuances per scout. All of that is currently ignored at runtime.

This brief weaves the deep blueprint data into the system message. After F2 + F3 land together, every scout's LLM call sees its voice, its urgency discipline, its failure-mode awareness, its allowed reason codes (per Josh's spec), and the state nuances for its codes. Without F2, the F3 seed work just sits in the DB unused.

---

## Scope

### Part A — Refactor system-prompt assembly

In `artemis/builders/executor.py`, extract the current prompt-build block (currently inline at ~lines 170-179 of `run_agent()`) into a dedicated function:

```python
def _build_system_prompt(
    agent: Agent,
    shared_context: dict[str, Any] | None,
) -> str | None:
    """Compose the rich system prompt from agent row + Josh's spec.

    Returns None only if every section would be empty (legacy compatibility for
    agents with no system_prompt + no goal + no persona).
    """
    ...
```

The returned string follows this template (sections appear only when their source data is non-empty — be defensive against None / empty strings / empty lists / empty dicts):

```
<agent.system_prompt verbatim>

## Voice
<agent.persona.voice_notes>

## Purpose
<agent.persona.purpose>
(omit if same as agent.system_prompt opening — but cheap to always include; LLMs handle dupes fine)

## Goal
<agent.goal>

## Reason codes you may emit
<from Josh's spec — see Part B>

## State nuances to watch
<from Josh's spec — see Part C>

## Urgency discipline
<agent.urgency_tiers — formatted as bullet list>

## Failure modes to avoid
<agent.failure_modes — formatted as bullet list of name → description>

## Implementation notes
<agent.implementation_notes verbatim>

## Inputs available
<agent.inputs_required — formatted as bullet list>

## Context (from upstream pipeline nodes)
<shared_context lines, formatted as "key: value">
```

Substitute the inlined block in `run_agent()` with a call: `system_prompt = _build_system_prompt(agent, shared_context)`. Pass it to `run_turn` exactly as before.

### Part B — Josh-spec reason-code allowlist injection

Inside `_build_system_prompt`, when the agent is a marketing scout (agent_id starts with `marketing.scout.`), call Josh's spec parser to get the allowlist:

```python
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout

scout_slug = agent.agent_id.rsplit(".", 1)[-1] if agent.agent_id.startswith("marketing.scout.") else None
if scout_slug:
    spec = parse_spec()  # cheap; parser reads ~128-line file
    scout_codes = reason_codes_for_scout(spec, scout_slug)
    if scout_codes:
        # Format: a section listing each allowed code + description + default urgency
        section = "## Reason codes you may emit\n\nYou may emit ONLY these reason codes. Any other code will be rejected.\n\n"
        for rc in scout_codes:
            section += f"- **{rc.code}** ({rc.default_urgency}) — {rc.description}\n  Scout's job: {rc.what_scout_looks_for}\n"
        # append section to prompt parts
```

**Caching:** `parse_spec()` is fast (string parsing of a single small file), but called every `run_agent`. Memoize it at module level using `functools.lru_cache(maxsize=1)` keyed on `raw_source_hash`. Build a tiny helper:

```python
@lru_cache(maxsize=1)
def _cached_josh_spec() -> JoshSpec:
    return parse_spec()
```

That's it for the cache — the spec file changes rarely; lru_cache(1) is right. (When Josh edits the spec we'll restart uvicorn anyway per the operational invariants.)

For non-scout agents (qualifier, content), the "Reason codes you may emit" section is omitted entirely — they don't emit signals.

### Part C — State nuances injection

For scouts, also inject state nuances. Filter Josh's spec `state_nuances` to those relevant to the scout's allowed codes:

```python
# Heuristic v1: include "All states" nuance always; include per-state nuance if
# the scout's allowlist includes any code mentioning that state (e.g. TX_HB1416_WAIVER → Texas nuance).
# Keep it simple — include all 4 nuance entries for scouts. Cost is small (~150 tokens) and the LLM benefits from full context.
```

Just include all `state_nuances` entries verbatim for scouts. The format:

```
## State nuances to watch

### Florida
<text from spec>

### Texas
<text from spec>

### Indiana, Maryland, Missouri, Michigan, Illinois
<text from spec>

### All states — vendor dissatisfaction
<text from spec>
```

For non-scouts: omit this section.

### Part D — Defensive against missing data

Right now (pre-F3), `agent.urgency_tiers`, `agent.failure_modes`, `agent.implementation_notes` are all NULL for 16/16 agents. Your code must handle this gracefully — when a field is None or empty, OMIT the section entirely (don't write a "## Urgency discipline\n(none)" stub).

After F3 lands and re-seeds, those fields populate and the sections activate automatically. No coordination needed between F2 and F3 — F2's defensive handling is the seam.

### Part E — Tests

`artemis/builders/tests/test_runtime_injection.py` (new):

1. **Voice + persona injection.** Create an in-memory Agent with `persona = {"voice_notes": "Curious, conversational", "purpose": "Catch local signals"}`, call `_build_system_prompt`. Assert the result contains `"Curious, conversational"` AND `"Catch local signals"`.
2. **Josh-spec reason codes for scout.** Create an Agent with `agent_id = "marketing.scout.regional_news"`. Call `_build_system_prompt`. Assert result contains at least 5 reason codes that `reason_codes_for_scout(spec, "regional_news")` returns. Assert it contains `"You may emit ONLY these reason codes"`.
3. **Non-scout omits reason codes section.** Create an Agent with `agent_id = "marketing.qualifier.cross_reference"`. Assert result does NOT contain `"You may emit ONLY these reason codes"`.
4. **State nuances for scout.** Same regional_news agent. Assert result contains `"### Florida"` AND `"### Texas"` AND `"### All states"`.
5. **Urgency tiers injection.** Agent with `urgency_tiers = {"hot": "RFPs and board votes only", "standard": "speculation"}`. Assert result contains `"## Urgency discipline"` AND both tier names AND both descriptions.
6. **Failure modes injection.** Agent with `failure_modes = [{"name": "PDF garbage", "description": "Skip the row"}]`. Assert result contains `"## Failure modes"` AND `"PDF garbage"` AND `"Skip the row"`.
7. **Defensive on None fields.** Agent with `persona = None`, `urgency_tiers = None`, `failure_modes = None`, `implementation_notes = None`. Assert result does NOT contain `"## Voice"` or `"## Urgency"` or `"## Failure modes"` or `"## Implementation notes"`. Empty sections must be omitted, not written as empty.
8. **lru_cache works.** Call `_cached_josh_spec()` twice, assert the same object identity returned (`is` check).

Mock the DB session if needed. Tests should not need to actually open the spec file twice — `parse_spec()` is allowed to be called for real (the spec is checked in).

---

## Files owned by this stream

- EDIT: `artemis/builders/executor.py` (extract `_build_system_prompt`, expand its contents per Parts A-D)
- NEW: `artemis/builders/tests/test_runtime_injection.py` (Part E)

**Do not touch any other files.** Especially do not touch:
- `artemis/marketing/seeds/marketing_agents.py` (F3 stream)
- `artemis/marketing/josh_spec.py` (F1, sealed seam — just import and call it)
- Any agent blueprint markdown (P1/P4 streams)
- `artemis/pipelines/node_executors/agent_executor.py` (different layer)
- The `tools=None` line at the bottom of run_agent (P2 stream replaces this)

---

## Acceptance criteria (Worker must demonstrate each)

1. `uv run pytest artemis/builders/tests/test_runtime_injection.py -v` shows all 8 tests passing. **Paste the test summary.**
2. **System prompt smoke for a real scout.** Run:
   ```bash
   uv run python -c "
   import asyncio
   from sqlalchemy import select
   from artemis.db import get_session_factory
   from artemis.builders.models import Agent
   from artemis.builders.executor import _build_system_prompt

   async def main():
       SessionLocal = get_session_factory()
       async with SessionLocal() as s:
           agent = (await s.execute(select(Agent).where(Agent.agent_id == 'marketing.scout.regional_news'))).scalar_one()
           prompt = _build_system_prompt(agent, {'pipeline_run_id': 'test-run-123'})
           print(prompt)

   asyncio.run(main())
   " | head -80
   ```
   **Paste the output.** Lead will verify it contains: persona voice, at least 5 Josh-spec reason codes, all 4 state-nuance entries, and the "Context" section with `pipeline_run_id`.
3. **System prompt smoke for a non-scout.** Same as #2 but for `marketing.qualifier.brief_composer`. **Paste the output.** Verify it does NOT contain "Reason codes you may emit" or "State nuances" sections.
4. `./scripts/check.sh` passes (modulo the known pre-existing j5b Jira flake). **Paste the final summary line.**
5. `git diff --stat` showing full-diff insertions ≤ 200 (250 hard stop). **Paste it.**
6. `git log --oneline -1` showing the commit on `worker/f2-runtime-injection`. **Paste it.**

---

## Hard constraints

- LOC cap: 200 (250 hard stop). At cap, commit what's done, ping back, do not push through.
- Do not polish formatting beyond what tests verify.
- Do not change `run_agent`'s signature, the `run_turn` call, or the cost-accounting block.
- Do not modify any file outside the two owned files.
- Local-only git. No `git push`.
- Worker commits on `worker/f2-runtime-injection`. terminal-Lead merges to `lead/j6a-granola-integration` after Lead approves.

---

## Report-back format (Worker pastes this verbatim, filled in)

```
F2 — Runtime Injection report

1. Commit hash:              <git log -1 --format=%H on worker/f2-runtime-injection>
2. Branch:                   worker/f2-runtime-injection
3. Worktree path:            <.claude/worktrees/agent-XXXX>
4. LOC diff stats:           <git diff --stat against fork point>
5. Files changed:            <numbered list>
6. Test pass:                <pytest summary line>
7. Scout prompt smoke:       <output from acceptance #2>
8. Non-scout prompt smoke:   <output from acceptance #3>
9. check.sh:                 <final summary line>
10. Anything surprising:     <free text>
```

---

**End of brief. Sonnet Worker: do not start until you've read this top to bottom. Operating principle: never assume — verify each acceptance assertion by running the actual command, not by inference.**
