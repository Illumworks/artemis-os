# OBS-1 — Make it provable which tools an agent actually ran

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this is the instrument
we will use to trust every other agent claim, so a plausible-but-wrong reading is
worse than no reading.

## Why this exists

Argus never ran once in five weeks while Callie told Jon and Josh it was running.
Nobody caught it because **there is no record anywhere of which tools a turn actually
invoked**, so "Callie says she dispatched" and "Callie dispatched" are indistinguishable
from the outside.

`agent_traces.tools_used` is supposed to be that record. It is `[]` for **every agent,
every turn, for 30+ days**. The cause is in `artemis/floating_artemis/chat.py` (~line
1096): it collects `ToolUseBlock`s out of `result.messages`, which works on the Anthropic
API path — but on the **claude-code** path the CLI runs tools inside a subprocess and
returns only a final text result, so there are no `ToolUseBlock`s to find. Every
Slack-facing agent (Callie, Kai, Artemis) is on that path.

Consequence worth being blunt about: **right now, nothing any of these agents claims to
have done is verifiable.** That is the actual thing being fixed here.

## What to build

Capture the tool calls the CLI really made and record them on the turn.

1. In `artemis/providers/claude_code/adapter.py`, the tool path runs the CLI with
   `--output-format json`, which returns only the final result. `--output-format
   stream-json --verbose` emits a line per event, including `tool_use` and `tool_result`
   blocks. Switch the **tool path** to stream-json, parse the events, and surface the
   invoked tool names (and whether each returned an error) on the `CompletionResponse` so
   callers can see them.
   - Verified working by hand on 2026-08-12: piping a real prompt through
     `--output-format stream-json --verbose` yielded `TOOL_USE -> ToolSearch` then
     `TOOL_USE -> mcp__artemis__dispatch_research` plus their `tool_result`s. This is
     the mechanism; you are wiring it, not discovering it.
   - Keep the final assistant text identical to what the JSON path produced. Users must
     not see a formatting change.
2. In `chat.py`, populate `tools_used` from that instead of (or in addition to) scanning
   `result.messages`, so the existing Anthropic path keeps working unchanged.
3. Strip the `mcp__artemis__` prefix when recording, so `tools_used` reads
   `dispatch_research`, matching the names in the registry and in every brief.
4. Record **tool failures too**, not just names. A tool that ran and errored is the case
   that matters most — that is precisely what happened here, and a name-only list would
   have looked identical to success.

No migration: `agent_traces.tools_used` already exists and is already the right shape.

## Out of scope

The MCP subprocess's own stderr logging (it does not reach `app.err.log`, which is a real
gap but a different one), Argus's durability (ARGUS-1, running concurrently — **do not
touch `artemis/floating_artemis/tools/argus_tools.py`**), and the non-tool streaming path
(`/messages/stream` stays text-only, as its docstring says).

## Hard constraints

- Do not change the text a user sees. Prove it.
- Do not touch `artemis/floating_artemis/tools/argus_tools.py`,
  `artemis/crisis_content/*`, or `artemis/pipelines/*`.
- No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- The tool path must not get materially slower. Streaming means reading N lines instead
  of one; say what it costs.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest artemis/floating_artemis/tests -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Use `artemis_test_c`; ARGUS-1 is concurrently using `artemis_test_b`. Both env vars are
required; worktrees have no `.env`.

## Tests (all required)

- [ ] A stream-json transcript containing two `tool_use` events yields both names, in
      order, deduped the way the current code dedupes.
- [ ] A `tool_result` carrying an error is recorded as a failure, not as a plain success.
- [ ] The `mcp__artemis__` prefix is stripped.
- [ ] A turn with no tool calls yields an empty list (and not a crash on absent events).
- [ ] The final assistant text is byte-identical to what the JSON path returns for the
      same transcript.
- [ ] Malformed / partial stream lines are skipped without failing the turn — the CLI
      can emit non-JSON noise, and losing a whole turn to log parsing would be a worse
      bug than the one being fixed.
- [ ] The Anthropic (`ToolUseBlock`) path still populates `tools_used` unchanged.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] **Live proof**: run one real Callie turn that calls a read-only tool
      (`list_candidates` is safe and reads the live DB), then paste the
      `agent_traces` row showing a non-empty `tools_used`. That row is the deliverable;
      everything else is scaffolding.
- [ ] Say what the streaming switch costs in latency.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you think is wrong rather than building to it silently.
