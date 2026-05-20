# Schema — Campaign Workspace

The artifact created when a Signal Brief is approved at Gate 1. The container the Content team operates on.

**Storage:** `campaign_workspaces` table.

## Schema (JSON)

```json
{
  "workspace_id": "ws_2026_05_07_pinellas_obc_001",
  "brief_id": "brief_2026_05_07_001",
  "signal_id": "sig_2026_05_07_pinellas_rfp_001",
  "campaign_type": "OBC",
  "district_id": "FL_pinellas",
  "approved_by": "josh@amiralearning.com",
  "approved_at": "2026-05-07T16:08:00Z",

  "campaign_brief": { "...see schemas/campaign-brief.md..." },
  "asset_bundle": { "...see schemas/asset-bundle.md..." },
  "writing_studio_drafts": [
    { "deliverable_type": "email", "draft_id": "ws_draft_abc123", "submitted_at": "2026-05-07T16:25:00Z" }
  ],
  "status": "in_content_preparation"
}
```

## Lifecycle

```
pending_content              ← created on Gate 1 approval
   ↓
in_content_preparation       ← 5.1 / 5.2 / 5.3 are running
   ↓
sent_to_writing_studio       ← 5.3 successfully POSTed all drafts
   OR
content_preparation_failed   ← something errored; see error logs
```

## campaign_brief sub-object

Populated by 5.1 Campaign Brief Assembler. See `schemas/campaign-brief.md` for the full sub-schema.

## asset_bundle sub-object

Populated by 5.2 Asset Selector Agent. See `schemas/asset-bundle.md` for the full sub-schema.

## writing_studio_drafts array

Populated by 5.3 Writing Studio Adapter as each deliverable is submitted. One entry per `(deliverable_type)`. For v1, possible deliverable types are: `email`, `social`, `long_form`, `landing_page`. 5.3 is responsible for setting `submitted_at` on success.

## Validation

- `district_id` must exist in `districts` table
- `campaign_type` must be one of: `OBC`, `biliteracy`, `dyslexia`, `general_growth` (extend as new rulesets ship)
- `approved_by` must be a valid email
- `status` must follow lifecycle order (no jumping from `pending_content` to `sent_to_writing_studio`)
