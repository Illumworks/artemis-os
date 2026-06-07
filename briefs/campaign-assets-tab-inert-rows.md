# Brief — Campaign Assets tab: deliverable rows are inert / unlabeled (bug)

**Type:** P2 Campaign-UI bug. For terminal → Sonnet, or fold into the campaign-UI polish. Own worktree,
don't merge, report.

## Bug (Jon screenshot 2026-06-06, campaign #15 → Assets tab)
The "Content Assets" list shows N rows each labeled just **"Draft"** with an empty circle + a dash (—) on
the right. **Nothing is clickable or actionable** — you can't open the draft, see its type/status, or act
on it. (`renderTabAssets` in `public/js/features/marketing-os.js` renders the deliverables.)

## Fix
Each content-asset row should be informative + actionable:
- **Label** = the asset's real identity (deliverable type / title — e.g. "Outreach email — Superintendent"),
  not a generic "Draft". Show its **status** (draft_ready / in_review / approved) as a clear pill, and the
  audience/type tags when present.
- **Clickable** → opens the draft in Writing Studio (reuse the existing `_navigateToWritingStudio(draftId)`
  / draft-open path). The empty circle + dash should become a real status indicator + a row action
  (open / view), or be removed if meaningless.
- Empty/placeholder state when a deliverable has no draft yet ("not drafted yet") instead of a blank "Draft".

## Note (data hygiene, not the bug)
The "8 deliverables" on #15 are partly empty shells from Lead's repeated content-node verification runs
(re-initiating #15) + the content-node hang producing empty deliverables. The UI fix (informative,
actionable rows) is the real ask; the duplicate empty shells will stop once the content-node P0 lands and
runs aren't re-fired for testing.

## Verify
On a campaign with deliverables, the Assets tab shows labeled, status-bearing, clickable rows that open the
draft; empty deliverables read as "not drafted yet". Lossless. Don't merge — report.
Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
