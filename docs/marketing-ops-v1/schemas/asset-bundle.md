# Schema — Asset Bundle

Output of 5.2 Asset Selector Agent. Picks ONE bundle for the whole campaign so all Writing Studio drafts reference the same supporting evidence.

**Storage:** embedded in `campaign_workspaces.asset_bundle` (JSONB).

## Schema (JSON)

```json
{
  "asset_bundle_id": "bundle_obc_efficacy_v3",
  "primary": {
    "asset_id": "obc_one_pager_2026q1",
    "format": "one_pager",
    "url": "https://...",
    "title": "Outcomes-Based Contract: Performance Guarantees",
    "campaign_types": ["OBC"],
    "tags": ["OBC", "RFP_EFFICACY_LANGUAGE", "efficacy_data"],
    "freshness_date": "2026-03-15",
    "approval_status": "approved"
  },
  "supporting": [
    {
      "asset_id": "case_study_pinellas_neighbor_district_2025",
      "format": "case_study",
      "url": "https://...",
      "title": "...",
      "campaign_types": ["OBC", "general_growth"],
      "tags": ["case_study", "florida_district", "growth_outcomes"],
      "freshness_date": "2025-11-02",
      "approval_status": "approved"
    }
  ],
  "scoring": {
    "primary_match_score": 0.91,
    "use_case_match": 0.85,
    "tag_overlap": 0.78,
    "recent_freshness_bonus": 0.10,
    "final_score": 0.91
  },
  "fallback_used": false,
  "selection_reasoning": "One-pager directly addresses OBC efficacy language in RFP scope. Florida case study reinforces regional relevance."
}
```

## Field-level requirements

### primary (required, one asset)
The single most relevant asset for the campaign. Must have an `asset_id` from the Content Registry.

### supporting (optional, max 3)
Additional assets that reinforce the primary. Writing Studio may or may not reference them per deliverable. Common pattern: case study + one-pager + research summary.

### scoring (required)
Numeric breakdown of why this bundle was chosen. Surfaces in the workspace for human inspection. See `agents/content/5.2-asset-selector-agent.md` for the scoring math.

### fallback_used (required, boolean)
True if the agent could not find a direct asset match AND used Writing Studio's training fallback (long-form regeneration from Content Registry inventory). When true, Asset Selector confidence is low and a flag should surface to human reviewers.

### selection_reasoning (required)
1–2 sentences in Asset Selector's own words. Used for auditability when humans review why a particular asset bundle was chosen.

## Validation

- `primary.asset_id` must exist in Content Registry (table TBD; see `services/contact-db-stub.md` placeholder note — the Content Registry is similar in that it's referenced but not fully built in v1).
- `primary.approval_status` must be `approved` (never reference draft or rejected assets).
- All `supporting` assets must have `approval_status: approved`.

## v1 simplification

The full Content Registry schema is on the canvas (screenshot 1, bottom panel) but not fully implemented for v1. For the build:

- Stub the Content Registry as a flat JSON file: `rulesets/content_registry_stub.json`
- Populate with 5–10 placeholder assets, enough to test the selection logic end-to-end
- Real Content Registry integration is a Phase 2 task

**Mark this with a `// TODO: integrate real Content Registry` comment in the implementation.**
