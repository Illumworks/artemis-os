# WS5 — Live Co-Editing: multi-agent DESIGN pass (terminal prompt)

**Goal of this session:** produce the *architecture + a phased, verifiable build plan* for real-time
multi-user co-editing in the Writing Studio composer — NOT implementation. Real-time collaboration is the
biggest, riskiest feature in the Studio; we design it carefully, then Lead (Opus) reviews and we sequence the
build. Jon's end goal = **full live co-editing** (Google-Docs-style: multiple cursors, simultaneous typing).

**Why multi-agent:** four independent design questions can be explored in parallel, then synthesized.

---

## PASTE THIS INTO A TERMINAL CLAUDE CODE SESSION (run from the repo root)

> I'm designing **real-time multi-user co-editing** for the Writing Studio composer (ProseMirror, vendored
> ESM, **no bundler**; vanilla-JS SPA; FastAPI + Postgres backend; per-user identity already via Cloudflare
> Access). This is a DESIGN pass — produce an architecture + phased plan, **write no feature code**. Spawn the
> following sub-agents in parallel (Task tool), each READ-ONLY (they investigate + return findings; no edits):
>
> **Agent A — Approach & library.** Compare the two canonical ProseMirror collaboration approaches for our
> exact stack (vendored ESM ProseMirror, **no build step**): `prosemirror-collab` (central authority +
> step rebasing) vs **Yjs + `y-prosemirror`** (CRDT). Recommend one. Cover: how it loads with no bundler,
> transport (WebSocket), offline/merge behavior, maturity, and the **7-day dependency rule** (any new dep must
> be a version released >7 days ago — verify candidate versions). Return: recommendation + integration points
> + risks.
>
> **Agent B — Composer audit.** Read `public/js/features/composer-v5.js` + its CSS. Map: the editor/doc state,
> `dispatchTransaction`, the autosave path, and every feature that touches the doc — **claim-flag decorations,
> comments, Google Docs export, version history**, and the **selection toolbar (HANDS-OFF — do not propose
> changes to `updateSelectionState`/`positionNearSelection`/`showSelToolbar`/`hideSelToolbar`/
> `handleOutsidePointerDown`)**. Return: where collab hooks in, and what conflicts with a multi-writer model.
>
> **Agent C — Sync backend.** Design a FastAPI WebSocket sync service: document "rooms", awareness/presence,
> the authoritative document store, and **how it reconciles with the existing single-writer draft content +
> autosave + version history** (campaign_deliverables). Reuse CF Access identity for auth. Return: backend
> design + persistence model + the coexistence story with today's save path.
>
> **Agent D — Presence & UX + the scope decision.** Design remote cursors, selection highlights, who's-here
> avatars, join/leave, offline/reconnect. Surface the key product fork: **(1) presence + soft-lock** (see who's
> here, prevent clobbering — much smaller) as a Phase-1 vs **(2) full live co-editing** (the end goal). Return:
> UX design + a recommendation on phasing.
>
> Then SYNTHESIZE the four into: (a) an **architecture decision** (chosen approach + why), (b) a **phased build
> plan** where each phase is independently shippable AND live-verifiable with multiple browser clients
> (suggested spine: Phase A presence/cursors over the current single-writer save → Phase B live text sync →
> Phase C selections/cursor polish → Phase D persistence + conflict + coexistence hardening), and (c) a **risk
> register** (top risks: CRDT↔existing-draft/autosave reconciliation; coexistence with claim-flags/comments/
> GDoc-export; the no-bundler ESM constraint). Write it to `docs/ws5-coedit-architecture.md`. **Stop at the
> plan — no feature implementation.**

---

## After the design lands
Lead (Opus) reviews `docs/ws5-coedit-architecture.md`, picks the approach + phase ordering with Jon, then each
phase becomes its own worker brief (isolated worktree — per AGENTS.md rule 6). **Every phase is live-verified
with 2+ real browser clients** before merge — multiplayer is even more interaction-sensitive than the
selection toolbar was, so synthetic checks are not enough.

**Note on sequencing:** co-editing finishes the Writing Studio backlog; after it, the roadmap resumes **P2
proactivity** (starting with the stale-review escalation, `briefs/p2-stale-review-escalation.md`).
