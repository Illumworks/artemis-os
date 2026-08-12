# Note for the other session — unifying on one daily brief in #market-signals

**From:** the crisis-content / Callie session, 2026-08-12 ~17:30
**Why you're getting this:** we are building two different things into the same
channel and Jon has picked which one is the frame. Nothing you've written is
wasted; the delivery step changes, not the collection.

---

## 1. Commits — main's working tree is shared right now

**Don't `git add -A` or `git add .` from the repo root.**

Right now `artemis/config.py` and `artemis/screentime/runner.py` carry your
uncommitted changes. I've left them untouched and scoped every one of my adds to
explicit paths. Please do the same in the other direction — I have ~100 commits
on `main` today and a broad add from either side sweeps up the other's in-flight
work.

- **Scope your adds:** `git add <specific paths>`, then read `git diff --staged`
  before committing.
- **Merges and conflict resolution go in a `lead/<scope>-merge` worktree**, never
  in main's working tree.
- **`main` is now at migration `0115`.** Run `uv run alembic upgrade head` before
  any endpoint smoke test — `--reload` does not re-run migrations.
- **Agree migration numbers before either of us writes one.** I handed two
  concurrent workers the same number today. The duplicate revision merged with
  *no conflict markers* and only failed at runtime. Next free number is `0116`.

Changed areas on `main` you may touch: `artemis/crisis_content/*`,
`artemis/floating_artemis/tools/callie_dm.py`, `artemis/integrations/slack/tools.py`
(`register_slack_tools` now takes `include_dm`), `artemis/pipelines/seeds/marketing_pipeline.py`.

---

## 2. Jon's decision on #market-signals (C0BPT2T2KFY)

**One combined daily brief from Callie is the main thing.** Not one post per
feed. His words: *"callie mentioning them in a daily brief in the Market signals
that combines the top campaign signals ... and we have screentime signals that
would be better and less noise."*

The brief combines **three feeds into one message**:

1. **Top campaign signals** — the best of what's qualified since the last brief.
2. **Crisis signals** — yours.
3. **Screentime signals** — the situational read you just wired up.

It **@mentions Josh and Angela**. It is **informational for now** — Jon chose
"inform now, add actions later" explicitly, so no approve/reject buttons in v1.
Individual signal cards keep landing in `#campaign-signals` exactly as they do
today; that firehose is not changing and is not the problem being solved.

## 3. What this changes about your work — and what it doesn't

Your `run_digest` / `register_digest_schedule` work is the right plumbing and
should stay. Two things change:

- **`post_screentime_digest` should stop posting directly** and instead return its
  composed section, so a combined composer can place it under a Screentime
  heading. Keep the "mark each included signal reported so a re-run posts
  nothing" behaviour exactly as it is — that idempotency is the valuable part and
  the combined brief needs it unchanged.
- **`register_digest_schedule` should not be registered as its own cron** once the
  combined brief exists, or #market-signals gets two posts a day and we have
  rebuilt the noise problem in the channel created to solve it.

Useful state, since it surprised me: `ARTEMIS_SCREENTIME_REPORT_CHANNEL` in `.env`
is **already** `C0BPT2T2KFY` (#market-signals), not #screen-time-signals. So your
digest is already aimed at the right channel. And `register_digest_schedule` has
**no caller yet** — nothing in `main` invokes it — so the cron is not live and
there is no race to stop. It is written and dormant, which is the easiest possible
moment to change its shape.

## 4. The thing that will bite us if we ignore it

**Gate 1 of `marketing.main` must stop blocking.**

It is a *blocking* human gate: the pipeline suspends there and waits for an
approver. Jon has now said explicitly that Josh and Angela should **not** be
pinged per signal. A daily brief cannot unblock a gate — so if the gate stays as
it is, the pipeline suspends and stays suspended.

This is not hypothetical. That gate sat suspended and silent, and the reason it
went unnoticed for 57 days is worth knowing: its approvers were configured as
`josh@amiralearning.com` / `angela@amiralearning.com`, **neither of which exists**
in Slack (real: `joshua.mukai@`, `angela.miata@`). The lookup missed, it fell back
to an in-app queue nobody watched, then escalated to `jon@amiralearning.com`,
which also does not exist. I fixed all five addresses today in both the seed and
the live pipeline rows. But fixing the addresses only means the *wrong design* now
delivers — per-signal approval DMs Jon does not want.

Also worth knowing for scoping the brief: in the last 7 days, **1** qualified
signal was attached to a pipeline run and **305** were written straight to the
queue by scouts running outside the pipeline. So "top campaign signals" has to
read from `signal_queue` broadly, not from the run's own node states, or the brief
will be nearly empty.

## 5. Proposed split, so we stop overlapping

Say if you'd rather swap — the point is that one of us owns the composer.

| | |
|---|---|
| **You** | Screentime section as a *returned* section; crisis-signals section, same shape. |
| **Me** | The combined composer + cron + delivery to #market-signals, the campaign-signals section, and turning Gate 1 non-blocking. |

Contract I'd suggest for a section, so we can build in parallel and not collide:
each feed exposes `async def build_<feed>_section(session) -> str | None`, returns
`None` when it has nothing to say that day, and marks its own items reported. The
composer owns the heading, the ordering, the Josh/Angela mention, and the
"nothing today" case.

One request either way: **no new cron posting to C0BPT2T2KFY** until we've agreed
who owns the composer.
