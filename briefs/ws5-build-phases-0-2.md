# Worker Brief — WS5 Co-Editing, Phases 0–2 (safe foundation + the clobber-bug fix)

**Owner:** terminal (may fan out sub-agents within a phase). **Lead:** Artemis (Opus) verifies LIVE with 2
browsers + merges. **Status:** READY.
**Full design:** `docs/ws5-coedit-architecture.md` — read it first. It has the exact file/line map (§4), the
risk register (§3), and per-phase detail. This brief is the build order + ship gates; the doc is the spec.

**Isolation (AGENTS.md rule 6):** work in an **isolated git worktree**, never the main checkout. Use a
**branch per phase** (`worker/ws5-p0`, `worker/ws5-p1`, `worker/ws5-p2`), each off the latest `main`.
**Do-NOT-merge-report per phase** — Lead verifies each live and merges *before* the next phase starts (they
build on each other and all touch `composer-v5.js`, so they must be sequential).

## Hard constraints (all phases)
- **Hands-off selection toolbar.** Do NOT touch `updateSelectionState` / `positionNearSelection` /
  `showSelToolbar` / `hideSelToolbar` / `handleOutsidePointerDown` or the `.cv5-paper`/`.ProseMirror`
  whole-paper-selectable CSS. Remote-cursor overlays must be `pointer-events:none`, absolutely positioned.
- **Phases 0–2 add ZERO new dependencies** and **never change the write path's source of truth.** Reuse
  `public/js/core/ws.js` (reconnect/backoff/keepalive), the CF Access verifier (`artemis/identity/`), and the
  existing decoration-plugin pattern (`claimFlagsPlugin`/`commentsPlugin`).
- **Presence traffic is ephemeral** — never persisted, never flushed to the DB.
- Match surrounding code/style. `./scripts/check.sh` before reporting each phase.

## Phase 0 — Identity-aware per-draft WebSocket (plumbing, no UI)
- New `GET /api/writing-studio/drafts/{draft_id}/collab` upgraded to WebSocket. **Verify the Cloudflare
  Access JWT on the WS upgrade** (`cf-access-jwt-assertion` header → same `get_cf_access_verifier().verify_jwt()`
  path as `resolve_request_identity`); on failure `close(code=4401)` before `accept()`. Yields the same
  `RequestIdentity(email, name)`. Keep the `dev@local` shim + a **dev-only query-param identity override** so
  presence is testable with distinct users locally.
- Client opens a **per-mount** socket (mirror `floating-artemis-api.js`'s per-session socket), reusing
  `ws.js`; tear down in the composer's `destroy()`.
- **Ship gate (Lead verifies):** two browsers connect to the same draft, server `room_count` = 2, heartbeats
  flow, kill one → reconnect works. No user-facing UI.

## Phase 1 — Presence: who's-here avatars + remote cursors + selection highlights
- **Avatars:** server tracks the per-draft roster + broadcasts join/leave. Client renders an avatar cluster
  in the `cv5-hdr` header (between status and spacer): 24px initials circles, color deterministically hashed
  from `User.id` (same color per person across caret/selection/avatar), cap ~5 + "+N", self omitted/faint,
  hover → name+email. Join/leave via the existing `callbacks.onStatus?.(...)`.
- **Cursors + selections:** a **third decoration plugin** (`presencePlugin`), updated via `setMeta` like the
  existing plugins. Remote collapsed selection → `Decoration.widget` (colored caret + name flag, idle-fades
  ~2s); remote range → `Decoration.inline` low-alpha band (reuse `cv5-comment-anchor-hl` styling). **Map
  remote `{from,to}` through `tr.mapping` on every local transaction** (or remote carets drift as you type).
- **Emit:** broadcast local selection in `dispatchTransaction` next to the existing `updateSelectionState()`
  call, debounced ~150–200ms, `{from,to}` only.
- **Reconnect UX:** on disconnect dim the cluster + stop emitting; on reconnect re-announce, refetch roster,
  clear stale remote decorations before repainting.
- **Ship gate:** select text in window A → window B sees a colored band + named caret tracking A; close A →
  its avatar + decorations vanish.

## Phase 2 — Soft-lock: kill the silent-clobber bug (R2) over the *current* save path
- Still whole-body save, but stop blind overwrites: add a **monotonic `live_content` version counter**;
  autosave sends the version it was based on; server does **compare-and-set** and **rejects stale writes**.
  The late writer gets a **non-destructive banner** ("X is also editing — your changes weren't saved, reload
  to merge") — never silent data loss. (Optional: a section-level "Jon is editing here" cue from Phase-1
  presence ranges.)
- **This is a complete, shippable safety fix on its own** and the natural stopping point if Phases 3–4 get
  deprioritized. It fixes a bug that exists in production TODAY (two editors/tabs silently clobber on each
  ~1.2s autosave tick).
- **Ship gate:** two windows edit the same draft → the late writer is warned, not clobbered; verify the
  rejected write left the other's content intact (assert the DB state, not just the banner).

## Verification protocol (Lead owns)
Each phase: Lead opens **2 real browser clients** (two CF Access identities, or `dev@local` + the dev override)
and confirms the ship gate live before merging. Multiplayer is more interaction-sensitive than the toolbar
saga was — synthetic checks are NOT sufficient. Lead merges each phase via the lead-merge flow, then
green-lights the next.

## Out of scope here (later briefs)
Phase 3 (live text sync via `prosemirror-collab` — the first new dep) and Phase 4 (coexistence/conflict
hardening) are separate briefs once 0–2 are proven. Do not start them in this branch set.
