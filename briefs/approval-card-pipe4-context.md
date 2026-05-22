# Approval Card PIPE4 Context — Surface Real Signal/Brief Data

**Owner:** Sonnet Worker via Agent({isolation: "worktree"})
**Branch:** `worker/approval-card-pipe4-context`
**LOC budget:** ~250 (cap 320)
**Depends on:** PIPE4 merged.

## Why

Approval Queue UI renders 6 blank cards (only "Approval required / Requested: — / Approve / Reject") because:
- PIPE4 creates approval rows with `subject_id = <run_id>:<node_id>` (composite key)
- Existing Approval Queue render path doesn't unpack this composite OR fetch pipeline_run context
- No signal evidence, district, reason codes, brief preview surface

Two complementary fixes:
1. **PIPE4 gate handler** populates the approval row with rendering context at creation time
2. **Approval Queue UI** detects PIPE4-style approvals and renders the context

## Scope

### Backend — gate handler enriches approval row

Update `artemis/pipelines/node_executors/human_gate_executor.py` to write rendering context into the approval row:

```python
approval = Approval(
    kind=config["approval_kind"],
    subject_id=f"{pipeline_run_id}:{node_id}",
    approvers=config["approvers"],
    timeout_at=now + timedelta(hours=config["timeout_hours"]),
    metadata={
        "pipeline_run_id": str(pipeline_run_id),
        "pipeline_name": pipeline.name,
        "node_id": node_id,
        "node_label": node.label,
        "context": {
            # For signal_brief gates:
            "brief_preview": brief_data.get("preview") if brief_data else None,
            "signal_count": len(qualified_signals) if qualified_signals else 0,
            "reason_codes": list({sc.code for s in qualified_signals for sc in s.reason_codes}) if qualified_signals else [],
            "districts": list({s.geography.district for s in qualified_signals}) if qualified_signals else [],
            "evidence_quote": qualified_signals[0].source.verbatim_snippet if qualified_signals else None,
            # For content_draft gates:
            "draft_summary": draft_data.get("summary") if draft_data else None,
            "deliverable_types": [...] if applicable,
        }
    }
)
```

When there's NO real signal data (current state — empty queue), the context fields are null but the approval still surfaces with the pipeline/node identifiers so the UI knows what to show.

### Frontend — Approval Queue card render

Update `public/js/features/approvals.js` (or wherever Approval Queue lives):

When approval has `metadata.pipeline_run_id`:
- Header: `{pipeline_name} — {node_label}` instead of generic "Approval required"
- Body sections:
  - **Signals:** N qualified | districts: [...] | reason codes: [...]
  - **Evidence:** verbatim_snippet (if present)
  - **Brief preview:** truncated text (if present)
  - **Empty state:** "No signals qualified this run" (when context.signal_count is 0)
- Footer: same Approve / Reject buttons + "View pipeline run →" link

For non-PIPE4 approvals (existing flow): render the existing card unchanged.

### Migration consideration

Existing 6 blank approval rows in DB don't have metadata.context populated (they were created before this brief). Two options:
- **(a)** Leave them blank; only new approvals get context. Existing ones still render with the new "empty state" message.
- **(b)** Backfill via a small script that fetches each approval's pipeline_run_id from subject_id, looks up the run + node, and writes context.

My rec: **(a)**. Backfill is unreliable for 6 stale rows; just clean them up via the new "no signals" empty state.

### Tests

- Gate handler writes context to approval.metadata
- Approval Queue renders PIPE4 card with context fields visible
- Approval Queue with empty context renders the "No signals qualified this run" empty state
- Existing non-PIPE4 approvals render unchanged

## Out of scope

- Real-time updates of approval card content (e.g., context updates after approval). The approval is a snapshot at gate-fire time.
- Slack DM rendering (separate Slack integration brief)
- Bulk-approve / bulk-reject

## Files expected

| File | LOC |
|---|---|
| `artemis/pipelines/node_executors/human_gate_executor.py` | ~50 delta (write context) |
| `public/js/features/approvals.js` (or wherever approval list lives) | ~80 delta (PIPE4 render branch) |
| `public/css/features/approvals.css` (or marketing-os.css) | ~40 delta (PIPE4 card styling) |
| `artemis/marketing/routes/approvals.py` | ~20 delta (ensure metadata returns in API) |
| Tests | ~60 |

**Total: ~250 LOC.** Cap 320.

## Invariants

- Existing approvals (non-PIPE4) render unchanged
- approval.metadata is JSONB; if it doesn't have `pipeline_run_id`, UI falls back to existing render path
- node --check on JS
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, screenshot of populated PIPE4 approval card + empty-context PIPE4 card + non-PIPE4 approval card unchanged, test pass count, branch + worktree path.
