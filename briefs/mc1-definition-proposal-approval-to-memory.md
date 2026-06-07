# MC1 — Definition-proposal approval → memory observation (Memory Carryover stream)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/mc1-proposal-approval-to-memory`
**Browser smoke owner:** Lead, post-merge — approve any pending definition_proposal via the existing route, verify a memory observation lands in BOTH `agent:<id>` (or `skill:<slug>`) AND `workspace:platform` scopes with evidence link to the proposal.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~100 (writer + failure isolation + multi-scope writes + tests).
**Priority:** HIGH — first of the Memory Carryover stream (MC1-MC5). Once it lands, every Builder approval (like Proposal #4 we just fired) auto-writes evidence-linked memory. Closes the gap between "approval happened" and "memory remembers."

---

## Why this exists

Per `docs/memory-shell-vision-2026-05-29.md` (LOCKED design): Memory is the observation layer; approvals carry over from domain surfaces. Today (post-Round 2): Floating Artemis writes conversation drawers (M3), agent runs write trajectory observations (M1), qualified signals write genealogy observations (M5). But **approval events don't write anything to memory.**

Empirical evidence: today we fired `engine.commit()` for the first time in production (Proposal #4 approved at 2026-05-29 — brief_composer received its new grounded definition). The agents table got updated. The definition_proposals row was marked approved. **No memory observation captured the decision.** If an operator asks Floating Artemis next month "why did brief_composer's prompt change?", FA has no memory of the approval.

MC1 closes this for definition_proposals. MC2-MC5 close it for other approval surfaces.

---

## Scope

### Part A — Hook into the approve route

In `artemis/builder/routes.py:approve_proposal_route` (line 397) and `artemis/builder/repository.py:approve_proposal` (line 168 where the status flip happens), after the successful approval + `engine.commit()` call, write a memory observation.

Use the same M1 pattern (fresh `SessionLocal()` per memory op + failure isolation). Code shape:

```python
# In approve_proposal_route or right after engine.commit()
from artemis.builder.memory_carryover import write_proposal_approval_observation

# After successful approval:
await write_proposal_approval_observation(
    proposal_id=proposal_id,
    kind=row.kind,                    # 'agent' or 'skill'
    target_id=row.target_id,          # agent_db_id for kind=agent
    target_slug=resolved_slug,         # agent_id slug or skill slug
    proposed_definition=row.proposed_definition,
    proposed_by=row.proposed_by,
    citations=row.citations,
    builder_session_id=row.builder_session_id,
)
```

The helper `write_proposal_approval_observation` lives in a new module `artemis/builder/memory_carryover.py` so MC2-MC5 can drop similar helpers alongside it.

### Part B — Multi-scope write (D6 locked decision)

The observation writes to TWO scopes:

1. **Primary scope** — what the approval was about:
   - `kind="agent"` → `scope_kind="agent"`, `scope_id=<dotted_agent_id>` (e.g. `marketing.qualifier.brief_composer`)
   - `kind="skill"` → `scope_kind="skill"`, `scope_id=<skill_slug>`

2. **Audit-trail scope** — platform-wide approval ledger:
   - `scope_kind="workspace"`, `scope_id="platform"`

**Until MW1 lands** (which adds the multi-scope join table), MC1 writes TWO observation rows — one per scope. **After MW1 lands**, MC1 gets refactored to write ONE observation with TWO scope-join rows. The MC1 brief assumes the pre-MW1 model (two observations) but the helper is designed to be trivially refactored later.

Document this transition explicitly in `memory_carryover.py` docstrings.

### Part C — Content shape

For each observation, compose content as:

```
{actor_label} approved definition proposal #{id} for {kind} {target_slug} on {iso_date}.
Citations: runs {comma-separated run_ids}. Proposed by: {proposed_by}.
Summary: {brief excerpt of proposed_definition.goal or first line of system_prompt}.
```

Where:
- `actor_label`: today always "Operator" since we have single-user dev mode. Future: pull from auth context.
- `iso_date`: timestamp of approval
- `summary`: smart-truncate to ~200 chars

This format is operator-readable and queryable (e.g. "show me all approvals of brief_composer this month" via memory retrieval).

### Part D — Evidence chain

Each observation gets two evidence links:

1. `source_kind="definition_proposal"`, `source_id=<proposal_id>` — the proposal record
2. For each cited run_id: `source_kind="agent_run"`, `source_id=<run_id>` — the runs that motivated the approval

This means a Builder-approved proposal that cited 3 runs writes 1 observation with 4 evidence rows (1 proposal + 3 runs). Per-scope.

**Note on `source_kind="definition_proposal"`:** this is NOT in the existing `EvidenceSourceKind` Literal (CC23 banked). Use the same raw `pg_insert` escape hatch M5 used for `signal_queue` source kind. Document in the report.

### Part E — Confidence (D12 locked)

MC carryover writes: `confidence=1.0`. Operator approved = max confidence.

`source_quality`: use `SourceQualityHint.operator` (or add this enum value if it doesn't exist).

### Part F — Failure isolation

Per M1 pattern: wrap memory write in try/except. Log warning on failure. **The approval response MUST succeed even if memory write fails.** The `definition_proposals.status` flip and `engine.commit()` are the durable source-of-truth; memory is an additive layer.

### Part G — Tests

`artemis/builder/tests/test_mc1_proposal_to_memory.py`:

1. **Approval of `kind="agent"` proposal writes 2 observations (multi-scope).** Fixture: pending proposal with target_id=17 (brief_composer). Approve. Verify (a) `agents.system_prompt` updates (existing behavior); (b) memory_observations gets 2 new rows — one with scope `agent:marketing.qualifier.brief_composer`, one with scope `workspace:platform`; (c) both have evidence rows linking to the proposal + cited runs; (d) confidence=1.0 on both.
2. **Approval of `kind="skill"` proposal writes 2 observations.** Same shape but scope_id is the skill slug.
3. **Failure isolation.** Monkeypatch `write_observation` to raise. Verify (a) `definition_proposals.status` still flips to 'approved'; (b) approval response succeeds; (c) warning logged; (d) no partial state in memory_observations.
4. **Idempotency.** Approve same proposal twice (second call should no-op via the existing `already_approved` check). Verify memory_observations doesn't duplicate.
5. **Empty citations handled.** Proposal with no run_ids. Verify observation lands with proposal evidence only (no run links).
6. **Content shape verified.** Inspect the observation's content string matches Part C format (with actor, proposal id, target slug, citations, summary).
7. **Source kinds verified.** Verify evidence rows use `definition_proposal` and `agent_run` source kinds correctly.

---

## Files owned

- EDIT: `artemis/builder/repository.py:approve_proposal` (call the carryover helper after status flip)
- EDIT: `artemis/builder/routes.py:approve_proposal_route` (alternative call site — pick whichever is cleaner; ideally the repository layer so carryover fires regardless of caller)
- NEW: `artemis/builder/memory_carryover.py` (helper module — designed so MC2-MC5 add sibling helpers)
- NEW: `artemis/builder/tests/test_mc1_proposal_to_memory.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_mc1_proposal_to_memory.py -v` — all 7 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - There may not be a pending proposal in DB. If not, the smoke is the integration test in #1 against a fresh fixture.
   - If pending proposals exist: approve one, verify the 2 observations land.
   - **Paste the SQL output showing observation count delta + scope_kind/scope_id of the 2 new rows.**
5. `git diff --stat` + `git log --oneline -1` on `worker/mc1-proposal-approval-to-memory`. **Paste.**

---

## Hard constraints

- **Failure isolation is non-negotiable.** Memory write CANNOT break the approval. The `definition_proposals.status` flip + `engine.commit()` are durable; memory is additive.
- **Multi-scope = 2 observations today** (pre-MW1). After MW1 lands, refactor to 1 observation + 2 scope-join rows. Helper module designed to support both.
- **No schema changes.** Migration 0047 unchanged.
- **SessionLocal pattern.** Fresh session per memory op per M1's surprise note.
- **`SourceQualityHint.operator` may not exist** in the enum yet. If not, add it as a 1-line addition to `artemis/memory/schemas.py`. Document in report.
- **Don't break the existing approve test suite.** The CC18-era tests covered the approve flow; they should continue passing without modification.
- **Local-only git.** Worker commits on `worker/mc1-proposal-approval-to-memory`; terminal-Lead merges after Lead approves.

---

## Coordination with future MC2-MC5

MC1 establishes the pattern. The `memory_carryover.py` module should be designed so MC2-MC5 add sibling helpers like:

- `write_signal_gate1_approval_observation` (MC2)
- `write_skill_promotion_observation` (MC3)
- `write_pipeline_gate_decision_observation` (MC4)
- `write_fa_marketing_approval_observation` (MC5)

Common helpers (composing actor label, formatting iso date, writing multi-scope) should be in a shared `_helpers.py` or at the top of `memory_carryover.py` so MC2-MC5 don't duplicate.

The Worker should NOT implement MC2-MC5 in this brief. Just structure MC1 so they're cheap to add later.

---

## Report-back format

```
MC1 — Definition-proposal approval → memory observation report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially #1 multi-scope write, #3 failure isolation, #4 idempotency)
4. Manual smoke result — PASTE: observation count delta + scope_kind/scope_id of new rows after approving a real proposal
5. Source-kind workaround — confirm raw pg_insert used for 'definition_proposal' source_kind (since not in Literal — CC23 banked)
6. SourceQualityHint.operator — was it already in the enum or did you add it?
7. check.sh summary
8. Anything surprising — especially around the existing approve_proposal test suite interaction, or transaction boundaries between engine.commit() and the memory write
```

---

**Worker: MC1 is the first carryover write — the moment every Builder-approved definition change starts leaving a trail in memory. Today we fired engine.commit() for the first time in production (Proposal #4) and the platform forgot it the moment the response returned. After MC1, every future approval is durable, evidence-linked memory. MC2-MC5 follow the same shape for other approval surfaces.**
