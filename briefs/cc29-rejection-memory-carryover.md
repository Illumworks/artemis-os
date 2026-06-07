# CC29 — Memory carryover for rejected proposals

**Paste-into:** terminal-Lead OR Lead's Agent tool → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc29-rejection-carryover`
**Browser smoke owner:** Lead, post-merge — reject a pending proposal with a reason via the API, verify a memory observation lands in `agent:<id>` (or `skill:<slug>`) primary + `workspace:platform` audit scopes with category=`definition_rejection`.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~100 (new helper + reject-path hook + tests).
**Priority:** MEDIUM — closes the rejection-feedback gap deferred from Bundle B Part C. After CC29, rejection signals (operator declined a Builder proposal) accumulate in memory the same way approvals do via MC1. Builder grounding (M2) can then reason about WHY proposals were rejected, not just which ones were approved.

---

## Why this exists

Bundle B (just merged) added `rejection_reason` + `rejected_at` columns to `definition_proposals` and extended the `/reject` route to accept an optional reason body. Operators can now capture WHY they're rejecting a proposal.

**Bundle B Part C was deferred** to avoid file-overlap with parallel Bundle A — both bundles would have touched `memory_carryover.py`. Now Bundle A is merged + the file is in a known state; CC29 adds the rejection carryover cleanly.

After CC29:
- Every rejected proposal writes a memory observation
- Observation captures the reason (if provided) + the proposed_definition summary + cited runs
- Builder's M2 grounding includes rejected-proposal context → Builder learns "the operator rejected my last proposal for X reason; don't propose that again"
- Stewardship (future SH stream) can surface "this agent has been getting rejected proposals consistently" patterns

---

## Scope

### Part A — New helper in `memory_carryover.py`

Add `write_proposal_rejection_observation` as a sibling to `write_proposal_approval_observation`. Same multi-scope shape, different category + content format.

```python
async def write_proposal_rejection_observation(
    *,
    proposal_id: int,
    kind: str,                          # 'agent' or 'skill' (or other DefinitionProposal kinds)
    target_id: int | None,
    target_slug: str | None,
    proposed_definition: dict[str, Any],
    proposed_by: str,
    citations: dict[str, Any] | None,
    rejection_reason: str | None,
    builder_session_id: int | None = None,
) -> None:
    """Write a memory observation when a proposal is REJECTED.
    
    Multi-scope: agent:<id> or skill:<slug> (primary) + workspace:platform (audit).
    If target_slug is None or kind doesn't have a target, falls back to workspace:platform only.
    
    Evidence: definition_proposal source + cited agent_run sources.
    
    Failure isolation: any exception caught + logged as WARNING. The /reject endpoint
    response succeeds regardless.
    """
    iso_date = _compose_iso_date()
    
    # Extract cited run_ids (same shape as MC1)
    run_ids: list[Any] = []
    if citations and isinstance(citations, dict):
        raw = citations.get("run_ids", [])
        if isinstance(raw, list):
            run_ids = [str(r) for r in raw if isinstance(r, (int, str))]
    
    # Compose content — operator-readable + queryable
    summary_excerpt = _smart_truncate(_extract_summary(proposed_definition), 200)
    reason_part = (
        f"Reason: {_smart_truncate(rejection_reason, 500)}" if rejection_reason
        else "Reason: (none captured)"
    )
    content = (
        f"Operator rejected definition proposal #{proposal_id} for {kind} {target_slug or '(unknown)'} "
        f"on {iso_date}. {reason_part}. "
        f"Citations: runs {', '.join(run_ids) if run_ids else '(none)'}. "
        f"Proposed by: {proposed_by}. "
        f"Summary: {summary_excerpt}."
    )
    
    # Determine scopes
    if kind == "agent" and target_slug:
        primary_scope = Scope(scope_kind="agent", scope_id=target_slug)
    elif kind == "skill" and target_slug:
        primary_scope = Scope(scope_kind="skill", scope_id=target_slug)
    else:
        # Fallback: workspace:platform as the only scope
        primary_scope = Scope(scope_kind="workspace", scope_id="platform")
    
    additional_scopes: list[Scope] = []
    if primary_scope.scope_kind != "workspace" or primary_scope.scope_id != "platform":
        additional_scopes.append(Scope(scope_kind="workspace", scope_id="platform"))
    
    try:
        obs_id = await _multi_scope_observation_write(
            primary_scope=primary_scope,
            additional_scopes=additional_scopes,
            content=content,
            category="definition_rejection",
            confidence_origin="mc_definition_rejection",
            source_quality=SourceQualityHint.operator,
            wing="durable",
        )
        # Evidence: link to the proposal + each cited run
        async with _db.SessionLocal() as session:
            await link_evidence(
                session,
                observation_id=obs_id,
                source_kind="definition_proposal",
                source_id=str(proposal_id),
            )
            for run_id in run_ids:
                await link_evidence(
                    session,
                    observation_id=obs_id,
                    source_kind="agent_run",
                    source_id=run_id,
                )
            await session.commit()
    except Exception:
        logger.warning("CC29 rejection memory write failed for proposal=%s", proposal_id, exc_info=True)
```

**Reuses the existing helpers** from MC1-MC5 work: `_multi_scope_observation_write`, `_compose_iso_date`, `_smart_truncate`, `_extract_summary`. No new shared utilities needed.

### Part B — Hook into the reject path

In `artemis/builder/repository.py:reject_proposal` (Bundle B's update added the `rejection_reason` parameter), after the status flip + commit, call the new helper:

```python
async def reject_proposal(
    session: AsyncSession,
    proposal_id: int,
    rejection_reason: str | None = None,
) -> DefinitionProposalRow:
    # ... existing flip-and-commit logic ...
    
    # CC29: write memory carryover observation (failure-isolated)
    from artemis.builder.memory_carryover import write_proposal_rejection_observation
    await write_proposal_rejection_observation(
        proposal_id=row.id,
        kind=row.kind,
        target_id=row.target_id,
        target_slug=_resolve_target_slug(session, row),  # helper as in MC1
        proposed_definition=row.proposed_definition,
        proposed_by=row.proposed_by,
        citations=row.citations,
        rejection_reason=rejection_reason,
        builder_session_id=row.builder_session_id,
    )
    
    return row
```

The hook lives at the repository layer (not route layer) so any future caller of `reject_proposal` — including future programmatic rejection paths — also triggers the carryover. Same shape as Bundle B's CC22 hook for the approve path.

### Part C — Idempotency

Like MC1, the rejection write is idempotent at the memory-store layer (content_hash uniqueness). Re-rejecting the same proposal (which the engine rejects as `already_rejected`) should NOT duplicate the observation.

Test this: call `reject_proposal` twice on the same proposal_id. Verify exactly 1 observation lands in memory.

### Part D — Tests

`artemis/builder/tests/test_cc29_rejection_carryover.py`:

1. **Reject with reason writes observation.** Fixture: pending proposal. Call `reject_proposal(proposal_id, rejection_reason="hallucinated state")`. Verify (a) `definition_proposals.status='rejected'` + `rejection_reason` set; (b) new observation in `agent:<id>` + `workspace:platform` with category=`definition_rejection`; (c) evidence rows link to proposal + cited runs.
2. **Reject without reason writes observation with "Reason: (none captured)".** Verify content still composes cleanly.
3. **Reject of `kind=skill` writes to `skill:<slug>` primary scope.** Same shape as MC1's skill kind handling.
4. **Reject of proposal with no target_slug falls back to `workspace:platform` only.** Edge case for orphaned proposals.
5. **Idempotency.** Reject same proposal twice (second call should no-op via existing `already_rejected` check). Verify exactly one observation in memory.
6. **Failure isolation.** Monkeypatch `write_observation` to raise. Verify (a) reject status flip still succeeds; (b) /reject endpoint returns successfully; (c) warning logged.
7. **Empty citations handled.** Proposal with no cited run_ids. Observation lands with `Citations: (none)`.
8. **Content shape verified.** Inspect the observation content matches the format spec.

---

## Files owned

- EDIT: `artemis/builder/memory_carryover.py` (add `write_proposal_rejection_observation` helper)
- EDIT: `artemis/builder/repository.py` (call the new helper in `reject_proposal`)
- NEW: `artemis/builder/tests/test_cc29_rejection_carryover.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0051`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_cc29_rejection_carryover.py -v` — all 8 tests pass. **Paste.**
3. **No regressions in existing MC1/MC2-MC5 tests.** `uv run pytest artemis/builder/tests/test_mc1_proposal_to_memory.py artemis/builder/tests/test_mc2_mc5_carryover_bundle.py artemis/builder/tests/test_bundle_b_observability.py -v` — all pass. **Paste.**
4. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation flakes). **Paste.**
5. **Manual smoke (Lead does this post-merge):**
   - There may be no pending proposals; if so, skip live smoke and rely on test #1.
   - If a pending proposal exists: reject it with a reason via `curl POST /api/builder/proposals/{id}/reject` with body `{"reason": "test"}`.
   - Verify memory observation lands with the expected scopes + content.
   - **Paste the observation row.**
6. `git diff --stat` + `git log --oneline -1` on `worker/cc29-rejection-carryover`. **Paste.**

---

## Hard constraints

- **Failure isolation is non-negotiable.** Memory write cannot break the /reject endpoint. Bundle B's reject path stays the durable source-of-truth.
- **No schema changes.** Use existing CC22 columns (`rejection_reason`, `rejected_at`) + memory tables.
- **Multi-scope via MW1 primitives.** Use `_multi_scope_observation_write` shared helper (added in MC2-MC5 bundle).
- **Confidence + source_quality matching MC1's operator-action shape.** `source_quality=SourceQualityHint.operator`, `confidence_origin="mc_definition_rejection"`, `wing="durable"`.
- **Pass cited run_ids as strings** (per CC28's source_id widening). All current callers already do this; verify.
- **Idempotent.** Re-rejecting the same proposal should NOT duplicate the observation.
- **Local-only git.** Worker commits on `worker/cc29-rejection-carryover`; merge after Lead approves.

---

## Coordination

H5 is firing in parallel via terminal-Lead. H5 touches `brief/*`, `pipelines/assistant/*`, possibly `floating_artemis/chat.py`. **Zero file overlap with CC29.**

---

## Report-back format

```
CC29 — Rejection memory carryover report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #5 idempotency, #6 failure isolation)
4. No-regression check on MC1/MC2-MC5/Bundle B tests
5. Manual smoke result (if pending proposal exists)
6. check.sh summary
7. Anything surprising — especially around target_slug resolution for kind=skill or empty-citations edge cases
```

---

**Worker: CC29 closes the rejection feedback loop. Approvals carry over to memory via MC1; rejections will carry over via CC29. After this lands, the Builder's M2 grounding can reason about WHY proposals were rejected, not just which ones were approved — turning rejection signals into structured learning data the platform can leverage going forward.**
