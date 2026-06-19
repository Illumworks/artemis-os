# Callie — Proactivity (#1) + Learn-from-reactions (#2) — Handoff for Terminal Opus

**Context:** Jon wants Callie to feel like a teammate who *initiates*, not a tool you query — and to get sharper
from how Jon reacts. This is the marketing/signals/Callie lane (yours — you just did the get_signal/district
work). Coordinate exact thresholds with Jon (he's the daily user). **First commit your enablement work so the
Lead can merge the held Argus-resilience branch (it collides on `main.py`).**

## #1 — Proactive top-signal push
When a signal **qualifies at top-tier**, Callie proactively posts to the marketing channel (no @ask needed):
the signal + a **recommended angle** + an offer ("want me to dig deeper with Argus, or kick off a brief?").
- **Event-driven, NOT a new scheduler job** — hook into the signal *qualification* flow (when the qualifier
  marks a signal top-tier), so it doesn't touch `proactivity/scheduler.py` (the Lead's Artemis work is there —
  avoid collision).
- **Top-tier gate:** reuse the existing qualifier scoring/tier (don't invent a new score) — only the genuinely
  high-value ones, so this is signal not noise.
- **Dedup + frequency cap:** never re-post the same signal; cap pushes (e.g., N/day) so Callie isn't spammy.
- Reuse the markdown→Slack formatting + the async Argus dispatch that already exist.

## #2 — Learn from Jon's reactions
Capture how Jon reacts to Callie's signals/pushes and use it to rank what she surfaces:
- **Capture as scoped MEMORY OBSERVATIONS (no new table — avoids migration collisions).** Record a lightweight
  "engagement" event when Jon acts on a signal (asks to dig/brief) vs ignores it, keyed by the signal's
  attributes (reason codes, campaign family, district type/size).
- **Use it:** an aggregate weighting that ranks proactive pushes (#1) — favor attributes Jon engages with,
  down-rank ones he ignores. v1 = capture + a simple weighting. (The sophisticated trace-based version is P6
  later — `agent_traces` is already collecting.)
- Keep the learning *pattern* simple + documented so the Lead can mirror it for Artemis (consistency, not two
  divergent systems).

## Also fold in: the Argus "dig deeper" button
A "dig deeper" affordance on top-tier qualified signal cards (the signals funnel UI) that fires the existing
async Argus dispatch for that signal's district. Same lane as #1 — natural to build together. (The async
dispatch is now resilience-hardened, so the button is safe.)

## Constraints
- No new dependencies. Match style. Don't touch `proactivity/scheduler.py` or the brief (the Lead's Artemis
  lane). Event-driven push + memory-based learning keep you out of those files.
- Live-verify with Jon: the top-tier threshold + push frequency are HIS call (tune to taste — too quiet vs too
  noisy). Confirm the EFFECT (a real top-tier signal → Callie posts unprompted with a good angle).
