# Worker Brief — Writing Studio: re-surface rule-proposals + memory-files UI in composer-v5 (FE)

**Owner:** terminal (frontend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/ws2-rules-memory-ui`. Writing Studio backlog #2. **Pure FRONTEND re-surfacing — the backend
already exists and must NOT be rebuilt.** Verify endpoints against the running app before wiring.

## Context (grounded)
Three features existed in the OLD Writing Studio UI but were dropped in the composer-v5 redesign. The backend
(endpoints + DB + even the FE API-client functions) is fully present; only the composer-v5 UI affordances are
missing. Build them natively in composer-v5's own idiom/style — the frozen Node app
(`/Users/artemis/Artemis/claudeck-artemis/public/js/features/writing-studio.js`) is a REFERENCE for what the
affordances did, NOT code to copy (per CLAUDE.md: reference only, don't copy).

## Features to restore (all FE-only)

### 1 + 2. Training candidates: AI-proposed rules + manual proposal
Backend (verify exact current paths first — namespace may be `/api/writing-studio/training-candidates`):
- `GET /training-candidates` (list by status/profile), `POST /training-candidates` (manual propose; `proposedText`
  min 10 chars, optional `candidateType`/`rationale`/`profileId`/`draftId`),
  `POST /training-candidates/{id}/decision` (approve/reject; approve promotes to a real WritingRule/Example).
- DB: `writing_training_candidates`. The compose endpoint ALREADY persists agent-proposed candidates at
  status="proposed" (`extract_proposed_learnings`), so AI proposals exist as soon as a compose runs.
- FE API client already has: `listWritingTrainingCandidatesApi`, `createWritingTrainingCandidateApi`,
  `decideTrainingCandidateApi` (`public/js/core/api.js` ~817-836) — reuse; verify the URLs match live routes.

Build in composer-v5:
- A **proposed-rules surface** (panel or modal) that lists `status="proposed"` candidates with Approve / Reject
  per item, wired to `decideTrainingCandidateApi`. Refresh after a compose completes (new AI proposals appear).
- A **manual "+ Propose a rule" form** (textarea + submit, min 10 chars) wired to
  `createWritingTrainingCandidateApi`, scoped to the current draft/profile where applicable.

### 3. View + edit Writing Studio "memory files"
"Memory files" = the editable voice assets: **WritingRule, WritingSource, WritingExample**.
Backend (verify exact paths; may be `/api/writing-studio/rules|sources|examples` or `/api/writing-rules/...`):
- List + edit each type (the overview endpoint likely already returns them). FE client has
  `updateWritingRuleApi`/`updateWritingExampleApi`/`updateWritingSourceApi` (`api.js` ~1130-1149).
- **Verify the HTTP verb:** the old client used PUT for source/example but routes may be PATCH — align the
  client to the actual route method (fix the mismatch if present; don't assume).

Build in composer-v5:
- A **"Memory" entry point** (sidebar card / button) that opens a memory-bank view listing rules, sources,
  examples, with an **edit** affordance per asset (textarea to revise body/content) wired to the update APIs.

## UX / placement
Placement, styling, and modal-vs-panel are Creative-Director calls — match composer-v5's existing patterns and
keep it clean; Jon may adjust visually after. Don't introduce a new design language. Respect the hard-won
composer-v5 selection-toolbar / ProseMirror code — do NOT touch `updateSelectionState`/`positionNearSelection`/
`showSelToolbar`/`hideSelToolbar`/`handleOutsidePointerDown` or the `.cv5-paper`/`.ProseMirror` padding.

## Constraints
- **No backend changes** expected. If you find a real backend gap (missing endpoint, broken route), STOP and
  flag it to Lead rather than building backend in an FE brief.
- Verify every endpoint/verb against the actually-running routes before wiring (don't trust the old client blindly).
- Don't regress composer-v5 compose, the draft picker, claims, or apply-to-document.

## Tests / verification
- FE: the proposed-rules surface lists real `proposed` candidates and approve/reject hits the decision endpoint
  (approve promotes); the manual form creates a candidate; the memory view lists rules/sources/examples and an
  edit saves via the correct verb. Verify in a real browser (this is UI) — load a draft, run a compose, see the
  AI proposal appear, approve it, confirm it becomes a rule; open memory, edit a rule, confirm it persists.
- Note any endpoint/verb mismatch you corrected.

## Acceptance
In composer-v5: AI-proposed rules surface for approve/reject, a user can manually propose a rule, and the
"memory files" (rules/sources/examples) can be opened and edited — all wired to the existing backend, no
backend rebuild. Lead verifies live with Jon in the browser.
