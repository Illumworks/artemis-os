# Worker Brief — Conversational Slack Confirm for layer-3 agent actions (no buttons)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-slack-confirm`. Test DB at head — real tests.

## Why
Propose→confirm (layer-3/4) was built for the web UI (suspend + WS event + `/tool-confirm`). In Slack there's
NO confirm path — `route_inbound` doesn't handle a layer-3 yield, so e.g. the OKR check-in can propose but
can't APPLY. Jon's steer: **no buttons** ("you wouldn't push a button on a real person") — make it
**conversational**: Artemis proposes, Jon replies "go"/"do it"/"approve" in natural language, she applies.

## How it works today (reuse)
`handle_turn` yields on a layer-3 tool → stores a `PendingConfirmation` in `confirmation_store` (keyed by
tool_use_id; has `list_for_session`) + returns a `TurnResult` with `pending_tool_use_id` set. `resume_after_confirm(session_id, decision)`
(chat.py) resumes + runs/cancels the tool. The web path resolves via `/tool-confirm`. We need the Slack
equivalent, conversationally.

## Scope (`artemis/routes/integrations_slack_events.py` + reuse chat.py)
1. **On a layer-3 yield in a Slack session:** after `handle_turn`, if the result has a pending confirmation
   (`pending_tool_use_id` / `confirmation_store.list_for_session(session_id)`), post Artemis's proposal text to
   Slack as normal (she should say what she'll do + "say go when you want me to apply"). The pending
   confirmation stays in `confirmation_store` keyed to the session.
2. **On the NEXT inbound message in a session that HAS a pending confirmation:** before treating it as a new
   turn, classify the reply with a CHEAP yes/no classifier (haiku-tier, like the channel gate):
   - **affirmative** ("go", "yes", "do it", "approve", "ship it") → `confirmation_store.resolve(tool_use_id, "run")`
     + `resume_after_confirm(...)` → post the result ("Done — updated KR X to Y.").
   - **negative** ("no", "hold", "not yet", "cancel") → resolve "cancel" → post a brief ack.
   - **neither** (a new unrelated question, or corrections/word-dump) → treat as a normal new turn (do NOT
     force a yes/no); the pending confirmation can expire or be re-proposed. Be conservative: only
     resolve on a clear affirmative/negative.
3. Keep it per-session + agent-aware (works for Artemis DM and Callie's channels). Respect dedupe + the
   bot-self filter. Time-box stale pendings if practical.

## Constraints
- No buttons. Lossless; no new deps; ruff + mypy strict; DB-backed tests where natural.
- Don't regress P1/C2 routing, the W2 channel relevance gate, or the web confirm path.
- This is the substrate for ALL P2c propose→confirm-in-Slack actions, not just OKR.

## Tests
- A Slack session with a pending layer-3 confirmation + an affirmative reply → resolves "run" + resumes
  (mock the tool); a negative reply → "cancel"; an unrelated reply → new turn, pending untouched.
- Web `/tool-confirm` path still works (no regression).

## Acceptance
In Slack, Artemis proposes a layer-3 action, Jon replies "go" in natural language, she applies + reports.
Lead verifies live via the OKR check-in: propose → Jon says go → an OKR KR actually updates (only then).
