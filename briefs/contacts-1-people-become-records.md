# CONTACTS-1 — The people Argus finds become records, not prose

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this stores personal data
about real people who did not ask to be in our database. The deletion path is not a
nice-to-have, it is the feature.

Read `docs/marketing-intelligence-direction.md` first — this is item 2 in it.

## Why

Argus researches **people**. Right now 14 `district_research` observations name a
superintendent, all inside narrative prose:

> "Dr. Dyann Mack, newly appointed superintendent (effective June 2026), is the primary
> district leader. She is the first Black superintendent and first HCPS graduate to
> lead the district."

That is genuinely useful and completely unusable. Nothing can answer "who runs Harford
County". The email drafter cannot personalise from it — and Callie has repeatedly told
Josh *"I don't have the new superintendent's name for Escambia"* while the answer sat
in an observation. And no single person could be removed on request without editing
narrative text.

`district_contacts` already exists with the right shape — `district_id, name, title,
email, phone, source, external_id, active` — and holds **6 rows across 3 districts**.

Jon's framing: *"we should have a database of contacts that can be referenced and also
wiped if need be. I don't want to lose information that Argus and her uncover, because
that leads to the power of the app."*

## The hard constraint — read this twice

CLAUDE.md **rule 3** says observations are never deleted, only superseded. Jon requires
contacts be wipeable. Both are correct and they are not in conflict once separated:

- **PII lives in `district_contacts`, which is deletable.**
- **Observations REFERENCE a contact; they do not embed the person's details.**

Do **not** resolve this by making observations deletable, and do not add a
`delete_observation` path. The lossless rule protects research; the personal data sits
in a table that can be purged without tearing holes in the knowledge graph.

## What to build

1. **Extraction.** A pass that reads `district_research` observations and produces
   `district_contacts` rows: name, title, district, and `source` recording which
   observation it came from so a reader can judge it. Retroactive over the existing 14,
   and available to run again.
   - **Do not guess.** A name you are not confident about should be skipped and
     reported, not stored at 60% confidence. A wrong person attached to a district is
     worse than an empty field, because someone will write to them.
   - No emails or phone numbers unless the source genuinely contains them. Do not
     construct an address from a naming convention — that mistake already bit us this
     week (`josh.mukai@` vs `joshua.mukai@`).
2. **Going forward.** When Argus writes a `decision_makers` finding, the person should
   land in `district_contacts` too. Same confidence bar.
3. **A wipe path.** Remove one person, or everyone for a district, cleanly — and say in
   the docstring what happens to observations that referenced them (they keep their
   prose; they lose the link). A `active=false` soft-delete is NOT sufficient on its
   own for a removal request; provide real deletion, and be explicit about which is
   which.
4. **Make it readable by Callie.** A read-only lookup so she can answer "who runs
   Harford County" and personalise a draft. Layer 1, no authorization implications —
   and note `resolve_person` is for INTERNAL Amira staff via `directory_people`; this
   is external district contacts and must stay separate. Do not merge them.
5. **Provenance is mandatory.** Every row records where the name came from. A contact
   we cannot trace is a contact we cannot trust or defend.

Migration is **`0118`**, `down_revision = "0117"`, only if you need a column
(`district_contacts` may already suffice — check before adding).

## Out of scope

Email sequence persistence (item 1 of the direction doc, and Josh's requirements land
first), campaign creation from Slack, HubSpot, Salesforce, and any change to
`resolve_person` or `directory_people`.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_worker_b uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_worker_b uv run pytest artemis/argus/tests -q -p no:randomly
uv run ruff check artemis
uv run mypy artemis
```

Use `artemis_test_worker_b`; both env vars required. **Never run against `artemis_os`.**
Expect one pre-existing mypy error about `Dimension` not being exported, and 22
pre-existing ruff errors in unrelated files. `artemis/argus/tests/conftest.py` guards
against binding to the live database — do not remove it.

## Tests (all required)

- [ ] A real observation naming a superintendent yields exactly one contact, with the
      district and the source recorded.
- [ ] An observation with no clearly identifiable person yields nothing and is
      reported, not stored speculatively.
- [ ] Re-running extraction does not duplicate a contact.
- [ ] Deleting a contact removes the row; observations that referenced it survive.
- [ ] The lookup answers by district and returns nothing (not a guess) for a district
      with no contacts.
- [ ] No email or phone is ever synthesised — only stored when present in the source.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] **The deliverable is the real extraction**: run it against the live 14
      observations and paste the contacts produced, plus which ones you skipped and
      why. Skipping several is a good sign, not a failure.
- [ ] Show a real wipe: contact gone, referencing observation intact.
- [ ] Paste the lookup answering "who runs Harford County".
- [ ] Say plainly whether anything in your design could attach the wrong person to a
      district, and what stops it.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than building to it silently. Six bugs
      this week were something claiming more than it had; a contacts table that
      confidently names the wrong superintendent would be the seventh, and the most
      embarrassing, because someone would email them.
