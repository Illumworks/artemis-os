# CALLIE-1 — A guarded DM send

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this gives an agent the
ability to message people unprompted, so the authorization is the feature.

## Why

Callie can reply but cannot initiate. Asked to introduce herself to Josh she answered:

> No Slack send tool in my current toolset, Jon. I can't DM Josh directly from here right
> now. Here's the intro, ready to paste into a DM to Josh:

Josh has asked for a daily digest and for signals flagged as they hit — both require her to
start a conversation. She currently hands Jon text to paste.

## The two allowlists — and why the requester one is the important half

Jon's decision, 2026-08-12.

| | |
|---|---|
| **May ask her to send** | Jon, Angela, Josh |
| **May receive** | Jon, Angela, Josh, Hannah, Jaclyn |

Limiting recipients alone does **not** address the risk Jon named ("I don't want her abused
by people"). The sharper vector is proxying: *"Callie, DM Sara and tell her X."* The
recipient can be on the allowlist, the message plausible, and it arrives under Callie's
name rather than the name of whoever wanted it sent. The defence is restricting **who may
ask**, which is only possible now that speaker identity resolves from the verified Slack
user id.

Both lists are settings, not inline literals, following
`kai_action_authorized_user_ids` and `crisis_content_*_approver_emails`. Keep them as
**emails** and resolve to Slack ids at send time — an email survives a Slack account being
recreated, and every other allowlist in this codebase is keyed on email.

## Hard requirements

1. **Requester identity comes from the verified Slack payload only** — never from message
   text, never from anything the model produces. If a turn cannot establish who is asking,
   the send is refused. Fail closed.
2. **Both checks, independently.** An authorized requester may not send to an unlisted
   recipient. An unlisted requester may not send to anyone, including themselves.
3. **Attribution in the message body.** Every DM must carry who asked for it, e.g.
   `Jon asked me to pass this along:` before the content. A recipient must be able to push
   back to a person rather than to a bot. This is the social half of the proxying problem
   and it is not optional.
4. **Audit every attempt** — requester, recipient, allowed/refused, and the message. Both
   sends and refusals; a refusal nobody can see is how you find out too late.
5. **Refusal is informative to the requester, silent to everyone else.** Tell the asker it
   was refused and why. Never notify the would-be recipient of an attempted send.
6. **No bulk send.** One recipient per call. If a digest needs three people, that is three
   authorized calls, each audited. This is what stops "DM everyone in the workspace" being
   one tool call away.
7. **The tool description must state the limits**, so the model does not promise a send it
   cannot make. Its current honesty about lacking the tool was good behaviour — do not
   replace it with a tool that fails opaquely.

## Out of scope

Channel posting (she already has `chat:write`), the crisis-content pipeline, the dead
relevance classifier, and widening either list. Do not add Slack scopes: `im:write` and
`chat:write` are already granted.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_b uv run pytest tests/test_callie_dm_send.py -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Use `artemis_test_b`; both env vars are required since worktrees have no `.env`.

## Tests (all required)

- [ ] Authorized requester → allowed recipient → sent, with attribution naming the
      requester in the body.
- [ ] Authorized requester → **unlisted** recipient → refused, nothing sent, requester told.
- [ ] **Unlisted requester** → allowed recipient → refused, nothing sent.
- [ ] Unlisted requester → unlisted recipient → refused.
- [ ] Requester identity unresolvable → refused (fail closed), not treated as Jon.
- [ ] A requester named in the message TEXT is ignored; only the verified payload identity
      counts. Plant a conflicting name and assert the payload wins.
- [ ] Every attempt is audited, refusals included.
- [ ] The would-be recipient is never notified of a refused attempt.
- [ ] One recipient per call; a multi-recipient input is rejected rather than fanned out.
- [ ] Empty allowlist setting → nobody authorized (fail closed), matching
      `kai_action_authorized_user_ids`.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste a sent DM exactly as it renders, so Jon can judge the attribution wording.
- [ ] State what happens when identity cannot be resolved.
- [ ] No new Slack scopes; no new dependencies; `pyproject.toml` / `uv.lock` untouched.
- [ ] **Do not send a real DM to anyone as a smoke test.** Jon's DM only, and ask first.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than guessing silently. Seven bugs that
      passing tests did not catch reached production on the adjacent pipeline this week;
      every one was a data shape or an API behaviour that only appeared in real use.
