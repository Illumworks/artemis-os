# O4 — Streaming SSE for Builder Message Endpoint

**Owner:** Lead designs, Worker implements. ~250 LOC total (backend + frontend combined).
**Depends on:** O1 (`o1-agent-builder-and-self-improvement.md`) must be merged to `lead/j6a-granola-integration` first. This brief assumes `artemis/builder/{routes.py, agent_builder.py}` and the Builder UI from O1 are present.
**Blocks:** Nothing — pure UX/transport upgrade. Future Skill-Builder / Workflow-Builder / Automation-Builder surfaces will reuse the same SSE plumbing.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

---

## 1. Why

Jon's symptom verbatim: **"Failed to send. Failed to fetch."** in the Builder UI, even when the server-side turn actually completed.

Root cause: `POST /api/builder/sessions/{id}/messages` is synchronous. It calls `handle_turn()` in `artemis/builder/agent_builder.py`, which runs the F1 agent loop with tools (`read_existing_agents`, `read_capabilities`, `read_recent_runs`, `propose_definition`, `test_run`). Multi-tool-call turns routinely take **60–120 s**, and complex chains can hit 2–3 minutes.

The timeout chain that kills these requests:
- **Browser `fetch()`**: ~60–90 s before "Failed to fetch"
- **Cloudflare tunnel edge timeout**: ~100 s (well-documented)
- **nginx-style proxies**: 60 s default `proxy_read_timeout`

So the Builder is unusable end-to-end as long as the message endpoint is a single blocking POST. The work is being done; the client just never sees the answer.

The fix is to convert the endpoint to a streaming Server-Sent Events (SSE) channel so:
- The connection stays warm with heartbeats (no proxy/browser timeout).
- The user sees the conversation unfold in real time (tool calls, tool results, streaming assistant tokens, proposal updates).
- Cancellation works (user closes tab → server stops generating).

---

## 2. What to ship

### 2.1 Event types (server → client)

The streaming endpoint emits these SSE event types. Each event is a JSON payload in the `data:` field; `event:` names the type.

| Event              | When                                                | Payload shape                                                                          |
|--------------------|------------------------------------------------------|----------------------------------------------------------------------------------------|
| `turn_start`       | First event of every stream                          | `{turn_id, session_id, started_at}`                                                    |
| `tool_call`        | Builder is about to invoke a tool                    | `{tool_call_id, tool_name, inputs}`                                                    |
| `tool_result`      | Tool returned                                        | `{tool_call_id, tool_name, ok, result_preview, duration_ms}`                           |
| `assistant_token`  | Streaming chunk of LLM text (token or chunk)         | `{delta}`                                                                              |
| `proposal_staged`  | Builder updated/created a draft definition mid-turn  | `{proposal_id, kind, definition_diff}`                                                 |
| `heartbeat`        | Every 15 s while turn is running                     | `{ts}`                                                                                 |
| `turn_complete`    | Final event of a successful turn                     | `{assistant_text, draft, stop_reason}` (same shape as the synchronous response today)  |
| `error`            | Terminal error                                       | `{code, message}`                                                                      |

SSE format on the wire:
```
event: tool_call
data: {"tool_call_id":"...","tool_name":"read_existing_agents","inputs":{}}

event: tool_result
data: {"tool_call_id":"...","tool_name":"read_existing_agents","ok":true,"result_preview":"...","duration_ms":412}

event: assistant_token
data: {"delta":"Okay, here's"}

event: heartbeat
data: {"ts":"2026-05-20T15:30:00Z"}

event: turn_complete
data: {"assistant_text":"...","draft":{...},"stop_reason":"end_turn"}
```

### 2.2 Server pattern

- Add a new route: `POST /api/builder/sessions/{id}/messages/stream` returning `StreamingResponse(media_type="text/event-stream")`.
- Convert (or wrap) `handle_turn()` in `artemis/builder/agent_builder.py` to an **async generator** that yields events at the natural seams in the F1 agent loop:
  - Before invoking a tool: yield `tool_call`.
  - After a tool returns: yield `tool_result`.
  - During model streaming: yield `assistant_token` per chunk from the provider adapter's streaming interface.
  - When `_propose` writes a proposal: yield `proposal_staged`.
  - When the loop exits: yield `turn_complete`.
- Heartbeat: wrap the generator in a helper that races the next event against an `asyncio.sleep(15)` and emits a `heartbeat` if the sleep wins.
- Response headers (load-bearing for Cloudflare tunnel + nginx):
  ```
  Cache-Control: no-cache
  X-Accel-Buffering: no
  Connection: keep-alive
  ```
- Flush after every event (`await writer.drain()` or equivalent — StarletteServer auto-flushes per yield, verify).
- **Backward compat**: keep the existing synchronous `POST /messages` exactly as-is. The CLI shim and any non-browser caller continue to work unchanged. The streaming endpoint is **additive**.

### 2.3 Client pattern

Replace the `fetch().then(json)` in the Builder UI's "send message" handler with a streaming reader.

Recommended transport: **`fetch` + ReadableStream** (not `EventSource`).
- `EventSource` is GET-only and can't carry a JSON body; spinning up a separate POST-then-GET handshake doubles the route surface for no gain.
- `fetch('/api/builder/.../messages/stream', { method: 'POST', body: JSON.stringify({...}) })` returns a response whose `body` is a ReadableStream; parse it line-by-line with a tiny SSE parser (~15 LOC) and dispatch by `event:` name.

UI rendering rules:
- **`tool_call`** → render an inline breadcrumb in the conversation (collapsible chip: `→ read_existing_agents`).
- **`tool_result`** → update the same breadcrumb with duration + ok/error state; keep the result body collapsible.
- **`assistant_token`** → append `delta` to the in-progress assistant message bubble. Auto-scroll.
- **`proposal_staged`** → update the right-rail draft definition panel live.
- **`heartbeat`** → no UI; just resets the client-side stall timer.
- **`turn_complete`** → finalize the assistant message, persist draft state, close the reader.
- **`error`** → surface inline error, close the reader.
- **"thinking…" indicator**: visible only between request-send and first event arriving. Once any event hits, switch to live event rendering.
- **Cancellation**: when the user closes the tab/Builder panel mid-turn, the ReadableStream cancels automatically; the server-side generator detects the client disconnect (Starlette raises `asyncio.CancelledError` in the generator) and stops the LLM call. Confirm this propagates to the provider adapter's cancellation hook.

---

## 3. Architectural considerations

### 3.1 Where the generator lives

`handle_turn()` today returns `dict[str, Any]`. Two viable patterns:

**Pattern A (preferred)**: refactor `handle_turn` into `handle_turn_stream()` as an `AsyncIterator[BuilderEvent]`. Keep the old `handle_turn` as a thin wrapper that drains the stream and returns the final dict — this preserves the synchronous endpoint for free.

```python
async def handle_turn_stream(...) -> AsyncIterator[BuilderEvent]: ...

async def handle_turn(...) -> dict[str, Any]:
    final = None
    async for ev in handle_turn_stream(...):
        if ev.type == "turn_complete":
            final = ev.payload
    return final
```

**Pattern B**: dual implementation, sync stays as-is. Faster to ship but doubles maintenance. Reject unless Pattern A turns out to be invasive.

### 3.2 Tool-call yield points

The F1 loop in `agent_builder.py` already has the tool dispatch in one place (`_read_existing`, `_read_capabilities`, `_read_recent_runs`, `_propose`, `_test_run`). The cleanest seam is to wrap each tool call in a helper that yields `tool_call` before invocation and `tool_result` after — don't sprinkle yields inside each `async def _foo()`.

### 3.3 Token streaming from the adapter

The provider cascade (`claude-code → codex → lm-studio → anthropic`) exposes different streaming surfaces. Check each adapter for a streaming method (likely `stream_completion()` or similar). If an adapter doesn't expose tokens, fall back to **chunk-by-chunk** (yield once per ~50 chars or once per provider chunk). Acceptance criterion below allows chunk granularity — token-perfect is nice-to-have.

### 3.4 Cancellation semantics

Starlette propagates client disconnect as `asyncio.CancelledError` raised inside the generator at the next yield. Wrap the LLM call in a try/finally that calls the adapter's cancel/abort hook so we don't leak orphaned upstream LLM requests when the user closes the tab.

### 3.5 Persistence ordering

In the synchronous endpoint today, `await session.commit()` runs after `handle_turn` returns. In the streaming endpoint, commit **after** the final `turn_complete` is yielded but **before** the SSE stream closes. If cancellation fires mid-turn, persist what completed (partial assistant text + any staged proposals already written) — never leave the DB in a half-updated state.

### 3.6 Backward compatibility

- Synchronous `POST /messages` endpoint stays bit-identical. The CLI shim (and any external caller) keeps working.
- Streaming endpoint is **additive** at `POST /messages/stream`.
- No DB migration. No schema changes.

---

## 4. Acceptance criteria

- [ ] A real Builder conversation (multi-tool-call turn lasting 1–2 minutes) completes end-to-end via the browser **without** "Failed to fetch".
- [ ] Tool calls appear inline in the conversation **as they happen**, not batched at the end.
- [ ] Assistant text streams chunk-by-chunk (token-by-token if the adapter supports it) into the in-progress message bubble.
- [ ] Right-rail draft definition updates live as the Builder accumulates fields via `proposal_staged` events.
- [ ] CLI shim (any non-browser caller hitting the old synchronous `POST /messages`) still works. Verified by hitting the endpoint with `curl -X POST .../messages`.
- [ ] Connection stays alive through long turns: insert an artificial `await asyncio.sleep(180)` in a tool stub, run the turn, confirm no client-side timeout (heartbeats keep it warm).
- [ ] Cancellation: closing the tab/Builder panel mid-turn stops the server-side generator within ~2 s (verify via log line); no orphaned LLM call to the upstream provider.
- [ ] Synchronous endpoint and streaming endpoint produce identical final state (same `assistant_text`, same `draft`, same `stop_reason`) for the same input — Pattern A guarantees this; add a test that drains the stream and compares against a sync run.
- [ ] SSE response headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- [ ] Tests cover: happy multi-tool-call turn, mid-turn cancellation, heartbeat emission during a slow tool, error path.

---

## 5. Hard constraints

- Total scope cap: **250 LOC** (backend + frontend combined). Tests excluded from the cap.
- Worker must run `git diff --staged` before commit and re-read the diff twice.
- CWD-trap defensive check before any commit (`pwd && git rev-parse --show-toplevel && git branch --show-current` — expect main worktree on `lead/j6a-granola-integration`).
- Single commit on `lead/j6a-granola-integration` directly.
- Commit message: `feat(o4): streaming SSE for Builder message endpoint`
- No new dependencies. Use stdlib + Starlette/FastAPI primitives already in the project.
- No DB migrations.

---

## 6. Where to start

1. Read `artemis/builder/agent_builder.py` end-to-end — specifically `handle_turn()` (line ~373) and the tool wrappers above it.
2. Read `artemis/builder/routes.py` `send_message()` handler (line ~134) — that's what gets the new sibling `send_message_stream()`.
3. Pick a provider adapter (`artemis/providers/*`) and confirm whether it exposes a streaming method. If not, choose chunk granularity for `assistant_token` emission.
4. Refactor `handle_turn` into `handle_turn_stream` (Pattern A above) + sync wrapper.
5. Add the `/messages/stream` route returning `StreamingResponse`.
6. Wire up the frontend `EventSource`-style reader against the existing Builder UI's send-message handler.
7. Manual smoke: trigger a multi-tool turn, watch events flow, close tab mid-turn, confirm cancellation log line.

---

## 7. Paste-ready Worker prompt

```
You are a Worker implementing brief briefs/o4-streaming-builder-responses.md on
the artemis-os repo. Branch: lead/j6a-granola-integration. Read the brief end-
to-end before touching code.

CWD-trap defensive check before any commit:
  pwd && git rev-parse --show-toplevel && git branch --show-current
Expect: main worktree on lead/j6a-granola-integration.

Scope: convert POST /api/builder/sessions/{id}/messages from synchronous to
streaming SSE at a NEW sibling route POST /api/builder/sessions/{id}/messages/
stream. Keep the existing synchronous endpoint bit-identical for the CLI shim.
Refactor handle_turn() in artemis/builder/agent_builder.py into an async
generator handle_turn_stream() yielding events at tool-call seams and during
LLM token streaming; keep the old handle_turn as a thin wrapper that drains
the stream.

Event types: turn_start, tool_call, tool_result, assistant_token,
proposal_staged, heartbeat (every 15s), turn_complete, error. Wire formats
in the brief.

Frontend: replace fetch().then(json) in the Builder UI's send-message handler
with fetch + ReadableStream SSE parser. Render tool calls/results inline as
collapsible breadcrumbs, stream assistant tokens into the in-progress bubble,
update right-rail draft panel on proposal_staged.

Response headers must include: Cache-Control: no-cache, X-Accel-Buffering: no.
Wrap the LLM call in try/finally that calls the adapter's cancel hook so
client disconnect (asyncio.CancelledError) stops the upstream call.

Hard constraints: 250 LOC max (backend + frontend, excluding tests). No new
deps. No DB migrations. Single commit on lead/j6a-granola-integration with
message exactly: feat(o4): streaming SSE for Builder message endpoint

Run git diff --staged before commit and re-read it twice. Tick every
acceptance-criteria box in your final report with verbatim evidence (curl
output, browser console snippets, test names).
```
