# Writing Studio — bug fixes + A/S-class elevation plan (2026-06-05)

Diagnosed via stress investigation. Jon's reported bugs map to 5 concrete root causes (most are
one-line field-name mismatches). Then the A/S-class gap plan.

## Bug-fix batch (do first — small + high-impact)

1. **Composer shows a blank thread / "draft doesn't render"** (Jon's #3, HIGH).
   `_serialize_thread_message` (`writing_studio.py:856`) emits `content`, but the chat renderer reads
   `entry.text` (`writing-studio.js:1990`). Every reloaded (and even live) thread message renders as
   "". Fix: include `text` in the serialized message (alias of content) — or read `content` in the
   renderer. One field. (Also: a pipeline draft with no saved *version* shows an empty body area —
   that's correct-but-confusing; see elevation #3 for streaming/first-render polish.)

2. **Pipeline drafts invisible in their campaign folder** (Jon's "Slack-sent draft missing", HIGH).
   Drafts created via the pipeline route (`campaign_deliverables.py:87-93`) never stamp `folder_id`
   into metadata (only the manual `create_draft_from_candidate` path does, `invoke.py:244`). So
   AI/pipeline drafts — the most common type — fall into "All drafts/unfiled" and show 0 inside their
   campaign folder. Fix: stamp `folder_id` (via `get_or_create_folder_by_candidate`) in the pipeline
   creation path, AND backfill existing pipeline deliverables (3,4,5,10,…). Reuse the folder-backfill
   pattern already in `invoke.py`.

3. **Folder counts wrong/inconsistent** (Jon's "5 vs 3 vs 0", MEDIUM).
   Two broken numbers: the right-side badge renders `folder.draftCount` which is NEVER computed
   server-side (always 0 after reload; `_serialize_folder` comment says "client-side" but the client
   never sets it). The "5" under the name is actually the folder's `campaign_id` (= numeric candidate
   id) rendered as a subtitle — not a count at all. Fix: compute `draftCount` from the real drafts
   array (per folder_id) — server-side in `_serialize_folder` or client-side in
   `renderWritingOrganizationRail` (`writing-studio.js:1188`); and stop rendering campaign_id as a
   count-looking subtitle. (Depends on #2 for pipeline drafts to count correctly.)

4. **"N proposed" training-candidate badge always 0** (MEDIUM — kills the propose/approve loop UI).
   Backend returns `training_candidates` (`writing_studio.py:163`); frontend reads
   `trainingCandidates` (`writing-studio.js:926,1324,1403,1426`). Snake/camel mismatch. Fix: align
   the key (return `trainingCandidates`).

5. **Active voice profile never surfaces** (MEDIUM). Backend returns `profiles` (list,
   `writing_studio.py:162`); frontend reads `activeProfile` (single object). Profile chip falls back
   to a hardcoded name; engine picker is blind. Fix: return `activeProfile` (e.g. the active profile)
   alongside `profiles`.

(Bugs 1, 4, 5 are pure key-name fixes; 2 is a stamp+backfill; 3 is a small count computation.)

## A/S-class elevation plan (after the bug batch)

WS is solid where it counts (compose-with-AI, ruleset grounding, version history, submit-for-review
all BUILT). Ranked capability gaps to "top-flight":

- **E1 — Google Docs integration is absent on the backend (BIGGEST, strategic).** The frontend has
  full UI + calls `/api/writing-studio/drafts/{id}/google-doc/import|export|unlink` and
  `/api/google/overview` — **none of those routes exist** (silent 404, hidden by a `.catch(()=>null)`).
  Google Docs is the editorial handoff format (and the banked long-form Slack-preview path depends on
  it). Build the backend routes against the Google Docs API. Largest, most product-visible.
  **Agreed model (Jon, 2026-06-05):** Artemis stays the source of truth + the training surface; the
  GDoc is a share/review/export surface kept updated from Artemis (auto-create on draft +
  re-export on compose). Pull-back is an **explicit "Pull changes from Google Doc" action** (NOT
  silent live bidirectional sync — that's the conflict-prone trap, and it would leak edits around the
  training loop). On pull-back, GDoc edits land as a new Artemis version → captured by training. See E7.
- **E7 — Edit-as-training (makes "all edits train the bot" real).** Today the bot learns only via the
  compose propose-a-rule loop; a plain edit (or a GDoc pull-back) is *saved but not mined*. Build an
  **automatic diff-analysis**: on a pull-back AND on an in-app save, diff the AI's version vs the
  human-edited version, extract candidate lessons ("consistently shortens the opener", "prefers X over
  Y"), and surface them as **human-gated proposed learnings** (reuse the existing
  training-candidates → approve → writing_rule loop). Not auto-applied (avoids brand-voice noise), not
  silent. This is the mechanism that turns every human edit into a training signal — the original goal.
- **E2 — Streaming compose.** Turns are synchronous (full blank while "Drafting…"). Best-in-class
  streams tokens as they generate. UX-defining for a writing tool.
- **E3 — Draft search.** No free-text/semantic search across drafts; only folder/campaign filter.
- **E4 — Inline "proposed learning" surfacing.** Extraction works but users aren't notified in-thread
  when a candidate is captured (they don't know to open the review modal).
- **E5 — Redline version diffs.** History shows word-count deltas only; no inline what-changed diff.
- **E6 — Live signal/campaign context in compose.** The AI writes from the brief but without the live
  account intelligence (which district, which signals fired) — connect the intel so drafts are
  grounded in why the campaign exists. (Synergy with the Marketing Intelligence layer + the
  memory-keystone Studio connection, #5 of the memory plan.)

**Sequencing:** bug batch first (small, makes WS *work*), then **E1 Google Docs** (biggest visible
value), then E2 streaming / E3 search / E4 inline learnings as polish, with E5/E6 as deeper. E6
overlaps the memory plan's Studio↔keystone (#5) — plan them together.
