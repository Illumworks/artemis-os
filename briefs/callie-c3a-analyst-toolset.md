# Worker Brief — Callie C3a: Analyst Toolset (read tools + analyst posting)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/callie-c3a-analyst-toolset`. **Builds on:** C2 (merged, Callie live, marketing-scoped).
**Plan:** `docs/callie-build-plan.md` (C3).

## Why
Callie is conversational but has no domain substance — she can't pull the Message Compass, claims, or
performance, so she can't lead with a proof-backed so-what (her whole persona). C3a gives her the read tools
+ an analyst posting tool. All the underlying read APIs already exist; this is mostly wiring tools into the
marketing tool registry.

## Scope — new marketing tools (gate `[surface:marketing-os]`, mostly `[layer:1]` read-only)
Register in `artemis/floating_artemis/tools/marketing.py` via `register_marketing_tools` (follow the existing
Tool-def + `registry.register(...)` pattern; see tool_registry.py:23-42). Each tool opens its own
`SessionLocal()` and returns a concise string, matching the existing marketing tools.

1. **`get_message_compass`** (layer 1) — read the canonical brand source.
   `writing_rules.repository.get_source_by_profile_key(session, profile_id, "01_MESSAGE_COMPASS")` →
   return `normalized_content` (falls back to `original_content`). Resolve the active writing profile_id the
   same way the claims route does (`artemis/marketing/routes/claims.py` auto-resolves the active profile);
   reuse that resolver, don't hardcode profile_id=1. The Coherence Map lives inside the Compass content, so
   this tool also covers "check coherence" for now (no separate API needed).
2. **`search_claims_register`** (layer 1) — query approved claims.
   `writing_rules.repository.list_claims(session, profile_id, status="approved")`; support an optional
   substring/tier filter in the input schema; return claim_code, tier, approved_phrasing (truncated).
   This is how Callie tiers a claim ("that's Tier 4, needs a proof pack").
3. **`get_campaign_performance`** (layer 1) — basic trend/performance read. No aggregate metric API exists;
   synthesize from `marketing.repository.list_candidates`, `get_campaign_brief`, `get_candidate_signals`,
   and `pipelines.repository.list_pipeline_runs`. Return a compact per-campaign status/age/signal-count
   summary Callie can narrate. (Flag in the tool description that these are raw reads, not aggregated KPIs.)
4. **`post_analyst_message`** (layer 2 or 3 — propose/confirm if side-effectful) — let Callie post a
   synthesized recommendation/digest to one of HER channels. Resolve **Callie's** Slack token from her
   integration row (`provider="slack", agent_id="callie"`, decrypt access_token) — NOT the default/Artemis
   client. Restrict the target to Callie's configured channels (campaign signals C0B9CHVC7KQ / Marketing
   Campaigns); reject other channels. Run the text through `lint_agent_text` (no em dashes/emojis) before
   posting. This is how she proactively delivers analysis (vs. only replying).

## Constraints
- These tools are marketing-scoped, so Callie gets them (she's marketing-scoped) and Artemis's personal DM
  does not (it strips marketing surfaces). Verify that scoping holds.
- Read tools are layer-1 (no confirm). `post_analyst_message` is outbound → make it layer-2/3 so it follows
  the propose→confirm gate appropriately (Callie announces, then posts).
- No new dependencies; lossless; ruff + mypy strict clean; `./scripts/check.sh` (note: pre-existing repo-wide
  format debt in ~9 unrelated files is a known baseline — don't fix those here).
- Don't touch the composer or the P1/C2 Slack-routing internals.

## Tests
- Each read tool returns content for a seeded profile (Message Compass present; approved claims listed;
  performance summary for campaign #18). Empty/missing → graceful string, never raises.
- `post_analyst_message` resolves Callie's token (mock the integration row), rejects a non-Callie channel,
  and lints the text. 
- Registry test: the new tools register under marketing scope and are absent when marketing surfaces aren't
  available (Artemis personal DM scope).

## Acceptance
In a marketing channel, Callie can pull the Message Compass + claims, tier a claim, summarize campaign #18's
state, and post a synthesized recommendation to her channel (lint-clean, her bot). Checks green. Lead verifies
live (ask Callie in `campaign signals` to "give me the angle on the HB27 campaign with the proof we can stand
behind").

## Out of scope (separate C3 slices — see docs/callie-build-plan.md)
- **C3b:** route the pipeline Gate-2 *channel card* via Callie's token (finish QW1) — `human_gate_executor.py`
  `_get_slack_token_for_agent(agent_id)`.
- **C3c:** retired Artemis-DM history handoff into Callie's memory scope (`callie_handoff_pending`).
- **C3d:** editable-draft body fix (QW2). A draft IS a `campaign_deliverables` row, content in
  `deliverable_metadata`, read by `_latest_draft_content`. Fix = align where the deliverables pipeline writes
  the composed body with where the composer reads it (+ ensure all deliverables get a real body, not a stub).
  Contained DB/pipeline fix — NO external backend / Google Docs (that was a retracted earlier guess).
