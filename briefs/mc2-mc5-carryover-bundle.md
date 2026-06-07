# MC2-MC5 + MC1 refactor — Memory Carryover bundle (single brief, single Worker)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/mc2-mc5-carryover-bundle`
**Browser smoke owner:** Lead, post-merge — exercise each approval surface (Gate 1 brief approve, skill approve, pipeline gate decide, FA tool approve), verify memory observation lands with multi-scope writes via MW1's join table.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~450 (4 new sibling helpers ~80 LOC each + MC1 refactor ~70 LOC + tests ~200 + minor route edits at 4 call sites).
**Priority:** HIGH — completes the Memory Carryover stream. After this lands, every approval surface in the platform writes evidence-linked memory automatically.

---

## Why this exists

Per `docs/ROADMAP-2026-05-30.md` Stream A: MC1 (definition-proposal approval) is live. Four more approval surfaces need the same carryover pattern. They all touch `artemis/builder/memory_carryover.py` (the helper module MC1 created) — so bundling them into one Worker brief avoids merge-conflict coordination.

ALSO bundled: the MC1 refactor to use MW1's multi-scope join table primitives (`memory_observation_scopes`) instead of writing 2 separate observations as a workaround. With MW1's `add_observation_scope` + extended `write_observation(additional_scopes=...)` signature available, this becomes 1 observation + 2 scope-join rows.

After this brief lands: memory accumulates from 6 production write paths (M1, M5, M3, MC1, MC2-MC5).

---

## Scope

### Part A — MC1 refactor (use MW1 multi-scope primitives)

In `artemis/builder/memory_carryover.py`, the existing `write_proposal_approval_observation` function:

**Current pattern (workaround):** writes 2 observations, one per scope.

**New pattern (using MW1):** write 1 observation with 2 scope-join rows.

```python
async def write_proposal_approval_observation(
    *,
    proposal_id: int,
    kind: str,
    target_id: int | None,
    target_slug: str,
    proposed_definition: dict[str, Any],
    proposed_by: str,
    citations: dict[str, Any] | None,
    builder_session_id: int | None = None,
) -> None:
    import artemis.db as _db
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import (
        get_or_create_scope,
        write_observation,
        link_evidence,
    )
    
    iso_date = datetime.now(UTC).isoformat(timespec="seconds")
    # ... compose content + extract run_ids as before ...
    
    primary_scope = Scope(scope_kind="agent" if kind == "agent" else "skill", scope_id=target_slug)
    audit_scope = Scope(scope_kind="workspace", scope_id="platform")
    
    try:
        async with _db.SessionLocal() as session:
            # Ensure both scopes exist
            await get_or_create_scope(session, primary_scope.scope_kind, primary_scope.scope_id)
            await get_or_create_scope(session, audit_scope.scope_kind, audit_scope.scope_id)
            
            # ONE observation, primary scope, additional_scopes for the audit
            obs = await write_observation(
                session,
                scope=primary_scope,
                additional_scopes=[audit_scope],
                content=content,
                category="definition_approval",
                source_quality=SourceQualityHint.operator,
                confidence_origin="mc_definition_proposal",
                wing="durable",
            )
            
            # Evidence: definition_proposal source + each cited run
            await _link_evidence_raw(session, obs.id, "definition_proposal", str(proposal_id))
            for run_id in run_ids:
                await _link_evidence_raw(session, obs.id, "agent_run", str(run_id))
            
            await session.commit()
    except Exception:
        logger.warning("MC1 memory write failed for proposal=%s", proposal_id, exc_info=True)
```

**Note:** `confidence_origin` is a new field added by MW1. `wing="durable"` per D1 locked decisions. `additional_scopes=[audit_scope]` uses MW1's extended write_observation signature.

**`_link_evidence_raw`** is a private helper at the top of `memory_carryover.py` that does the raw pg_insert for source_kinds NOT in the existing Literal (since `definition_proposal`, `agent_run`, `signal_queue`, etc. aren't yet in `EvidenceSourceKind`). CC23 (banked) will extend the Literal later; for now this helper bypasses the type check.

### Part B — MC2: Signal Gate 1 approval → memory

Hook into `artemis/marketing/routes/signal_queue.py:approve` (line ~292) and the generic `artemis/marketing/routes/approvals.py:approve` route. After successful approval, call:

```python
await write_signal_gate1_approval_observation(
    signal_id=signal_id,
    new_status=row.signal_status,  # "approved" or "rejected_at_gate_1"
    decided_by="operator",  # extend with auth context when multi-user
    decision_payload=body.decisionPayload,
)
```

Helper in `memory_carryover.py`:

```python
async def write_signal_gate1_approval_observation(
    *,
    signal_id: int,
    new_status: str,
    decided_by: str,
    decision_payload: dict[str, Any] | None,
) -> None:
    """MC2: write memory observation when Gate 1 approves/rejects a signal-brief.
    
    Multi-scope: workspace:marketing (primary) + workspace:platform (audit).
    Evidence: signal_queue source + (if approved) the brief_snapshot source.
    """
    # ... compose content ...
    # Content shape: "{decided_by} {decision} signal #{id} at Gate 1 on {iso_date}. 
    #                 Headline: {headline}. Reason codes: {comma_csv}."
    
    primary = Scope(scope_kind="workspace", scope_id="marketing")
    audit = Scope(scope_kind="workspace", scope_id="platform")
    
    async with SessionLocal() as session:
        await get_or_create_scope(session, "workspace", "marketing")
        await get_or_create_scope(session, "workspace", "platform")
        
        obs = await write_observation(
            session, scope=primary, additional_scopes=[audit],
            content=content, category="signal_gate1_decision",
            source_quality=SourceQualityHint.operator,
            confidence_origin="mc_signal_gate1",
            wing="durable",
        )
        await _link_evidence_raw(session, obs.id, "signal_queue", str(signal_id))
        # If there's a brief_snapshot row for this signal, link it too
        await _maybe_link_brief_snapshot(session, obs.id, signal_id)
        
        await session.commit()
```

Content format: `"Operator approved signal #182 at Gate 1 on 2026-05-30T15:20:00Z. Headline: {headline}. Reason codes: POLICY_EDTECH_TIME_LIMIT."` (or `"Operator rejected"` for rejections — both decisions are observable).

### Part C — MC3: Skill promotion → memory

Hook into `artemis/routes/builders/skills.py:approve` (line ~114) after `set_skill_status(slug, "approved")`.

Helper:

```python
async def write_skill_promotion_observation(
    *,
    skill_slug: str,
    skill_name: str,
    description: str | None,
    promoted_by: str,
) -> None:
    """MC3: skill promotion to approved status.
    
    Multi-scope: skill:<slug> (primary) + workspace:platform (audit).
    Evidence: skill source.
    """
    primary = Scope(scope_kind="skill", scope_id=skill_slug)
    audit = Scope(scope_kind="workspace", scope_id="platform")
    
    content = (
        f"{promoted_by} promoted skill '{skill_slug}' to approved status on {iso_date}. "
        f"Name: {skill_name}. "
        f"Description: {description[:200] if description else 'no description'}."
    )
    
    # ... same shape as MC1/MC2 ...
    await _link_evidence_raw(session, obs.id, "skill", skill_slug)
```

### Part D — MC4: Pipeline human-gate decisions → memory

Hook into `artemis/pipelines/node_executors/human_gate_executor.py` after a gate node resolves with `decision` and `decided_by` populated.

Helper:

```python
async def write_pipeline_gate_decision_observation(
    *,
    pipeline_run_id: str,
    node_id: str,
    decision: str,           # "approved" | "rejected"
    decided_by: str,
    decision_payload: dict[str, Any] | None,
) -> None:
    """MC4: pipeline human-gate decision.
    
    Multi-scope: pipeline:<pipeline_id> (primary, derived from run) + 
                 workspace:platform (audit).
    Evidence: pipeline_run + (if available) node decision rows.
    """
    # Look up the pipeline_id from the run
    # ... compose content ...
```

Content format: `"{decided_by} {decision} pipeline {pipeline_id} gate at node {node_id} on {iso_date}. Context: {decision_payload_summary or 'no payload'}."`

### Part E — MC5: FA tool-driven approvals → memory

Hook into `artemis/floating_artemis/tools/marketing.py:94` (where FA's `update_signal` helper qualifies/approves signals on the user's behalf).

Helper:

```python
async def write_fa_marketing_approval_observation(
    *,
    signal_id: int,
    new_status: str,
    fa_session_id: str,
    user_directive: str | None,
) -> None:
    """MC5: FA approved signal on user's behalf during chat.
    
    Multi-scope: agent:floating-artemis (primary — FA is the author) + 
                 workspace:marketing (target) + workspace:platform (audit).
    Evidence: signal_queue + floating_artemis_messages source.
    """
    # 3-scope write (this is the case that uses additional_scopes most fully)
```

Content format: `"FA approved signal #{id} on behalf of {user} during chat session {session_id}. User directive: {user_directive[:200] if user_directive else 'inferred from context'}."`

### Part F — Shared helpers in `memory_carryover.py`

Extract DRY helpers from MC1 + the new sibling functions:

```python
def _compose_iso_date() -> str:
    """Standard ISO timestamp for observation content."""
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _link_evidence_raw(
    session: AsyncSession,
    observation_id: int,
    source_kind: str,
    source_id: str,
) -> None:
    """Raw pg_insert for evidence linking — bypasses the EvidenceSourceKind Literal
    (CC23 banked: extend Literal to include these source kinds).
    
    Used by MC1-MC5 for source_kind values like 'definition_proposal', 'agent_run',
    'signal_queue', 'pipeline_run', 'skill', 'floating_artemis_messages'.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from artemis.memory.models import MemoryEvidence
    
    stmt = (
        pg_insert(MemoryEvidence)
        .values(
            observation_id=observation_id,
            source_kind=source_kind,
            source_id=source_id,
            weight=1.0,
        )
        .on_conflict_do_nothing(constraint="uq_evidence_obs_source")
    )
    await session.execute(stmt)


async def _multi_scope_observation_write(
    *,
    primary_scope: Scope,
    additional_scopes: list[Scope],
    content: str,
    category: str,
    confidence_origin: str,
    source_quality: SourceQualityHint = SourceQualityHint.operator,
    wing: str = "durable",
) -> int:
    """Shared multi-scope write pattern. Returns the new observation_id.
    
    Pattern: open fresh SessionLocal (per M1's pattern), ensure all scopes exist,
    call write_observation with additional_scopes, commit. Failure is caller's
    responsibility to catch.
    """
    import artemis.db as _db
    async with _db.SessionLocal() as session:
        for scope in [primary_scope] + additional_scopes:
            await get_or_create_scope(session, scope.scope_kind, scope.scope_id)
        obs = await write_observation(
            session,
            scope=primary_scope,
            additional_scopes=additional_scopes,
            content=content,
            category=category,
            source_quality=source_quality,
            confidence_origin=confidence_origin,
            wing=wing,
        )
        await session.commit()
        return obs.id
```

This DRY helper makes MC2-MC5 helpers much shorter — each becomes ~20 LOC of content composition + scope setup + a call to `_multi_scope_observation_write` + evidence links.

### Part G — Tests

`artemis/builder/tests/test_mc2_mc5_carryover_bundle.py` (or split per surface if cleaner):

For EACH of MC2/MC3/MC4/MC5:
1. **Approval triggers multi-scope memory write.** Fixture: appropriate row in approval state. Call the route. Verify (a) the existing approval behavior unchanged; (b) ONE observation lands in primary scope; (c) `memory_observation_scopes` join table has 2 (or 3 for MC5) rows; (d) evidence links present.
2. **Failure isolation.** Mock `write_observation` to raise. Verify the approval response succeeds + warning logged + no partial state.
3. **Content shape verified.** The observation content matches the spec format with date + actor + target + summary.

For MC1 refactor:
4. **Existing MC1 behavior preserved.** Approve a definition_proposal. Verify ONE observation lands now (vs 2 pre-refactor), with `memory_observation_scopes` having 2 rows (`agent:<id>` is_primary + `workspace:platform`).

---

## Files owned

- EDIT: `artemis/builder/memory_carryover.py` (refactor MC1 + add MC2-MC5 helpers + shared utilities)
- EDIT: `artemis/marketing/routes/signal_queue.py` (MC2 hook in `approve` route)
- EDIT: `artemis/marketing/routes/approvals.py` (MC2 hook in generic approve)
- EDIT: `artemis/routes/builders/skills.py` (MC3 hook in `approve` route)
- EDIT: `artemis/pipelines/node_executors/human_gate_executor.py` (MC4 hook after gate resolves)
- EDIT: `artemis/floating_artemis/tools/marketing.py` (MC5 hook after FA update_signal call)
- NEW: `artemis/builder/tests/test_mc2_mc5_carryover_bundle.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0048`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_mc2_mc5_carryover_bundle.py -v` — all tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **MC1 refactor regression check:** existing MC1 test suite still passes unmodified. **Paste.**
5. **Manual smokes (Lead does this post-merge):**
   - MC2: approve one of the 7 awaiting-approval Gate 1 signal-briefs via curl → verify obs lands in workspace:marketing + workspace:platform
   - MC3: there may not be a pending skill — skip if no fixture; integration test in #1 covers this
   - MC4: same — would need a pending pipeline gate; integration test sufficient
   - MC5: simulate FA approve via direct invocation or skip
   - **Paste memory observation count delta after each smoke that runs.**
6. `git diff --stat` + `git log --oneline -1` on `worker/mc2-mc5-carryover-bundle`. **Paste.**

---

## Hard constraints

- **Failure isolation is non-negotiable.** Memory writes cannot break approvals.
- **No schema changes.** Migration 0048 unchanged.
- **MC1 refactor must not introduce regressions.** Existing MC1 test suite + existing approval flows continue to work.
- **All carryover writes use SessionLocal pattern.** Per M1's surprise — fresh session per memory op.
- **Multi-scope via MW1 primitives.** Use `additional_scopes` parameter, NOT two separate write_observation calls.
- **confidence_origin field populated correctly per source.** Per D12 decisions: `mc_definition_proposal`, `mc_signal_gate1`, `mc_skill_promotion`, `mc_pipeline_gate`, `mc_fa_marketing`.
- **Wing always `durable`** for all MC carryover writes (per D1).
- **`SourceQualityHint.operator`** for all MC writes (operator-driven = max confidence).
- **Local-only git.** Worker commits on `worker/mc2-mc5-carryover-bundle`; terminal-Lead merges after Lead approves.

---

## Coordination with parallel Worker B (cleanup batch)

Worker B fires in parallel with this brief, touching DIFFERENT files (`artemis/memory/schemas.py`, `artemis/routes/memory.py`, `public/js/features/memory-shell.js`, `artemis/memory/retrieval.py`). Zero expected conflicts.

If CC23 (Worker B's `EvidenceSourceKind` Literal extension) lands BEFORE this brief, the `_link_evidence_raw` helper can be deleted in favor of the standard `link_evidence` (which would then accept the new source kinds). Document as a small follow-up if so.

If this brief lands first, Worker B's CC23 extension naturally includes the source kinds this brief introduces (`definition_proposal`, `pipeline_run`, `skill`, `floating_artemis_messages`).

---

## Report-back format

```
MC2-MC5 + MC1 refactor bundle report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Test pass count (especially failure-isolation + multi-scope-via-join-table for each surface)
4. MC1 refactor regression check (existing MC1 test suite still passes)
5. Manual smoke results — PASTE: memory observation count delta after exercising at least one approval surface (MC2 is easiest — there are 7 Gate 1 signals awaiting approval right now)
6. Shared helper extraction — confirm _multi_scope_observation_write is used by all 5 helpers (MC1 + MC2-MC5)
7. check.sh summary
8. Anything surprising — especially around the MC1 refactor's interaction with the existing approve test suite, or new source_kind values that need CC23 to formalize
```

---

**Worker: this bundle completes the Memory Carryover stream. After it lands, every approval anywhere in the platform leaves a durable, evidence-linked memory trail. The cycle Jon described — "approvals in other sections should carry over" — becomes structurally real. Combined with MW1's multi-scope join table, observations now properly belong to multiple scope contexts (the future foundation for Salesforce/ChurnZero/Gong integration where signals span district + campaign + account simultaneously).**
