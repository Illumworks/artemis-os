# Artemis — Conversation & Brief Fixes (2026-06-20)

## The incident
Saturday 8am: Artemis sent a weekend morning brief, then when Jon asked her to stop
weekend briefs / "why are you giving me a daily brief?", she replied with a broken
repeated clarification ("the calendar change, the calendar change, the calendar change,
or more than one?"), ignored the actual request, and went off-topic (posted a commitments
digest). Jon: "she feels really dumb… she's more rigid than you, conversation-wise."

These were **five specific bugs**, not a capability problem — Artemis runs the same Claude
as the CLI. The rigidity was the **scaffolding in front of her conversational brain.**

## Root causes + fixes
1. **Weekend brief** (`5767a29`) — APScheduler `CronTrigger.from_crontab` reads numeric
   day-of-week as 0=Mon..6=Sun, so `* * 1-5` ran **Tue-Sat** (ran Saturday, skipped Monday).
   8 weekday crons affected. Fixed all to day-names (`mon-fri`/`fri`/`mon`). See
   `feedback-apscheduler-cron-dow` memory.
2. **"calendar change ×3"** (`7e06091`) — `_proposal_short_label` returned a generic label
   per action-type, so 3 distinct calendar proposals (Irving/CNN/EdTech) all read "the
   calendar change". Now uses the specific preview + dedup. Expired the 3 stale proposals.
3. **Agents counted as "people waiting"** (`2565e6f`) — the awaiting-reply radar didn't
   filter bots, so Callie/Ares/Artemis @-mentions counted as people. Now skips `bot_id`.
4. **Ignored Jon / went off-topic** (`a7a0f7a`) — THE big one. A DM runs a gauntlet of
   deterministic routers before reaching `handle_turn` (her real conversation). With
   proposals open, the pending-context router (`_ROUTER_SYSTEM`) classified Jon's *question*
   and *instruction* as `clarify` → hijacked → templated reply, never reached her brain.
   Hardened the router to **default to converse**; questions/instructions/topic-changes/any
   doubt fall through; `clarify` reserved for genuinely-ambiguous approvals. Verified on
   Jon's exact messages.
5. **Rigid / clipped** (`0c63470`) — her persona was all format+brevity rules with nothing
   about engaging. Added a "How you converse" block (answer the real question, thinking
   partner not a form, reason about novel requests, never go quiet/change subject). Voice
   preserved.

## The principle (why she felt rigid vs the CLI)
**Trust the model, scaffold lightly.** The CLI Claude: message → reason + tools → respond.
Artemis had three deterministic interceptors in front of that. Every interceptor costs
flexibility. Fix #4 made the greedy one (the router) default to conversation; the other two
(confirm gate, `try_apply_proposals_reply`) were already conservative (fall through on
non-match) — so #3 ("interceptors reason-driven, not greedy") is satisfied. See
`feedback-artemis-rigid-routers` memory.

## Verify
- Monday's brief: arrives **Monday** (not weekend), no agents in "people waiting".
- Conversation: ask her a question or give an instruction with proposals open — she now
  engages directly instead of "which one did you mean?".

## Remaining / future polish
- Review the full `PERSONALITY_PROFILE` background (Autonomy Levels / Hard Limits / Example
  Lines) for residual rigidity if she still feels templated.
- Make other proactive interceptors reason-driven (let her decide when to surface, vs cron).
- Routing-hardening guard could be unit-tested with the live classifier for regressions.
