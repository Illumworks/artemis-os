# Worker Brief — Personal-Scope Artemis's Slack DM (App Modes, slice 1)

**Owner:** Codex (backend slice). **Lead:** Artemis (Opus) verifies in real Slack + merges.
**Status:** READY for pickup. **Branch:** `worker/personal-scope-artemis-dm`.
**Depends on:** P1 inbound hardening (merged, d9b5b62) and the named-agent lint (merged, 67617d0).
**Design source:** `docs/agent-slack-architecture.md` (Artemis DM = personal/ops/upgrades, no unprompted
marketing; Callie owns the marketing channels).

## Why
Jon's rule (2026-06-10): Artemis's private DM is for personal things, app issues, and upgrades. She is the
orchestrator + personal partner. **She does not volunteer marketing.** Marketing belongs to Callie in the
marketing channels. The live test on 2026-06-10 showed the opposite: a plain "Hey Artemis" got a Gate-1
marketing answer, because Jon's DM session had been used as a marketing workspace and its history is full of
signal/campaign turns, and because Artemis's tool set includes marketing tools.

This slice scopes the **Slack DM** to personal. It does NOT touch the web app nav (secondary/cosmetic) and
it does NOT build Callie (that is the later P4 build that fully moves marketing off Artemis).

## Scope

### 1. Personal surface scope for the Slack DM session
Artemis's surfaces/tools are gated by `available_surfaces` (see `artemis/routes/status.py:_AVAILABLE_SURFACES`,
consumed in `artemis/floating_artemis/chat.py:handle_turn` and `tool_registry.build_authorized_tool_registry`).

- Introduce a **per-session surface scope**. When an FA session is a Slack DM (session_id starts with
  `slack-` AND metadata `surface == "slack"`, i.e. the personal 1:1), resolve a **personal surface set** =
  the full set MINUS the marketing surfaces:
  `{scouts, signal-queue, signal-criteria, campaign-ops, campaign-deliverables, content-assets, approvals,
  writing-studio, marketing-os}`.
- Implement cleanly: e.g. a helper `personal_surfaces(all_surfaces) -> set[str]` in a sensible module, and
  thread a `surface_scope` (or derive from session metadata) into `handle_turn` so the tool registry + the
  system-prompt surfaces line use the scoped set. Web/floating sessions keep the full set (unchanged).
- Effect: in the DM, marketing tools are not registered (the registry already gates on these surfaces), and
  marketing surfaces are absent from her prompt, so she has no means or prompt-nudge to surface marketing.

### 2. Personal-scope framing in the DM system prompt
The Slack branch of `_build_system_prompt` (chat.py) already adds Slack context. Extend it for the personal
DM: state plainly that this DM is **personal, app/ops, and upgrades**; she is the orchestrator and personal
partner; **she does not volunteer marketing** here; marketing is Callie's lane and is surfaced only if Jon
explicitly asks. Keep it short.

### 3. Retire the marketing-polluted DM history (LOSSLESS) and start the personal DM clean
Jon's instruction: the marketing history (the incoming-signal/campaign conversation that accumulated in the
Artemis DM) should be **removed from Artemis and given to Callie**. Callie does not exist yet, so for now:

- **Do NOT delete anything** (house rule: lossless; supersession/archival only).
- Mark the existing marketing-laden DM session(s) as **archived / retired from Artemis's active context**
  (e.g. an `archived_at` or `scope='retired'` flag on `floating_artemis_sessions`, or a "history cutover"
  timestamp), so its old turns are **not loaded into the personal DM context** going forward.
- The personal DM continues as a **fresh personal session** (new session row for the same channel, or a
  cutover marker so history before the cutover is excluded from `_load_message_history`). Pick the simplest
  approach that keeps `route_inbound`'s stable-key behavior working for new messages.
- Tag the retired history so it can be **handed to Callie's ownership** when she is built (a marker/owner
  field is enough; the actual migration is deferred to the Callie build). Leave a one-line note in the code
  or a follow-up brief pointer.

> The known DM sessions today are `slack-T4MNZ8CCV-D0AN8CCJC4C-_` and `slack-T4MNZ8CCV-D0B4EQ175FD-_`
> (one of these is Jon's personal DM with the marketing history). Confirm which carries the marketing turns
> before retiring; do not retire a session that is not the Artemis↔Jon DM.

## Constraints
- **Lossless.** No DELETE of sessions/messages. Archive/supersede/flag only.
- **Do not regress P1** (`integrations_slack_events.py`): the bot-self filter, allowlist gate, dedupe, and
  identity threading must stay intact. Only the surface scope + history-load behavior change.
- Web/floating (non-Slack) sessions keep full surfaces. No behavior change there.
- No new dependencies (org policy). ruff + mypy strict clean; run `./scripts/check.sh`.

## Tests
- A Slack DM session resolves the personal surface set (no marketing surfaces); a full/web session keeps all.
- The tool registry built for a DM scope contains no marketing tools.
- History retirement is lossless: the old session/messages still exist (queryable), but the personal DM
  context no longer loads pre-cutover marketing turns.
- The personal-DM system prompt contains the "personal, no unprompted marketing" framing.
- P1 regression: existing `test_p1_slack_inbound_gate.py` + `test_j1_slack_events.py` stay green.

## Acceptance
In a Slack DM, Artemis has no marketing tools and does not surface marketing unprompted; a plain greeting
gets a personal/ops reply, not a Gate-1 dump. Old marketing DM history is preserved (lossless) but retired
from her active context and tagged for Callie. Checks green. Lead verifies live: Jon DMs "hey", reply is
personal; no echo, allowlist intact.

## Out of scope (radar, not now)
- Web-app marketing nav hiding (cosmetic).
- Building Callie + actually migrating the retired history to her (P4).
- Cleaning up the mockup campaigns/docs (Jon: leave ONE campaign for Friday's presentation mockup; clean the
  rest later). Tracked in SESSION-STATE.
