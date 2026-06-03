# Brief-sufficiency audit — Gate-1 + Initiation decision points (2026-06-02)

**Question (Jon):** does an operator have enough info, up front, to make an informed approve/initiate
decision? **Answer: partially.** Signal-level *evidence* is good. But several fields the system
*already computes* are dropped before the operator sees them, there's **zero history/trend context**
anywhere, and a do-not-contact flag is never surfaced. Feeds the Marketing Intelligence Layer
(`docs/marketing-intelligence-layer-design.md`) — this is the "decision-enrichment first" path.

## 🔴 Safety gap (fix first)
**`districts.on_skip_list` (do-not-contact) is surfaced NOWHERE** — not at Gate-1, not at
initiation. An operator can approve a signal and initiate a campaign for a skip-listed district
without any warning. This is a compliance/brand risk independent of the intelligence work.

## Cheap wins — data already in the DB, just not rendered
**Gate-1 (signal approval):**
- `provenance.why_flagged` — the scout's actual reasoning (in payload, never shown).
- `discovered_by` / scout identity + `agent_run_id` trace (so the operator can trust-weight source).
- `on_skip_list` badge (the safety gap above).
- Dedup / "N related signals" count (`find_signal_by_dedupe_key` logic exists; result not attached).

**Initiation (CI3 confirm):**
- **`proposal.rationale`** — the LLM's reasoning for the campaign is fetched (`initiation.py:126`)
  then **discarded** by the modal. Cheapest, highest-value fix.
- Per-signal **reason codes + why_flagged** in the cluster rows (`reasonCodes` already serialized;
  cluster render shows only headline/summary/family/state).
- Candidate **`metrics_json`** (adjustedScore / recommendedFamilies — the score that justified
  promotion) — written at approval, never read at initiation.
- District **enrollment + supported + on_skip_list** parity in the modal header.
- **Target-scope cardinality** — "states=[TX] → N districts" (one `count(*)`); de-risks the send
  fan-out (#119) and tells the operator how big a scope they're picking.
- Inline **lineage brief/asset summaries** (already loaded by `get_candidate_lineage_context`;
  rendered today as just a boolean "available").

## Needs the trend substrate / #106 (the intelligence layer proper)
- Per-district / per-family **signal history + trend** ("3rd biliteracy signal in IL this quarter").
- **Memory readback before the decision** — Gate-1/proposal approval *write* observations but nothing
  *reads* them at decision time ("you rejected a near-identical signal last month").
- **Predecessor / prior-campaign outcomes** — `predecessor_id` + lineage exist, but no outcome
  columns anywhere (blocked on #106 + CRM).
- **Contact reachability** — `district_contacts` exists; surface "we have/don't have a contact" so
  operators don't approve/initiate undeliverable campaigns.

## Recommended next build — ENRICH1 (the cheap wins)
Surface the already-computed fields above (esp. the safety `on_skip_list` badge + `proposal.rationale`
+ per-signal reasons + target-scope count). No new substrate, no new sources — pure
decision-enrichment from data we already hold. This is the lowest-hanging half of "enrich the
decision" and makes both approval moments meaningfully more informed today. The history/trend half
follows once the trend substrate (intelligence layer Phase 1) exists.
