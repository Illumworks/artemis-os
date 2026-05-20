# Schema — Signal

The shared output schema all 9 scouts emit. Versioned contract; Qualifier reads it.

**Storage:** `signal_queue` table, column `signal` (JSONB).

## Schema (JSON)

```json
{
  "signal_id": "sig_2026_05_07_pinellas_rfp_001",
  "discovered_at": "2026-05-07T14:32:00Z",
  "discovered_by": "starbridge_researcher | regional_news_scout | linkedin_observer | legislative_scout | federal_funding_scout | state_doe_scout | procurement_scout | board_minutes_scout | leadership_transition_scout",
  "discovery_mode": "scheduled | event | batch",
  "priority": "hot | standard | enrichment",

  "source": {
    "type": "starbridge | news_article | board_minutes | state_doe | linkedin_post | legiscan | federal_register | grants_gov | procurement_portal | district_press | governor_press",
    "url": "https://...",
    "verbatim_snippet": "...exact 1-3 sentences from source, never paraphrased...",
    "speaker_attribution": "Supt. [name], 2026-04-12 board meeting"
  },

  "geography": {
    "state": "FL",
    "district": "Pinellas County Schools",
    "district_id": "FL_pinellas"
  },

  "reason_codes": [
    {
      "code": "RFP_EFFICACY_LANGUAGE",
      "evidence_quote": "...verbatim quote from the source that triggered this reason code...",
      "confidence": 0.85
    }
  ],

  "candidate_campaign_types": ["OBC", "general_growth"],

  "urgency": {
    "deadline": "2026-06-15",
    "days_until": 39,
    "tier": "hot | standard | enrichment"
  },

  "contact_hints": [
    { "name": "...", "role": "...", "linkedin_url": "..." }
  ],

  "flags": [
    "source_quality_low",
    "evidence_quote_partial",
    "proposed_new_code"
  ],

  "dedupe": {
    "embedding_hash": "...",
    "near_duplicates_checked": ["sig_..."],
    "material_change_reasoning": "..."
  }
}
```

## Field-level requirements

### signal_id (required, unique)
Format: `sig_YYYY_MM_DD_<district_short>_<topic_short>_<3-digit-seq>`. Scouts generate. Codex: use a deterministic generator so retries don't create duplicate IDs.

### discovered_at (required)
ISO 8601 UTC. The time the scout detected the signal (not the time the underlying event occurred — that's elsewhere in `source`).

### discovered_by (required)
Exactly one of the nine enum values. No "other" or freeform.

### discovery_mode (required)
- `scheduled` — scout's normal cadence
- `event` — webhook-triggered or push
- `batch` — bulk re-processing (e.g., backfill)

### priority (required)
Set by the scout based on its own urgency-tier rules. Each agent file documents what counts as hot vs. standard vs. enrichment for that source type.

### source (required, object)
- `type` — must be one of the listed enum values
- `url` — direct link to the source artifact
- `verbatim_snippet` — exact 1–3 sentences. **NEVER PARAPHRASE.** This is the evidence the human will see in the Signals Inbox and that will land in Writing Studio drafts. If you paraphrase, downstream sales outreach will misquote the source.
- `speaker_attribution` — required when a person is speaking (board minutes, press quotes, LinkedIn posts). Format: `Role / Name, date, venue`. Null is OK when not applicable.

### geography (required)
- `state` — two-letter (`FL`, not `Florida`)
- `district` — human-readable canonical name
- `district_id` — canonical ID from the `districts` table. If scout cannot resolve, log to `unresolved_signals` and do NOT emit a signal. Never invent district_ids.

### reason_codes (required, non-empty array)
Every entry must be in the `reason_code_registry` table. If a scout believes a new code is warranted, it MAY include the code with `confidence: <0.5` AND add a `proposed_new_code` entry in `flags`, but must also write a row to `proposed_reason_codes` for human review.

- `code` — from registry
- `evidence_quote` — verbatim quote from source that triggered the reason code. May be the same as `source.verbatim_snippet` or a sub-quote of it.
- `confidence` — float 0–1, scout's confidence that the reason code applies

### candidate_campaign_types (optional)
Scout's guess at relevant campaign types. Qualifier may agree or disagree. Free signal for Cross-Reference Agent — do not over-engineer this on the scout side.

### urgency (required)
- `tier` — `hot` / `standard` / `enrichment`. See per-agent definitions of these tiers.
- `deadline` — ISO date if applicable, null otherwise
- `days_until` — derived from deadline; null if no deadline

### contact_hints (optional)
Scouts may populate when they encounter named individuals (board member quoted in minutes, signatory on a press release). For v1 these are not enriched — Contact team is out of scope. They are stored on the signal for future use.

### flags (optional)
Quality and routing flags. Permitted values:
- `source_quality_low` — e.g., scanned PDF with poor OCR
- `evidence_quote_partial` — paywalled source, partial extract only
- `proposed_new_code` — at least one reason_code is not in registry; see `proposed_reason_codes`
- `cross_state_pattern` — scout detected a pattern relevant beyond one state

### dedupe (required)
- `embedding_hash` — hash of the embedding of `source.verbatim_snippet`
- `near_duplicates_checked` — list of signal_ids the scout considered during dedupe
- `material_change_reasoning` — string explanation when scout chose to emit despite high similarity

## Dedupe rules (enforced by scout, NOT by queue)

Before writing a new signal, scouts query `memory_layer` for the same `(district_id, reason_code)` pair. If a prior signal exists:

1. Compute embedding similarity between new `verbatim_snippet` and prior signal's snippet.
2. Decision table:
   - similarity > 0.92 → **suppress**, do not emit. Update `memory_layer.last_seen_at`.
   - similarity < 0.70 → **emit** as a new signal (genuinely different content).
   - 0.70 ≤ similarity ≤ 0.92 → **run material-change check**. LLM call: "Is this materially new information vs. the prior signal?" If yes, emit with `material_change_reasoning` populated. If no, suppress.

`// JUDGMENT CALL:` thresholds 0.92 and 0.70 should be tuned after first 500 signals. Surface dashboard for Josh / Angela to inspect borderline cases.

## Validation (DB-level)

- `signal_id` must be unique (primary key).
- `signal->>'discovered_by'` must match one of the nine enum values.
- `signal->'geography'->>'district_id'` must exist in `districts` table OR be a recognized federal/state-level entity (see special cases in `services/territory-config.md`).
- `signal->'reason_codes'` array must have at least one entry.

## Sample valid signal

```json
{
  "signal_id": "sig_2026_05_07_pinellas_rfp_001",
  "discovered_at": "2026-05-07T14:32:00Z",
  "discovered_by": "procurement_scout",
  "discovery_mode": "scheduled",
  "priority": "hot",
  "source": {
    "type": "procurement_portal",
    "url": "https://procurement.pinellas.k12.fl.us/rfp/2026-042",
    "verbatim_snippet": "The District seeks a Reading Intervention solution that provides measurable student growth, with monthly reporting of efficacy data tied to state ELA standards.",
    "speaker_attribution": null
  },
  "geography": {
    "state": "FL",
    "district": "Pinellas County Schools",
    "district_id": "FL_pinellas"
  },
  "reason_codes": [
    {
      "code": "RFP_LITERACY_POSTED",
      "evidence_quote": "The District seeks a Reading Intervention solution",
      "confidence": 0.95
    },
    {
      "code": "RFP_EFFICACY_LANGUAGE",
      "evidence_quote": "measurable student growth, with monthly reporting of efficacy data",
      "confidence": 0.90
    }
  ],
  "candidate_campaign_types": ["OBC", "general_growth"],
  "urgency": {
    "deadline": "2026-06-15",
    "days_until": 39,
    "tier": "hot"
  },
  "contact_hints": [
    { "name": "...", "role": "Director of Curriculum", "linkedin_url": null }
  ],
  "flags": [],
  "dedupe": {
    "embedding_hash": "a4c8b2e1...",
    "near_duplicates_checked": [],
    "material_change_reasoning": null
  }
}
```
