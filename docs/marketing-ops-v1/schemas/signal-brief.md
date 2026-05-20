# Schema — Signal Brief

The output of 2.4 Brief Composer Agent. The Josh-readable inbox card that Gate 1 (Signals Inbox) renders.

**Storage:** `signal_briefs` table.

## Schema (JSON)

```json
{
  "brief_id": "brief_2026_05_07_001",
  "signal_id": "sig_2026_05_07_pinellas_rfp_001",
  "headline": "Pinellas County (FL) — Reading Intervention RFP with measurable-growth language",
  "why_flagged": "RFP scope explicitly requires monthly efficacy reporting tied to state ELA standards — strong OBC fit signal.",
  "evidence": "The District seeks a Reading Intervention solution that provides measurable student growth, with monthly reporting of efficacy data tied to state ELA standards.",
  "fit_scores": {
    "primary": { "type": "OBC", "score": 0.82 },
    "secondary": [
      { "type": "general_growth", "score": 0.71 }
    ]
  },
  "suggested_campaign": "OBC + general_growth variant",
  "related_history": [
    "Q4 2025 — Pinellas board discussed vendor accountability",
    "Mar 2026 — Pinellas curriculum review announced"
  ],
  "urgency": {
    "tier": "hot",
    "deadline": "2026-06-15"
  },
  "actions_available": ["approve", "reject", "snooze", "ask"],
  "status": "pending_human_review"
}
```

## Field-level requirements

### headline (required)
≤ 80 characters. Format: `District (State) — single most important fact`. No marketing language. Just the fact.

### why_flagged (required)
1–2 sentences. Summary in Brief Composer's own words (NOT verbatim source). What made this signal pass through Qualifier with what fit score, and why a human should care.

### evidence (required)
Verbatim snippet pulled from the underlying `Signal.source.verbatim_snippet`. **NEVER paraphrased.** This is what the human will reference and what may land in outreach.

### fit_scores (required)
- `primary` — highest-scoring campaign type from Qualifier Phase 3
- `secondary` — array of additional campaign types if score > 0.6 AND not redundant with primary

Score format: float 0–1, two decimals.

### suggested_campaign (required)
Free-text string. Format: `<primary> + <secondary> variant` or just `<primary>` if no secondary.

### related_history (optional, max 3 bullets)
Prior campaigns, contacts, or signals at the same district. Pulled from prior `signal_queue` rows for the same `district_id`. Each bullet ≤ 100 characters.

### urgency (required)
Copied from the upstream signal, may be overridden by Qualifier if scoring reveals different urgency.

### actions_available (always the four)
Always: `["approve", "reject", "snooze", "ask"]`. The UI may filter based on permissions but the brief itself does not constrain.

## Voice rules for Brief Composer (when generating headline, why_flagged, related_history)

From the canvas:
- Calm, dependable. Never breathless.
- Never sell the signal — just describe. Josh decides.
- Amira brand terminology required:
  - "Learning Agent" not "platform"
  - "Dynamic Assessment" not "screener" (as primary label)
  - "Coherence Map" not "Mastery Map"
  - "Neuroscience" not "brain science"
  - "Assess-Instruct-Tutor" not "Assess-Tutor-Instruct"

## Validation (DB-level)

- `headline` ≤ 80 characters
- `evidence` must be a substring of (or identical to) the originating signal's `verbatim_snippet`
- `fit_scores.primary.score` must be ≥ 0.7 (otherwise the signal would have been rejected at Phase 3, not composed)

## Status lifecycle (signal_briefs.status)

```
pending_human_review              ← Brief Composer wrote this row
   ↓
approved                          ← Josh / Angela approved at Gate 1
   OR
rejected_by_human                 ← rejected with rejected_reason populated
   OR
snoozed                           ← snooze_until populated, will re-surface
```
