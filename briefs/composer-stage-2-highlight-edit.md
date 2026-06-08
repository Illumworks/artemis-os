# Build brief — Composer Stage 2: highlight → AI edit (the killer feature)

**Agent:** terminal (design-coupled interaction + builds on Stage-1's ProseMirror editor — Lead reviews look
+ code). **Branch:** `worker/composer-highlight-edit` off `main`. **Own git worktree, cwd inside. Own test
DB** (`artemis_test_highlightedit`). **Do NOT merge — report.** Read first: `docs/AGENT-WORKING-PRINCIPLES.md`,
`docs/COMPOSER-REBUILD-PLAN.md` (Stage 2), `docs/mockups/composer-v5-prototype.html` (the approved selection-
toolbar look — match it), and Stage-1's `public/js/features/composer-v5.js` (the live editor you extend).

## The point of this stage
Select a passage in the document → a floating toolbar offers AI edits → the AI rewrites **only that span**,
grounded in the full draft + the Amira voice + **the tag-scoped rules matching the draft's tags** → the user
sees it as an **accept/reject** change. This is where Phases 1–4 (tagging + rules) pay off visibly: a
superintendent-email rewrite automatically applies that audience's rules. "AI proposes, human confirms."

## Build on what exists (don't fork)
- Editor: `composer-v5.js` (Stage 1) — a live ProseMirror `EditorView`. It already tracks selection. The
  approved mockup shows the floating selection toolbar (appears on text selection, near the selection):
  buttons **Rewrite · Shorten · Lengthen · Tone · Make on-brand · ✎ (custom ask)**.
- Grounding (backend, DONE): `compose_engine.build_writing_memory_prompt` (voice) + `resolve_grounding_rules`
  (Phase 3 — tag-scoped rules for the draft's `structured_tags`) + `_latest_draft_content` (now live_content-
  aware). The compose route (`POST /api/writing-studio/drafts/{id}/compose`) already grounds in the draft's
  tag-scoped rules.
- Content format: plain-text + light markdown (Stage-1 decision) — the rewritten span comes back in that
  format and swaps into the PM doc via the same text↔doc conversion Stage 1 uses.

## Deliverables
1. **Functional selection toolbar** in the PM editor: appears on non-empty text selection, positioned near
   the selection (like the mockup + the Stage-1 prototype stub), hides on empty selection. Buttons:
   Rewrite · Shorten · Lengthen · Tone (small submenu or cycle) · Make on-brand · custom free-text ask.
2. **Scoped-rewrite backend** — a focused call that rewrites ONLY the selected span. Recommended: a new
   endpoint `POST /api/writing-studio/drafts/{id}/rewrite-span` body `{selectedText, instruction, fullText?}`
   → returns `{rewrittenText}` (the clean replacement, same plain-text+markdown format). It MUST ground via
   the existing machinery: the Amira voice (`build_writing_memory_prompt`) + `resolve_grounding_rules` on the
   draft's `structured_tags` (so "Make on-brand" / any rewrite applies the audience/type/platform rules) +
   the full draft as context. Single-shot (`max_iterations=1`). Reuse the provider cascade like
   `compose_draft`. (If you instead extend `compose`, it must still return a clean span replacement, not a
   chat reply — but a purpose-built endpoint is cleaner. Your call; justify it.)
3. **Accept/reject diff UX** — show the proposed rewrite against the original span (inline diff or a compact
   preview popover with Accept / Reject). **Accept** replaces only that span in the PM doc (then autosave
   fires as usual). **Reject** leaves the doc untouched. Never silently overwrite — the user confirms.
4. **"Make on-brand"** should clearly exercise the tag-scoped rules (it's the showcase) — verify a rewrite on
   a draft tagged e.g. `audience=superintendent` pulls that scope's rules into the grounding.

## Acceptance (verify the EFFECT — show it)
- Select a passage → toolbar appears near it; clears on deselect.
- Click Rewrite/Shorten/etc. → the scoped endpoint returns a rewritten span; it's shown as accept/reject;
  **Accept swaps only that span** (rest of the doc unchanged); Reject changes nothing.
- After Accept, the autosave round-trip persists the new body (fresh GET shows it; versions unchanged —
  lossless, per Stage 1).
- Prove the rules grounding: a "Make on-brand" rewrite on a tagged draft includes that tag-scope's rules in
  the prompt (log/trace the resolved rule titles, or assert via the resolver) — NOT all rules, NOT none.
- Mock the LLM in unit/integration tests (no live provider); a live manual smoke is fine for the report.
- `./scripts/check.sh` for touched Python; note PRE-EXISTING failures separately (don't fix). No console
  errors on select/rewrite/accept/reject.

## OUT OF SCOPE (later stages)
Claim flags (Stage 4), comments (Stage 6), pagination (Stage 5), drafts-picker beyond Stage 1, Google Doc,
Actions menu. Don't touch the claims backend or the templates work (Codex is in those in parallel).

## Constraints
Lossless (Accept goes through the Stage-1 autosave; never lose content; rejects are no-ops). Reuse the voice
+ tag-rule grounding + provider cascade — do NOT fork the compose engine. Match the mockup's toolbar look.
If you add a backend endpoint, it's additive (likely no migration). Isolated worktree + own test DB. **Do
NOT merge** — report branch + SHA + worktree + a manual smoke of select→rewrite→accept (paste before/after
span + proof the rest of the doc is unchanged) + proof the rewrite used the draft's tag-scoped rules.
Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
