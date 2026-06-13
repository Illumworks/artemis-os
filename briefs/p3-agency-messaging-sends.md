# Worker Brief — Agency messaging sends: 2b Slack-send + 2d Gmail-send

**Owner:** Codex (backend). **Lead:** Artemis (Opus) **MERGES — non-negotiable for sending slices.** Do NOT
self-merge: these are the actions that send messages *as Jon*, so Lead audits + merges + does the live test.
**Isolation (AGENTS.md rule 6):** own worktree (`worker/p3-agency-messaging-sends`), own test DB (name contains
`artemis_test`); commit before reporting; do-NOT-merge.
**Status:** READY. Builds on the SHIPPED `artemis/proactivity/agency_gate.py` propose→confirm gate.

## Context — the gate already exists and is proven
`agency_gate.py` has the full propose→confirm gate (calendar + Jira live, verified end-to-end 2026-06-13). Two
executors are currently `NotImplementedError` stubs — implement them. **Reuse the gate as-is; do NOT add any
new approval mechanism.** The one-shot / no-execute-without-approval / expiry safety is already proven; these
just slot in as new `action_type` executors.

## 2b — Slack-send (`_execute_slack_send`) — primary; token already authorized
- Execute via the **`slack_user` token** (`provider="slack_user"`, `chat:write`) so the message is sent **as
  Jon** — the same token the radar uses (no re-auth needed). Resolve it like `radar._resolve_slack_user_token`.
- Payload: `{channel, text, thread_ts?}` — `thread_ts` set when replying in a thread.
- **Pair it with the radar (the payoff):** add a path to propose a *reply to a radar mention* — given a radar
  item (channel + thread), draft the reply text, `propose_action(action_type="slack.send", ...)` with a clear
  preview, DM Jon, and on approval it posts the reply in that thread as Jon. (Drafting copy can be simple/
  deterministic or LLM — keep it minimal; the gate + send is the deliverable.)

## 2d — Gmail-send (`_execute_gmail_send`) — needs a scope re-consent
- Execute a Gmail send/reply via the Gmail API (`messages.send`, RFC822/raw) on the **personal** credential.
  Payload: `{to, subject, body, thread_id?, in_reply_to?}` (thread_id/in_reply_to for replies).
- **Scope:** add `https://www.googleapis.com/auth/gmail.send` to `GOOGLE_PERSONAL_SCOPES`
  (`artemis/google_integration.py`). **Jon re-consents the personal account at Lead's verify time** to pick it
  up (we currently hold `gmail.readonly` only). Until then the executor will 403 — that's expected pre-consent.

## Constraints (safety — these SEND on Jon's behalf)
- Everything goes through the existing gate — **no message sends without an approved proposal.** No new bypass.
- Tokens encrypted; never logged. Don't break the radar's use of the `slack_user` token or the bot flows.
- **Tests must never send to real third parties.** Slack-send → test only against Jon's own DM or a dedicated
  test channel; Gmail-send → only to Jon's own address. Mock the network in unit tests; assert the gate gating.

## Ship gate (Lead verifies LIVE — Lead merges first)
- **Slack-send:** propose a reply → DM Jon preview → reply "no" → nothing sent; reply "yes" → the message
  actually posts (to Jon's own DM/test channel) as Jon; proposal → `executed`; double-"yes" sends once.
- **Gmail-send:** after Jon re-consents `gmail.send`, propose an email to Jon's own address → approve → it
  actually sends; reject → nothing sent.
- Re-confirm the gate invariant holds for the new types: **no execution path without `status='approved'`.**
