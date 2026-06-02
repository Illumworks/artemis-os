# Schema — Ruleset

Rulesets are the qualification logic. One ruleset per campaign type (OBC, biliteracy, dyslexia, ...). Versioned, append-only.

**Storage:** `ruleset_versions` table.
**Author:** YAML, written by Ruleset Manager Agent (2.2) via Josh's chat panel.
**Compiler:** 2.3 Ruleset Compiler converts YAML → executable runtime object.

## YAML format

```yaml
ruleset_id: obc
version: 7
description: Outcomes-Based Contract campaign qualification
focus_areas:
  - Prior reading-program churn
  - State-approved screener requirements
  - Vendor accountability signals
  - ESSER cliff pressure

# Hard filters — DETERMINISTIC. Run in Phase 1 of Cross-Reference Agent. No LLM.
hard_filters:
  - id: priority_state
    type: deterministic
    check: signal.geography.state IN territory_config.priority_states
    fail_action: reject_signal

  - id: district_min_enrollment
    type: deterministic
    check: district.enrollment >= 5000
    fail_action: reject_signal

  - id: has_contact_or_stub
    type: deterministic
    check: contact_db_stub.has_contact(signal.geography.district_id)
    fail_action: reject_signal

# Weighted signals — DETERMINISTIC scoring. Phase 2 of Cross-Reference Agent. No LLM per signal.
weighted_signals:
  - id: obc_w1
    description: Prior reading-program churn in past 24 months
    check: |
      EXISTS (SELECT 1 FROM signal_queue
              WHERE signal->'geography'->>'district_id' = :district_id
                AND signal->'reason_codes' @> '[{"code": "SUPERINTENDENT_TRANSITION"}]'
                AND discovered_at > NOW() - INTERVAL '24 months')
    weight: 0.15

  - id: obc_w2
    description: State has state-approved screener
    check: signal_has_reason_code('STATE_DYSLEXIA_MANDATE')
    weight: 0.10

  - id: obc_w3
    description: SOR-aligned curriculum in last 3 years
    check: signal_has_reason_code('BOARD_LITERACY_CURRICULUM_REVIEW')
    weight: 0.20

  - id: obc_w4
    description: News reference to ESSER cliff
    check: signal_has_reason_code('ESSER_CLIFF_REFERENCE')
    weight: 0.10
    per_mention: true
    cap: 0.30

  - id: obc_w5
    description: RFP with explicit efficacy language
    check: signal_has_reason_code('RFP_EFFICACY_LANGUAGE')
    weight: 0.25

  - id: obc_w6
    description: Board approved OBC-structured RFP
    check: signal_has_reason_code('BOARD_OBC_RFP_APPROVED')
    weight: 0.30

# Qualitative rubrics — LLM-EVALUATED. Phase 2 of Cross-Reference Agent. One LLM call per rubric.
qualitative_rubrics:
  - id: obc_q1
    description: Board minutes show frustration with current vendor accountability
    prompt: |
      You are evaluating one qualitative rubric against one signal's evidence.
      Apply strictly — do not stretch interpretation.
      False positives cost more than false negatives.

      Rubric: "Board minutes show frustration with current vendor accountability"

      Evidence:
      {{signal.source.verbatim_snippet}}

      Speaker: {{signal.source.speaker_attribution}}

      Return JSON: { "applies": true|false, "confidence": 0.0-1.0, "reasoning": "..." }
    fires_when: applies == true AND confidence > 0.6
    weight_when_fires: 0.20

# Computed fit_score (Phase 2 output)
fit_score_formula: |
  fit_score = normalize_0_1(
    sum(weight for each weighted_signal that fired)
    + sum(weight_when_fires for each qualitative_rubric that fired)
  ) / count(qualitative_rubrics evaluated)

# Routing rules (Phase 3)
routing:
  primary_threshold: 0.7
  secondary_threshold: 0.6
  redundancy_check:
    - if primary is OBC, secondary cannot be general_growth (redundant)
    - if primary is biliteracy, secondary cannot be OBC (different campaigns, but acceptable)

# Metadata
author: josh@amiralearning.com
approved_by: josh@amiralearning.com
created_at: 2026-04-12T10:14:00Z
notes: |
  Light seed for MVP. Mostly hard filters + weighted signals (low risk, deterministic).
  1-2 qualitative rubrics per campaign type to seed the pattern.
```

## Compiled output (what 2.3 Ruleset Compiler produces)

```json
{
  "ruleset_id": "obc",
  "version": 7,
  "compiled_at": "2026-04-12T10:14:00Z",
  "hard_filters": [
    { "id": "priority_state", "type": "deterministic", "sql": "...", "fail_action": "reject_signal" }
  ],
  "weighted_signals": [
    { "id": "obc_w1", "weight": 0.15, "check_sql": "...", "per_mention": false, "cap": null }
  ],
  "qualitative_rubrics": [
    { "id": "obc_q1", "prompt_template": "...", "weight_when_fires": 0.20, "applies_threshold": 0.6 }
  ],
  "fit_score_formula_compiled": "...",
  "routing": { "primary_threshold": 0.7, "secondary_threshold": 0.6 }
}
```

## Validation (compiler-enforced)

- Every `check` SQL must compile and reference only known columns (compiler runs `EXPLAIN`).
- Every `signal_has_reason_code('...')` argument must exist in `reason_code_registry`.
- Sum of all positive weights cannot exceed 2.0 (sanity check; normalization handles the rest).
- Each rubric prompt must compile as Jinja2 template.
- `routing.primary_threshold >= routing.secondary_threshold`.

If any validation fails, the new ruleset version is **rejected** with an alert to the Ruleset Manager Agent. The prior active version remains live.

## Versioning rule

A ruleset is identified by `(ruleset_id, version)`. New writes always increment version. The `is_active` column flips:

1. New version compiles cleanly → `is_active = false` on insert.
2. Josh approves → transaction: set old version `is_active = false`, set new version `is_active = true`.
3. Only one active version per ruleset_id at any time.

In-flight campaigns continue under their original version (captured in `campaign_workspace.metadata.ruleset_version`).

## Light seed for MVP — what to ship

From the canvas:
- 5–10 rules per campaign type at launch
- Mostly hard filters + weighted signals (low risk, deterministic)
- 1–2 qualitative rubrics per campaign type to seed the LLM pattern
- All seeded rules require Josh's review before MVP-3 ships
- Future rubrics emerge organically from Josh's chat panel use
