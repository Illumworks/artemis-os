# Terminal Orchestration Brief — Callie/Artemis Slack Polish

**Owner:** terminal (orchestrator). **Executors:** 2 Sonnet sub-agents (worktree-isolated → parallel-safe).
**Lead:** Artemis (Opus) reviews + merges sequentially + live-verifies + restarts. **Plan:**
`docs/callie-build-plan.md` (post-launch polish). Test DB is repaired (artemis_test @ 0079) — write real
DB-backed tests, not mock-only.

## Two workers (different files → run in parallel, own worktrees)

### W1 — Callie marketing-only surface scope (the over-scope bug)
File: `artemis/floating_artemis/session_scope.py` (+ tests).
- Today `resolve_surface_scope` only strips marketing from Artemis's DM; **everyone else (incl. Callie) gets
  the FULL surface set** — that's why Callie had Jira/Calendar/OKR tools.
- Build a **per-agent surface allowlist** (extensible — NOT a hardcoded exclusion). Callie's allowed set =
  the marketing surfaces (the same `_MARKETING_SURFACES` set, but as her ALLOW-list:
  scouts, signal-queue, signal-criteria, campaign-ops, campaign-deliverables, content-assets, approvals,
  writing-studio, marketing-os — plus whatever the marketing tools need). Artemis unchanged (personal DM
  stripped of marketing; full elsewhere). Default/other agents unchanged.
- **Leave a clear, documented seam** to add surfaces per agent later: per Jon, Jira + Calendar will be added
  to Callie when the app's scope expands. Make that a one-line edit + comment it.
- Acceptance: a Callie session resolves to marketing-only surfaces (NO jira-board/calendar/okr/dev-projects);
  Artemis personal DM unchanged; Artemis full elsewhere. Unit tests prove each.

### W2 — Natural channel replies + relevance gate + @mention the asker
File: `artemis/routes/integrations_slack_events.py` (+ tests). `message.channels` is now subscribed on
Callie's app.
1. **Handle `message.channels`** for agents with `listen_channel_messages=True` (Callie has it). Route them
   through the same agent-aware path + the SAME session key as app_mentions in that channel (so a non-mention
   reply continues the existing conversation — context continuity). Keep bot-self filter + dedup intact (her
   own posts + the pipeline ticker are bot-authored → must be dropped; verify no echo).
2. **"Should I respond?" gate (load-bearing — conservative + cheap).** Do NOT reply to every channel message.
   Reply only when:
   - it's an `app_mention` (always), OR
   - it's in a thread the agent has already participated in (continuity), OR
   - a CHEAP classifier (haiku-tier, short yes/no prompt — not a full turn) judges the message is addressed to
     her / is a marketing question she can help with.
   Default = **stay silent** (matches Callie's persona: speaks only with a so-what). Never run a full agent
   turn just to decide silence — the gate must be cheap. Never respond to bot/automated messages.
3. **@mention the asker — but ONLY when the reply is "cold," not every turn.** Pinging on every reply in an
   active back-and-forth is annoying (Jon's explicit note). Rule: prefix the reply with `<@{user_id}>` only
   when the asker is probably not actively watching:
   - the **first reply** in this session/thread (cold start), OR
   - **re-engagement after an idle gap** — the last message in this conversation (session/thread) was more
     than ~5 minutes ago.
   During **active flow** (last message < ~5 min ago) reply **without** the `<@…>` ping. Use the existing
   message-history timestamps to measure the gap. DMs (1:1) never need the ping — skip there. Make the
   threshold a small named constant so it's easy to tune.
- Acceptance: first/cold question → reply @mentions the asker; rapid follow-ups in the same thread → replies
  WITHOUT pings (no spam); re-engaging after a lull → ping again; idle chatter does NOT trigger her; her
  own/ticker posts never loop; @mention still works; DM replies unchanged (no self-ping). Artemis's personal
  DM path untouched.

## Constraints (both)
- Do NOT regress P1 (Artemis Jon-allowlist), C2 routing/HMAC, slice-1 personal scope, or the bot-self filter.
- Cost: the relevance gate must be cheap (no full LLM turn for a "no"). Lossless; no new deps; ruff + mypy
  strict; DB-backed tests where natural.
- Each sub-agent: own worktree, own `worker/callie-polish-w1|w2` branch, commit + self-verify, **do NOT merge**.

## Merge (Lead, sequential)
W1 then W2 (different files → clean). Combined Slack/scope suite after each. Live verify: Callie has no Jira
tool; a natural channel question gets an @-pinged reply; idle chatter ignored; no echo; Artemis DM still
personal. One launchd restart makes it live. Jon already added `message.channels` to Callie's app.
