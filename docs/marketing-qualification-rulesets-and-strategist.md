# Marketing Qualification: Rulesets + the AI Strategist loop (design)

**Status:** plan agreed with Jon 2026-06-05. Seed = next build. UI + AI agent = phased after.
**Origin:** the qualifier gap fix (scout signals now score on write) exposed that the only *active*
ruleset was an empty `smoke-1` stub → everything scored 0.0. We need real, strategy-grounded rulesets.

## Principle: faithful to Josh, AI only augments, everything reversible

Josh's `decisions/campaign-signal-spec-v1.md` is the authoritative source. It already encodes the
strategy (reason codes, territory, campaign-type routing, and §4 boost/suppress/skip rules) grounded in
real sales conversations. We translate it — we do not invent. Anything beyond Josh is **tagged and
reversible** (see Provenance).

## Phase 1 — Seed `josh_spec_v1` rulesets (faithful, per-family)

Build **one ruleset per campaign family** (Josh §3), so routing matches his model:
`OBC`, `Dyslexia / structured literacy`, `Biliteracy / DLL`, `High-impact tutoring (HIT)`,
`General growth`. Each ruleset contains:

- **weighted_signals**: the reason codes that emit that family (Josh §3), each weighted by Josh's
  default urgency tier (§2). Faithful tier→weight translation:
  - `hot` (event = the buying window) → ~0.90–0.95
  - `standard` → ~0.50–0.70
  - `enrichment` (context only) → ~0.30
  The weight *numbers* are a translation of Josh's stated tiers (not new rules); refine with Jon.
- **hard_filters** (Josh §4.1, shared across families): skip HMH/Into-Reading partner districts,
  single-school opportunities, districts with enrollment < 5,000. (Verify the filter engine can express
  district-attribute conditions — enrollment, skip-list, HMH flag; any it can't becomes a small qualifier
  logic rule.)
- **state**: activate v1, replacing `smoke-1` as the active ruleset(s).
- Re-score the existing ~199 qualified signals against the new rulesets → real scoring.

**Not captured as ruleset data (needs qualifier logic, Phase 3):** Josh's highest-conviction rules are
relational/temporal and can't live in a weight table:
- §4.3 **Stacked signals** — two reason codes in one district within 30 days → upgrade tier.
  Josh: *"the highest-conviction pattern from sales conversations."* (Elevate — likely highest-value rule.)
- §4.3 Leader-transition + curriculum pairing → hot.
- §4.2 Stale dedup, single-source-hold (7-day retry), paywalled-evidence downgrade.
- §5 state nuances (FL OBC framing, TX HB1416/HB3 anchors, cross-state OBC pattern, competitor watch
  keywords) — partly territory config, partly scout-prompt deltas, partly qualifier nuance.

## Provenance & tagging (auditable, reversible)

Every rule (weighted_signal / hard_filter entry) carries a **`source`** stamp:
- `josh_spec_v1` — faithful to Josh's doc
- `ai_suggested` — proposed by the AI Strategist (carries `rationale`, `evidence`, `confidence`)
- `human_added` — Josh/Angela's own edits

The qualifier ignores extra fields (reads `reason_code` + `weight`), so `source` is safe metadata. The
ruleset-editor UI filters/highlights by source; `ai_suggested` rules are visually flagged and one-click
removable. Josh stays in control; AI only ever augments; nothing is silent or irreversible.

## Phase 2 — Ruleset-editor UI

Today's "Signal Playbook" UI edits the reason-*code* vocabulary, NOT ruleset weights/filters. Build a true
ruleset editor (mirror the Playbook pattern): assign weights, set hard filters, version, activate/draft,
filter by `source`, accept/dismiss `ai_suggested` rules. This is where Josh & Angela live once trained.

## Phase 3 — Relational/temporal qualifier logic

Implement the rules that aren't simple weights (stacked signals, dedup, single-source hold, §5 nuances).
Some may already exist in `cross_reference.py` — audit first. Prioritize **stacked signals** (Josh's
highest-conviction pattern).

## Phase 4 — The AI Marketing Strategist agent (capstone)

A *meta* agent: scouts **find** signals, the qualifier **scores** them, this agent **improves the rules**.

- **Inputs:** the signal landscape (surging codes/combos/geos); **outcome data** (which signals → campaigns
  → wins — the real training signal, needs CRM loop); the external market (documented ed-tech/literacy
  marketing strategies + competitor moves + policy shifts — leans on the Intelligence/trends layer);
  current rulesets + Josh's spec.
- **Outputs (never auto-applied):** tagged `ai_suggested` proposals — new reason codes, weight nudges, new
  boost/suppress patterns, territory shifts — each with rationale + evidence + confidence → land in Josh &
  Angela's review queue → one click accept/dismiss.
- **Reuses existing infra:** the proposal→human-approval pattern, the `ruleset_change` approval rail, the
  Intelligence/trends substrate, and memory (remembers what's been tried/rejected; doesn't re-suggest).
- **Cadence:** weekly pass or trend-shift-triggered, as a pipeline.
- **Guardrails:** cap suggestions/cycle; evidence threshold; track its own accept-rate (a suggestion
  scorecard — learn from what Josh accepts/rejects); can also flag STALE rules for retirement, not just adds.

## Sequencing & dependencies

`Seed (P1)` → `UI (P2)` → `relational logic (P3)` → `AI Strategist (P4)`.
P4's quality ceiling is the **outcome/CRM feedback loop** — until campaign→win data flows back, it reasons
from landscape + heuristics, not from what actually converts. CRM is already on the roadmap; the Strategist
gets dramatically better once it lands.

## Committed enhancements (baked into the roadmap — Jon 2026-06-05)

These three are agreed, not optional — bake them in as we build:

1. **Outcome/CRM feedback loop** — the real prize, and the fuel for P4. Capture qualified-signal →
   campaign → meeting → opportunity → closed-deal so weights are *learned from wins*, not guessed. This is
   the biggest quality lever for the AI Strategist; it's why CRM is a hard dependency for P4, not a nicety.
2. **Ruleset backtest/simulator** — before ANY change goes live (human or AI), run it against the existing
   signal corpus and show "what would change" (which signals re-rank, which campaigns would newly propose).
   Makes refinement + AI suggestions safe, data-driven, and *trustable*. Highest-value buildable-now
   improvement; build alongside the P2 editor UI.
3. **Elevate "stacked signals" (Josh §4.3)** — two reason codes in one district within 30 days → upgrade.
   Josh's own highest-conviction rule. Pull it forward to the front of P3 (relational logic), not the back.

### Further ideas (lower priority, tracked)
- Per-family `min_fit_score` tuning (flat 0.5 today; per-family rulesets enable it).
- Version A/B comparison (schema already supports version_tag + state).
- Stale-rule retirement (Strategist flags decayed rules, not just additions).

## CRM & contact-data integration (Popl / HubSpot / Salesforce) — PARKED, needs dedicated conversation

**Tracked here so it isn't lost — this is a much bigger conversation Jon wants to have separately, NOT yet
scoped.** Two distinct reasons it matters:

1. **Feeds the outcome loop (above):** Salesforce holds the win/loss + opportunity + account data that turns
   the Strategist's weights from guesses into learned-from-wins. Josh's §4.1 already references a "Salesforce
   account flag = HMH partner" — so CRM data is *already* assumed by his skip rules.
2. **Contact / people data:** Popl, HubSpot, Salesforce each carry contacts, accounts, activity. Open
   questions for the dedicated session — do NOT decide unilaterally:
   - What's the system of record for a contact/account? How do we reconcile the same person/district across
     three sources (dedupe/identity)?
   - What flows IN (read: account flags, opportunity stage, contacts) vs what (if anything) flows OUT
     (write-back: e.g., a qualified signal → a CRM task/lead)? Likely read-first.
   - Privacy/PII handling, sync cadence, source-of-truth precedence, and how district resolution
     (resolved_district_id) maps to CRM accounts.
   - Which integration is first (Salesforce for outcomes? Popl/HubSpot for contacts?).

**Roadmap placement:** a dedicated planning conversation BEFORE the AI Strategist (P4); the outcome-loop
slice of it gates P4's quality. Schedule the deep-dive once the pipeline scores correctly.
