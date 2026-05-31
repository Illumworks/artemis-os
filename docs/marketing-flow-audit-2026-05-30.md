# Marketing flow audit — 2026-05-30

**Status:** Findings doc. Drives next brief sequence after CC12.
**Scope:** Marketing Dashboard / Campaigns / Approval Queue / Signals Inbox / Writing Studio.
**Method:** 5-dimension classification per surface (substrate / integration / UI / exercise / tests).
**Captured at:** post-PIPE6 merge (migration 0053, branch `lead/j6a-granola-integration`).

---

## TL;DR

| Surface | Substrate | Integration | UI | Exercise | Tests | Verdict |
|---|---|---|---|---|---|---|
| Signals Inbox | 🟢 healthy | 🟢 wired | 🟢 live | 🟢 202 rows | 🟢 covered | **healthy** |
| Approval Queue | 🟢 healthy | 🟢 wired | 🟢 live | 🟢 13 pending | 🟢 covered | **healthy** |
| Writing Studio | 🟢 healthy | 🟡 handoff broken | 🟢 live | 🔴 1 stub | 🟢 covered | **CC12 closes the gap** |
| Marketing Dashboard | 🟢 healthy | 🟡 client-side | 🟡 partial mock | 🟡 mixed | 🟡 partial | **mocks gated as skeleton — defensible but should be removed** |
| **Campaigns** | 🟢 healthy | 🟡 client-side merge | 🔴 **renders fake demo data** | 🔴 2 real / 4 mock | 🔴 none | **HOLLOW SURFACE** — `CAMPAIGNS` hardcoded array (Michigan Field Guide etc.) shows through the UI |

Headline: **one new hollowness finding** — the Campaigns tab renders hardcoded demo campaigns (`CAMPAIGNS` array in `marketing-os.js` lines 74-292). They never get displaced because the real `campaign_candidates` table only has 2 rows, and the merge code keeps the mocks when the API set is small.

---

## Data flow (actual, post-investigation)

The earlier mental model "signal → campaign_brief → deliverable" was wrong. Actual flow:

```
1. signal_queue (202 rows)
   ↓ qualifier
2. signal_queue.qualification_json populated (64/202 = 31.7% qualified)
   ↓ brief_composer (signal_briefs.write tool)
3. signal_queue.qualification_json.brief populated (same 64 rows)
   ↓ campaign-candidate creation
4. campaign_candidates (2 rows — id=1 historical, id=2 fresh from MC2 smoke)
   ↓ approval row created with kind=signal_brief, pipe4_context = full brief content
5. approvals (13 pending signal_brief approvals)
   ↓ Gate 1 human review (Approval Queue UI)
6. If approved → candidate.decision_state = approved → promotion event
   ↓ post-Gate-1 brief composition
7. campaign_briefs (0 rows — no candidate has promoted past Gate 1 in current substrate)
   ↓ content composer agent
8. campaign_deliverables (1 row — historical stub-draft-1 from May 18, status=generating)
   ↓ writing_studio.enqueue (MISSING — CC12 fixes)
9. Writing Studio (no live drafts; 1 generating stub)
```

**Key insight:** `campaign_briefs=0` is **not** hollowness. The brief content lives in two places:
- **Pre-Gate-1:** `signal_queue.qualification_json.brief` (64 populated) + `approvals.pipe4_context` (13 pending review)
- **Post-Gate-1:** `campaign_briefs` table, keyed by candidate (would be populated after a Gate 1 approval — currently 0 because nothing has been approved in the current run)

The 13 pending approvals are real work waiting for Josh/Angela to review.

---

## Per-surface findings

### 1. Signals Inbox — 🟢 HEALTHY

**Substrate:** `signal_queue` (202 rows) + `signal_reason_codes` (post-SP migration) + `skipped_signals`. All wired.
**Integration:** `listSignalQueueApi` + `listPipelinesApi`. Live data hits the surface.
**UI:** `loadMarketingSignals` (marketing-os.js:2012). Skeleton uses `SIGNALS_MOCK` for ~100ms during async load, then replaced by real signal_queue data. Defensible pattern but the mock skeleton could be a blank state instead — minor cleanup.
**Exercise:** 202 rows of real signal traffic, 64 qualified (31.7%). The scout → qualifier → brief_composer pipe is alive.
**Tests:** Signal-side has solid coverage (CC4 + signal_queue path tests).

**Findings:**
- (Cosmetic) `SIGNALS_MOCK` skeleton during load could be empty-state instead. Low priority.

### 2. Approval Queue — 🟢 HEALTHY

**Substrate:** `approvals` (13 pending signal_brief). `pipe4_context` holds full brief content per row.
**Integration:** Approval endpoints fire MC1/MC2 carryover writes on approve/reject. CC29 closed rejection memory carryover.
**UI:** Inbox surface renders, approve/reject buttons functional. CC22 added rejection-reason capture.
**Exercise:** 13 unresolved pending Gate 1 reviews. Real work backed up. (User has not been actively reviewing.)
**Tests:** Approvals path has coverage; CC29 added rejection observation tests.

**Findings:**
- (Behavioral, not platform) 13 pending approvals is operator backlog, not a platform issue.
- (Banked) Approval-Queue-level audit of the 5 oldest timed-out items may surface stale review patterns.

### 3. Writing Studio — 🟡 HANDOFF BROKEN (CC12 fixes)

**Substrate:** `campaign_deliverables`, `writing_profiles`, deliverable lifecycle state machine — all healthy.
**Integration:** `artemis/marketing/writing_studio/invoke.py` exists and does the right thing. The MCP boundary tool `writing_studio.enqueue` is **missing** (per W1 finding). Content agents declare it but it doesn't exist in the registry.
**UI:** Studio surface renders, voice profiles selectable, deliverable detail view live.
**Exercise:** 1 historical stub (status=generating) from May 18. No new drafts flowing in.
**Tests:** Writing Studio invoke + state machine covered.

**Findings:**
- **W1 → CC12 (in flight via Codex):** writing_studio.enqueue + campaign_brief.read + content_registry.list_approved_assets missing from registry. CC12 wraps existing implementations.
- (Banked) `content_assets` + `content_asset_links` both 0 rows. The registry has no approved assets to surface even after CC12 lands. Need a seed or an Assets Inbox UI to populate.

### 4. Marketing Dashboard — 🟡 PARTIAL MOCK FALLBACK

**Substrate:** No dedicated `marketing_dashboard_*` tables. Surface composes from signal_queue + approvals + campaign_candidates client-side. No server-side `/api/marketing/dashboard` route.
**Integration:** marketing-os.js calls `listSignalQueueApi`, approval endpoints, campaign_candidates endpoints in parallel.
**UI:** Renders with real counts when API returns. Falls back to mock counts (`APPROVALS_MOCK.length`, `SIGNALS_MOCK.length`, `CAMPAIGNS.length`) when API returns empty.
**Exercise:** Real data populates correctly when present.
**Tests:** No dashboard-specific tests.

**Findings:**
- **MD1:** Dashboard summary uses mock counts as fallback (lines 1949, 1951, 1953). When backend returns 0 rows, user sees mock numbers — confusingly looks like real data. Replace with explicit empty-state ("No signals yet").
- **MD2:** No backend dashboard aggregation route. Currently fine (client-side composition works) but if the surface grows, a single `/api/marketing/dashboard` would reduce request fan-out.

### 5. Campaigns — 🔴 HOLLOW SURFACE (new finding)

**Substrate:** `campaign_candidates` (2 rows real) + `campaign_state_transitions` (202 — every signal has transitions).
**Integration:** marketing-os.js fetches campaign_candidates and merges into `_campaignMap = new Map(CAMPAIGNS.map(...))` (line 376).
**UI:** **The map is seeded with the hardcoded `CAMPAIGNS` array** (lines 74-292). When the API returns fewer than the hardcoded set, the mock entries (Michigan Field Guide, plus 3 others) **remain visible to the operator as if they were real campaigns**. Line 1991: `mergedCampaigns.length > 0 ? mergedCampaigns : CAMPAIGNS` — pure fallback. Line 624: `campaigns = CAMPAIGNS` — outright assignment to mocks.
**Exercise:** 2 real candidates (id=1 historical-obc, id=2 fresh-reading_growth). Operator sees those PLUS 4 fake campaigns.
**Tests:** No campaign-rendering tests.

**Findings:**
- **CMP1 (CRITICAL):** Hardcoded `CAMPAIGNS` array seeds the campaign list. User cannot distinguish real candidates from demo data. **Remove the array; render real campaign_candidates only; add empty state when none exist.**
- **CMP2:** Mock fall-throughs at lines 624, 1949, 1953, 1991 should all be removed once CMP1 lands.
- **CMP3:** No campaign-level detail tests. Add coverage as part of CMP1 fix.

---

## Cross-cutting findings

### XC1 — SITE-MAP staleness

`docs/SITE-MAP.md` mentions "Campaigns (3 rows)" but there is no `campaigns` table — only `campaign_candidates` (2 rows). The doc needs a refresh post-CC12 + CMP1 to reflect actual tables and counts.

### XC2 — Mock-as-skeleton vs mock-as-fallback distinction

Pattern audit across `marketing-os.js`:
- **Defensible (skeleton during async load):** `loadMarketingSignals` uses `SIGNALS_MOCK` for ~100ms then replaces. Could still be cleaner as empty-state.
- **Problematic (fallback that user sees):** `loadCampaigns`-equivalent fallback to `CAMPAIGNS` array. User sees mocks as real data.

Establish a coding rule: **mock data is only allowed as initial skeleton, never as fallback for empty API result. Empty API result → render empty state.**

### XC3 — content_assets+links both 0 rows

CC12 will wire `content_registry.list_approved_assets` but the registry is empty. Two follow-up moves needed:
- **CMP4:** Seed `content_assets` with the existing Amira brand assets (PDFs, one-pagers, etc.).
- **CMP5:** Build an Assets Inbox UI (mirrors the Definition Proposals Inbox) for operators to approve new assets uploaded by content agents.

---

## Proposed brief sequence

After CC12 lands (in flight via Codex), in priority order:

| # | Brief | What it does | LOC | Notes |
|---|---|---|---|---|
| 1 | **CMP1 — Remove Campaigns mock fallback** | Drop the `CAMPAIGNS` hardcoded array; render only real `campaign_candidates`; add empty-state UI; small tests | ~150 | Codex-suitable (well-specified UI cleanup) |
| 2 | **MD1 — Dashboard mock fallback cleanup** | Drop `APPROVALS_MOCK.length` / `SIGNALS_MOCK.length` / `CAMPAIGNS.length` fallbacks; render real counts only with empty-state copy | ~80 | Codex-suitable; pairs with CMP1 |
| 3 | **CMP4 — Seed content_assets** | Populate `content_assets` with the canonical Amira brand assets so CC12's `content_registry.list_approved_assets` returns real rows | ~50 + seed data | Needs Jon to source/approve the asset list |
| 4 | **XC1 — SITE-MAP refresh** | Update `docs/SITE-MAP.md` to reflect actual tables/counts | ~doc-only | Lead-owned |
| 5 | **Banked — CMP5 Assets Inbox UI** | Operator surface for approving new content_assets proposed by content agents | ~400 | After CMP4 has real data |
| 6 | **Banked — D3 campaign_ruleset_versions** | Locked decision still outstanding | ~200 + migration | Already on task list #76 |
| 7 | **Banked — D4 district_marketing_flags** | Locked decision still outstanding | ~150 + migration | Already on task list #77 |

CMP1 + MD1 together close the **last visible hollowness** on the marketing surfaces. CC12 closes the **last backend hollowness** in the pipeline.

---

## What this audit confirms

1. **The marketing pipeline IS alive.** 202 signals, 64 qualified+briefed, 13 awaiting Gate 1 — that's a real backlog, not a dead pipe.
2. **The campaign_briefs=0 was a red herring.** Brief content is in `signal_queue.qualification_json` + `approvals.pipe4_context`. Post-Gate-1 brief composition hasn't fired because no Gate 1 approval has landed yet in this run.
3. **The visible-to-user hollowness is the Campaigns tab.** Hardcoded demo campaigns show through. Other surfaces are clean once mock-skeletons are tightened.
4. **CC12 (in flight) closes the backend handoff.** After CC12 + CMP1 + MD1 + CMP4, the marketing surface is end-to-end real.

---

## What this audit does NOT cover

- The 13 pending approvals themselves (operator backlog, not platform)
- D3 campaign_ruleset_versions + D4 district_marketing_flags (banked tasks #76, #77)
- Pipeline AI Panel grounding for the marketing pipeline (separate stream)
- Memory Wings UI per-surface affordances (deferred ~4 weeks)
