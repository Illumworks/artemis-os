# CC15 — Claude CLI Per-Tool-Run Timeout Headroom

**Paste-into:** terminal-Lead → Codex direct (small mechanical change) OR Claude Code Worker if Codex unavailable.
**Target branch:** `lead/j6a-granola-integration` (Codex direct commit) OR `worker/cc15-cli-timeout`.
**Browser smoke owner:** Lead, post-merge — re-run the marketing pipeline, confirm `qualifier_cross_reference` reaches `succeeded` (not the 300s timeout).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~50.
**Priority:** HIGH (pairs with CC14) — pipeline can't reach Gate-1 cleanly while the qualifier times out.

---

## Why this exists — the smoke finding the self-improvement loop *named*

CC13's smoke crashed the qualifier at 300s with `ClaudeCodeTimeoutError: Provider API error 408: Claude CLI (tool run) timed out after 300s`. **And the self-improvement loop's one surviving summary diagnosed it**:

> *"The marketing.qualifier.cross_reference agent timed out after 300s during a Claude CLI tool run (ClaudeCodeTimeoutError 408), never producing qualification output."*
>
> *"What was missing: A configurable timeout ceiling above the 300s default — the cross_reference qualifier likely requires multi-step tool calls that exceed the hard CLI limit with no retry or partial-result fallback."*

That is the system telling us what it needs. The qualifier does multi-step tool flow (read pending signals → lookup reason codes → evaluate ruleset → transition status), which legitimately exceeds 300s on a busy run. Scouts finish in ~30-60s; qualifier doesn't.

The fix is what the summary literally said: raise the ceiling, env-configurable so it's tunable without code changes.

---

## Scope

### Part A — Investigate (~10 LOC of findings)

`artemis/providers/claude_code/adapter.py` has `_TIMEOUT_SECONDS = 300.0` near the top. Two questions for the report:

1. Is it **already env-configurable** (i.e. did CC2 add `os.environ.get("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "300")`)? If yes, this brief just bumps the default. If no, this brief adds the env override.
2. Is the constant used in the run-with-tools path (the MCP subprocess) AND the text-only `complete()` path, or only one? Both should respect the new default.

Paste the answers in the report.

### Part B — Make timeout env-configurable with a higher default

In `artemis/providers/claude_code/adapter.py`:

```python
import os
_TIMEOUT_SECONDS = float(os.environ.get("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "900"))
```

- Default: **900s** (15 min). 3× the qualifier's observed hit point (300s); generous headroom for multi-step tool flows. Scouts finishing in 30-60s are unaffected.
- Env var `ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS` lets operators dial up or down without a code change.
- Apply to **both** the text-only `complete()` and the MCP `run_with_tools()` subprocess calls (whichever wrap the `_TIMEOUT_SECONDS` constant — confirm in Part A).

### Part C — Tests

`artemis/providers/tests/test_claude_code_timeout.py` (or extend the existing claude_code tests):
1. With `ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS` unset, the adapter's timeout reads as 900.0.
2. With `ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS=120`, the adapter's timeout reads as 120.0 (env override works).
3. With `ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS=900`, a subprocess that exits in <900s does NOT raise `ClaudeCodeTimeoutError` (regression — the timeout still actually applies as the wall-clock bound).

These are config tests, not real-subprocess tests — mock the timeout boundary or use a fast fake subprocess.

---

## Files owned
- EDIT: `artemis/providers/claude_code/adapter.py` (one constant change, possibly two if both paths reference it independently)
- EDIT or NEW: `artemis/providers/tests/test_claude_code_timeout.py`

**Do not touch:** the MCP server, the agent loop, the tool registry, the runtime injection, the trajectory summarizer, any blueprint, any seed.

---

## Acceptance criteria

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/providers/tests/test_claude_code_timeout.py -v` — all pass. **Paste.**
2. Code grep proof: `grep -n "_TIMEOUT_SECONDS\|ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS" artemis/providers/claude_code/adapter.py` shows the env-configurable default of 900. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1`. **Paste.**

**Note:** the real proof (qualifier completes in a pipeline run) is Lead's post-merge smoke, bundled with the CC14 smoke. Worker doesn't need to run the full pipeline.

---

## Hard constraints

- Default 900s. Not higher (still want runaway protection). Not lower (qualifier hit 300s).
- Both paths (text-only `complete()` and MCP `run_with_tools()`) use the same env-configurable timeout.
- Env var name: **`ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS`** — matches the convention CC2 / the design doc used.
- If Part A finds the env override is already present, this brief is even smaller — just bump the default from 300 to 900.
- Local-only git.

---

## Report-back format

```
CC15 — Claude CLI Timeout Headroom report
1. Commit / branch (or note: Codex direct commit)
2. LOC diff stats
3. Part A findings — was env override already there? both paths?
4. New default + env var name (paste the line)
5. Test pass summary
6. check.sh summary
7. Anything surprising
```

---

**Note for Lead's post-merge smoke (not Worker's job):** when CC14 + CC15 both land, the smoke pipeline run should: (a) all 9 scouts succeed, (b) qualifier_cross_reference SUCCEEDS (not timeout — CC15 holding), (c) downstream nodes proceed past qualifier, (d) `agent_run_trajectory_summaries` count jumps by ~10-12 (CC14 holding), (e) Gate-1 reaches `awaiting_approval` with real content. If qualifier still times out at 900s, that's a separate finding — likely the qualifier's tool flow is genuinely too long and needs chunking (CC16 territory).
