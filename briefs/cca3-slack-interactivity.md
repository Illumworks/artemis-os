# CCA3 — Slack interactivity endpoint (slice B2a)

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — security-sensitive request
verification, and it repairs an existing broken path rather than adding a greenfield one.

Design doc: `docs/crisis-content-approval-pipeline.md`, section "Blocker for slice B2".
Read it first.

## Why this exists

Slack approval cards in this repo already render **interactive** buttons carrying
`action_id` values — `build_approval_dm_blocks`, `build_escalation_dm_blocks` and the
pipeline gate blocks in `artemis/integrations/slack/messages.py` (see
`_CALLBACK_ACTION_ID_PREFIX` around lines 143, 261, 354, 361).

Slack delivers those clicks to an app's **Interactivity Request URL** as a
`block_actions` payload. **Nothing in this repo handles that payload.** Verified:

- `grep -rn "block_actions" artemis/ --include="*.py"` → nothing.
- `grep -rn "_CALLBACK_ACTION_ID_PREFIX" artemis/ --include="*.py"` → matches only inside
  the file that defines it. No consumer.
- Only Slack POST routes are the two Events API endpoints
  (`artemis/routes/integrations_slack_events.py:1571,1585`).
- Production traffic confirms it:
  `grep -oE '"POST /[^ ]*' ~/Library/Logs/artemisos/app.out.log | sort | uniq -c` shows
  Slack has only ever hit `/events` paths. Real approvals arrive via the web UI.

**So clicking "Approve" in a Slack gate card today does nothing and reports nothing.**
That is worse than offering no button. This slice makes those clicks work.

## Scope

One new route plus its dispatch layer. **No Callie card, no crisis-content polling, no
Writing Studio harvest** — those are later slices. Do not modify
`artemis/crisis_content/` at all.

Target ≤400 LOC of implementation.

## The endpoint

```
POST /api/integrations/slack/interactivity/{agent_id}
```

Per-agent to mirror the existing `/events/{agent_id}` convention, because Slack allows
only one Interactivity Request URL per app and this workspace runs multiple bots
(`callie`, `kai`). Resolve the signing secret for `agent_id` exactly the way the events
route already does.

Request shape — note it differs from the Events API and this is the most common thing to
get wrong:

- Content type is `application/x-www-form-urlencoded`, **not** JSON.
- The interesting data is a single form field named `payload` whose value is a
  JSON-encoded string. Parse the form first, then JSON-decode that field.
- Signature verification must run against the **raw request body bytes**, before any
  parsing. Read the body once and reuse it; consuming it twice in Starlette will bite you.

### Verification — reuse, do not reimplement

`_verify_slack_signature(body, timestamp, signature, signing_secret)` already exists at
`artemis/routes/integrations_slack_events.py:604` and already handles HMAC-SHA256,
timestamp freshness, and `hmac.compare_digest`. **Use it.** If it needs to move to a
shared module to be importable cleanly, move it and update the existing caller in the
same commit — do not copy it.

Reject with **401** on a bad or missing signature. Reject stale timestamps (replay
protection) — confirm the existing helper's freshness window and state what it is in your
report.

### Response contract

Slack requires a response within **3 seconds** or it shows the user an error. Do the
minimum synchronously — verify, parse, record the decision, acknowledge. If any work
could be slow, acknowledge first and continue in the background.

Return `200` with an empty body to acknowledge silently, or a JSON body per Slack's
message-update semantics if you choose to update the original message. Updating the
card so the buttons disappear after a click is strongly preferred — it prevents
double-approval and gives the user visible feedback that their tap registered. State
which you implemented.

## Dispatch

Dispatch on `action_id`. Route the existing `_CALLBACK_ACTION_ID_PREFIX`-prefixed
approve/reject actions to whatever already persists those decisions for pipeline human
gates — find that path rather than inventing a parallel one; `POST /api/enablement/review/{id}/approve`
and `artemis/pipelines/node_executors/human_gate_executor.py` are the places to look.

If an `action_id` has no registered handler, log at WARNING and acknowledge — never 500.
An unhandled action must not look like a delivery failure to Slack, or Slack will retry.

**Identity matters.** The payload carries the clicking Slack user. Resolve them to an
Artemis user and record *who* approved. Do not trust any user id embedded in the button
value — take it from the verified payload only. A signed request tells you the request
came from Slack; the `user.id` inside it tells you who clicked.

## Out of scope — do not build

Callie's crisis-content card, the poller, routing to Angela/Hannah/Jaclyn, the ⭐
reaction handler, doc write-back, Writing Studio harvesting. Do not touch
`artemis/crisis_content/`.

## Verification

Do **not** run `./scripts/check.sh` — known pre-existing TRUNCATE deadlock, has never
passed. Run exactly:

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_c uv run pytest tests/test_slack_interactivity.py -q -p no:randomly
uv run ruff check artemis/routes artemis/integrations/slack
uv run mypy artemis/routes/integrations_slack_events.py
```

Two different env vars on purpose: alembic reads `ARTEMIS_DB_URL`, the pytest conftest
reads `ARTEMIS_TEST_DB_URL`. Worktrees have no `.env`, so both must be explicit.

Note ruff currently reports ~18 **pre-existing** errors in old `alembic/versions/` files
(`0034`–`0088`). Those are not yours. Verify your own scope is clean and say so.

## Tests (all required)

- [ ] Valid signature + well-formed `block_actions` payload → 200, decision recorded.
- [ ] **Invalid signature → 401**, and nothing is recorded.
- [ ] **Missing signature headers → 401.**
- [ ] **Replayed stale timestamp → 401** even with an otherwise-valid signature.
- [ ] Form body with no `payload` field → 400, no traceback.
- [ ] `payload` containing malformed JSON → 400, no traceback.
- [ ] Unknown `action_id` → 200 with a WARNING logged (assert via `caplog`), not 500.
- [ ] Unknown `agent_id` → 404 or 401, and never a 500.
- [ ] The approving user is taken from the verified payload, **not** from button value —
      write a test where the two disagree and assert the payload wins.
- [ ] Double-click of the same approve action does not double-record.

## Quality acceptance

- [ ] All commands above pass; paste verbatim output.
- [ ] `_verify_slack_signature` is reused, not duplicated. If moved, the existing caller
      is updated in the same commit and its tests still pass.
- [ ] No new dependencies; `pyproject.toml` and `uv.lock` untouched.
- [ ] `artemis/crisis_content/` is untouched — confirm with `git diff --stat`.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Report the signature freshness window, whether you update the original message
      after a click, and which existing code path you routed approve/reject into.
- [ ] Report explicitly that the Interactivity Request URL still has to be set in the
      Slack app config by the owner — until then this endpoint receives nothing. Do not
      claim end-to-end verification you cannot perform.
- [ ] Flag anything in this brief you believe is wrong rather than guessing silently.
