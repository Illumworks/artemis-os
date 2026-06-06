# Brief — Implement the campaign → Writing Studio handoff route (P0 bugfix)

**Type:** P0 — missing backend route (frontend calls it, gets 405). **For terminal to delegate.** Own
worktree, cwd inside, branch `worker/ws-campaign-handoff` off `main`. Own test DB. Do NOT merge — report.

## Problem (confirmed live)
The frontend "Create draft in Writing Studio" button (`createCampaignWritingHandoffApi`, `public/js/core/
api.js:305`) POSTs to `/api/campaign-ops/candidates/{id}/writing-handoff` → **405**. The route was never
ported from the old Node app (there's a literal comment in `artemis/marketing/routes/campaign_ops.py:9`
noting the Node app had `/writing-handoff`). So the button hangs on "Creating…" and no draft is created.

**Context — this is the MANUAL/ad-hoc draft path, distinct from the automatic one.** The pipeline already
auto-drafts deliverables (assemble brief → initiate → deliverables run → `content_draft` at Gate-2). This
handoff is the secondary path: an operator starts a *hand-crafted* draft in the studio, seeded from the
campaign, without running the full deliverables pipeline. Both should exist.

## Implement
Add `POST /api/campaign-ops/candidates/{candidate_id}/writing-handoff` to `campaign_ops.py` (prefix
`/api/campaign-ops`, already mounted). It should:
1. Load the candidate (404 if missing).
2. Create a `writing_studio` draft **linked to the campaign** (set the draft's `deliverable_id`/
   `deliverable_metadata`/candidate link the way the overview's draft↔candidate join expects — see
   `repository.py:~771` and `_serialize_folder`/candidate-name derivation), placed in the campaign's
   auto-derived folder.
3. **Seed it from campaign context** (so the studio opens with a useful starting point, not blank):
   - title: the campaign name (or `"{campaign} — {asset}"` if an asset/payload type is given),
   - brief field: the campaign objective + the assembled Campaign Brief summary if one exists
     (`get_campaign_brief`) + primary signal context,
   - setup tags from what's available today: campaign family, state/geography (see the asset-tagging
     taxonomy we're designing — wire audience/type/platform here once that lands; for now seed family+state).
   - voice: the Amira Marketing Voice default (as the manual New-draft composer does).
4. Do NOT auto-compose — return the created draft (with `id`); the frontend navigates to it and the operator
   composes in the studio. (Reuse the existing `POST /drafts` create logic / WS repo — don't fork it.)
5. Return shape the frontend expects: `_readJsonOrThrow` reads JSON; return the draft object incl. `id`.

## Verify (live — assert the effect)
- From a campaign detail, click "Create draft in Writing Studio" → it creates a draft and navigates into the
  studio with the seeded title/brief/folder (no 405, no hang). The draft appears in `/overview` linked to
  the campaign. A second campaign's handoff creates a separate draft (no bleed).
- Existing WS + campaign_ops tests pass. ruff + mypy clean.

## Constraints
Lossless (creates a draft; no deletes). Reuse the WS draft-create path, don't fork it. Org dep rule. Local
git; isolated worktree. Do NOT merge — report branch + SHA + how the click→draft→studio flow was verified.
Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Related (not in this brief — note for sequencing)
- **Folder CRUD routes** (create/rename/delete → also 405) are a sibling gap — separate brief.
- **Asset-tagging taxonomy** (audience/type/platform tags captured at initiation, feeding WS rules + the
  draft agent) is being designed with Jon — once defined, the seed step (3) and the auto-draft agent both
  consume those tags. Keep the seed fields easy to extend.
