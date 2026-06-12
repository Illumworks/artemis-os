# Worker Brief — Writing Studio: stop silently persisting truncated/failed compose results (the mid-write stall)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/ws1-compose-stall`. Writing Studio backlog item #1 (highest). Real tests.

## The bug (live, demo)
The writing agent composing a draft stopped mid-sentence ("The key word is" then nothing) and the run "completed" —
the truncated text was **saved as a normal draft**. Verified mechanism (grounded):
- Compose is **non-streaming**: FE does a plain `fetch` POST to
  `/api/writing-studio/drafts/{id}/compose`; `compose_draft()` (`artemis/marketing/routes/writing_studio.py`
  ~612-883) calls `run_turn()` → `ClaudeCodeAdapter.complete()` (the subscription subprocess; no API key).
- The adapter (`artemis/providers/claude_code/adapter.py` ~155-226) takes `data.get("result")` from the
  `claude -p --output-format json` subprocess and **always returns `stop_reason="end_turn"`** (line ~224),
  regardless of whether the result was complete, empty, truncated, or an error.
- `compose_draft` then persists that text as the assistant thread message and **commits** (~800-860) with NO
  completeness/error check. A short/truncated CLI result becomes a permanent truncated draft, HTTP 200.
- Not a `max_tokens` cutoff ("The key word is" ≈ 5 tokens, default max_tokens=4096). The CLI returned a short
  result for an upstream reason; nothing detected or surfaced it.

## The fix — make incompleteness detectable, and NEVER silently persist it
Don't chase the exact upstream flake; harden the path so a truncated/failed compose is caught and surfaced
(retry/error), never saved as a clean draft.

### 1. Adapter: surface real completion status (`ClaudeCodeAdapter.complete`)
- Parse the `claude -p --output-format json` payload for its ACTUAL status, not a hardcoded `end_turn`. The CLI
  JSON includes fields like `is_error`, `subtype`, `num_turns`, `duration_ms`, `result` — inspect the real
  shape in code and use them. If `is_error` is true, or `result` is empty/missing, raise a clear error
  (don't return a fake clean response). If the CLI exposes a truncation/limit signal, map it to a non-`end_turn`
  `stop_reason`.
- Keep the existing subprocess-timeout + returncode handling.

### 2. compose_draft: do not persist an incomplete result
- After `run_turn()`, before persisting: if the response text is empty, or the turn ended abnormally
  (error / non-`end_turn` stop_reason indicating truncation / suspiciously empty), DO NOT write the assistant
  thread message + commit. Instead surface a clear failure to the FE (e.g. HTTP 5xx or a structured
  "compose_failed" payload) so the user can retry — rather than saving "The key word is".
- Optional, nice: one automatic retry on an empty/errored compose before surfacing failure.
- A genuinely complete result persists exactly as today (no behavior change on the happy path).

### 3. (Consider) a saner compose timeout
- compose uses the default 900s wall-clock subprocess timeout. Content generation has a proven shorter bound
  elsewhere — `_content_node_timeout_and_turns()` (`artemis/builders/executor.py`) returns (120s, max_turns 5)
  for content agents. Reuse/mirror that for compose if it fits, so a real hang fails fast instead of hanging
  15 min. (Secondary to #1/#2 — the demo stall was a short truncation, not a long hang.)

## Constraints
- No new deps; ruff + mypy strict; real tests. Don't regress the happy-path compose, the draft-fence parsing
  (`parse_draft_fence`), proposed-learning extraction, or thread persistence.
- Approval-first/lossless unaffected (this is compose, not OKR/memory writes).
- The adapter change touches a SHARED path (every claude-code completion) — be careful: a complete normal
  result must still return `stop_reason="end_turn"` and parse exactly as before. Only ADD detection of
  error/empty/truncated; don't change the happy path.

## Tests
- Adapter: a subprocess payload with `is_error=true` (or empty `result`) → `complete()` raises / signals error,
  does NOT return a fake clean `end_turn` response. A normal complete payload → unchanged (`end_turn`, full text).
- compose_draft: an empty/errored compose result → NO assistant thread message persisted, NO commit of a stub,
  a failure surfaced to the caller; a complete result → persisted normally (happy path intact).
- Regression: parse_draft_fence + proposed-learning extraction still work on a complete response.

## Acceptance
A truncated/failed compose no longer silently saves a mid-sentence draft — the user sees a clear failure (and
can retry), while normal composes are unchanged. Lead verifies live (trigger a compose; confirm normal works;
if reproducible, confirm a failed/truncated run surfaces an error instead of a stub draft) + checks no truncated
draft is persisted.
