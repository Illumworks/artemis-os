# Composer Rebuild — Build Plan (v5)

**Read for anything composer.** The Writing Studio composer rebuild to the v5 spec. Design intent lives in
`briefs/writing-studio-composer-ux.md` + the visual mock `docs/mockups/composer-design-pass.html`. This doc
is the BUILD plan: the engine decision, the staged sequence, who builds what, and the design touchpoints.
Maintained by Opus Lead. **Jon signed off (2026-06-07): "robust engine foundation."**

## Reality check (from a fresh code recon, 2026-06-07)
The composer today is **chat-only**. The old editable `<textarea>` (`data-writing-field='draft-content'`)
is a DEAD reference — it no longer renders. The draft shows as a **read-only bubble inside the chat thread**;
the only editable field is the AI message box. So selection-capture (`selectedText`) is always empty in
practice. **There is no editable document surface today.** Front-end is one monolithic vanilla-JS module
(`public/js/features/writing-studio.js`, ~3,383 lines) + `public/css/features/writing-studio.css` (~3,518),
no framework, no build step, `innerHTML`-blitting renders. All BACKEND the composer needs exists (compose
engine, draft CRUD, tag-scoped rules Phases 1–4, training candidates). **This is a front-end build; the
AI-writing brain it plugs into is ready.**

Conclusion: this is "build the document half of the composer," not "polish the editor."

## Engine decision — ProseMirror (Lead's technical call; Jon approved the "robust" direction)
**Use ProseMirror** as the editing engine. Rationale:
- Framework-agnostic (fits our vanilla SPA — no React required), gold-standard for collaborative rich-text.
- Its **decorations + marks** model is exactly right for inline claim-flags (orange double-underline) and
  comments anchored to spans — the v5 features that a `<textarea>`/`contenteditable` can't host cleanly.
- **`prosemirror-collab` / Yjs** gives a real path to the roadmapped real-time co-editing — no rewrite later.
- No paid tiers / vendor lock-in; AI models know ProseMirror deeply (helps the "AI maintains this" goal).
- Considered + rejected for now: **Tiptap** (great PM wrapper, but pushes a paid cloud for Comments/collab
  and leans framework — we'd build comments on PM decorations ourselves anyway); **Lexical** (viable, but
  PM's mark/decoration maturity for comments-on-spans is the safer fit for our exact needs); **Quill/Slate**
  (weaker fit for complex marks / vanilla).
- **Integration without a heavy bundler:** vendor pre-built **ESM** bundles of the ProseMirror packages into
  `public/vendor/prosemirror/` and import them as ES modules — keeps the no-build vanilla setup, no runtime
  CDN dependency. Pin versions; record them (org dep rule: PM is mature/years-old, fine — never adopt a
  build <7 days old; commit any lock/manifest).

## Staged build sequence (each stage shippable + Lead-verified before the next)
1. ✅ **FOUNDATION — DONE + MERGED + VERIFIED (2026-06-07).** chat LEFT / document RIGHT (38/62) with a real
   ProseMirror editable document (vendored locally — `public/vendor/prosemirror/`, ESM + import map, works
   offline; new `composer-v5.js`/`.css`). Debounced autosave persists a transient `live_content` (PUT
   `/drafts/{id}` `liveContent`) **without minting a version** — Save-version mints + clears it; serializer +
   `compose_engine._latest_draft_content` prefer `live_content`. **Lossless + backward-compatible** (pipeline
   drafts have no live_content → unchanged). Content format = **plain-text + light markdown (NOT HTML)** so
   all consumers (LLM prompt, chat fallback, Google Doc export) keep working. Teardown+flush on draft-switch/
   page-leave = no data loss. Comments-rail toggle in. Verified on main: assets serve, autosave round-trip
   lossless (versions untouched), PM loads offline (terminal's no-CDN proof). *Legacy layout kept for memory-
   bank + version-history panels.*
2. ✅ **HIGHLIGHT → AI EDIT — DONE + MERGED + VERIFIED (2026-06-07).** Floating selection toolbar (Rewrite·
   Shorten·Lengthen·Tone·**Make on-brand**·custom) on the PM editor → `POST /drafts/{id}/rewrite-span`
   (single-shot, grounds via voice + `resolve_grounding_rules(structured_tags)` — the tagging payoff) →
   accept/reject popover; Accept replaces ONLY the selection range (`tr.replaceWith(from,to)`) then autosaves
   (lossless), Reject is a no-op. Verified: tag-scoped rules resolved (not all/none); span-replace invariant
   holds; no compose-engine fork. *Deferred polish: Tone submenu (fixed instruction for now).*
3. **DRAFTS PICKER → header popover.** Move the drafts list out of the sidebar into a Finder-style popover
   from a header button; single **"+"** menu (New draft / New from template / New folder).
4. ✅ **INLINE CLAIM FLAGS — DONE + MERGED + BROWSER-VERIFIED (2026-06-08).** Conservative deterministic
   detector (`claim_detector.py`): flags only strong-claim language (quantified/superlative/comparative) NOT
   matching an approved register claim (token-set similarity ≥0.60 suppresses approved language); quiet on
   ordinary copy (0 flags on the on-brand draft). `POST /drafts/{id}/claim-scan`. PM-decoration orange
   double-underline (single-pass exact char→PM-pos map — underline lands exactly on the claim in multi-
   paragraph drafts, browser-verified), hover peek + click popover (Approve/Edit/Find source), ＋Add-to-
   register from the selection toolbar — both grow the register (lossless). Register = 88 approved claims
   (8 seed + 80 harvested verbatim from published content). Backend (Claims Register table + CRUD, migration
   0072) merged earlier. Tunables: SUPPRESS_THRESHOLD, pattern classes.
5. **FORMAT-AWARE PAGINATION.** Long-form types (guides/papers) break into Page 1·2·3; email/short = one
   continuous page.
6. **FLOATING COMMENTS (Google-Docs margin).** Anchored to a span via connector, expand/collapse, reply/
   resolve, **@mention + ping**. **DEPENDS ON identity/SSO** (attribution) — sequence after the identity
   track (`briefs/writing-studio-identity-and-gdoc.md`).
7. **GOOGLE DOC link/import/export.** Compact header affordance. Backend stubs exist but are UNIMPLEMENTED;
   **rides Google OAuth** (same as SSO).
8. **ACTIONS MENU (⋯).** Save-as-template (+ apply via the "+" menu — ship both or drop templates), Repurpose,
   Brand+readability check. **Templates BACKEND DONE (2026-06-07):** structured `templates` table + `/api/
   writing-studio/templates` CRUD (retire=lossless) + seed of 6 corpus templates + `POST /templates/{id}/
   apply` (instantiates a real draft from the template body) — merged + verified (migration 0073). Known
   compromise: applied drafts share a placeholder campaign candidate (candidate_id still required) — revisit
   when wiring into the composer. Remaining for this stage: the composer ⋯ menu UI (Save-as / New-from /
   Repurpose / Brand-check).

**Order:** 1 → 2 (highest value, showcases tagging) → 3, 4, 5, 8 (largely independent) → 6 (after identity)
→ 7 (after OAuth). Stage 1 must land + be Jon-approved on look before 2+.

## Design touchpoints (Creative-Director — Jon + Lead together; Lead prototypes + screenshots)
The foundation look (stage 1), the selection-toolbar UX (stage 2), claim-flag styling (stage 4), comment
float behavior (stage 6). Lead builds a working prototype + screenshots via Claude_Preview for Jon to react
to BEFORE the full build of each design-sensitive stage.

## Who builds what
- **Novel / design-coupled (foundation, selection toolbar, claim flags, comments):** terminal + Lead (Lead
  prototypes/reviews; the ProseMirror integration is new to the repo, warrants care).
- **Well-scoped slices (drafts picker, pagination, actions menu):** Codex-friendly briefs.

## Constraints (carry into every stage brief)
Lossless (draft history/versions preserved; never silently overwrite — AI edits are accept/reject previews).
Reuse the existing compose engine, selection plumbing, and header affordances (proposed/rules/history); don't
fork. No persistent chrome — features live in contextual surfaces (selection toolbar / inline flag / margin /
collapsible bar / actions menu / small header indicators). Org dep rule on the vendored PM packages.
