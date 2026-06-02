# Content / deliverable path audit — 2026-06-01

**Read-only audit (parallel Sonnet worker) while CMP1+MD1 was in flight.**
**Purpose:** map what happens AFTER a campaign is initiated, to plan the next stream.

## TL;DR
"Signal → named campaign" (Stream 2) works. **"Campaign → reviewed draft → sent" does NOT.**
The pipeline creates a draft and dead-ends at `status="generating"`: no Gate-2 review UI, no
approval-decision handler, no send/outbound mechanism (`DeliverableState.approved` is terminal,
no `sent` state). The **next stream is CMP-SEND**.

## Stage map (after a campaign is initiated)
| Stage | Verdict | Notes |
|---|---|---|
| 1. Initiated campaign → deliverable nodes fire | ✅ WIRED | registry-driven nodes (only outreach_email active) |
| 2. Writing Studio draft creation | ✅ WIRED | `writing_studio/invoke.py` create_draft_from_candidate → campaign_deliverables (stub by default; Real if `ARTEMIS_WRITING_STUDIO_URL` set) |
| 3. Content agents (5.1/5.2/5.3) | ✅ WIRED | tools real (campaign_brief.read, content_registry.list_approved_assets, writing_studio.enqueue); event subscription updates workspace_state |
| 4. Gate-2 review + approval | 🔴 HOLLOW | table exists; gate suspends run; but NO UI, NO decide endpoint, NO resume handler — dead-ends at `awaiting_approval` |
| 5. SEND / outbound (email) | 🔴 DEAD-END | NO send mechanism anywhere; `approved` is terminal; no `sent` state |
| 6. Real prod data | drafts created (1 stub deliverable), 0 approvals, never sent |

## Next stream — CMP-SEND (3 briefs, proposed)
1. **CMP-SEND-1 — Gate-2 approval drawer UI:** render pending content-draft approvals + draft
   metadata; approve / reject / request-revision buttons → POST decide.
2. **CMP-SEND-2 — approval resume handler:** `POST /api/marketing/approvals/{id}/decide` →
   update approval + deliverable state (`draft_ready → approved|rejected|revised`) + workspace
   state + **resume the suspended pipeline** (PIPE4 resume with the gate decision).
3. **CMP-SEND-3 — outbound send:** add `DeliverableState.sent` (+ maybe `queued_for_send`);
   on approve → compose + send (SMTP/SendGrid/etc. — infra TBD) to recipients (signal → district
   → contacts); record `campaign_outcomes`-adjacent send event. **This also creates the
   capture seam for the future outcome-tracking (#106).**

Key files: `artemis/marketing/writing_studio/`, `artemis/pipelines/seeds/marketing_pipeline.py`
(gate_2_approval_drawer), `artemis/pipelines/node_executors/human_gate_executor.py`,
`artemis/marketing/state_machine.py` (DeliverableState), `artemis/marketing/models.py` (Approval).

## Engine live-health (companion audit)
- **Healthy + autonomous:** all 9 scouts ran (last 48h), no crashes/leaks, out-of-process stable.
- **Yield:** ~61 signals (last few days), 82% district-resolved. regional_news (43, 88% resolved)
  + leadership_transition (12, 83%) are the producers; canonical families + urgency throughout.
- **Taxonomy fix VERIFIED working** — old-list write failures were all pre-fix (05-29/30/31); ~3%
  recent edge where an agent emits an unrecognized family string (minor).
- **federal_funding:** now produces (4) but 0% resolved — federal grants have no district context;
  needs prompt logic (skip district-level OR map national→state).
- **Non-producing scouts:** legislative / starbridge / linkedin / procurement call their APIs but
  emit 0 signals — connectors offline/unconfigured (API keys). Expected-ish; audit before relying.
- **Tech debt:** `artemis/marketing/scout_sources/*` are all NullAdapter stubs (dead — the agentic
  path via real news tools is production); safe to retire.
