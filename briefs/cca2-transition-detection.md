# CCA2 — Crisis-comms transition detection + copy version log

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Read it first. This is slice B1;
slice A (`briefs/cca1-doc-card-reader.md`) is merged and provides `ReviewCard`.

## What you are building

The persistence and decision layer between the parser and Slack. Given a freshly parsed
`list[ReviewCard]`, it records what was observed, appends to a copy version log, and
returns the list of **transitions worth notifying about**.

**No Slack, no HTTP, no scheduler, no notifications.** Slice B2 consumes what you return
and does the delivery. If you find yourself importing a Slack client, stop.

Scope: `artemis/crisis_content/` (extend the existing package) + one migration + tests.
Target ≤450 LOC of implementation.

## Migration 0106

Current alembic head is `0105`. Three tables — all additive, no changes to existing
tables.

**`crisis_content_cards`** — latest observed state, one row per card identity.

| Column | Notes |
|---|---|
| `id` | PK, BigInteger autoincrement |
| `identity_header` | text, not null |
| `identity_platform` | text, **nullable** (platform chip can be unset) |
| `identity_ordinal` | int, not null |
| `title` | text |
| `asset_status`, `copy_status` | text, nullable |
| `asset_url` | text, nullable |
| `copy_hash` | text, not null |
| `first_seen_at`, `last_seen_at` | timestamptz, not null |

Unique constraint on `(identity_header, identity_platform, identity_ordinal)`.
Note `identity_platform` is nullable, and **Postgres treats NULLs as distinct in a
unique constraint** — so two platform-less cards under the same header would both
insert. Handle this deliberately: either use `COALESCE`-based uniqueness via an
expression index, or normalize unset platform to a sentinel string. State which you
chose and why in a comment.

**`crisis_content_copy_versions`** — append-only. Never updated, never deleted (this
repo's lossless-memory discipline, `CLAUDE.md` rule 3).

| Column | Notes |
|---|---|
| `id` | PK |
| `card_id` | FK → `crisis_content_cards.id`, not null |
| `copy_hash` | text, not null |
| `copy_body` | text, not null |
| `first_seen_at` | timestamptz, not null |

Unique on `(card_id, copy_hash)`.

**`crisis_content_notifications`** — the dedup ledger. You create the table and the
read/write helpers; **slice B2** calls `mark_notified` after a successful post.

| Column | Notes |
|---|---|
| `id` | PK |
| `card_id` | FK → `crisis_content_cards.id`, not null |
| `route` | text, not null — `'asset'` or `'copy'` |
| `status_value` | text, not null |
| `notified_at` | timestamptz, not null |

Unique on `(card_id, route, status_value)`.

Migration must round-trip: verify `alembic upgrade head` then `alembic downgrade -1`
then `upgrade head` again, and paste the output.

## The observation function

```python
async def record_observation(
    session: AsyncSession,
    cards: Sequence[ReviewCard],
) -> list[Transition]
```

Per card, in one transaction:

1. Resolve the card row by identity. Insert if new, update `last_seen_at` and the
   mutable fields otherwise.
2. If `copy_hash` differs from any existing version for that card, append a
   `crisis_content_copy_versions` row. Existing hash → no write. **This is what makes
   "Jen wrote X, we changed it to Y" recoverable later** — the poller is the only place
   her original is ever visible, so this write is load-bearing, not bookkeeping.
3. Compute transitions and return them.

`Transition` should carry at least: the card, the route (`'asset'`/`'copy'`), the
previous status, the new status, and whether the card was newly created.

## Transition rules

Emit a transition when a status becomes exactly **`Ready`**:

- `asset_status` → `Ready` gives route `'asset'`
- `copy_status` → `Ready` gives route `'copy'`

Suppression rules, all required:

- **Never emit for a terminal status.** `Approved` and `Published` are terminal. A card
  moving `Ready → Approved → Published` produces nothing.
- **Never emit the same `(card, route, status_value)` twice** — check
  `crisis_content_notifications`.
- **Asset route requires an asset.** If `asset_url` is `None`, emit no `'asset'`
  transition. Jon approves visuals; there is nothing to look at without one.
- **Header-rename guard.** A newly created card whose `copy_hash` matches a card that
  already has a `crisis_content_notifications` row must **not** emit. Jen writes
  `August XX` placeholders; when she fills in a real date the identity changes and an
  already-actioned post would otherwise look brand new and re-ping. This is the whole
  reason `copy_hash` exists — see the design doc's "Card identity".
- Unrecognized status values (neither actionable nor terminal) emit nothing, but must be
  **logged at WARNING**. Jen can add dropdown options at will; silence here means the
  pipeline stops working and nobody finds out.

### First-run behaviour — specify, don't discover

On the very first poll every card is new, and two cards in the live doc are already at
`copy_status='Ready'`. Those **should** emit — they are genuinely awaiting review. Do
not add a bootstrap-suppression mode. Slice B2 routes everything to Jon alone during
testing, so this is intentional and safe.

Write a test that asserts this explicitly, so nobody "fixes" it later.

## Out of scope — do not build

Slack, Callie, the poll loop, scheduler registration, doc write-back, `@mentions`,
email, and the Writing Studio harvest (including channel/platform alias normalization —
that is slice D). Do not modify `parser.py`, `models.py`, or `export_client.py` from
slice A except to add exports.

## Verification

Do **not** run `./scripts/check.sh` — known pre-existing TRUNCATE deadlock, has never
passed in this repo. Run exactly:

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_crisis_content_transitions.py -q -p no:randomly
uv run ruff check artemis/crisis_content alembic/versions
uv run mypy artemis/crisis_content
```

The two env vars are different on purpose: alembic reads `ARTEMIS_DB_URL`, the pytest
conftest reads `ARTEMIS_TEST_DB_URL`. Passing the wrong one migrates the wrong database
silently. Worktrees have no `.env`, so both must be explicit on every command.

These tests need a database, so they go in `tests/` (not `tests/unit_no_db/`).

## Tests (all required)

- [ ] New card at `Draft` → no transition; card row and one version row created.
- [ ] `Draft` → `Ready` on copy → one `'copy'` transition.
- [ ] `Draft` → `Ready` on asset **with** `asset_url` → one `'asset'` transition.
- [ ] `Draft` → `Ready` on asset with `asset_url is None` → **no** transition.
- [ ] `Ready` → `Approved` → no transition. `Approved` → `Published` → no transition.
- [ ] Same `(card, route, status)` already in `crisis_content_notifications` → no
      re-emit.
- [ ] Unchanged copy across two observations → exactly **one** version row.
- [ ] Changed copy across two observations → **two** version rows, oldest first by
      `first_seen_at`, both bodies retrievable.
- [ ] Header rename (same `copy_hash`, new identity) where the old card was notified →
      **no** emit.
- [ ] Header rename where the old card was **never** notified → emits normally.
- [ ] Unknown status (`Needs legal`) → no transition, and a WARNING is logged (assert
      via `caplog`).
- [ ] First-run with a card already at `Ready` → emits (per the section above).
- [ ] Two platform-less cards under the same header do not collide into one row.
- [ ] Migration round-trips: `upgrade head` → `downgrade -1` → `upgrade head`.

## Quality acceptance

- [ ] All commands above pass; paste verbatim output.
- [ ] Migration `down_revision` is exactly `0105`, and `revision` is `0106`. Confirm no
      other file in `alembic/versions/` claims `0106` (this repo has previously shipped
      a broken chain with three files claiming one revision).
- [ ] No writes to any Google Doc; no Slack imports anywhere in the diff.
- [ ] No new dependencies; `pyproject.toml` and `uv.lock` untouched.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Report anything in this brief you believe is wrong or ambiguous rather than
      guessing silently.
