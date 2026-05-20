# Schema — Writing Studio Draft Payload

The payload Artemis OS POSTs to Writing Studio's `/drafts` endpoint. This is the external integration contract; **Writing Studio defines this shape**, not us.

**Caller:** 5.3 Writing Studio Adapter
**Endpoint:** `POST {WRITING_STUDIO_URL}/drafts`
**Auth:** Bearer token in `.env` (`WRITING_STUDIO_API_KEY`)

## Schema (JSON request)

```json
{
  "campaign_id": "ws_2026_05_07_pinellas_obc_001",
  "deliverable_type": "email | social | long_form | landing_page",
  "campaign_type": "OBC | biliteracy | dyslexia | general_growth",
  "district": {
    "district_id": "FL_pinellas",
    "name": "Pinellas County Schools",
    "state": "FL",
    "enrollment": 95000
  },
  "target_contact": {
    "name": "...",
    "role": "...",
    "linkedin_url": "..."
  },
  "evidence": {
    "verbatim_snippet": "The District seeks a Reading Intervention solution that provides measurable student growth, with monthly reporting of efficacy data tied to state ELA standards.",
    "speaker_attribution": null,
    "source_url": "https://...",
    "source_date": "2026-05-07"
  },
  "reason_codes": [
    { "code": "RFP_LITERACY_POSTED", "evidence_quote": "..." },
    { "code": "RFP_EFFICACY_LANGUAGE", "evidence_quote": "..." }
  ],
  "urgency": {
    "deadline": "2026-06-15",
    "tier": "hot"
  },
  "asset_bundle": {
    "primary_asset_id": "obc_one_pager_2026q1",
    "supporting_asset_ids": ["case_study_pinellas_neighbor_district_2025"]
  },
  "format_rules_id": "format_email_v3",
  "callback_url": null
}
```

## Schema (JSON response — success)

```json
{
  "draft_id": "ws_draft_abc123",
  "status": "queued | drafting | drafted | approved | rejected",
  "created_at": "2026-05-07T16:25:00Z",
  "expected_completion_at": "2026-05-07T16:27:00Z"
}
```

## Schema (JSON response — error)

```json
{
  "error_code": "missing_field | invalid_format | brand_voice_unavailable | rate_limited | internal_error",
  "error_message": "...",
  "retry_after_seconds": 60
}
```

## Field-level requirements (request)

### campaign_id (required)
Same as `workspace_id`. Writing Studio uses this to thread multiple deliverables under the same campaign.

### deliverable_type (required)
One of four. Writing Studio uses this to apply the right format rules from its own Rules primitive.

### evidence (required)
The single most important field. **Writing Studio is contractually required NOT to invent evidence beyond what's in this object.** The verbatim_snippet anchors the draft.

### asset_bundle (required)
Writing Studio uses these asset IDs to fetch the actual assets from its own Content Registry (note: Writing Studio's Content Registry and Artemis's are the same registry — Marketing owns it; both systems read from it).

### format_rules_id (required)
Tells Writing Studio which of its internal Rules primitives to apply. Map deliverable_type → format_rules_id:
- `email` → `format_email_v3`
- `social` → `format_social_v3`
- `long_form` → `format_long_form_v3`
- `landing_page` → `format_landing_page_v3`

`// TODO: confirm format_rules_id values with Angela / Writing Studio team.`

### callback_url (optional, null in v1)
For future v2 webhook integration. v1 ships with this null; Writing Studio runs its existing approval workflow with no callback to Artemis.

## What Writing Studio owns (NOT in Artemis scope)

From the canvas (screenshot 1, Writing Studio panel):
- The actual draft writing
- Brand voice memory (Trained Marketing Voice)
- Format-specific rules (the Rules primitive)
- Training routes (additive only; Marketing OS doesn't override)
- Draft Library + History + Versioning
- Programmable API to Marketing OS to invoke
- The Approval Drawer / human review loop
- Routing to Olivia, Julia, Angela, or Hubspot for distribution

## What Artemis OS does NOT touch

- Draft content (Writing Studio is source of truth through Gate 2)
- Brand voice memory (lives in Writing Studio)
- Rule content (Writing Studio Rules primitive)
- Edit history / training signal capture (Writing Studio existing loop)

## Retry / failure logic (5.3 Writing Studio Adapter)

- On HTTP 429 → wait `retry_after_seconds`, retry up to 5 times
- On HTTP 5xx → exponential backoff (10s, 30s, 60s, 5min, 30min)
- On 5 failed attempts → set workspace status to `content_preparation_failed`, raise alert
- On 4xx (other than 429) → do NOT retry; brief is malformed; raise alert with response body
