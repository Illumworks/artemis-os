# Worker Brief — Callie C3b: Marketing Gate card posts via Callie's bot

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/callie-c3b-gate-card`. **Builds on:** C2 (Callie integration row exists, agent_id=callie),
QW1 (owner-DM suppression). **Plan:** `docs/callie-build-plan.md` C3b.

## Why
QW1 stopped marketing Gate-2 approval cards from DMing Jon's personal Artemis DM, but the **channel post still
goes out via the Artemis bot token**. Now that Artemis is OUT of the marketing channels and Callie owns them,
those cards should post **as Callie**.

## Scope (file: `artemis/pipelines/node_executors/human_gate_executor.py`)
- `_get_slack_token(session)` currently returns the first active slack integration (Artemis). Add/ös refactor
  to `_get_slack_token_for_agent(session, agent_id)` selecting the integration row by `agent_id` (the
  `integrations` table now has `agent_id`; Callie's row is `agent_id="callie"`). Keep a default of "artemis".
- For **marketing gates** (kind in `_MARKETING_CHANNEL_KINDS`), post the channel card
  (`_post_review_notification` / `_post_approval_to_channel`, ~lines 179-258) using **Callie's** token. The
  DM path (non-marketing approvers) stays on Artemis. Keep QW1's owner-DM suppression intact.
- Confirm `_MARKETING_CHANNEL_KINDS` covers the right kinds (content_draft / signal_brief / campaign_initiation
  — verify current membership) and decide if content_draft posts as Callie too (it should — it's marketing).

## Constraints
- Don't regress non-marketing gates (still Artemis token). Don't break QW1 owner-DM suppression.
- Lossless; no new deps; ruff + mypy strict clean; tests now CAN be DB-backed (test DB repaired to head).

## Tests
- Marketing gate → channel card uses Callie's token (mock integration rows: artemis + callie); non-marketing
  gate → Artemis token. Owner-DM still suppressed for marketing gates.

## Acceptance
Marketing approval cards post to the marketing channel **as Callie's bot**, not Artemis. Lead verifies live:
trigger a marketing gate, confirm the card appears authored by Callie (`U0B9S32PTAM`).
