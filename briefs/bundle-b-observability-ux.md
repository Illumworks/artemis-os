# Bundle B — Observability + UX (CC21 + CC22)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/bundle-b-observability-ux`
**Browser smoke owner:** Lead, post-merge — re-run a Builder session, verify `tool_invocations` captures `builder_session_id` for the propose tool. Reject a proposal with a reason, verify the reason stored.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~200 (2 migrations + 2 column additions + invocation logging update + reject route extension + Inbox UI hook + tests).
**Priority:** HIGH — closes observability + feedback-loop gaps surfaced during CC19/CC20 work and the consumer-side audit.

---

## Why this exists

**CC21:** `tool_invocations` table requires `agent_run_id` NOT NULL — Builder tools (CC19's `builder_propose`, etc.) have `builder_session_id` instead. Empirical: smoke session 12 produced 2 proposals (meaning `builder_propose` fired twice + `builder_read_recent_runs` fired) but `tool_invocations` has 0 builder_* rows. Builder tool calls are silently un-logged today. Banked from CC19 follow-up.

**CC22:** `definition_proposals` has no `rejection_reason` column. When Lead rejected proposals 1, 2, 3 yesterday, the WHY was lost (hallucinated state names → rejected, but the reason isn't queryable). The Builder can't learn from rejection patterns if rejection reasons aren't captured. Banked from consumer-side audit gap #3.

Bundle these because: both are surgical migrations + small column additions + route hook updates. Different tables, no overlap.

---

## Scope

### Part A — CC21: `tool_invocations.builder_session_id`

**Files:**
- `alembic/versions/0050_tool_invocations_builder_session_id.py` (NEW migration)
- `artemis/tools/models.py` (extend `ToolInvocation` model)
- `artemis/tools/mcp_server.py` (capture builder_session_id when logging)
- `artemis/builder/agent_builder.py` (pass builder_session_id to invocation logger)

**Migration:**
```python
def upgrade():
    op.add_column(
        "tool_invocations",
        sa.Column("builder_session_id", sa.BigInteger(), nullable=True),
    )
    op.alter_column("tool_invocations", "agent_run_id", nullable=True)
    
    # CHECK constraint: exactly one scope is set
    op.create_check_constraint(
        "ck_tool_invocations_scope",
        "tool_invocations",
        "(agent_run_id IS NOT NULL AND builder_session_id IS NULL) OR "
        "(agent_run_id IS NULL AND builder_session_id IS NOT NULL)",
    )
    op.create_index(
        "idx_tool_invocations_builder_session",
        "tool_invocations",
        ["builder_session_id"],
        postgresql_where=sa.text("builder_session_id IS NOT NULL"),
    )

def downgrade():
    op.drop_constraint("ck_tool_invocations_scope", "tool_invocations")
    op.drop_index("idx_tool_invocations_builder_session")
    op.drop_column("tool_invocations", "builder_session_id")
    op.alter_column("tool_invocations", "agent_run_id", nullable=False)
```

**Model update (`tools/models.py`):**
```python
class ToolInvocation(Base):
    ...
    agent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # was nullable=False
    builder_session_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # ... existing fields ...
```

**Invocation logging update (`tools/mcp_server.py`):**

Find the path that writes `ToolInvocation` rows after each tool call (CC17's work). Currently writes with `agent_run_id` populated from the CLI args. For Builder tools, the CLI args include `builder_session_id` instead (per CC19's mutual-exclusion pattern). Update the write to:

```python
# Extract scope from CLI args / context
ti = ToolInvocation(
    agent_run_id=ctx.run_id if ctx.run_id else None,
    builder_session_id=ctx.builder_session_id if ctx.builder_session_id else None,
    pipeline_run_id=ctx.pipeline_run_id,
    tool_name=tool_name,
    args_summary=args_summary,
    result_preview=result_preview,
    success=success,
)
```

(Use whatever the actual context object is — verify against the CC17 implementation.)

**Builder integration (`builder/agent_builder.py`):**

The `_propose`, `_read_recent_runs`, `_read_existing`, etc. closures already have `builder_session_id` in scope. After CC21, they should propagate this to the tool invocation logger so the rows land with builder_session_id populated.

If the in-process closures aren't currently logging tool invocations (only the MCP subprocess path was), add the logging here too. The shared logger function should accept either scope and write the row correctly.

### Part B — CC22: `definition_proposals.rejection_reason`

**Files:**
- `alembic/versions/0051_definition_proposals_rejection_reason.py` (NEW migration)
- `artemis/builders/models.py` (extend `DefinitionProposal` model)
- `artemis/builders/schemas.py` (extend rejection request schema)
- `artemis/builders/repository.py` (extend reject function signature)
- `artemis/builder/routes.py` (extend `/reject` endpoint to accept reason body)
- `public/js/features/agents.js` (Inbox UI: prompt for rejection reason when operator clicks Reject)

**Migration:**
```python
def upgrade():
    op.add_column(
        "definition_proposals",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "definition_proposals",
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

def downgrade():
    op.drop_column("definition_proposals", "rejected_at")
    op.drop_column("definition_proposals", "rejection_reason")
```

**Model + schema updates:** add `rejection_reason: str | None` + `rejected_at: datetime | None` to the model and the read schema. Request schema for `/reject` endpoint accepts `reason: str | None` optionally.

**Reject route update (`artemis/builder/routes.py:reject_proposal_route`):**

```python
class ProposalRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal_route(
    proposal_id: int,
    body: ProposalRejectRequest | None = None,  # body is optional for backward-compat
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ...
    row = await reject_proposal(
        session, 
        proposal_id, 
        rejection_reason=body.reason if body else None,
    )
    ...
```

**Repository function update:**
```python
async def reject_proposal(
    session: AsyncSession,
    proposal_id: int,
    rejection_reason: str | None = None,
) -> DefinitionProposalRow:
    row.status = "rejected"
    row.rejection_reason = rejection_reason
    row.rejected_at = datetime.now(UTC)
    ...
```

**Inbox UI hook (`public/js/features/agents.js`):**

Find the Inbox row's "Reject" button click handler (added during Proposals Inbox work). Currently just POSTs to the reject endpoint with no body. After CC22:

1. Operator clicks "Reject"
2. Prompt opens (small modal or inline textarea): "Optional: why are you rejecting this? (Helps the Builder learn.)"
3. Operator types or clicks "Reject without reason"
4. POST request body: `{"reason": "<text>"}` or empty
5. Inbox row updates to show "Rejected" with the reason as a tooltip/expandable

**Don't require a reason** — keep the existing one-click reject path as the default. The reason is additive: if provided, it's captured; if not, the proposal still rejects cleanly.

### Part C — Memory carryover for rejections (small follow-up, OPTIONAL)

Currently MC1 writes a memory observation when a proposal is APPROVED. Rejections don't carry over.

**Decision for this brief:** ALSO write a memory observation when a proposal is rejected. Pattern matches MC1's approval observation but with category=`definition_rejection` and content noting the rejection reason. The audit trail (workspace:platform) captures rejection signals so the Builder can learn from them (future SH stewardship would surface "your Builder gets rejected often for X reason — consider tightening").

If this scope-creeps Bundle B, skip this and bank as CC29. Worker's call — flag in report.

### Part D — Tests

`artemis/builder/tests/test_bundle_b_observability.py`:

1. **CC21 — Builder tool call lands in `tool_invocations` with `builder_session_id` populated.** Mock a Builder session firing `builder_propose`. Verify row in `tool_invocations` with `builder_session_id` set + `agent_run_id` NULL + correct `tool_name`.
2. **CC21 — Pipeline tool call still lands with `agent_run_id` populated.** Regression check — existing CC17 behavior unchanged for agent_run-scoped tools.
3. **CC21 — Mutual-exclusion CHECK constraint fires.** Try to insert with BOTH scope fields set or NEITHER — verify DB rejects.
4. **CC22 — Reject with reason captures it.** POST `/api/builder/proposals/{id}/reject` with `{"reason": "hallucinated state name"}`. Verify row has rejection_reason + rejected_at populated.
5. **CC22 — Reject without body backward-compat.** POST with no body. Verify proposal rejected normally + rejection_reason=NULL + rejected_at populated.
6. **CC22 — Inbox UI hook (JS unit test or Python integration).** Verify the reject button click flow includes the reason prompt path.
7. **(Optional Part C) — Rejection writes memory observation.** If implemented, verify carryover observation lands with category=definition_rejection.

---

## Files owned

- NEW: `alembic/versions/0050_tool_invocations_builder_session_id.py`
- NEW: `alembic/versions/0051_definition_proposals_rejection_reason.py`
- EDIT: `artemis/tools/models.py` (CC21)
- EDIT: `artemis/tools/mcp_server.py` (CC21 — invocation logging update)
- EDIT: `artemis/builder/agent_builder.py` (CC21 — in-process tool logging)
- EDIT: `artemis/builders/models.py` (CC22)
- EDIT: `artemis/builders/schemas.py` (CC22)
- EDIT: `artemis/builders/repository.py` (CC22)
- EDIT: `artemis/builder/routes.py` (CC22)
- EDIT: `public/js/features/agents.js` (CC22 — reject UI hook)
- NEW: `artemis/builder/tests/test_bundle_b_observability.py`
- POSSIBLE: `artemis/builder/memory_carryover.py` (Part C — rejection carryover, OPTIONAL)

---

## Acceptance criteria

1. `uv run alembic upgrade head` shows `0051_definition_proposals_rejection_reason`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_bundle_b_observability.py -v` — tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **CC21 smoke (Lead does this post-merge):** trigger a Builder session, send a propose-prompting message, verify new `tool_invocations` rows with `builder_session_id` populated. **Paste SELECT showing the new rows.**
5. **CC22 smoke (Lead does this post-merge):** reject a pending proposal via UI with a reason, verify the rejection_reason persists. **Paste SELECT.**
6. **Part C decision:** confirm in report whether you implemented rejection-carryover memory write or banked as CC29.
7. `git diff --stat` + `git log --oneline -1` on `worker/bundle-b-observability-ux`. **Paste.**

---

## Hard constraints

- **Backward-compat on `tool_invocations`.** Existing rows have `agent_run_id` set + `builder_session_id` NULL. New rows for Builder tools have `agent_run_id` NULL + `builder_session_id` set. CHECK constraint enforces exactly-one.
- **Backward-compat on `/reject` endpoint.** Existing callers (current Proposals Inbox JS) post without a body — must still work. The `reason` is purely additive.
- **`rejected_at` is new metadata.** Existing rejected proposals (1, 2, 3 from yesterday) get `NULL` for rejected_at. Don't backfill.
- **No regression on existing approval flow** (MC1 still fires correctly post-CC22 changes).
- **Inbox UI's reject path stays cheap.** Don't add modal-heavy UX. A small inline textarea or a one-line `prompt()` dialog is fine. Operators should still be able to one-click-reject for low-friction.
- **Migrations claim numbers 0050 and 0051.** Bundle A claims 0049. If A lands first, B's migrations stay 0050+0051. If B lands first, A rebases to 0050. Terminal-Lead handles merge ordering.
- **Local-only git.** Worker commits on `worker/bundle-b-observability-ux`; terminal-Lead merges after Lead approves.

---

## Coordination with parallel Bundle A (CC27+CC28)

Bundle A touches `memory/schemas.py`, `memory/models.py`, `memory_evidence` (migration 0049), `memory_carryover.py`, `signal_queue_ops.py`, `marketing/repository.py`.

Bundle B touches `tools/models.py`, `tools/mcp_server.py`, `builder/agent_builder.py`, `builders/models.py`, `builders/schemas.py`, `builders/repository.py`, `builder/routes.py`, `public/js/features/agents.js`.

**File overlap with Bundle A:**
- `memory_carryover.py` — Bundle A modifies (remove _source_id_to_int). Bundle B's optional Part C also touches it. If Part C is implemented, coordinate ordering. **Simplest: Bundle B skips Part C (banks as CC29).** Then zero overlap with A.

**Migration ordering:** A=0049, B=0050+0051. No collision unless something else lands first.

---

## Report-back format

```
Bundle B — Observability + UX report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Migrations applied (0050, 0051)
4. Test pass count (especially CC21 mutual-exclusion + CC22 reason persistence)
5. CC21 smoke result — PASTE SELECT showing builder_session_id populated for Builder tool calls
6. CC22 smoke result — PASTE SELECT showing rejection_reason persists
7. Part C decision — implemented carryover for rejections, or banked as CC29?
8. UI implementation note — inline textarea or prompt() dialog or modal? What's the operator UX?
9. check.sh summary
10. Anything surprising — especially around the existing CC17 invocation logging code (whether it's already scope-aware or needs more refactoring)
```

---

**Worker: Bundle B closes two observability and UX gaps that accumulated during the rapid CC10-MC1 stream. After this lands, Builder tool calls are queryable for analytics + future Stewardship layer; rejection reasons feed back to operators (and eventually to the Builder LLM for learning). Both are small but high-leverage for the platform's self-correction capability.**
