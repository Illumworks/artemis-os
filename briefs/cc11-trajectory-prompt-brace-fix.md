# CC11 — Trajectory Prompt Brace Fix (the SECOND blocker behind CC10)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`) OR Codex direct — small enough either way.
**Target branch:** `worker/cc11-trajectory-prompt-fix`
**Browser smoke owner:** Lead, post-merge — bundled with CC8/CC9/CC10 in one combined smoke.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~30 (tiny — escape braces + a test).
**Priority:** HIGH — without this, CC10 ships inert. Merge CC11 alongside CC10/CC8/CC9 in one batch.

---

## Why this exists — CC10's hidden second blocker

CC10's worker found and *honestly didn't fix* (per the brief's scope) a second bug that keeps the self-improvement loop inert even with the GC fix in place:

`artemis/builder/trajectory_summarizer.py` `_TRAJECTORY_PROMPT` is built with `str.format(run_data=...)`. The template body contains a literal JSON example with `{` and `}` characters. Python's `str.format()` interprets `{what_worked}` as a placeholder → `KeyError: '\n  "what_worked"'`. The exception is then **swallowed silently** by `_safe_summarize`'s bare `except Exception`, so the loop *appears* to run (no error to the user) but always exits through the exception path.

Net effect with CC10 alone: tasks now run (retained ref, no GC), but every single one hits `KeyError` and silently aborts. `agent_run_trajectory_summaries` stays at 0 in production. CC10's worker proved this — they got the DB write to land only by temporarily fixing the braces + calling synchronously.

**This is exactly the "wired but hollow" pattern in microcosm: one bug behind another.**

---

## Scope

### Part A — Escape the literal braces in `_TRAJECTORY_PROMPT`

In `artemis/builder/trajectory_summarizer.py`, find `_TRAJECTORY_PROMPT`. The JSON-example block looks like:
```
{
  "what_worked": "<one sentence or null>",
  "what_stalled": "<one sentence or null>",
  "what_was_missing": "<one sentence or null>"
}
```

For `str.format()` to treat these as literal braces, every `{` must become `{{` and every `}` must become `}}`. **Only** the literal-JSON braces — keep the legitimate `{run_data}` substitution placeholder intact (single braces).

After the fix, the call `_TRAJECTORY_PROMPT.format(run_data=json.dumps(...))` should NOT raise. Verify by eyeballing: the only single-braced placeholders left should be the intended `{run_data}` substitutions.

**Alternative if you prefer** (less surprising for future readers): switch from `str.format(run_data=...)` to `.replace("{run_data}", ...)` so braces in the body are always literal. Either is fine — pick the one with fewer edits + clearer intent, and explain the choice in the report.

### Part B — Tests

Add to (or create) `artemis/builder/tests/test_trajectory_prompt.py`:
1. **The unit test that should have caught this:** call `_TRAJECTORY_PROMPT.format(run_data='{"a":1}')` (or the equivalent under whichever templating you use) → does NOT raise; returns a string containing both the JSON example (literal) and the substituted run_data. This is the regression guard.
2. **Integration:** call `summarize(run_id)` synchronously (CC10's pattern) on a committed agent_run with a mocked LLM adapter that returns a valid JSON response → `agent_run_trajectory_summaries` row lands with `what_worked`/`what_stalled`/`what_was_missing` populated (not all-null). This proves the prompt-format path actually completes end-to-end now.
3. Existing CC10 GC test still passes.

---

## Files owned
- EDIT: `artemis/builder/trajectory_summarizer.py` (the prompt template only — do not touch the GC fix or the `_do_summarize` body)
- EDIT or NEW: `artemis/builder/tests/test_trajectory_prompt.py`

**Do not touch:** anything else. This is the brace fix and the test only.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass, including the new test that proves `_TRAJECTORY_PROMPT` formats without `KeyError`. **Paste.**
2. **DB proof, real async path** (the headline that CC10 alone couldn't deliver): Before — `SELECT count(*) FROM agent_run_trajectory_summaries` shows the current count. Trigger ONE real pipeline run (or call `summarize_async` on an existing committed agent_run id; **do NOT** synchronously inline-call summarize this time — we want the real async path). Wait for the summaries to commit. After — the count goes UP, AND a sample row has non-null `what_worked`/`what_stalled`/`what_was_missing` content (real LLM output, not the all-null fallback). **Paste before/after + 1 sample row.**
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1` on `worker/cc11-trajectory-prompt-fix`. **Paste.**

---

## Hard constraints
- Touch ONLY the prompt template + the new test. Do not adjust the GC fix or `_do_summarize`.
- Either brace-escape OR switch templating (`.replace`) — your call; document which and why.
- The DB proof must use the **real async path** (`summarize_async` + drain via task await), not synchronous inline calls. CC10 proved sync works; CC11 must prove async + drained.
- Local-only git. Worker commits on `worker/cc11-trajectory-prompt-fix`.

---

## Report-back format
```
CC11 — Trajectory Prompt Brace Fix report
1. Commit / branch / worktree
2. LOC diff stats
3. Choice: brace-escape OR replace-templating (+ why)
4. Test pass summary (acceptance #1)
5. DB proof, REAL ASYNC PATH: before/after count + 1 sample row with non-null fields (acceptance #2) — the headline
6. check.sh summary
7. Anything surprising
```

---

**Worker: CC10 fixed the outer bug (GC). CC11 fixes the inner bug (KeyError swallowed). After both, the count must go from 0 → non-zero via the real async path — not a synchronous inline shortcut. That's the proof the self-improvement loop is actually live in production.**
