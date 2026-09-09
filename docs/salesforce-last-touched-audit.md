# "Last touched" is a due date, not a contact date — Salesforce `LastActivityDate` audit

**Date: 2026-09-09.** Audited against the live Salesforce org (read-only, SOQL SELECT only).
Re-run instructions at the bottom. Read the "What this audit could NOT determine" section
before quoting any of it as settled.

## The trigger

Callie's `check_salesforce_activity` rendered this for Pinellas County Schools:

```
Michael Feeney — Executive Director of Middle School Education — last touched 2027-03-29
Kelly Austin — Director of Elementary Education — last touched 2026-11-30
```

Both dates are after today. An agent read them as recent past contact and concluded the
contact list was "warm". Reproduced live on 2026-09-09 — the output above is byte-for-byte
what the tool still returns.

---

## 1. The field, and where it enters and leaves the code

**Field: `Contact.LastActivityDate`** (Salesforce standard field; `type: date`,
`calculated: false`, label "Last Activity" — confirmed via `describe_sobject("Contact")`).

| Step | Location |
|---|---|
| Field requested | `artemis/marketing/salesforce_account_lookup.py:190` — inside `_CONFLICT_FIELDS` (185–195) |
| **SOQL query** | `artemis/marketing/salesforce_account_lookup.py:245-249` — `SELECT ... FROM Contact WHERE AccountId = '...' ORDER BY LastActivityDate DESC NULLS LAST LIMIT 25` |
| Parsed onto the dataclass | `artemis/marketing/salesforce_account_lookup.py:262` — `last_activity=str(row.get("LastActivityDate") or "")` |
| **The render** | `artemis/marketing/salesforce_account_lookup.py:226` — `bits.append(f"last touched {self.last_activity}")` in `AccountContact.describe()` |
| Called from | `artemis/floating_artemis/tools/salesforce_tools.py:50` (`fetch_account_contacts`), then `:62` and `:73` (`describe()`), surfaced at `:224` |

There is no transformation between the SOQL value and the rendered string. The date the
agent read is the raw field, unmodified, prefixed with the words "last touched".

**A second defect on the same line, worth stating separately from the date itself:** the
verb is wrong even when the date is in the past. `LastActivityDate` is a **due date**, not a
record of an interaction that happened. "Last touched" asserts something the field does not
say.

---

## 2. Cause: the field is a scheduled-activity date. Verdict settled.

**Salesforce defines `LastActivityDate` as the later of: the due date (`ActivityDate`) of the
most recent Event on the record, or the due date of the most recently *closed* Task.** It is
a due date. Events count whether or not they have happened yet, so a meeting booked for
2027-03-29 sets `LastActivityDate = 2027-03-29` today. The field is behaving as designed;
our label is what is wrong.

The two candidate explanations needed separating, because they need different fixes:

- **(A) The field is a scheduled-activity date** → our reading of the field is wrong → fix in
  our code.
- **(B) The CRM data is dirty** (fat-fingered years) → fix in Salesforce, by a human, record
  by record.

### The evidence that settles it: weekday distribution

All 233 future-dated contacts, by the weekday their date falls on:

| Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|
| 59 | 52 | 36 | 47 | 38 | **1** | **0** |

232 of 233 fall on a business day. Under hypothesis (B), typo years land on an arbitrary
weekday, so ~2/7 — about 67 of 233 — should fall on a weekend. Observed: one.
(Binomial: expected 66.6, sd 6.9, observed 1 → roughly 9.5 sd below expectation.) Random
data entry error cannot produce this. **Business-day-only dates are calendar entries.**

Both Pinellas dates fit: 2027-03-29 and 2026-11-30 are both Mondays.

### The dirty-data residue, quantified rather than hand-waved

Hypothesis (B) is not zero — it is 2 records out of 233:

| `LastActivityDate` | Weekday | Contact Id | Last modified |
|---|---|---|---|
| 2763-01-03 | Thu | `0031W000024ImiRQAS` | 2026-03-24 |
| 2202-08-07 | **Sat** | `0031W00002N5e0BQAR` | 2026-03-30 |

Note the single Saturday in the whole set is one of the two absurd years. The dirt is
self-identifying and is 0.9% of the problem.

**Verdict: ~99% mechanism (A), ~1% dirt (B). Fix the code. Two records can be mentioned to
whoever owns Salesforce data hygiene, but they are not the bug.**

### One reading I initially got wrong, corrected here

41 dates are shared by more than one contact (170 contacts total), which looks at first like
multi-invitee Events. It is not: only **3** of those groups are same-account *and* same-date,
covering 6 contacts. The rest are different districts that happen to share a popular business
day — consistent with a rep scheduling many follow-ups onto the same day. This still supports
(A), but it is weaker evidence than the weekday distribution and should not be quoted as
"shared meetings".

---

## 3. How widespread — live counts, 2026-09-09

| Measure | Count |
|---|---|
| Contacts in the org | 792,945 |
| Contacts with any `LastActivityDate` | 267,734 |
| **Contacts with `LastActivityDate > TODAY`** | **233** |
| Contacts with `LastActivityDate = TODAY` | 77 |
| As a fraction of all contacts | 0.029% |
| **As a fraction of contacts that have a date at all** | **0.087%** |
| Maximum future date | **2763-01-03** |
| Nearest future date | 2026-09-10 |
| Distinct accounts affected | **184** |
| Accounts with any activity-dated contact (denominator) | 81,095 |

By year: 2026 → 184 · 2027 → 17 · 2028 → 10 · 2029 → 15 · 2030 → 5 · 2202 → 1 · 2763 → 1.
200 of 233 (86%) fall within the next 12 months — the shape of ordinary scheduled follow-ups.

**The raw rate understates the user-facing impact, and this is the number that matters.**
The query at `salesforce_account_lookup.py:248` sorts `ORDER BY LastActivityDate DESC`, so on
every one of those 184 accounts the future-dated contacts sort to the **top** of the rendered
list. Pinellas is exactly this: two future rows, and they are the first two names Callie
printed. A 0.087% field-level error rate becomes a 100% error rate on the first row shown for
184 districts. The rows most likely to be read as "our warmest contacts" are precisely the
wrong ones.

---

## 4. Per-consumer breakage

Three consumers. Grep confirms no others (`grep -rn "LastActivityDate\|last_activity" --include="*.py" artemis/`).

| # | Consumer | file:line | What a future date does | Direction |
|---|---|---|---|---|
| 1 | `AccountContact.describe()` render | `salesforce_account_lookup.py:226` | Prints "last touched \<future date\>". Reader concludes the contact is warm and recently engaged. | **Wrong, and unsafe in judgement.** This is the reported bug. It inverts the meaning: a *pending* commitment is read as a *completed* one. |
| 2 | `fetch_account_contacts` sort | `salesforce_account_lookup.py:248` | Future rows outrank every genuine row, taking the top slots of `LIMIT 25`. | **Wrong (prominence).** Amplifies #1 and is a *latent* safety risk — see below. |
| 3 | `_recent_contact_from_contact_record` suppression | `salesforce_suppression.py:185-197`, reached via `check_suppression` → `check_suppression_for_recipients` → `sends.py:248-250` | The test is `last_dt >= now - 90d`, a lower bound only, with no upper bound. Every future date passes it. Contact is suppressed as `recent_sales_contact`. | **Wrong, but fails SAFE.** Over-suppresses: an email that could have been sent is not. No unsafe inversion exists here — there is no code path where a future date makes a contact look *clearer* to email. |

Confirmed against the live window: `settings.salesforce_recent_contact_window_days = 90`, and
all 233 future-dated contacts satisfy the comparison, so all 233 currently suppress.

### On consumer #2's safety risk — checked, and currently latent, not live

`_render_people` (`salesforce_tools.py:50-62`) computes the "⚠ DO NOT SEND" conflict list
*only over the 25 rows returned*. In principle a future-dated row can push a contact in an
active Gong sequence past rank 25 and out of the conflict warning entirely — a silent
unsafe-direction failure. I tested this rather than assuming it:

- 126 of the 184 affected accounts have more than 25 contacts, so `LIMIT 25` does truncate them.
- **Accounts where an active-flow contact is displaced past rank 25: 0.**
- Only 2 of the 184 affected accounts have any active-flow contact at all; those sit at ranks
  1, 2, 5, 6 and 10 — well inside the window.
- Org-wide there are 258 contacts in an active Gong flow, and **none** of them is future-dated
  (so a future date never masks a conflict flag on the same record; the conflict branch at
  `salesforce_account_lookup.py:221` wins over the date branch anyway).

So: the mechanism is real and does currently push genuine conflict rows *down* on two
accounts, but nothing is being dropped off the list today. Report it as a latent hazard that
the fix should close, not as an active leak.

---

## 5. Recommended fix

**Recommendation: render future dates as what they are — a scheduled commitment — and do not
filter them out. Pair it with an upper bound on the suppression comparison.**

Concretely, three changes, in priority order:

1. **`salesforce_account_lookup.py:225-226` — branch on the date.** Below/equal today, keep
   the existing line but fix the verb: `"last activity 2026-08-24"`. Above today:
   `"a follow-up is SCHEDULED for 2027-03-29 — no completed activity on record"`. This is not
   damage control; it is *more* useful than the current line. "This district has a meeting on
   the calendar" is a fact demand gen wants and cannot get anywhere else today, and it makes
   the "no completed activity" half explicit rather than silently absent.
2. **`salesforce_account_lookup.py:248` — sort by the effective past date.** SOQL cannot
   express `LEAST(LastActivityDate, TODAY)`, so sort in Python after fetching: contacts with a
   real past activity date first, scheduled-only contacts after them. Keep the SOQL `ORDER BY`
   as the fetch order but raise the limit modestly so the re-sort has something to work with.
   This closes the latent displacement hazard in §4 as a side effect.
3. **`salesforce_suppression.py:191` — bound the comparison on both sides.** Add
   `last_dt <= now` so a scheduled date stops counting as "recently contacted". A booked
   future meeting is arguably still a reason not to cold-email someone, but that should be a
   *deliberate, separately-worded* rule (`scheduled_meeting_pending`), not an accident of a
   one-sided inequality. Today the right answer is given for the wrong reason, which is how it
   survives until it doesn't.

**Why not the alternatives:**

- *Filter to `LastActivityDate <= TODAY` in the SOQL* — rejected. It is the smallest diff and
  it destroys information. The contact would render as "no recorded activity", which is a
  different false statement (there IS a scheduled activity), and demand gen loses the meeting
  signal entirely. Filtering also cannot fix consumer #3, which queries a single contact by Id.
- *Use a different field* — rejected, because there is no better one available. `Task` and
  `Event` are **not readable** by the Connected App's run-as user (verified live today, see
  below), so the underlying `ActivityDate`/completion status cannot be reached. `LastModifiedDate`
  is a record-edit timestamp, not contact activity, and substituting it would be a worse lie
  told more confidently. `LastActivityDate` really is the best field we can read; it just needs
  to be described accurately.

**Do not implement from this document alone** — item 1 changes agent-facing copy, which is
Creative Director territory per CLAUDE.md §2.

---

## What this audit could NOT determine

State these as open, not as covered:

1. **The specific Task or Event behind any individual future date is unverifiable from here.**
   `Task`, `Event` and `OpenActivity` all return HTTP 400 `sObject type ... is not supported`
   for our run-as user — re-confirmed live on 2026-09-09, consistent with the permission gap
   recorded at `salesforce_suppression.py:313-328` (verified 2026-08-20). So the cause in §2 is
   established by the weekday distribution plus Salesforce's documented field semantics, **not**
   by reading the source activity record. It is a strong inference, not a direct observation.
   If someone grants Task/Event read, re-run and confirm directly.
2. **The Event-vs-closed-Task split is unknown** for the same reason. I cannot say how many of
   the 231 non-dirty records come from a scheduled Event versus a Task closed with a future due
   date.
3. **Salesforce's field definition is quoted from vendor documentation, not from this org.**
   The describe call confirms `calculated: false` and `type: date` but returns no help text.
   Per CLAUDE.md's rule on unverified outbound-API semantics, this is flagged at the point of
   use rather than assumed silently.
4. **No historical trend.** These are point-in-time counts for 2026-09-09. Whether 233 is
   growing, shrinking or steady is unknown; nothing snapshots this field over time.
5. **Accounts, not just Contacts.** `Account.LastActivityDate` has the same semantics and was
   not audited. It is not currently read by any code path found in the grep, but if a future
   consumer reads it, assume the same defect until checked.

---

## How to re-run the counts

Read-only. Requires the app's Salesforce credentials resolved from the DB; no writes possible
(`SalesforceClient` exposes only `describe_sobject` and `query`, both GET).

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio, collections, datetime
async def main():
    import artemis.db as _db
    from artemis.marketing.salesforce_suppression import _get_client
    async with _db.SessionLocal() as s:
        c = await _get_client(s)
        for label, soql in [
            ("total contacts",            "SELECT COUNT(Id) n FROM Contact"),
            ("with any LastActivityDate", "SELECT COUNT(Id) n FROM Contact WHERE LastActivityDate != null"),
            ("LastActivityDate > TODAY",  "SELECT COUNT(Id) n FROM Contact WHERE LastActivityDate > TODAY"),
        ]:
            print(f"{label}: {(await c.query(soql))[0]['n']}")
        rows = await c.query_all(
            "SELECT Id, Name, AccountId, LastActivityDate FROM Contact "
            "WHERE LastActivityDate > TODAY ORDER BY LastActivityDate")
        print("max future:", rows[-1]["LastActivityDate"], "| accounts affected:",
              len({r["AccountId"] for r in rows}))
        wd = collections.Counter(
            datetime.date.fromisoformat(r["LastActivityDate"]).strftime("%a") for r in rows)
        print("weekday distribution:", dict(wd))   # weekday-only => scheduled, not typos
asyncio.run(main())
PY
```

To reproduce the Pinellas render itself, call
`artemis.marketing.salesforce_account_lookup.lookup_district(client, "Pinellas County Schools")`
then `fetch_account_contacts(client, matched.account_id, limit=25)` and print `p.describe()`.
