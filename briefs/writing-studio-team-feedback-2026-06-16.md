# Backlog: Writing Studio + Connectors — team feedback (2026-06-16, first multi-user session)

Captured from Jon during the first real team use of Writing Studio (teammates logged in, RBAC mostly
working — they saw only their permitted surfaces). PINS FOR LATER unless noted. Owner: Lead (proactivity/
access lane), coordinate with terminal for the realtime-collab pieces.

## Access / Connectors
1. **Non-owner teammates can still see the Connectors card** (bottom of the integrations area). The M3/surface
   lockdown (`ddd7e1f`) hid Personal Workspace + Dev Projects, but the Connectors card is still visible to
   marketing humans. Decide: hide Connectors for non-owners, OR show a read-only/limited version. (The card
   exposes integration status + connect/disconnect — non-owners should not manage the owner's connections.)
2. **Signal Playbook is in an owner-gated area, but Josh (non-owner marketing) needs to edit it.** Either move
   Signal Playbook OUT of the owner-only area, OR make it role-aware so marketing can edit it while the
   account/connector controls stay owner-only. (i.e. the surface gating is too coarse — playbook ≠ connectors.)
   → Make the surface adjust to whoever is logged in.

## Writing Studio realtime collaboration (terminal's co-edit lane — see ws5-coedit-architecture.md)
3. **No live presence / "who's where."** A user only sees a coworker's avatar when on the SAME document;
   the doc list/dropdown has no indicator of which docs are being actively edited or by whom. Own avatar is
   always pinned top regardless of location. Want: presence indicators in the doc list (person icon on docs
   with active editors) + per-doc active-editor list, so the team doesn't have to verbally coordinate.
4. **Stale UI — changes need a manual refresh.** Some changes don't propagate live — e.g. **renaming a
   document** didn't show for the other user until they refreshed. Want live propagation (title + likely other
   metadata) without a refresh. (Co-edit content may sync; metadata/rename clearly does not.)

## Connector / Google (Lead)
5. **Auto-refresh token sweep — VERIFIED WORKING (2026-06-16), no bug.** `refresh_google_credentials_tick`
   refreshes + persists both google_credentials; the production caller `run_refresh_tick` commits
   (`scheduler.py:217`), and re-test confirmed REFRESHED+PERSISTED for both purposes. (An earlier "no-op"
   reading was a test artifact — the standalone test didn't commit the session.) Google client is correct
   (`612420684593`, "Artemis Google Docs Access"). Google credentials thread CLOSED.
7. **"Ready for review" routing for NON-campaign docs.** When a doc is NOT attached to a marketing campaign
   (a one-off / general marketing doc) and it's marked "ready for review," Callie should ping **Angela in a
   different channel**, NOT the `marketing-campaigns` channel (that channel is for campaign-attached work).
   Campaign-attached docs keep going to `marketing-campaigns`. Need: the target channel for one-off reviews
   (Angela's DM vs a dedicated review channel — TBD with Jon). Logic lives in the Gate-2 / "ready for review"
   notification routing (human_gate / Callie's review-ping path); branch on whether the draft has a campaign
   linkage.

6. **New Google Docs destination.** Writing Studio "create new doc / export" uses the Docs API
   (`google_docs/client.py:295`) which creates the doc in the **My Drive ROOT of the token account** (the
   marketing account, amiracentral@, for Callie's docs) — no target folder (the Docs API can't set a parent;
   needs a Drive API move). Consider creating into a dedicated Drive folder so exports don't clutter root +
   are shareable to the team.
