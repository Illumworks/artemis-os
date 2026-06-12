# Worker Brief — speaker_id must come from the authenticated session, not the model

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-speaker-id`. Test DB at head (0082). Real DB-backed tests.

## The bug (live)
`stage_okr_updates` and `complete_okr_checkin` take `speaker_id` from **model input** (`inp.get("speaker_id")`).
The reconcile context embeds Jon's real ID for the model to copy, which works WHILE a check-in is live — but
when it isn't, the model guesses (live: it invented "UJONFILA" instead of `U09F3EPJXSQ`). Beyond fragility,
a model supplying the user ID for a data-write tool is a security smell: the model effectively chooses WHOSE
OKRs to stage/close. The authenticated Slack user is already known server-side; the model should never set it.

## The fix — inject speaker_id server-side, ignore model-supplied values
The authenticated inbound Slack user (`slack_user_id`, e.g. `U09F3EPJXSQ`) is known in `route_inbound` and
passed to `handle_turn(speaker_id=…)`. Thread THAT to the OKR tools and have them use it, ignoring any
`speaker_id` in model input.

- **In-process path:** set a per-turn contextvar (e.g. `floating_speaker_id_var`, mirroring
  `floating_session_id_var`) in `handle_turn` from the `speaker_id` arg; `_stage_okr_updates` /
  `_complete_okr_checkin` read the contextvar, not `inp`.
- **Claude Code subprocess path:** the tools run in the `artemis.tools.mcp_server` subprocess, which does NOT
  see the contextvar. The adapter (`providers/claude_code/adapter.py`) already passes `--floating-session-id`
  to the subprocess; add `--speaker-id <id>` (sourced the same way the session id is — from the caller-owned
  contextvar the adapter reads), have `mcp_server` capture it (like `_serve_floating_artemis` captures the
  session id), and expose it to the tool impls so they use it instead of model input.
- **Remove `speaker_id` from the tool input schemas** (or keep it but IGNORE it server-side — prefer removing
  so the model can't pass it). Update the reconcile context (`chat.py`) to stop telling the model to pass
  `speaker_id` (it no longer needs to).
- The route_inbound apply path already resolves `slack_user_id` server-side — keep that; just make sure
  staging/closing use the same server-trusted identity.

## Constraints
- Approval-first unchanged; this is purely about WHERE the identity comes from. No new deps; ruff + mypy strict;
  DB-backed tests. Don't regress reconcile/stage/apply/breadcrumb/opener.
- Single-user system today, but do this right: the write must target the authenticated session's user, full stop.

## Tests
- `stage_okr_updates` / `complete_okr_checkin` use the server-injected speaker_id even when model input omits
  it or supplies a DIFFERENT id (assert the injected id wins; a wrong/absent model id does not misroute).
- Subprocess path: `mcp_server` receives + uses the `--speaker-id`; the served tools stage against the correct
  breadcrumb without a model-supplied id.
- Full round-trip still works (stage → go → apply) with no speaker_id in model input.

## Acceptance
Artemis can stage/apply/close an OKR check-in without ever guessing or being told the Slack user ID — it's
taken from the authenticated session. A model-supplied speaker_id is ignored. Lead verifies live.
