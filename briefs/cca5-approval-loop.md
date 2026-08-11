# CCA5 — The approval loop: buttons, decisions, change notes

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high

Design doc: `docs/crisis-content-approval-pipeline.md`. Slices A, B1, B2a, B2b are all
merged and live in production — the poller is DM'ing Jon every 2 minutes right now. Read
`artemis/crisis_content/` before writing anything.

## What you are building

The decision half of the pipeline. Today Callie's card is read-only: it tells you copy is
ready and links to the doc. After this slice, the card carries working buttons, a click is
authenticated and recorded, and a change request captures *why*.

This is the seam two later slices depend on (doc write-back + notifying Jen; Writing
Studio harvest), so the persisted decision record is the important artifact — design it
to be read by things that don't exist yet.

## What already exists — use it, don't rebuild it

- `POST /api/integrations/slack/interactivity/{agent_id}` (`artemis/routes/integrations_slack_interactivity.py`)
  is merged, verified end-to-end from the public internet, and Jon has already set the
  Request URL in Callie's Slack app config. Signature verification, 401s, per-agent secret
  resolution, and unknown-action acking are all done. **You add a dispatch branch; you do
  not touch verification.**
- Callie's card rendering is `artemis/crisis_content/notify.py::render_transition_message`.
- `mark_notified` / `Transition` / the cards + copy-version tables are in
  `artemis/crisis_content/transitions.py` and `orm.py`.

## Migration 0107

Head is `0106`. One new table.

**`crisis_content_decisions`** — append-only. Never UPDATE, never DELETE (`CLAUDE.md`
rule 3). A changed mind is a new row, not an edit.

| Column | Notes |
|---|---|
| `id` | PK BigInteger |
| `card_id` | FK → `crisis_content_cards.id`, not null |
| `route` | text, not null — `'asset'` / `'copy'` |
| `decision` | text, not null — `'approved'` / `'changes_requested'` |
| `decided_by_slack_user_id` | text, not null — from the **verified payload** |
| `decided_by_email` | text, nullable — resolved if possible |
| `note` | text, nullable — the change-request rationale |
| `slack_message_ts` | text, nullable — the card that was acted on |
| `decided_at` | timestamptz, not null |

Index on `(card_id, route)`. No unique constraint on it — a card can legitimately go
changes_requested → approved later, and both rows must survive.

Verify the round-trip (`upgrade head` → `downgrade -1` → `upgrade head`) and confirm no
other file in `alembic/versions/` claims `0107`. This repo has shipped a broken chain
before.

## Buttons on the card

Add to `render_transition_message`'s output as Block Kit `actions` elements. The card body
stays exactly as it is today — do not restyle it; Jon has approved the current format.

Two buttons:

| Text | `action_id` | Style |
|---|---|---|
| `Approve` | `crisis_content_approve` | `primary` |
| `Request changes` | `crisis_content_request_changes` | (default) |

The button `value` must carry enough to identify the target: `{card_id}:{route}`.
**Do not put an approver identity in the value** — identity comes only from the verified
payload's `user.id`. A value is attacker-supplied data; a signature does not authenticate
its contents.

Keep the `Open the doc` link and the `⚠️ Testing` footer.

## Authorization — this is the part to get right

Per `docs/crisis-content-approval-pipeline.md` "Routing":

| Route | Who may decide |
|---|---|
| `asset` | Jon only |
| `copy` | Angela (`angela.miata@`), Hannah (`hannah.slater@`), Jaclyn (`jaclyn.wright@`), all `@amiralearning.com` — **any one** is sufficient |

Rules:

- Resolve the clicking Slack user to an email, then check the allowlist **for that route**.
- A click from anyone not on the route's allowlist: record nothing, and reply ephemerally
  saying they are not an approver for this route. Do not fail silently and do not 500.
- This deliberately widens the existing Jon-and-Missy-only rule
  (`project-kai-action-authorization`). Scope the allowlist to **this pipeline only** — do
  not touch or generalise any existing authorization helper.
- Put the allowlist in settings/config, not inline literals, so changing an approver is
  not a code edit.

## Change notes — use a modal

`Request changes` must capture *why*, because that rationale is the training signal slice D
harvests. A rejection with no reason is nearly worthless.

Open a Slack modal (`views.open` using the payload's `trigger_id`) with one multiline
plain-text input, then handle the `view_submission` payload in the same interactivity
endpoint. Carry `{card_id}:{route}` through `private_metadata`.

`views.open` must be called within Slack's 3-second window, so open the modal first and do
the persistence on submission.

## After a decision

1. Insert the `crisis_content_decisions` row.
2. **Update the original card** so the buttons are gone and it shows the outcome — e.g.
   `✅ Approved by Angela · 7:14pm` or `✏️ Changes requested by Jon · 7:14pm` followed by
   the note. This is the double-click guard and the user's only feedback that their tap
   registered.
3. A second click on an already-decided card: do not insert a duplicate; reply
   ephemerally that it is already decided, and by whom.

Do **not** write to the Google Doc, email Jen, or touch Writing Studio here. Those are the
next two slices and they will read your decision rows.

## Out of scope

Doc write-back, Drive `@mention`, Gmail, Writing Studio harvest, the ⭐ reaction, and
flipping routing away from DM-to-Jon. Leave `crisis_content_notify_destination` alone.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_crisis_content_decisions.py tests/test_slack_interactivity.py -q -p no:randomly
uv run ruff check artemis/crisis_content artemis/routes alembic/versions
uv run mypy artemis/crisis_content artemis/routes/integrations_slack_interactivity.py
```

Both env vars are required and differ on purpose; worktrees have no `.env`. Note ruff
reports ~18 **pre-existing** errors in old `alembic/versions/` files (`0034`–`0088`) —
not yours; confirm your own scope is clean.

## Tests (all required)

- [ ] Approve click by an allowed approver → one decision row, card updated, buttons gone.
- [ ] Approve on the `copy` route by **each** of Angela / Hannah / Jaclyn → allowed.
- [ ] Approve on the `copy` route by Jon → **rejected** (he does not approve copy).
- [ ] Approve on the `asset` route by Jon → allowed; by Angela → **rejected**.
- [ ] A click from an unknown Slack user → no row, ephemeral reply, no 500.
- [ ] Identity comes from the verified payload, not the button value — plant a conflicting
      identity in `value` and assert the payload wins.
- [ ] `Request changes` → modal opened with `private_metadata` carrying card+route.
- [ ] `view_submission` → decision row with the note text persisted.
- [ ] Second click on a decided card → no duplicate row, ephemeral "already decided".
- [ ] `changes_requested` then a later `approved` → **both** rows survive.
- [ ] Existing `tests/test_slack_interactivity.py` still passes unchanged.

## Quality acceptance

- [ ] All commands pass; paste verbatim output, plus the migration round-trip.
- [ ] Signature verification untouched — confirm with `git diff` that you added a dispatch
      branch and did not modify the verification path.
- [ ] Migration `revision="0107"`, `down_revision="0106"`, and no other file claims 0107.
- [ ] No new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] Nothing written to any Google Doc.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] You cannot fully verify a real button click end-to-end (that needs a human tapping in
      Slack). Say so plainly rather than implying you did. Do describe exactly what a
      reviewer should click to confirm.
- [ ] Flag anything in this brief you believe is wrong rather than guessing silently.
