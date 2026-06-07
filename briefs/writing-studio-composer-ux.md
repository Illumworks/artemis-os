# Roadmap design — Writing Studio composer UX (3-mode editor)

**Status:** ROADMAP design (part of S-class Writing Studio). Captured 2026-06-06 from Jon's feature
requests. Touches Writing-Studio composer + rules → keep Jon in the loop. Build after the content-node P0
(a draft must contain real content before composer polish matters).

## Current state (verified in code)
- **Manual editing EXISTS:** the draft body is an editable `<textarea>` (`data-writing-field='draft-content'`),
  tracked in `writingState.draftContent` with a live char counter. Direct typing/editing works.
- **AI compose chat EXISTS:** `data-writing-input='draft-request'` → `composeWritingDraftApi`
  (POST `/drafts/{id}/compose`) — "ask for a first draft, request a rewrite, drop source notes."
- **Highlight-to-edit FOUNDATION EXISTS:** the compose path already captures the editor's selected text
  (`editor.value.slice(selectionStart, selectionEnd)`) and passes it to the AI. The polished inline UX is
  what's missing.

## Feature requests (Jon 2026-06-06)

### 1. Pipeline-created drafts open in the composer immediately, content visible & editable
When the auto-draft pipeline produces a draft, it must land **in the composer with the written content
populated and the draft auto-opened/visible**, ready to edit — not an empty shell or a record you have to
hunt for. Two parts:
- (a) **Depends on the content-node P0** — today the content node hangs/produces an empty shell (no written
  content). Real content must be produced first.
- (b) Once produced, the draft's content must flow into the editable `draft-content` body and the draft
  should be the selected/open draft on arrival (deep-link / auto-select). Small wiring on top of (a).

### 2a. Manual editing — DONE (confirm save-on-edit persists)
Already a live textarea. Confirm/strengthen: edits persist to the backend (auto-save or explicit save via
`updateWritingDraftApi(id, {content})`), so manual edits aren't lost on navigation.

### 2b. Highlight → inline AI edit (the killer feature)
Select a passage → a small **"Ask AI to…"** popover with quick actions (Rewrite · Shorten · Lengthen ·
Change tone · Fix grammar · **Make on-brand**) + a custom free-text ask → AI alters **only that selection**
(grounded in the full draft for context + the Amira voice + the rules matching the draft's tags) → show the
result as a **diff/preview the user accepts or rejects** (AI proposes, human confirms). The selection is
already captured today; the build is the popover UX + a scoped "rewrite this span" compose call + the
accept/reject replacement.

## The 3-mode composer (target)
1. **Manual** — type/edit anywhere (have it).
2. **AI chat** — whole-draft requests (have it).
3. **Highlight → inline AI edit** — span-scoped rewrites with accept/reject (foundation there; build the UX).

## Why this compounds with the other systems
Highlight-to-edit is where **tagging + rules pay off**: rewriting a superintendent-email passage applies
that audience's rules + the brand voice to just that span. Composer + tag registry + tag-scoped rules lock
together into "writes well, on-brand, for the right reader."

## Constraints (carry into worker briefs)
Lossless (draft history/versions preserved — there's already a save-chat-version concept; never lose
content). AI edits are previews the human accepts (don't silently overwrite). Reuse the existing compose
engine + selection capture; don't fork. Org dep rule. Sequence after the content-node P0.

## Open questions
- Keep the plain textarea, or upgrade to a rich-text/structured editor (formatting, headings)? (textarea is
  fine for v1; richer later.)
- Auto-save cadence vs explicit save for manual edits.
- Where the quick-action set lives (fixed list + custom ask) and whether it's tag-aware (e.g. "Make
  on-brand" pulls the draft's tag rules).
