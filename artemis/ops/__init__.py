"""Operational visibility helpers.

`artemis.ops.health` answers one question in one place: **is this system
actually alive, and where is it stuck?**

It exists because agent activity is recorded in six unrelated stores, and
reading any single one gives a confidently wrong answer.  On 2026-08-10 a
session read `floating_artemis_messages` (conversational turns only),
found nothing for Artemis since 2026-07-21, and reported that Artemis had been
down for 20 days -- while she was in fact delivering the morning brief every
single morning via `morning_brief_deliveries`, and Callie was pushing signal
cards daily via `memory_observations`.  Both are separate write paths that the
conversation table never sees.

Run it with::

    uv run python -m artemis.ops
"""
