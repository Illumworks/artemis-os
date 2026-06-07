# H1 — Self-teaching tool error messages (platform-wide runtime hallucination recovery)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/h1-self-teaching-errors`
**Browser smoke owner:** Lead, post-merge — re-trigger any failing tool call (manually via curl OR observe in next pipeline run), verify error message lists the valid enum values.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (error formatter + registry hook + tests).
**Priority:** CRITICAL — foundation brief for the anti-hallucination stream (H1 → H2 → H3 → H4 → CC20 → H5). Platform-wide leverage at smallest cost. Every agent benefits immediately.

---

## Why this exists — the runtime-hallucination class of bug

Per `docs/hallucination-audit-2026-05-29.md`: Run #329 of `marketing.qualifier.brief_composer` called `signal_queue.update_status(state="pending_human_review")` and got 7 consecutive `ILLEGAL_TRANSITION` errors. The agent retried with the same hallucinated state because **the error message didn't tell it what the valid states were.**

The tool DID validate the input. The validation DID reject the call. But the error returned was opaque:

```
{"error": "ILLEGAL_TRANSITION"}
```

The agent had no way to recover. It tried the same invalid state 7 times in a row, then gave up.

**Self-teaching error messages close this loop at the platform level.** When a tool's input_schema rejects a value, the response MUST enumerate the valid alternatives:

```
{"error": "Invalid value for parameter 'state': 'pending_human_review'. Valid values are: pending_qualification, qualified, suppressed_stale, rejected_hard_filter, archived, held_pending_corroboration."}
```

The next LLM turn sees the valid set and can self-correct. The runtime-hallucination class of bug becomes single-retry-recoverable instead of silent-failure-persistent.

This is the highest-leverage brief in the anti-hallucination stream because:

- Every agent uses ToolRegistry (200+ agents)
- The fix is ~80 LOC at one place (the registry error path)
- It's a precondition for several downstream briefs — they assume tool errors carry recovery info
- No schema changes, no agent changes, no prompt changes

---

## Scope

### Part A — Error formatter that consumes input_schema

In `artemis/agent/tools.py` (where `ToolRegistry` lives — confirm via `grep -rn "class ToolRegistry"`), find the path that handles input_schema validation failures. Replace generic error messages with self-teaching ones derived from the tool's declared JSONSchema.

For each validation failure type:

1. **enum violation** — input_schema declares `{"type": "string", "enum": ["a", "b", "c"]}`, agent passes `"d"`. Error message:
   ```
   Invalid value for parameter '{name}': '{actual}'. Valid values are: {comma-separated enum list}.
   ```

2. **type mismatch** — input_schema declares `{"type": "integer"}`, agent passes `"forty-two"`. Error message:
   ```
   Invalid type for parameter '{name}': expected {expected_type}, got {actual_type}. Example valid values: {one or two examples derived from schema if available}.
   ```

3. **missing required field** — input_schema declares `required: ["agent_id"]`, agent omits it. Error message:
   ```
   Missing required parameter: '{name}'. Expected type: {type}. {description if present in schema}.
   ```

4. **constraint violation** (minimum, maximum, minLength, maxLength, pattern) — Error message names the constraint and provides the limit:
   ```
   Value for parameter '{name}' violates constraint: {constraint}={limit}. Got: {actual}.
   ```

5. **additionalProperties violation** (extra unknown keys) — Error message:
   ```
   Unexpected parameter: '{name}'. Allowed parameters are: {comma-separated list from properties}.
   ```

### Part B — Hook into the registry execution path

Find the tool execution path in `ToolRegistry`. There's likely a `validate_input(tool_name, raw_input)` or equivalent that runs JSONSchema validation. Replace its error production with a call to the new self-teaching formatter.

Implementation pattern:

```python
def _format_validation_error(tool_name: str, schema: dict, raw_input: dict, exc: jsonschema.ValidationError) -> dict:
    """Convert a JSONSchema validation error into a self-teaching error message."""
    # Extract the failed field path
    field_path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
    # Look up the relevant subschema for context (enum values, type, constraints)
    # ... build the message per Part A taxonomy ...
    return {
        "error": "validation_failed",
        "tool": tool_name,
        "field": field_path,
        "message": self_teaching_message,
        "valid_values": enum_list_or_none,
        "raw_value": actual_value,
    }
```

The exact return shape should match what the registry currently returns (so callers don't need to change). The `message` field is the load-bearing improvement.

### Part C — Verify self-teaching messages flow back to the LLM

In `artemis/builders/executor.py` (or wherever tool results are returned to the LLM via `ToolResultBlock`), confirm the error message is passed verbatim to the LLM, not swallowed/redacted. The LLM must see the full message to self-correct.

If today the executor only logs the error and returns a generic "tool failed" to the LLM, fix that path. The agent's next turn needs the schema-enumeration to recover.

### Part D — Tool that signal_queue.update_status uses (verify enum is in schema)

Spot-check `artemis/tools/signal_queue.py` — does the `update_status` tool's input_schema actually declare an `enum` constraint on the `state`/`status` parameter? If not, the self-teaching error won't fire because there's nothing to enumerate. The schema needs the enum constraint for the error message to be useful.

Per `docs/hallucination-audit-2026-05-29.md`, the valid states empirically observed are: `pending_qualification`, `qualified`, `suppressed_stale`, `rejected_hard_filter`, `archived`, `held_pending_corroboration`, `suppressed_deprioritized` (legacy — see CC20 banked finding).

If the schema enum is missing or incomplete, add it now as part of H1. Document the canonical state list in `artemis/marketing/models.py` as the source of truth.

### Part E — Tests

`artemis/agent/tests/test_h1_self_teaching_errors.py`:

1. **Enum violation produces enumerated error.** Mock tool with `{"type": "string", "enum": ["a", "b", "c"]}` schema. Call with `"d"`. Assert error message contains `"Valid values are: a, b, c"`.
2. **Type mismatch produces typed error.** Mock tool with `{"type": "integer"}`. Call with `"hello"`. Assert error mentions expected/actual types.
3. **Missing required parameter produces named error.** Mock tool with `required: ["x"]`. Call with `{}`. Assert error names "x" as the missing param.
4. **Constraint violation reports the constraint.** Mock tool with `{"type": "integer", "maximum": 10}`. Call with `20`. Assert error mentions "maximum=10".
5. **Additional properties violation lists allowed.** Mock tool with `properties: {"a": ..., "b": ...}`, additionalProperties: false. Call with `{"a": 1, "c": 2}`. Assert error lists "a, b" as allowed.
6. **End-to-end LLM recovery test (integration).** Mock an agent that calls `signal_queue.update_status` with `"pending_human_review"` once, sees the error, then calls again with `"qualified"`. Verify the second call succeeds. This proves the self-teaching round-trip works.
7. **Regression: existing tool calls with valid inputs are unaffected.** Random sample of 5 existing real-world tool invocations from `tool_invocations` table — verify they still succeed under the new code path.

### Part F — Documentation

Add a short section to `docs/pipeline-authoring-principles.md` (the principles doc Lead wrote earlier this session) titled "Tools fail loud and recoverable." Capture:

- Every tool input_schema MUST declare enum/type/required/constraints rigorously
- Validation errors return enumerated valid alternatives, not opaque codes
- Agents recover via the next turn — single-retry-to-correct is the expected behavior pattern

---

## Files owned

- EDIT: `artemis/agent/tools.py` (or wherever `ToolRegistry` lives — find via grep)
- EDIT: `artemis/builders/executor.py` (verify error flows back to LLM)
- EDIT: `artemis/tools/signal_queue.py` (confirm/add enum constraint on `state` parameter)
- EDIT: `artemis/marketing/models.py` (document canonical state list if not already present)
- NEW: `artemis/agent/tests/test_h1_self_teaching_errors.py`
- EDIT: `docs/pipeline-authoring-principles.md` (new section)

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/agent/tests/test_h1_self_teaching_errors.py -v` — all 7 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **Live regression smoke (Lead does this post-merge):**
   - Curl an agent execution that intentionally hallucinates a state (e.g. invoke a test agent that calls `signal_queue.update_status(state="invalid_state")`)
   - Inspect the response — the error message in `tool_invocations.result_preview` should enumerate the valid states
   - **Paste the error message returned to the agent.**
5. `git diff --stat` + `git log --oneline -1` on `worker/h1-self-teaching-errors`. **Paste.**

---

## Hard constraints

- **No regression on existing successful tool calls.** This is critical — H1 changes the validation error path but must not change the success path. Spot-check at least 5 real-world tool calls from `tool_invocations` to confirm.
- **No schema changes.** Pure error-message improvement at the registry layer.
- **Error format must match what callers expect.** If the registry returns a dict today, return a dict tomorrow. If a string, a string. The `message` field is the new content; the wrapper stays compatible.
- **Self-teaching messages must be concise.** Under 300 chars per error. Long enumerations should truncate to first 10 + "...and N more (see tool schema)" rather than dumping 50 values.
- **Local-only git.** Worker commits on `worker/h1-self-teaching-errors`; terminal-Lead merges after Lead approves.

---

## Why this is the foundation brief

H2/H3/H4 add Pydantic validation at JSON-emit sites. CC20 added grounding tools so the Builder can read truth ahead of time. **H1 closes the recovery loop at runtime** — when an LLM does emit a hallucinated value, the tool layer rejects it AND teaches the correct value in the same response. The LLM self-corrects on the next turn. No human intervention. No DB pollution. The bug class becomes single-turn-recoverable.

Without H1, the other anti-hallucination work is incomplete — agents would still produce hallucinated values, just persist them less often. WITH H1, the platform's tool layer becomes a teaching surface: every wrong input is met with the right hint.

---

## Report-back format

```
H1 — Self-teaching tool errors report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially the end-to-end recovery integration test)
4. Live regression smoke output — PASTE the error message format that real LLMs will see
5. Spot-check of 5 random existing successful tool calls — confirm none regressed
6. The signal_queue.update_status schema — does it have the enum now? PASTE the schema if newly added.
7. check.sh summary
8. Anything surprising — especially around JSONSchema validator behavior, error path edge cases, or LLM-visible error truncation
```

---

**Worker: H1 is the platform-wide foundation for "no hallucinations" as an invariant. Every agent that calls a tool inherits self-teaching error recovery the moment this merges. The remaining briefs (H2 scout intake, H3 trajectory summarizer, H4 meeting summarizer, CC20 already merged, H5 brief/pipeline) all benefit from this foundation — they assume tool errors carry recovery info.**
