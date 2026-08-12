# CCA8 — Card voice: varied openers, and who gets pinged

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Everything through CCA7 is merged
and the pipeline is LIVE in production — cards post to `C0BM9TL63TL`, buttons work, and
write-back to Jen's doc is enabled.

## What you are building

The card currently opens with a bare machine line: `📝 Copy ready for review — X`. Jon
wants a conversational opener that credits Jen and addresses the approvers, and that
**varies between cards** so a channel full of them doesn't read like a mail merge.

Purely a rendering change. No new tables, no migration, no lifecycle changes.

## Scope

`artemis/crisis_content/notify.py` + `artemis/config.py` (one setting) + tests. Nothing
else. Target ≤250 LOC.

## The openers

Six variants for the copy route. Jon wrote the first one; the rest follow its register —
warm, brief, human, no exclamation-mark spam:

1. `Thanks Jen — {approvers}, we've got another copy piece ready to approve.`
2. `New one in from Jen. {approvers} — ready for your eyes.`
3. `Jen just sent this over. {approvers}, ready when one of you is.`
4. `Fresh copy from Jen. {approvers} — whoever gets there first.`
5. `Thanks Jen! {approvers}, another one ready to approve.`
6. `Jen has this one ready. {approvers} — over to you.`

`{approvers}` renders as real Slack mentions of the three copy approvers, in the
existing `Any one of …` style: `<@U…>, <@U…> or <@U…>`.

The asset route needs its own three, addressed to Jon alone (no approver list — he is the
only asset approver, and naming him in a DM to him would be odd):

1. `Thanks Jen — the visual's ready for your eyes.`
2. `New visual in from Jen, ready when you are.`
3. `Jen attached the asset for this one — over to you.`

Put the variant lists in module constants, not inline in a render function, so adding or
editing one is obvious.

### Selection must be deterministic per card, not random

Pick the variant from a stable hash of the card's identity plus route (e.g.
`sha256(f"{identity_key}:{route}")` reduced modulo the variant count).

**Do not use `random`.** The card is re-rendered when it repaints after a decision, and a
random pick would silently rewrite the opener at that moment — the reader would see the
message they are acting on change wording under them. Deterministic selection also makes
the tests meaningful.

Different cards land on different variants; the same card always reads the same way.

## Jen: named in the text, @-mentioned only when she must act

Jon's call, and the reasoning matters so it does not get "tidied" later: Jen is an
**external vendor** on Slack Connect. A ping on every single card is warm the first three
times and grating by the fortieth. So:

- **Ready-for-review cards:** the plain word `Jen` — no mention, no ping.
- **Change requests:** a real `<@…>` mention, because that is the moment she genuinely has
  to do something.

The change-request notification itself is **not** in this slice (it needs the card's
posted message ts, which nothing stores yet — that is CCA9). What IS in this slice: the
`jen_slack_user_id` setting and a `jen_mention()` helper that CCA9 will call, with a test
proving ready-cards never contain her id.

### Her Slack id must be configured, not resolved

`users.lookupByEmail` returns **nothing** for Jen — verified live for both
`jen@digigeeks.com` and `jen@justrightstrategy.com`. She is an external Slack Connect
user on a different team (`users.info` on `U016P00LP08` confirms `jen@digigeeks.com`,
team `TUQ6KJT0V`), and the email lookup only sees your own workspace.

So add a setting `crisis_content_jen_slack_user_id`, default `U016P00LP08`, with a
docstring explaining exactly that — otherwise the next person will "fix" it into an email
lookup that silently returns `None` forever. If it is empty, fall back to the plain word
`Jen` rather than rendering a broken mention.

## What must not change

- The body: copy text, the `NNN chars · fits X (280)` line, the other route's status, and
  the `Open the doc:` link. Jon has approved that format and is using it.
- The buttons and their `action_id`s.
- The `⚠️ Testing` footers under the `dm_jon` override.

The opener **replaces** the current `📝 Copy ready for review — {platform}` line. Keep the
`August XX, 2026 · Welcome Back blog` line beneath it — it identifies which post this is.

Keep the platform visible; the opener drops it, and an approver needs to know whether they
are reading an X post or a LinkedIn one. Fold it into the date line or keep a short
`— {platform}` marker.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_routing.py tests/test_crisis_content_poller.py tests/test_crisis_content_voice.py -q -p no:randomly
uv run ruff check artemis/crisis_content
uv run mypy artemis/crisis_content
```

## Tests (all required)

- [ ] Same card + route → same opener across repeated renders (determinism).
- [ ] Different cards → the variant set is actually exercised, not all landing on one.
- [ ] No `random` import anywhere in `notify.py`.
- [ ] Copy-route opener contains the three approver mentions as `<@U…>`.
- [ ] Copy-route opener contains the plain word `Jen` and **not** her Slack id.
- [ ] Asset-route opener addresses Jon, contains no approver list.
- [ ] `jen_mention()` returns a real `<@…>` when the setting is populated.
- [ ] Empty `crisis_content_jen_slack_user_id` → falls back to `Jen`, never a broken
      `<@>`.
- [ ] Platform still appears somewhere on the card.
- [ ] Body, char-count line, doc link, and buttons are unchanged — assert against the
      existing expected strings rather than rewriting them.
- [ ] `dm_jon` override still renders both `⚠️ Testing` footers.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste one fully rendered copy card and one asset card as they would appear, so a
      human can read the voice without deploying.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] No migration, no new table.
- [ ] Nothing written to any Google Doc.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Every worker on
      this pipeline has surfaced a real problem that way; one caught a bug that would have
      silently lost approvals forever.
