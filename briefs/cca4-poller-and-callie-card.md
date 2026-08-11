# CCA4 — Poller + Callie's card (slice B2b, ship-tonight scope)

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Slices A and B1 are merged and
provide everything you consume.

## Deadline scope — read this first

This must be **live tonight**. Scope is deliberately cut to the notification path:

- **IN:** poller, parse, transition detection (already built), Callie posting a card,
  Jon-only routing, loud failure alerts.
- **OUT:** interactive buttons. A separate slice (CCA3) is building the Slack
  interactivity endpoint in parallel and **must not block you**. Your card links to the
  doc instead of offering buttons. When CCA3 lands, buttons get added on top.
- **OUT:** doc write-back, `@mention` to Jen, Gmail, Writing Studio harvest, ⭐ handling,
  channel routing to Angela/Hannah/Jaclyn.

If you find yourself blocked on anything in the OUT list, you have gone out of scope.

## What you consume (already on main, do not modify)

```python
from artemis.crisis_content import (
    TARGET_DOCUMENT_ID,
    fetch_crisis_content_export_html,   # GET the export; needs an access_token: str
    parse_review_cards,                 # html -> list[ReviewCard]
    record_observation,                 # session, cards -> list[Transition]
    mark_notified,                      # session, card_id, route, status_value
    classify_status,
    SignInPageError,
    NoReviewCardsFoundError,
)
```

**Two contracts you will break if you skim:**

1. `record_observation` and `mark_notified` **flush but never commit** — transaction
   management is the caller's job (mirrors `artemis/memory/store.py`). You are the
   caller. Commit, or every poll silently discards its own work.
2. `mark_notified` must be called **only after a successful Slack post**, never before.
   A delivery failure must not be recorded as delivered, or the retry never happens.

`Transition` carries `card` (the full `ReviewCard`), `route` (`'asset'`/`'copy'`),
`previous_status`, `new_status`, `is_new_card`.

## The poller

Register on the existing automation scheduler (`artemis/automations/scheduler.py`,
started from `main.lifespan`). Interval **every 2 minutes**, configurable via settings.

Each pass:

1. Resolve Jon's **personal** Google credential (`purpose="personal"`) and a valid access
   token. Mirror `_valid_access_token` in `artemis/routes/google_docs.py` — do not write a
   new refresh path.
2. `fetch_crisis_content_export_html` → `parse_review_cards` → `record_observation`.
3. For each returned transition: render and post, then `mark_notified`, then commit.
4. **Overlap guard:** a slow pass must not run concurrently with the next tick. Use a
   simple in-process lock or a DB advisory lock and skip (log at INFO) rather than queue.

### Failure handling — every one of these alerts Jon, loudly

Silence is the failure mode this repo keeps getting burned by. On each of the following,
DM Jon and log at ERROR:

- `NoReviewCardsFoundError` — labels renamed or export shape changed. **Most likely real
  failure**, and indistinguishable from "no work" unless you say so.
- `SignInPageError`, non-200 export, or an HTTP error.
- Google token refresh failure — reuse the existing owner-alert path from the GCal token
  fix if one exists.
- Slack post failure.

**Do not alert on every tick for the same ongoing condition.** Alert on the transition
into a failing state and again on recovery. A 2-minute poll that DMs on every pass would
send ~720 messages a day and get muted, which converts a loud failure into a silent one.

## Callie's card

Post as **Callie**. Resolve her Slack credentials the way the existing per-agent path
does (`artemis/integrations/slack/client.py`, `artemis/integrations/config_resolver.py`,
and the agent id used by `/api/integrations/slack/events/callie`).

**Destination: a DM to Jon, and only Jon.** Not the channel. Make the destination a
setting (default DM-to-Jon) so flipping to channel `C0BM9TL63TL` plus the real approvers
later is a config change, not a code change. Look Jon's Slack user up by email
(`lookup_user_by_email`) — **not** by listing users and filtering, which paginates and
silently misses people.

Content, per route:

```
📝  Copy ready for review — LinkedIn          ← or 🎨 Asset ready for review
    August XX, 2026 · Welcome Back blog

    <the full copy body, inline, not truncated>

    734 chars · fits LinkedIn (3,000)
    Asset: still in Draft — no visual attached yet
    Open the doc: <link>

    ⚠️ Testing — routed to you only. Live: Angela, Hannah, Jaclyn.
```

- Full copy inline. The point is approving without opening the doc.
- Note the *other* route's status, so Jon knows whether the post is actually shippable.
- Include the asset URL when present; say plainly when it is absent.
- The `⚠️ Testing` line stays until routing is flipped. It is the guard against anyone
  thinking this is the live workflow.

### Character count — must be t.co-aware

Show `<count> chars · fits <Platform> (<limit>)`, or flag when over.

**X counts every URL as 23 characters** regardless of real length (t.co wrapping). A
naive `len()` produces false alarms: the live X card is 305 raw characters but **220**
adjusted, comfortably inside 280. Replace each URL with 23 chars before counting.

Limits: X 280, Instagram 2200, LinkedIn 3000, Facebook 63206. Unknown or combo platform
(`FB, LI, & X`, `All`, `TBD`) → show the raw count with no limit claim rather than
guessing which limit applies.

## Live smoke — required, and this is the acceptance test that matters

Tests passing is not shipping. Before you report done:

1. Run one real poll against the live doc with the real credential.
2. Confirm **an actual Slack DM arrives** for the two cards currently sitting at
   `copy_status='Ready'` (LinkedIn and X).
3. Paste the observed result: what was posted, and the `crisis_content_notifications`
   rows created.
4. Run a **second** poll and confirm **nothing is re-sent** — this is the dedup working.

Assert the effect, not an HTTP 200.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_poller.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

Both env vars are needed and are different on purpose; worktrees have no `.env`.

## Tests (all required)

- [ ] A transition produces exactly one Slack post, and `mark_notified` is called after.
- [ ] Slack post failure → **no** `mark_notified` row, so the next poll retries.
- [ ] Two polls with unchanged doc → exactly one post total.
- [ ] `NoReviewCardsFoundError` → alert sent, no crash, poller survives to the next tick.
- [ ] Repeated failing polls → alert on entry and recovery only, not every tick.
- [ ] t.co-aware count: a 305-raw-char X post with a 108-char URL reports 220 and is
      **not** flagged as over 280.
- [ ] Combo platform (`FB, LI, & X`) → raw count, no limit claim, no crash.
- [ ] An overlapping tick is skipped rather than run concurrently.
- [ ] Destination defaults to DM-to-Jon; nothing posts to `C0BM9TL63TL`.

Mock Slack in unit tests; the real post is covered by the live smoke.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Live smoke pasted verbatim, including the second no-resend poll.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc — this slice is still read-only against the doc.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Report explicitly whether the `⚠️ Testing` line is present and the destination is
      DM-only. Do not flip routing to the channel or the other approvers.
- [ ] Flag anything you believe is wrong rather than guessing silently.
