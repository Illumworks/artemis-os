# Writing Studio — Live Typing (token streaming) — FUTURE ENHANCEMENT

**Status:** Idea captured, NOT built. Enhancement, not a bug fix.
**Captured:** 2026-07-17 (Jon)
**Related:** the compose gateway-timeout fix (async job + polling) shipped separately — see
"Background" below.

## The idea

When you click **Draft** in Writing Studio, Amira should stream her reply into the
chat/editor **word-by-word as she writes it** — the way claude.ai types out an answer —
instead of showing a spinner and then dropping the whole finished draft in at once.

Two payoffs:
1. **UX:** you see progress immediately; long drafts feel fast and alive instead of frozen.
2. **Robustness (bonus):** a connection that is constantly sending text never sits idle
   long enough to trip an edge/proxy timeout, so it *also* dodges the ~100s Cloudflare
   ceiling that caused the original "Failed to generate Writing Studio draft" error.

## Background — why this is a SEPARATE, bigger project

The original bug was: compose was one long synchronous HTTP request; Cloudflare's edge
kills any single request that runs past ~100s and returns a non-JSON 5xx, which the
browser surfaced as the generic "Failed to generate Writing Studio draft" toast. That was
fixed the contained way — **async job + polling** (start the work, return a ticket, poll a
fast status endpoint) — which removes the timeout WITHOUT touching shared infrastructure.

Live typing is deliberately deferred because it requires changing **shared, voice-critical
infrastructure** that every agent (Callie, Artemis, Kai, …) depends on:

### The blocker
`artemis/providers/claude_code/adapter.py` invokes the Claude CLI with
`--output-format json` — it **waits for the whole answer and parses it once**. There is no
token-by-token output today. The streaming event *types* already exist
(`artemis/providers/streaming.py`: `StreamTextDelta`, `StreamToolUseStart`, …) but **no
adapter emits them** and `run_turn` (`artemis/agent/loop.py`) has no streaming variant.

## What building it would entail (sketch, to be validated)

1. **Adapter (shared, highest risk):** add a streaming invocation path to the claude-code
   adapter using `--output-format stream-json --include-partial-messages`, parse the
   event stream, and yield `StreamEvent`s. Must not regress the existing non-streaming
   `run_turn` callers (marketing pipeline, all agents). Gate behind a flag / separate
   method so the default path is untouched.
2. **Agent layer:** a `stream_turn` (async generator) alongside `run_turn`, or a
   `on_delta` callback on `run_turn`. Preserve the completeness guard (do not persist a
   truncated result) and the em/en-dash lint on the FINAL text.
3. **Transport:** stream deltas to the browser over the **collab WebSocket that Writing
   Studio already keeps open** (`/api/writing-studio/drafts/{id}/collab`) — or the
   floating-artemis WS pattern (`/ws/floating-artemis/...`). WebSockets are not subject to
   the 100s HTTP ceiling. Reuse the existing room/broadcast plumbing in
   `artemis/marketing/writing_studio/collab/`.
4. **Frontend:** render the incoming deltas into the existing "Drafting…" bubble in
   `public/js/features/writing-studio.js` (the `autoComposeNewDraft` /
   `applyWritingChatPrompt` paths). On `message_stop`, run the same
   fence-parse + apply-to-document logic that the async-job path uses today.
5. **Parity to keep:** cost event recording (`writing_studio_compose`), proposed-learning
   extraction/persistence, thread-message persistence, trace payload. The async-job path
   already does all of this — streaming must not drop any of it.

## Risk / scope note
The blast radius is the shared provider adapter — a regression there hits every agent, not
just Writing Studio. Treat as its own project with live smokes across at least one
non-Writing-Studio agent path before merge. Do NOT fold it into a Writing-Studio-only PR.
