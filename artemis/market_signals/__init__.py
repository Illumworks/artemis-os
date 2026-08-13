"""Callie's combined daily brief for #market-signals.

Jon's decision, 2026-08-12: that channel carries **one** post a day from Callie
combining the top campaign signals, crisis signals and screentime, mentioning
Josh and Angela — not one post per feed. Individual signal cards keep landing in
`#campaign-signals` exactly as before; this exists because that firehose is
unreadable, not because it is wrong.

His words: *"i dont want them to get pinged about every signal that comes in
thats what pops up in the campaign signals slack channel ... callie mentioning
them in a daily brief in the Market signals that combines the top campaign
signals ... would be better and less noise"*.

**Informational in v1, by explicit choice.** No approve/reject buttons: ship a
readable post, see whether it gets read, then add actions. This is also why
`gate_1_signals_inbox` was removed from `marketing.main` rather than merely
quietened — a brief cannot unblock a blocking gate, and that gate silently
stopped every later scheduled run for 57 days.

Section contract, agreed across two concurrent sessions:

    async def build_<feed>_section(session) -> str | None

- returns the section body, or ``None`` when the feed has nothing to say today;
- marks its own items reported, so a re-run contributes nothing;
- never raises into the composer — one dead feed must not take down the brief.

The composer owns the heading, the ordering, the mention and the "quiet day"
case. See `docs/market-signals-unification-note.md`.
"""

from artemis.market_signals.composer import (
    build_daily_brief,
    post_daily_brief,
    register_market_signals_schedule,
    run_daily_brief,
)

__all__ = [
    "build_daily_brief",
    "post_daily_brief",
    "register_market_signals_schedule",
    "run_daily_brief",
]
