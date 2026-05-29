# Responsiveness — Design + Plan (provider-adapter-wide, not just Builder)

**Status:** Design captured. Build phased after CC18 + Proposals Inbox + SP1 land. Not blocking anything; preserves the architectural intent for whenever responsiveness becomes the bottleneck.

**Goal (in Jon's words, clarified 2026-05-29):** ANY claude-code-driven surface in Artemis — **Floating Artemis (the personal assistant)**, Agent Builder, Pipeline AI Panel, Skills builder, and similar — should feel as snappy as Claude Code or terminal-Lead. Sub-second responsiveness for chat turns, visible progress for longer thinking, never opaque "Thinking..." waits over a minute. The fix lives at the provider-adapter layer, not per-surface.

## Scope — every surface that goes through `artemis/providers/claude_code/adapter.py`

| Surface | User-facing? | Path | Priority |
|---|---|---|---|
| **Floating Artemis** chat | YES (Jon's primary assistant) | `floating_artemis/chat.py` → `run_turn` → adapter | **HIGHEST** — most-used surface |
| **Agent Builder** chat | YES (operator) | `builders/executor.py` → `run_agent` → adapter | HIGH |
| **Pipeline AI Panel** (canvas) | YES (operator) | Routes through same adapter | HIGH |
| Pipeline node agents (scout / qualifier / content) | Background | `pipelines/node_executors/agent_executor.py` → `run_agent` → adapter | LOWER — affects cycle time but not user-watching |
| Dev Projects loop runner | Less user-facing | `dev_projects/loop_runner.py` → adapter | Banked |
| Legacy `scout_runner` (marketing) | Banked for deprecation | n/a | n/a |

**Single fix, wide leverage.** This is the architectural reason to do Phase 2 (Option C) properly when the time comes — every chat-style surface in the platform gets the speedup, not just the Builder.

---

## Why the Builder is slow today (precise mechanism)

When a user sends a message in the Builder, `artemis/providers/claude_code/adapter.py` does this per turn:

1. **Spawns a fresh `claude -p` subprocess.** Cold startup overhead: ~5-15s every turn.
2. **Re-flattens the entire conversation history** into a single text prompt via `_flatten_to_prompt`, fed to the subprocess via stdin. claude-code has no memory of prior turns under this design — every turn re-grounds from scratch.
3. **The Builder LLM may run multiple internal tool-call rounds** inside the subprocess (the prompt instructs it to call `read_existing → read_capabilities → read_recent_runs → propose` for edit sessions). Each round is its own LLM inference, all happening inside the single subprocess invocation.
4. **Waits for the subprocess to fully complete** before returning anything — `--output-format json` buffers the entire response. No streaming reaches the UI.
5. **Parses the buffered JSON, returns the final text + tool calls** at once.

So the user sees opaque *"Thinking..."* for 30s-2min while all of this completes serially with no progress signal.

## Why I (Lead) and terminal-Lead feel different

We operate as **long-lived sessions**:

| Component | The artemis Builder | Lead / terminal-Lead |
|---|---|---|
| Process lifecycle | New subprocess per turn | One persistent runtime |
| Context grounding | Re-flatten full history every turn | In-memory, always warm |
| Response delivery | Buffered (wait for complete JSON) | Streaming (tokens as generated) |
| Tool-call visibility | Hidden inside subprocess until exit | Visible inline during the run |

That's why we feel sub-second-snappy on most turns and the Builder doesn't. **It's purely an adapter design choice — not a subscription-auth limitation.** The same `claude` CLI / subscription credentials that power us could power a fast Builder if the adapter were structured differently.

---

## The three options for closing the gap (none require an API key)

### Option A — `--continue` flag (claude-code's own conversation continuation)

**What:** claude-code CLI has `--continue` (and `--resume <session_id>`) that picks up the most recent conversation without re-grounding from a fresh prompt. The adapter would track the conversation ID per Builder session and use `claude -p --continue` for follow-up turns.

**Why it helps:** eliminates the "re-flatten + re-process the entire conversation every turn" cost. claude-code's internal session caching kicks in. The cold-spawn cost remains (each turn is still a fresh subprocess), but the LLM doesn't re-read history.

**Expected speedup:** ~30-50% on multi-turn conversations. First turn is unchanged (no prior context). Each subsequent turn drops the re-grounding cost.

**Effort:** ~50 LOC. Track conversation_id from claude-code's first response; pass `--resume` on subsequent turns. Doesn't change UI; doesn't add streaming.

### Option B — Streaming output (`--output-format stream-json`)

**What:** claude-code supports streaming JSON output. The adapter switches from buffered JSON to streaming, and the Builder UI subscribes to the partial-token stream — showing the response as it generates, plus tool-call progress (*"Reading recent runs..."*, *"Drafting proposal..."*).

**Why it helps:** doesn't speed total wall-clock time, but **eliminates the opaque "Thinking..." anxiety entirely.** The user sees the Builder thinking and acting, in real time. Most of the perceived slowness is the *opacity* of the wait, not the wait itself.

**Expected speedup:** zero on raw timing; massive on perceived UX. *"Felt like 10s"* even if it's 60s actual.

**Effort:** ~80 LOC. Adapter switches to stream-json parsing, surfaces progress events. UI hook in `agent-builder.js` to render incremental output. The agent SDK's streaming infrastructure (`artemis/providers/streaming.py`) likely supplies the building blocks.

### Option C — Persistent claude-code subprocess per Builder session (the real fix)

**What:** instead of spawning `claude -p` per turn, the adapter spawns ONE `claude` interactive-mode subprocess per Builder session (via pty for clean stdin/stdout handling). Subsequent messages are sent to that long-lived process's stdin; responses stream out continuously. The subprocess stays alive for the lifetime of the Builder session (with a timeout for idleness).

**Why it helps:** matches our architecture — long-lived session, warm context, streaming responses, no cold spawn per turn, no re-grounding. **This is what makes Lead / terminal-Lead feel snappy.**

**Expected speedup:** 3-5× on chat-style multi-turn. First turn similar to today (still has startup). Every turn after = near-instant first token + streaming. Should feel like Claude Code does to us.

**Effort:** ~300-400 LOC. Real engineering — pty management, lifecycle (session start, idle timeout, graceful close), error handling (subprocess crash mid-session, session resume), concurrency (multiple Builder sessions = multiple subprocesses). Plus tests for all the lifecycle edges.

---

## Recommended phased plan

**Phase 1 (post-SP1, when responsiveness becomes the bottleneck):** Bundle **B + A** in one stream.

- B (streaming) is the bigger UX win — eliminates the "is it stuck?" anxiety even before A speeds anything up.
- A (--continue) adds real wall-clock speedup on multi-turn.
- Together: ~130 LOC, biggest perceived-quality lift per LOC.
- Acceptance: Builder chat turns under 30s for the common case, with visible progress; opaque "Thinking..." over 5s never happens.

**Phase 2 (only if Phase 1 isn't enough):** Option C (persistent subprocess).

- The real "feels like Claude Code" answer.
- Real engineering investment. Worth doing if Phase 1 still feels slow for operators who'd use the Builder daily.
- Acceptance: Builder turns sub-second to first token; matches the perceived snappiness of an interactive Claude Code session.

**Decision criteria for moving to Phase 2:**
- Operators report that even with streaming progress, the 30-60s/turn for tool-using turns feels too slow for iterative work.
- Average Builder session has 5+ turns and the cumulative wait time becomes a productivity drag.
- The Builder becomes a primary workflow (operators using it daily for agent maintenance) rather than an occasional review tool.

---

## Why this isn't blocking anything now

The producer side of the self-improvement loop (CC10-CC17) is structurally complete. CC18 wires `target_id` so the Builder reads the right summaries. Proposals Inbox (queued next) makes them discoverable. **All of that works regardless of speed.** Operators can already approve / reject; the loop fires; agents improve.

Responsiveness becomes the bottleneck *after* surfaces are being actively used:
- Floating Artemis: when Jon is asking it frequent questions in the personal workflow and waiting >30s per response becomes a daily friction.
- Agent Builder: when operators are reviewing 5+ agents per session and the wait compounds.
- Pipeline AI Panel: when iterating on a pipeline design conversationally.

When responsiveness IS the bottleneck, Phase 1 (B + A) is the cheap-and-big-win move applied at the adapter level — every consumer surface benefits in one shot. Phase 2 (C) is reserved for if/when the persistent-session architecture is needed to match Claude Code's interactive feel.

## Trigger criteria (when to fire each phase)

**Fire Phase 1 (B + A) when ANY of:**
- Floating Artemis daily-use volume makes the wait noticeable to Jon.
- Operator feedback from Builder use cites speed as a friction point.
- Pipeline AI Panel adoption picks up.
- Any surface is opaque "Thinking..." over 10s for what should feel like a quick exchange.

**Fire Phase 2 (C) when ANY of:**
- Phase 1 shipped and the *perceived* speed is still slower than Claude Code despite streaming progress.
- Average session crosses 5+ turns and cumulative wait time becomes a productivity drag.
- Floating Artemis use shifts toward "primary assistant" frequency.

---

## What this doc preserves

- **The scope:** ANY claude-code-driven surface in the platform (Floating Artemis, Agent Builder, Pipeline AI Panel, pipeline agents). Provider-adapter layer = single point of leverage.
- **The architectural insight:** subscription-only doesn't preclude snappiness. The current slowness is an adapter design choice, fixable.
- **The three options + their costs:** so when this stream fires, no re-discovery time.
- **The decision criteria:** so we know when to move from Phase 1 to Phase 2 rather than guessing.
- **The reference point:** "feels like Claude Code" is the goal, not "feels OK." Set the bar at the experience Jon already knows is possible — including matching the snappiness of this very Lead session he's having with terminal-Lead and me.

When the stream fires, it starts with the Phase 1 brief (CC[N] — bundled B + A at the adapter layer). Every surface in the scope table inherits the speedup. This doc is the spec.
