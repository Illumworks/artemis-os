# Writing Studio — Backlog (surfaced while demoing, 2026-06-11)

**Status:** DEFERRED — pivot back here after the current P2 (proactivity) trajectory. Captured so nothing's
lost. The composer-v5 redesign is solid, but the demo surfaced regressions + gaps. Rough priority order below.

## 1. Writing agent STALLED mid-write (BUG — highest; demo risk)
In a demo, the writing agent (Artemis composing a 5-min demo-video script) **stopped mid-sentence** ("The key
word is" then nothing) — the compose run didn't complete. Likely related to a prior content-draft-node hang
(see the old `fix: prevent content-draft-node 900s hang` work — verify it regressed or is a different stall).
Investigate the compose/streaming path (`marketing/writing_studio/compose_engine.py` + the compose route +
the agent loop) for truncation/timeout. **If the Writing Studio is in the Friday demo, this is the one to fix
sooner** — flag to Jon.

## 2. Lost-in-redesign features (regressions to restore)
The old Writing Studio UI had these; they did NOT carry over to the composer-v5 redesign (NB: this is distinct
from the earlier empty-state bug — no data was lost, but these FEATURES weren't ported):
- **AI proposes new writing rules** (the learning loop — agent suggests rules from drafts).
- **Manual rule proposal** (user proposes a rule).
- **"View Writing Studio memory files" button** — open + EDIT the writing-rules/memory source files.
Backend likely still exists (`writing_rules/` has the rules engine + the propose/approve learning loop landed
in "Phase 3 Piece B"); this is mostly re-surfacing the UI affordances in composer-v5.

## 3. "Ready for review" flag → notify (high value; ties to Callie)
No way to flag a document **"Ready for review"** so the reviewer gets pinged. Angela's ask. **Cooler version
(Jon): Callie pings Angela** — Angela loves Callie. So: a draft state/flag = "ready_for_review" + reviewer →
a Slack ping, ideally posted BY Callie (reuse her token + the analyst-posting path) in the marketing channel
or Angela's DM. Natural fit with Callie's proactivity (P2). (Gate-2 review surfaces already exist —
`submit_draft_for_review` + the human-gate cards — reconcile this with those rather than duplicating.)

## 4. File picker — drag & drop + folder nesting (UX)
The Finder-style drafts picker can't **drag-drop drafts into folders**, nor **folders into folders** (nesting).
Flesh out: DnD for drafts→folder and folder→folder, with the folder-CRUD backend that already exists
(`writing-studio-folder-crud`).

## 5. Cowork / multiplayer presence (biggest new feature)
See **multiple people in a document at once** (live presence / collaborative editing). Net-new, largest effort
(real-time presence + likely CRDT/OT or at least presence cursors + conflict handling on the ProseMirror doc).
Scope carefully on its own when we get here.

## Suggested order when we pivot back
1 (stall bug) → 2 (restore lost features) → 3 (ready-for-review + Callie ping) → 4 (DnD folders) → 5 (cowork).
