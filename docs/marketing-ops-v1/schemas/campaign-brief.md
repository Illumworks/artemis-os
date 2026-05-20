# Schema — Campaign Brief

Output of 5.1 Campaign Brief Assembler. **Immutable for the campaign's lifetime.** All Writing Studio invocations reference the same brief.

**Storage:** embedded in `campaign_workspaces.campaign_brief` (JSONB).

## Schema (JSON)

```json
{
  "campaign_id": "ws_2026_05_07_pinellas_obc_001",
  "campaign_type": "OBC",
  "variant": "primary",
  "district": {
    "district_id": "FL_pinellas",
    "name": "Pinellas County Schools",
    "state": "FL",
    "enrollment": 95000,
    "grade_range": "PK-12",
    "superintendent": "..."
  },
  "target_contacts": [
    { "name": "...", "role": "...", "linkedin_url": "...", "email": null }
  ],
  "originating_signal": {
    "signal_id": "sig_2026_05_07_pinellas_rfp_001",
    "discovered_by": "procurement_scout",
    "verbatim_snippet": "The District seeks a Reading Intervention solution that provides measurable student growth, with monthly reporting of efficacy data tied to state ELA standards.",
    "speaker_attribution": null,
    "source_url": "https://...",
    "source_date": "2026-05-07"
  },
  "campaign_history": [
    { "name": "Q4 2025 OBC outreach", "outcome": "no_response" }
  ],
  "per_contact_snapshots": [],
  "reason_codes": [
    { "code": "RFP_LITERACY_POSTED", "evidence_quote": "...", "confidence": 0.95 },
    { "code": "RFP_EFFICACY_LANGUAGE", "evidence_quote": "...", "confidence": 0.90 }
  ],
  "urgency": {
    "deadline": "2026-06-15",
    "tier": "hot"
  },
  "validation": {
    "passed": true,
    "checks": {
      "evidence_present": true,
      "reason_codes_in_registry": true,
      "campaign_type_approved": true
    }
  },
  "assembled_at": "2026-05-07T16:09:00Z",
  "assembled_by": "campaign_brief_assembler_v1"
}
```

## Field-level requirements

### campaign_id (required)
Same as `workspace_id`. Use the workspace ID; do not generate a new one.

### campaign_type, variant
`campaign_type` is from the approved brief. `variant` is `primary` for v1 (multi-variant outreach is out of scope).

### district (required, fully populated)
Joined from `districts` table at assembly time. All fields required.

### target_contacts (array)
Populated from `contact_hints` on the originating signal. For v1, contacts are not enriched — `email` will typically be null. That's expected. Writing Studio will use what's present.

### originating_signal (required)
Verbatim copy of evidence from the underlying signal. **Never paraphrased.** This is the field Writing Studio uses to anchor the outreach in real evidence.

### campaign_history (optional)
Pulled from prior `campaign_workspaces` rows for the same `district_id`. Bounded to last 12 months.

### per_contact_snapshots
Reserved for future Contact team work. For v1, always empty array `[]`.

### reason_codes (required, non-empty)
Copied from the originating signal's `reason_codes` array.

### urgency (required)
Copied from the originating signal's `urgency`.

### validation (required, see below)
The deterministic input-validation check 5.1 runs before declaring the brief complete. If any check fails, `validation.passed = false` and the brief does NOT proceed to Asset Selector. Signal returns to `content_preparation_failed` and surfaces an error to the human.

## Validation checks (run by 5.1)

These are NOT compliance / brand checks. They are input hygiene to catch malformed briefs before they hit Writing Studio.

1. **evidence_present** — `originating_signal.verbatim_snippet` is non-empty.
2. **reason_codes_in_registry** — every entry in `reason_codes` has a `code` that exists in `reason_code_registry` (not deprecated).
3. **campaign_type_approved** — `campaign_type` is one of: `OBC`, `biliteracy`, `dyslexia`, `general_growth`.

If any check fails, write the failure to `campaign_workspaces.metadata` and set status to `content_preparation_failed`. Alert raised.

## Immutability rule

Once `campaign_brief` is written to `campaign_workspaces`, **no agent may modify it**. If a human discovers an error post-assembly, the entire workspace must be rejected and re-created from a re-approved brief at Gate 1. This prevents downstream drift between drafts in flight and the underlying signal.
