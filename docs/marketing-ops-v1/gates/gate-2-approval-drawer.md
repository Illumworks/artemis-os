# Gate 2 — Approval Drawer (REFERENCE ONLY)

**Not built by Artemis OS.** Owned by Writing Studio.

This document exists so Codex understands what happens to drafts after 5.3 Writing Studio Adapter POSTs them — and what Artemis OS does NOT need to build.

## What Gate 2 is

The human review surface where draft outputs from Writing Studio get reviewed before send / publish. From the canvas (screenshot 1): "Approval Drawer — sits in Writing Studio. Manual approvals → routed to Olivia, Julia, Angela, or Hubspot."

## Who owns it

- **Build:** Writing Studio team (Angela's existing system)
- **Reviewers:** Olivia, Julia, Angela
- **Approval routing logic:** Lives in Writing Studio, not Artemis

## What Artemis OS does NOT do

- Does NOT build the Approval Drawer UI
- Does NOT define Writing Studio's review policies
- Does NOT track which reviewer touched which draft
- Does NOT receive callbacks on approval / rejection in v1

## What Artemis OS DOES do at the boundary

- POSTs the brief + asset bundle to Writing Studio (5.3)
- Stores the returned `draft_id` in `campaign_workspaces.writing_studio_drafts`
- Sets workspace status to `sent_to_writing_studio`
- **Stops there.** No further state changes per workspace until v2 webhook integration.

## v2 — future Gate 2 → Artemis loopback (out of scope for v1)

When ready, Writing Studio webhook → Artemis OS will populate:

```
POST /api/writing-studio/webhook
  {
    "draft_id": "ws_draft_abc123",
    "campaign_id": "ws_2026_05_07_pinellas_obc_001",
    "deliverable_type": "email",
    "status": "approved | rejected | sent",
    "reviewer": "angela@amiralearning.com",
    "reviewed_at": "..."
  }
```

This will enable:
- Track / Learn loop (Phase 3 of full Artemis OS roadmap)
- Approval-rate analytics by ruleset
- Campaign-outcome attribution back to scout sources

`// FUTURE: implement webhook endpoint when Writing Studio team is ready to send.`

## Why this is separated

Strict separation of concerns. Writing Studio owns brand voice, format rules, draft quality, and review workflows. Artemis owns signal detection, qualification, brief assembly. The Writing Studio API is the boundary — neither side reaches across it.

This separation lets Writing Studio evolve independently (Angela can change format rules, swap LLMs, retrain voice model) without breaking Artemis. And Artemis can swap qualification logic without breaking Writing Studio.
