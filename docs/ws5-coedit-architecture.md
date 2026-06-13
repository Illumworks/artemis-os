# Writing Studio — Real-Time Co-Editing Architecture

**Status:** ✅ SHIPPED 2026-06-13 — P0–P4 merged + live (P0 collab WS, P1 presence, P2 soft-lock,
P3 live text sync via prosemirror-collab@1.3.1, P4 coexistence hardening). Verified in real 2-browser tests
(live bidirectional sync converges; soft-lock rejects stale writes; presence dedupes to 2 avatars).

**v1 residuals / tracked follow-ups (not blocking — edges):**
- **Collab-aware undo/redo** not done — `Cmd+Z` during *simultaneous* editing may undo a peer's recent edit
  (ProseMirror history × collab interplay). Recoverable; edge case. Track for v1.1.
- **Socket teardown on repeated reload** incomplete — presence avatar count inflates if a user reloads the
  same draft many times in a session (sockets not torn down on navigate); normal single-open = correct 2.
  Cosmetic; track.
- **Multi-worker fan-out** not implemented — moot today (prod runs a single uvicorn worker). Do Redis pub/sub
  or Postgres LISTEN/NOTIFY (one elected flusher per draft) **before** scaling to >1 worker.
**Date:** 2026-06-12
**Author:** Lead architect synthesis of four parallel investigations (A approach/library, B composer audit, C sync backend, D presence/UX + phasing).
**Scope:** composer-v5 (Writing Studio drafts) live multi-writer co-editing, presence, and the backend that backs it.

---

## 0. The decision in one paragraph

We will ship **presence and cursors first over the existing single-writer save path**, then introduce true live text sync using **`prosemirror-collab` (server-authoritative steps + rebasing)** — **not Yjs**. The deciding factor is our persistence model: drafts are stored as a lossy plain-text snapshot inside one JSONB column (`deliverable_metadata.live_content`), read everywhere through a single `live_content`-over-`versions[0]` precedence rule. Yjs's value proposition is that its binary CRDT update stream becomes the source of truth — adopting it means a second, divergent durable store bolted next to the text column, plus 4+ new vendored ESM modules with a fragile `external`-bundle config. `prosemirror-collab` is a tiny pure-ProseMirror module (one new vendored `.mjs`, one import-map line, zero new transitive deps) that keeps the server authoritative over *steps* while `live_content` stays the materialized snapshot — it layers on top of what exists instead of replacing it. The phase ordering exists so the hard, least-reversible backend decision (the step-authority service) is made *after* the socket, auth, room model, and reconnect UX are already proven in production with real multi-client traffic.

---

## 1. ARCHITECTURE DECISION

### 1.1 Chosen approach: `prosemirror-collab` + server-authoritative step log; whole-body autosave demoted to a derived snapshot

**Editor side:** add the `collab()` plugin to the ProseMirror plugin stack. Edits flow out as `sendableSteps()` over a WebSocket; remote steps arrive and are applied via `receiveTransaction()`. The server is the central authority that orders steps, assigns a monotonic version, rebases, and rejects on version mismatch (client re-requests and reapplies).

**Backend side:** a new per-draft WebSocket room becomes the **single writer** to `deliverable_metadata.live_content`. The room holds the authoritative step log + version + an in-memory working copy hydrated from the existing column via `_latest_draft_content`. It debounce-flushes the materialized text back into `live_content` through the *same* mutation helper autosave uses today. The durable truth never leaves the JSONB column; the step log is the live-coordination mechanism, not a new durable store. Version history (`versions[]`) is completely untouched — only the explicit "Save version" HTTP path mints version rows and clears `live_content`, exactly as today.

### 1.2 Why `prosemirror-collab` and not Yjs (resolving the A/B/C conflict)

The four investigations split: A → collab, B → leaned CRDT, C → no-CRDT whole-body LWW, D → presence-first then collab. The decisive arguments:

1. **Persistence model is the real blocker for Yjs (A, C).** Our autosave path serializes the PM doc to a plain-text blob (`serializeDocToText`) and reparses on load (`textToProseMirrorDoc`). The stored artifact is lossy markdown-ish text, read through one precedence rule (`compose_engine._latest_draft_content`, mirrored in the detail serializer). Yjs wants the Y.Doc binary update stream to *be* the truth — that forces a new binary `ydoc` column, a backend Yjs persistence provider, and continuous reconciliation against the text column. That is precisely the "divergent source of truth" C was told to avoid. `prosemirror-collab` keeps the server authoritative over *steps* and lets `live_content` remain the derived snapshot.

2. **No-bundler vendoring model (A, D).** ProseMirror is vendored as esm.sh `?bundle-deps&external=<other-PM-pkgs>` builds in `public/vendor/prosemirror/`, resolved by the import map in `public/index.html`. `prosemirror-collab@1.3.1` is pure PM whose only dependency (`prosemirror-state`) is already vendored — one more `.mjs`, one import-map line, re-run `scripts/vendor-prosemirror.sh`. Yjs pulls a runtime family (`yjs`, `y-prosemirror`, `y-protocols`, `lib0` transitive), each needing its own esm.sh bundle, and `y-prosemirror`'s `instanceof` checks against `prosemirror-state/-view/-model` mean its build must externalize those to share the single vendored copy — exactly the single-shared-copy fragility VERSIONS.md warns about.

3. **Conceptual fit + maintenance (A).** collab is authored by Marijn (same as all of PM), API-stable since 1.3.x, and uses the transaction/step model the team already manipulates in `dispatchTransaction`. Yjs is a second independent state engine with its own mental model to learn and debug in an unmanned no-build SPA.

**The one honest reason to revisit:** offline-first, multi-day divergent editing. collab is an *online* central-authority model; on reconnect it rebases queued local steps onto the server's newer version, which works for short disconnects but has no conflict-free merge of long-divergent sessions. Yjs's CRDT is the technically superior answer *only* if genuine offline-first divergence becomes a hard product requirement. Our editor lives behind Cloudflare Access and is used online, so this boundary is acceptable. If that requirement ever lands, the migration is to make the column a *projection* of a CRDT — a deliberate, explicit project, not a default.

**Why not C's "whole-body LWW over WebSocket, no collab library at all"?** C's design is correct and is in fact what we ship for Phases 0–2 (presence + soft-lock). But as the *end state* for true co-editing it cannot merge two people typing in different paragraphs within one flush window — one snapshot silently wins. That is the exact clobber bug we are trying to kill, just moved from the 1.2s autosave tick to the room flush tick. So we adopt C's room/coordinator/coexistence design wholesale as the transport and persistence spine, and layer collab's step log on top of it at Phase 3 to get convergent merge without the lossy whole-body replace. C and A are complementary, not competing: C is the backend skeleton, A is the merge engine that rides it.

### 1.3 How it loads in our no-bundler ESM setup

- **Vendor:** `prosemirror-collab@1.3.1` (last publish 2023-05-17, 3+ years old — clears the 7-day rule with enormous margin) as an esm.sh bundle that keeps `prosemirror-state` **external**, matching the existing 14 vendored PM packages. Add one `.mjs` to `public/vendor/prosemirror/`, one line to the import map in `public/index.html` (lines 42-61), and extend `scripts/vendor-prosemirror.sh`. Commit the lockfile.
- **Verify the shared-copy invariant:** after vendoring, confirm `collab`'s `instanceof`/version interop against the single vendored `prosemirror-state` (VERSIONS.md invariant). This is the one vendoring footgun and it is small.
- **No CRDT runtime, no new transitive tree.** This is the entire dependency delta for the merge engine. Phases 0–2 (presence/cursors/soft-lock) add **zero** new vendored deps — they reuse `prosemirror-history` (already vendored via `exampleSetup`) and the existing decoration-plugin pattern.
- **Fix while you're in there:** VERSIONS.md (lines 7-8) names `writing-studio.html`/`writing-studio.js` as the import-map host, but the map actually lives in `public/index.html`. Cosmetic; correct it when vendoring so the next person targets the right file.

### 1.4 Pinned versions (7-day rule, verified against today = 2026-06-12)

| Package | Pin | Last publish | Margin | Notes |
|---|---|---|---|---|
| **prosemirror-collab** | **1.3.1** | 2023-05-17 | 3+ yrs | The chosen merge engine. |
| prosemirror-history | (already vendored) | — | — | Via `exampleSetup`; collab-aware undo lives here. |
| ~~yjs~~ | ~~13.6.31~~ | 2026-05-28 | 15 days | Rejected. Would clear the rule, but not adopted. |
| ~~y-prosemirror~~ | ~~1.3.7~~ | 2025-07-03 | ok | Rejected. |
| ~~y-websocket~~ | ~~3.0.0~~ | 2025-04-02 | ok | Rejected. **Never** pin 3.1.0-rc.* (published this week) — prerelease AND <7 days, doubly excluded. |

Org rule reminder for implementers: never add/upgrade any dep (Python, npm, GitHub Actions, Docker base, **or transitive**) to a version <7 days old unless directly answering a known CVE (documented in the PR). Lockfiles committed regardless. The chosen pin clears this by years.

---

## 2. PHASED BUILD PLAN

Design principle carried from D: **every phase ends at a point you can open two browser windows (two Cloudflare Access identities, or the `dev@local` shim plus a second) and *see* the new behavior.** Phases 0–2 never touch the write path; Phase 3 swaps the merge engine underneath an already-proven presence surface. Phases 1–2 are independently shippable products on their own — if Phase 3 gets deprioritized, soft-lock alone already kills the silent-clobber bug.

### Phase 0 — Identity-aware per-draft WebSocket (enabler, thin)

**What:** New endpoint `GET /api/writing-studio/drafts/{draft_id}/collab` upgraded to WebSocket. Verify the Cloudflare Access JWT *on the WS upgrade* — read `websocket.headers.get("cf-access-jwt-assertion")` and run the same `get_cf_access_verifier().verify_jwt()` path as `resolve_request_identity`; on failure `await websocket.close(code=4401)` before `accept()`. Yields the same `RequestIdentity(email, name)` used everywhere else — one verifier, one trust boundary. Dev mode keeps the `dev@local` shim (with a dev-only query-param identity override so presence is testable with distinct users). Client opens a **per-mount** socket (mirroring `floating-artemis-api.js`'s per-session socket), reusing `public/js/core/ws.js`'s reconnect/backoff/keepalive; torn down in the composer's `destroy()`.

**Why this is its own phase:** WS auth today is identity-blind (shared `ARTEMIS_TOKEN`, `artemis/ws/routes.py`). Presence needs *who*, not just *allowed*. This is genuinely new code and the riskiest auth surface — isolate it.

**Live-verifiable ship gate:** two browsers connect to the same draft; `room_count` shows 2; heartbeats flow; kill one and reconnect works. Nothing user-facing required.

### Phase 1 — Presence: who's-here avatars + remote cursors + selection highlights

**What (1a) Avatars:** server tracks the per-draft roster and broadcasts join/leave. Client renders an avatar cluster in `renderShell`'s header (`cv5-hdr`, between status and spacer), 24px initials circles, deterministic color hashed from `User.id` (same color for a person across caret/selection/avatar everywhere), capped ~5 with a "+N" chip, self faint/omitted, hover → name+email. Join/leave toasts via the existing `callbacks.onStatus?.(...)` channel.

**What (1b) Cursors + selections:** a third decoration plugin `presencePlugin`, updated via `setMeta` exactly like `claimFlagsPlugin`/`commentsPlugin`. Remote collapsed selection → `Decoration.widget` (2px colored caret + name flag that idle-fades after ~2s). Remote range → `Decoration.inline` low-alpha band in the peer's color (reuse `cv5-comment-anchor-hl` visual language). **Critical:** remote `{from,to}` positions must be mapped through `tr.mapping` on every *local* transaction — same discipline the existing plugins already model — or your own typing makes remote carets drift. The remote-cursor overlay must be `pointer-events:none` and absolutely positioned so it never reintroduces dead margin or intercepts drag (respects the hands-off whole-paper-selectable design).

**Emit:** local selection broadcast added in `dispatchTransaction` right next to the existing `updateSelectionState()` call, debounced ~150–200ms, sending `{from,to}` only. Presence/cursor traffic is ephemeral — **never persisted, never flushed to the DB.** This is the high-frequency, low-value-if-lost traffic the WebSocket is perfect for.

**Reconnect UX:** reuse `ws.js` state. On `ws:disconnected` dim the cluster, show a reconnecting pill, stop emitting. On `ws:reconnected` re-announce self, request a fresh roster, clear all stale remote decorations before repainting.

**Live-verifiable:** select text in window A → window B sees a colored band + named caret that tracks as A moves; close A → its avatar and decorations vanish.

### Phase 2 — Soft-lock: kill the silent-clobber bug over the *current* save path

**What:** while the doc is still whole-body LWW, stop autosave from blindly overwriting concurrent edits. Add a monotonic `live_content` version counter; autosave includes the version it was based on; the server does a compare-and-set and **rejects stale writes**. The late writer sees a non-destructive banner ("X is also editing — your changes weren't saved, reload to merge") instead of silently losing a paragraph. Optionally a section-level "Jon is editing here" indicator driven off presence selection ranges from Phase 1.

**Why before full sync:** this is a complete, shippable product increment that fixes the *actual* user-facing bug (today two people on a draft silently clobber each other on every 1.2s autosave tick). It is a small fraction of full co-editing and de-risks it: by the time we build the hard part, the socket, auth, room model, reconnect/resync UX, and cursor-mapping discipline are all proven with live multi-client traffic. **This is the natural stopping point if co-editing is deprioritized.**

**Live-verifiable:** both windows edit; the late writer is warned, not clobbered.

### Phase 3 — Live text sync via `prosemirror-collab` (the end goal)

**What:** vendor `prosemirror-collab@1.3.1` (§1.3). Add `collab({version, clientID})` to **both** plugin arrays (the editor at composer-v5.js:233 *and* the duplicate inside `replaceEditorContent` at :3442 — state rebuilds must re-include it). In `dispatchTransaction`, after `view.updateState(next)`, call `sendableSteps(view.state)`; if non-null ship `{version, steps, clientID}` over the Phase-0 socket. On inbound remote-step messages, build a transaction with `receiveTransaction(state, steps, clientIDs)` and dispatch it through the *same* path (so the selection toolbar and decoration mapping all run normally). The backend WS room becomes the authoritative step-ordering service: per-draft version, step log (or current version + periodic snapshot), broadcast to other connected clients, **per-draft serialization point** for concurrent step submission against Postgres.

**Persistence reconciliation (C's coexistence story, now load-bearing):**
- The **room is the single writer** to `live_content`. Connected clients stop PUTting `liveContent` directly; they emit steps and the room debounce-flushes the materialized text (`serializeDocToText` of the converged doc) into `deliverable_metadata.live_content` through a shared `set_live_content(session, draft_id, text)` helper extracted from the current PUT handler (one mutation site, correct `flag_modified` on the JSONB).
- **Save-version stays HTTP and the room yields to it.** The Save-version handler, after committing and clearing `live_content`, publishes a "committed at version N" event (in-process, or via the existing `writing_studio.events` bus). The room re-hydrates its working copy from the new `versions[0].content`, bumps version, and broadcasts a `doc.rebase` so all editors snap to the saved version — preventing the room from re-flushing stale `live_content` over the just-saved version.
- **Fallback:** if the socket is unavailable, the composer falls back to its existing HTTP autosave (Phase 2 soft-lock semantics). The feature degrades to today's behavior rather than breaking.

**Replace, don't bypass, the state-swap flows (from the B audit):** `replaceEditorContent` (Google Docs import, AI "apply to document", chat refresh, undo-apply) currently rebuilds the EditorState and `view.updateState()` — under collab that destroys shared history and silently overwrites peers. These must become collaborative steps (a `replaceWith` transaction that goes through collab), not state swaps, and must re-include the `collab()` plugin if a rebuild is unavoidable.

**Re-map queued positions through remote steps (from B audit):** `selectionRange`, `pendingRewrite.{from,to}`, claim `data-claim-from/to`, and `commentAnchorRange` are captured once and not re-mapped through remote transactions. The collab integration layer must map them through every remote step's mapping — *not* the selection toolbar (which stays untouched).

**Live-verifiable:** both windows type in the same paragraph simultaneously and converge to identical text.

### Phase 4 — Coexistence + conflict hardening

**What:** the cross-cutting correctness work that makes Phase 3 safe with the rest of the editor's features. Items (detailed in the Risk Register §3):
- Claim-flag replace (`handleClaimReplace`) must re-map stamped `from/to` through remote steps before `replaceWith`, not just clamp to docSize.
- Comment/claim anchors: migrate from char-offset-over-serialized-text (recomputed per-client, drifts across clients) toward collab-mapped PM positions, or accept the existing drift-tolerant "anchor lost" fallback as the documented boundary for v1.
- Decoration *data* sync timing: scan/comment batches fetched async against doc-vN can apply to doc-vN+k; gate `setMeta` payloads on the version they were computed against, or re-map on arrival.
- Undo/redo: `exampleSetup`'s history undoes *other people's* edits under multi-writer; collab requires rebased local-only undo (prosemirror-collab + history interplay).
- Multi-worker fan-out: the in-memory room registry clobbers across >1 worker. Pin collab WS to a single worker for v1 (sticky routing / single process); schedule Redis pub/sub or Postgres LISTEN/NOTIFY fan-out (one elected flusher per draft) before scale-out.

**Live-verifiable:** with claims, comments, and a Google Docs import exercised live across two clients, anchors land on the right spans and no client's edits are lost.

---

## 3. RISK REGISTER

| # | Risk | Severity | Likelihood | Mitigation | Owner phase |
|---|---|---|---|---|---|
| R1 | **Divergent source of truth if a CRDT is adopted.** Yjs would create a second durable store next to `live_content`, requiring continuous reconciliation; the materialized text and CRDT binary can drift in formatting fidelity. | High | — (avoided by decision) | **Decision: reject Yjs.** Keep `live_content` JSONB as the one durable truth; collab step log is ephemeral coordination, room is the single writer. If offline-first ever becomes a hard requirement, migrate the column to a *projection* of the CRDT as an explicit project. | §1.2 |
| R2 | **Autosave whole-body LWW silently clobbers concurrent editors.** Two editors → one 1.2s snapshot wins, the other's paragraph vanishes with no error. This is the actual user-felt bug. | High | High (today) | Phase 2 soft-lock (version counter + compare-and-set + non-destructive banner) fixes it before full sync. Phase 3 replaces it with convergent step merge. | P2, P3 |
| R3 | **`prosemirror-collab` rebasing fails on long-divergent offline sessions.** collab is online central-authority; no conflict-free merge of multi-day divergence. | Medium | Low (editor is online, behind CF Access) | Accept as documented boundary; fall back to reload/reconcile on rebase failure. Revisit only if offline-first becomes a product requirement. | §1.2 |
| R4 | **Coexistence with claim-flag decorations & comments.** Decorations map through `tr.mapping` (safe for local steps), but anchor *data* is char-offset-over-serialized-text recomputed per-client → same comment resolves to different spans across clients until convergence; claim replace uses stale stamped `from/to`. | High | High | Re-map queued positions (`selectionRange`, `pendingRewrite`, claim `data-claim-from/to`, comment anchors) through remote steps in the collab layer; gate async scan/comment `setMeta` on the doc version they were computed against; migrate anchors toward collab-mapped PM positions. Toolbar stays untouched. | P4 |
| R5 | **`replaceEditorContent` nukes EditorState** (Google Docs import, AI apply, chat refresh, undo-apply) — destroys shared collab history/plugin state and overwrites peers. | High | High | Convert these flows to collaborative `replaceWith` transactions through collab; if a state rebuild is unavoidable, re-include `collab()` in the rebuilt plugin array (composer-v5.js:3442). | P3, P4 |
| R6 | **Hands-off selection toolbar regression.** Remote edits routed through a path that bypasses `updateSelectionState()`, or any spurious refocus/blur, makes the toolbar flash/mis-anchor; `selectionRange` not re-mapped through remote steps points the toolbar at stale positions. | Medium | Medium | Route ALL remote edits through `dispatchTransaction`/`view.updateState` (so the toolbar's own cycle runs); map `selectionRange`/`pendingRewrite` in the collab layer, not the toolbar; remote-cursor overlay is `pointer-events:none` absolutely-positioned to preserve the whole-paper-selectable design. **No toolbar code changes.** | P1, P3, P4 |
| R7 | **No-bundler ESM constraint.** collab must vendor as an esm.sh bundle keeping `prosemirror-state` external to share the single vendored copy; wrong bundle config breaks `instanceof`/version interop. | Medium | Low | Vendor via `scripts/vendor-prosemirror.sh` matching the existing 14 packages' `external=` pattern; verify interop after vendoring (VERSIONS.md invariant); fix the VERSIONS.md import-map-host doc drift while there. | P3 (§1.3) |
| R8 | **7-day dependency rule.** Any new/upgraded dep <7 days old is prohibited (incl. transitives, GH Actions, Docker, except documented CVE). | Low | Low | Chosen pin `prosemirror-collab@1.3.1` is 3+ years old. Never pin `y-websocket@3.1.0-rc.*` (this-week prerelease, doubly excluded). Phases 0–2 add zero new deps. Commit lockfile. | P3 |
| R9 | **WS auth currently identity-blind** (shared `ARTEMIS_TOKEN`); presence needs per-user identity. | Medium | — (gap to close) | Phase 0: verify Cf-Access JWT on the WS upgrade via the same verifier as HTTP; `dev@local` shim + dev-only identity override for local multi-user testing. | P0 |
| R10 | **Single-process in-memory WS manager** — multiple workers → multiple rooms per draft → flush clobbering returns. | Medium | Medium (on scale-out) | v1 single-worker / sticky routing to one process; before scale-out add Redis pub/sub or Postgres LISTEN/NOTIFY fan-out with one elected flusher per draft. Verify deploy worker count. | P4 |
| R11 | **Server restart / room loss.** Room is a cache; in-flight unflushed edits (sub-2s window) lost. | Low | Low | Identical exposure to today's autosave debounce. Room rehydrates `working_text` from `live_content` on reconnect. | P3 |
| R12 | **Save-version race during live editing.** A user cutting a version mid-session clears `live_content` and changes the shared baseline. | Medium | Medium | Save-version stays HTTP; room subscribes to the commit event, re-hydrates from `versions[0]`, broadcasts `doc.rebase` so all editors snap to the saved version. Version-history mechanism otherwise unchanged. | P3 |

### Top 3 risks (rank-ordered)

1. **R2 — silent whole-body clobber** (the real user-facing data-loss bug; addressed early by Phase 2 soft-lock, then eliminated by Phase 3).
2. **R4/R5 — coexistence with claim flags, comments, and the state-swap flows** (`replaceEditorContent`, stale queued positions); this is where co-editing bugs will actually live and is the bulk of Phase 4.
3. **R1 — divergent source of truth** if a CRDT were adopted; resolved by the decision to use `prosemirror-collab` with `live_content` as the single durable truth.

---

## 4. Key files (implementation map)

**Client / editor**
- Editor, plugin array, `dispatchTransaction`, decoration-plugin pattern, autosave, serializers, `replaceEditorContent`, `renderShell` header, `destroy()`: `/Users/artemis/Artemis/artemis-os/public/js/features/composer-v5.js` (plugins :233 + dup :3442, dispatchTransaction :237, decoration plugins :159-226, autosave :1103-1158, serializeDocToText :4458, textToProseMirrorDoc :4387, replaceEditorContent :3436, renderShell header ~:4034, destroy ~:3936)
- WS reconnect/backoff/keepalive: `/Users/artemis/Artemis/artemis-os/public/js/core/ws.js`
- Per-session socket precedent: `/Users/artemis/Artemis/artemis-os/public/js/core/floating-artemis-api.js`
- Mount/destroy: `/Users/artemis/Artemis/artemis-os/public/js/features/writing-studio.js` (:934, :967)
- Import map + vendored PM: `/Users/artemis/Artemis/artemis-os/public/index.html` (:42-61), `/Users/artemis/Artemis/artemis-os/public/vendor/prosemirror/` (+ VERSIONS.md), `/Users/artemis/Artemis/artemis-os/scripts/vendor-prosemirror.sh`

**Backend**
- Draft model / authoritative JSONB column: `/Users/artemis/Artemis/artemis-os/artemis/marketing/models.py:463`
- Save path (autosave `liveContent`, version mint, `live_content` clear): `/Users/artemis/Artemis/artemis-os/artemis/marketing/routes/writing_studio.py:468` (esp. 544-588)
- Content precedence rule: `/Users/artemis/Artemis/artemis-os/artemis/marketing/writing_studio/compose_engine.py:176`
- Detail serializer (same precedence): `/Users/artemis/Artemis/artemis-os/artemis/marketing/routes/writing_studio.py:1961`
- CF Access identity (reuse for WS auth): `/Users/artemis/Artemis/artemis-os/artemis/identity/dependencies.py:41`, verifier `/Users/artemis/Artemis/artemis-os/artemis/identity/cf_access.py`
- Event bus for version-commit subscription: `/Users/artemis/Artemis/artemis-os/artemis/marketing/writing_studio/adapter.py:53`
- Existing WS room manager + endpoint pattern: `/Users/artemis/Artemis/artemis-os/artemis/ws/manager.py`, `/Users/artemis/Artemis/artemis-os/artemis/ws/routes.py`
- New collab service location (do NOT overload the unrelated external sync): new module `artemis/marketing/writing_studio/collab/`; avoid `/Users/artemis/Artemis/artemis-os/artemis/marketing/writing_studio/sync.py`

---

## 5. What this design deliberately does NOT do

- Does **not** adopt Yjs/CRDT in v1 (R1).
- Does **not** create a new durable table for documents — the JSONB column stays the single truth. (Optional non-authoritative `writing_draft_presence` audit row is explicitly out of scope for v1.)
- Does **not** change the version-history mechanism — only the explicit Save-version HTTP path mints `versions[]` and clears `live_content`.
- Does **not** touch the selection-toolbar code (`updateSelectionState`, `positionNearSelection`, show/hide, outside-pointer-down) or the whole-paper-selectable CSS. All position mapping happens in the collab integration layer.
- Does **not** solve multi-worker fan-out in v1 — single-worker is the stated scaling boundary (R10).
