# Worker Brief — Human-Feel Polish (Slack formatting + per-person memory)

**Owner:** terminal/Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branches:** `worker/humanfeel-fmt` (A) + `worker/humanfeel-relationship-memory` (B) — different files, parallel-safe.
Goal: make Callie/Artemis read like a human teammate. Test DB at head (artemis_test @ 0079) — real tests.

## A — Slack output formatting (small, clear)
Callie posted a markdown TABLE (`| Field | Value |` + `---`) which Slack renders as raw pipes/dashes — ugly.
- **Persona/prompt rule (both agents):** in Slack, NO markdown tables. Use **bold labels** + short bullet
  lines / plain sentences. Add to the Slack-context block in `_build_system_prompt` (chat.py) — applies to
  any agent replying in Slack.
- **Lint backstop:** extend `artemis/writing_rules/agent_lint.py` (already strips em-dashes/emojis for
  outbound) to detect a markdown table and convert it to bullet lines (or at least flatten the `|---|`
  separator rows). Applied on the Slack outbound path where the lint already runs. Keep it deterministic.
- Acceptance: a reply that would have been a table renders as clean bold+bullets in Slack; lint converts a
  table if the model still emits one.

## B — Per-person ("relationship") memory (the "remembers you" goal)
Today her memory captures WHAT was discussed (agent:callie scope) but not robustly WHO. Make agents remember
the person + their history so people forget they're talking to an AI.
- **Capture speaker identity on memory writes.** `route_inbound` already resolves `speaker_name` + has the
  Slack `user_id`; thread the speaker (id + display name) into `write_turn_drawer`/the observation so each
  memory carries who said it (e.g. an observation field or a structured prefix). Resolve display names via
  the J9b `slack_users` cache where possible.
- **Per-person recall.** On a turn, surface prior context for THIS speaker (e.g. retrieval biased toward
  observations attributed to them, or a short "what I know about <person>" digest injected into the
  prompt). Keep it cheap; reuse the existing memory retrieval (`memory/retrieval.py search_observations`).
- **Design note / investigate first:** check how observations store/scope speaker today (they may not).
  Pick the least-invasive approach — a speaker tag on the observation + a retrieval filter/boost — over a
  schema overhaul. Report the approach before a big change. Lossless (no rewriting existing obs).
- Acceptance: after Person X talks to Callie, a later turn from X surfaces relevant prior context about X;
  attribution is captured on new memories; Artemis's existing memory untouched.

## Constraints (both)
- Lossless; no new deps; ruff + mypy strict; own worktree/branch; DO NOT merge (Lead merges sequentially).
- Don't regress P1/C2 routing, slice-1 scope, or the W2 relevance gate.

## Merge (Lead)
A then B (different files, clean). Combined suite, restart, live-verify with Jon: a table-y answer renders
clean; Callie recalls a specific person's prior context.
