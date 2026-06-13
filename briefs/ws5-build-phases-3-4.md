# Worker Brief — WS5 Co-Editing, Phases 3–4 (live text sync + coexistence hardening)

**Owner:** terminal (fan out sub-agents within a phase as useful). **Lead:** Artemis (Opus) verifies LIVE with
2 browsers + merges. **Status:** READY. Builds on P0–P2 (all merged + live on `main`).
**Full design:** `docs/ws5-coedit-architecture.md` — §1 (decision), §2 Phase 3 & 4, §3 risk register (R4/R5/R6),
§4 file/line map. **Read it first; this brief is the build order + ship gates + the load-bearing constraints.**

**Isolation (AGENTS.md rule 6):** isolated worktree, branch per phase (`worker/ws5-p3`, `worker/ws5-p4`),
each off latest `main`. **Do-NOT-merge-report per phase** — Lead verifies each live and merges before the next.

## Hard constraints (both phases)
- **Hands-off selection toolbar.** Route ALL remote edits through `dispatchTransaction`/`view.updateState` so
  the toolbar's own cycle runs; map `selectionRange`/`pendingRewrite` in the **collab layer, not the toolbar**.
  No changes to `updateSelectionState`/`positionNearSelection`/`show`/`hide`/`handleOutsidePointerDown` or the
  `.cv5-paper`/`.ProseMirror` CSS. (Risk R6.)
- **Single durable truth stays `deliverable_metadata.live_content`.** The collab step log is ephemeral
  coordination; the room is the single writer and debounce-flushes materialized text via a shared
  `set_live_content` helper. Do NOT introduce a second durable document store. (Risk R1.)
- **Degrade gracefully:** if the socket is down, fall back to today's HTTP autosave with the P2 soft-lock.

## Phase 3 — Live text sync via `prosemirror-collab`
1. **Vendor the dependency (org-rule-sensitive — Lead will verify this at merge).** `prosemirror-collab@1.3.1`
   (published 2023-05-17, 3+ years old — clears the 7-day rule). Vendor as an esm.sh bundle keeping
   `prosemirror-state` **external** (match the existing 14 PM packages); add one `.mjs` to
   `public/vendor/prosemirror/`, one import-map line in `public/index.html`, extend
   `scripts/vendor-prosemirror.sh`, **commit the lockfile.** Verify the `instanceof`/shared-copy invariant
   against the single vendored `prosemirror-state` (VERSIONS.md). **No CRDT runtime, no new transitives** —
   this one module is the entire dependency delta. (Do NOT pin any `<7-day-old` version; never `y-websocket`.)
2. **Editor:** add `collab({version, clientID})` to **both** plugin arrays (composer-v5.js:233 **and** the dup
   in `replaceEditorContent`:3442). In `dispatchTransaction`, after `updateState`, `sendableSteps()` → ship
   `{version, steps, clientID}` over the P0 socket. On inbound remote steps → `receiveTransaction()` and
   dispatch through the **same** path (toolbar + decoration mapping run normally).
3. **Backend room = authoritative step-ordering service:** per-draft version + step log, broadcast to other
   clients, **per-draft serialization point** for concurrent step submission. Room is the single writer to
   `live_content` (debounce-flush via the shared `set_live_content` helper extracted from the PUT handler,
   correct `flag_modified`).
4. **Save-version coexistence:** Save-version stays HTTP; after it commits + clears `live_content`, it
   publishes a commit event; the room re-hydrates from `versions[0].content`, bumps version, broadcasts a
   `doc.rebase` so all editors snap to the saved version (prevents re-flushing stale `live_content`).
5. **Convert the state-swap flows (R5):** `replaceEditorContent` (Google Docs import, AI apply-to-document,
   chat refresh, undo-apply) must become collaborative `replaceWith` transactions through collab — NOT
   `view.updateState` state swaps (which destroy shared history + overwrite peers). If a rebuild is
   unavoidable, re-include `collab()` in the rebuilt plugin array.
- **Ship gate (Lead, 2 browsers):** both type in the **same paragraph simultaneously** → converge to identical
  text; Save-version in one snaps both to the saved version; socket-down falls back to soft-lock autosave.

## Phase 4 — Coexistence + conflict hardening
Cross-cutting correctness so Phase 3 is safe with the rest of the editor (detail in §3 R4–R10):
- **Claim-flag replace** (`handleClaimReplace`): re-map stamped `from/to` through remote steps before
  `replaceWith` (not just clamp to docSize).
- **Comment/claim anchors:** migrate char-offset-over-serialized-text toward collab-mapped PM positions, OR
  document the drift-tolerant "anchor lost" fallback as the explicit v1 boundary.
- **Decoration data timing:** gate async scan/comment `setMeta` payloads on the doc version they were computed
  against (or re-map on arrival) — a vN batch must not apply to vN+k blindly.
- **Undo/redo:** collab-aware, rebased **local-only** undo (don't undo other people's edits).
- **Multi-worker boundary (R10):** the in-memory room registry clobbers across >1 worker. **Pin collab WS to a
  single worker for v1** (sticky/single-process); document Redis pub/sub or Postgres LISTEN/NOTIFY (one elected
  flusher per draft) as the pre-scale-out follow-up. Verify the deploy's worker count.
- **Presence roster over-count (found in P3 live test):** with 2 users, the avatar cluster showed **3
  avatars** — the roster isn't deduping by user and/or sockets aren't torn down on composer
  unmount/re-render/navigate (a per-connection leak). Dedup the roster by user identity AND ensure the
  per-mount collab socket is closed in `destroy()` / on every re-mount. (Text sync + data are correct; this is
  the presence-display layer.) Verify: 2 users → exactly 2 avatars; reload one → still 2, not 3.
- **Ship gate (Lead, 2 browsers):** with claims, comments, and a Google Docs import exercised live across two
  clients, anchors land on the right spans and no client's edits are lost.

## Verification protocol (Lead)
Each phase: Lead spins an isolated dev instance (throwaway DB, dev mode, `?collab_as=alice/bob`) and confirms
the ship gate live with 2 browsers before merging. Lead also verifies the P3 dependency: vendored version is
1.3.1 (3+ yrs old), lockfile committed, zero new transitives. Merge p3 → verify → p4 → verify. This closes WS5.
