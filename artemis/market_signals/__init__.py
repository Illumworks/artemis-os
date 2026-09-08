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

What this brief IS, in Jon's words (clarified 2026-09-08)
--------------------------------------------------------

**A pulse.** What is going on in the market: Amira's own name, AI in schools,
screen time in schools, literacy policy, competitor movement. Topic-shaped, not
audience-shaped.

It is deliberately NOT segmented by customer status. Jon: *"market pulse was
dealing with non customers or a mix not a single bucket it was just a general
whats going on with amira, ai in schools, screentime in schools and what not."*
Whether a district is a customer is a question for the campaign feed and for
Salesforce, not a filter on this one.

**Two things that DO belong even though they are district-specific:** a customer
having a problem, and a district saying something good about us. Both are pulse,
because both are the market talking about Amira.

**What does not belong: buying intent.** Starbridge RFPs, procurement notices and
deadlines are demand-gen signals for Josh and land in `#campaign-signals`. A
Kansas screener RFP is a strong signal in the wrong room. This was proposed and
rejected on 2026-09-08; do not re-propose it without a new reason.

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
