# SFDC-1 — Salesforce read, and the suppression it exists for

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4` / high — this decides whether an email
goes to someone sales is already talking to. Getting it wrong damages a real
relationship, not a test.

**Sequencing: do not start until CALLIE-2 has merged** — it is rewriting
`artemis/floating_artemis/tool_registry.py`, which this must also touch to register a
tool. Check `git log` for "scope Callie's registry" before beginning.

## Why this exists

Jon, verbatim: *"Salesforce shouldn't be sending anything. It stores our clients and
communications that people are sending — so it's something that should be referenced for
contact information and what is happening on the sales side, to help support or influence
campaigns and communications, so we are not stepping on people's toes or communicating
with someone who already got an email."*

**Read-only, and the point is suppression.** Marketing must not cut across sales. The
integration's job is to answer, before anything goes out: *is this person already in a
conversation, already a customer, or already emailed?*

This is not a convenience feature and it is not "after HubSpot" — **it is a prerequisite
for HubSpot.** You cannot safely enable sending until you can check who has already been
contacted. `campaign_sends` currently has **zero rows**, so this guard can land before
anything real has ever flowed through the send path. That will not be true forever.

## Credentials — read this before you write any code

Neil (external Salesforce admin) has issued a **Client ID and Client Secret**. Jon holds
them. He is not waiting on an IP restriction.

- **You will not be given the secret, and you must not ask for it.** Build against the
  documented Salesforce API and a fake credential in your tests.
- Credentials belong in `integrations.encrypted_credentials`, Fernet-encrypted via
  `artemis/connectors/encryption.py::encrypt_credentials`, with `provider='salesforce'` —
  the same path as `gcal` and `jira`. **Never plaintext in `.env`.**
- Provide the **input path** Jon uses to install them himself (mirror how an existing
  connector accepts credentials), and say plainly in your report what he has to run or
  click. If the only honest answer is "a short one-time local script where he pastes
  them", say that.
- OAuth flow is **Client Credentials** (server-to-server, no redirect URL). Confirmed
  with Neil.

## What to build

1. **A read-only Salesforce client** — token fetch via Client Credentials, then queries.
   Follow the shape of an existing connector; do not invent a new pattern. It must be
   structurally incapable of writing: no POST/PATCH/DELETE to Salesforce, and say so in
   the module docstring.
2. **The four things we need to read.** Confirm the real field/object names against
   Salesforce's API rather than assuming ours:
   - **Account**: is this district a customer? (Jon's note: an `is customer` flag)
   - **Opportunity**: is there an open one?
   - **Contact**: name, title, email — better data than the names Argus extracts from
     prose, so this should be able to enrich `district_contacts`.
   - **Activity / EmailMessage / Task**: has this person been emailed recently, and when?
3. **The suppression check, wired into `artemis/marketing/sends.py`** at the existing
   queue-or-skip seam (today's only `skip_reason` is `no_contacts_on_file`). Add explicit,
   distinguishable reasons — a skip must say WHICH rule fired:
   - `existing_customer`
   - `open_opportunity`
   - `recent_sales_contact`
   - `salesforce_unavailable`
4. **Fail CLOSED on that last one, and this is the most important decision in the brief.**
   If Salesforce cannot be reached, **skip the send** — do not queue it. An unsent email
   costs a day; an email to someone sales is mid-negotiation with costs a relationship.
   Make that explicit and testable, and make sure the skip is visible rather than silent.
5. **A read tool for Callie** so she can answer "is this district already in play?"
   before drafting. Layer 1, read-only. Register it inside her scoped registry (see
   CALLIE-2), not on the general path.

## Judgment call to surface, not to bury

`recent_sales_contact` needs a window. **Default to 90 days** and make it a setting with
a description explaining the trade-off. Do not tune it silently — say in your report what
you chose and why, so Jon can move it.

## Hard constraints

- **Read-only. No write path to Salesforce at all**, not even unused.
- Do not touch `artemis/crisis_content/*`, `artemis/market_signals/*`, `artemis/memory/*`.
- Do not weaken the existing `email IS NOT NULL` guards in
  `artemis/tools/contact_db.py` / `artemis/tools/signal_queue.py` — those exist because a
  name-only contact was being counted as a routable send target.
- No new dependencies without saying why in your report.
- Migration, if needed, is the next free number — check `alembic/versions/` at the time
  you start, and say which you took.

## Verification

Do **not** run `./scripts/check.sh` (known pre-existing TRUNCATE deadlock, never passes).

Use `artemis_test_worker_b`; both `ARTEMIS_DB_URL` and `ARTEMIS_TEST_DB_URL` required.
**Never run against `artemis_os`.** Expect one pre-existing mypy error about `Dimension`
not being exported and 22 pre-existing ruff errors in unrelated files.

## Tests (all required)

- [ ] A customer account → `skip_reason='existing_customer'`, nothing queued.
- [ ] An open opportunity → `skip_reason='open_opportunity'`, nothing queued.
- [ ] A contact emailed inside the window → `skip_reason='recent_sales_contact'`.
- [ ] The same contact emailed outside the window → queued normally.
- [ ] **Salesforce unreachable → skipped, not queued**, with
      `skip_reason='salesforce_unavailable'`.
- [ ] A clean prospect with a real email → queued, exactly as today.
- [ ] The client raises on any attempted write (assert the method does not exist, or that
      a non-GET is refused).
- [ ] Contact enrichment never overwrites a real email with a null.

## Quality acceptance

- [ ] All commands pass; paste verbatim output.
- [ ] Paste the four Salesforce queries you will issue, with the real object and field
      names you verified. If our assumed `is customer` field does not exist under that
      name, say so — Jon can ask Neil.
- [ ] State exactly what Jon must do to install the credentials.
- [ ] State the window you chose for `recent_sales_contact` and why.
- [ ] Say plainly what happens on a Salesforce outage, and confirm it is fail-closed.
- [ ] `git diff --staged` re-read twice before commit.
- [ ] Flag anything you believe is wrong rather than building to it silently. This week
      produced six bugs where something claimed more than it had; a suppression check
      that reports "clear to send" when it never reached Salesforce would be the worst of
      them, because the failure is an email to a real customer.
