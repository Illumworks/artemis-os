# Data access requests — Salesforce, Gong, HubSpot

**Date:** 2026-08-20 · **Owner:** Jon Fila
**For:** Neil (Salesforce + Gong) · Jon self-serves HubSpot

Everything below is **read-only**. Nothing in Artemis OS writes to Salesforce,
Gong or HubSpot, and that is a deliberate design constraint, not an accident of
scope: side-effecting actions run behind a human approval gate inside Artemis,
never as an automated write into a system of record.

**Why now.** New Mexico is moving statewide against Amira with AI as the stated
reason, and it is not isolated — Hillsborough, Baltimore and Georgia are live in
the same week. The gap this closes is that nobody currently has one view of
*which districts are affected, who we know there, and what we have already sent
them.* Salesforce is already connected and answered the first question today:
**102 Amira customer districts in New Mexico**, including Albuquerque, Rio
Rancho, Carlsbad and Alamogordo.

---

## Part 1 — Salesforce (Neil)

The Connected App using the Client Credentials flow is working. **No new OAuth
scopes are needed** — the existing `api` scope covers the surface. What is
missing is **object-level Read on the Connected App's run-as user profile**.

### Current state, probed live (not assumed)

| Readable today | No access |
|---|---|
| Account · Contact · Opportunity · User · OpportunityContactRole · AccountContactRelation · ContentDocument | **Task · Lead · EmailMessage · Event · Campaign · CampaignMember · Case** |

### The ask, in priority order

**1. `Task` — Read + "View All Activities"  ← already breaking something**

This is not a new feature request. Artemis already queries `Task` to check
whether a contact was emailed recently, and that query returns HTTP 400 for
every contact that exists in Salesforce. The check fails safe (it reports
"unverified" rather than "clear to contact"), so nothing incorrect has gone out
— but the safety check itself cannot function.

It is also the single most-requested capability from the marketing side:
*"before I send out that newsletter, I want to know when in the last 7 days
those contacts have received something from us."* Frequency capping is
impossible without it.

⚠ **"View All Activities" matters as much as Read.** Without it the run-as user
sees only Tasks it owns — which would return almost nothing while *looking*
like the grant worked. That is a worse failure than no access.

**2. `Lead` — Read**

The intelligence picture is "current customers, prospects, and leads." We have
customers and open opportunities. Leads are invisible, so roughly half the
prospect view is missing.

**3. `EmailMessage` + `Event` — Read (+ View All Activities on Event)**

The rest of "what have we already sent this person" — actual sent mail and
meetings. Task alone covers logged activity; these three together are what make
a frequency check trustworthy rather than indicative.

**4. `Campaign` + `CampaignMember` — Read**

So we can check a contact against what is already in flight before adding them
to something. This is specifically how a district gets double-touched during a
crisis.

**5. `Case` — Read**

Lower priority. Support volume in a district is a real health signal, and it
supports tier-1 routing.

### One data question for Neil (not a permission)

`Account.Customer_Status__c` ("Customer Status (AML)") is what we now use to
mean *is an Amira customer*. We chose it over `Is_Customer__c` deliberately:
because the org is shared with Istation, `Is_Customer__c` returns 11,360
accounts versus ~4,900 genuine Amira ones, so using it would have suppressed
around 6,000 real Amira prospects as existing customers.

**Please confirm `Customer_Status__c` is the right field, and that
`Customer / Child / Parent / Pilot` are the values meaning "current customer"**
(we deliberately treat `Loss` and `Write Off` as NOT current — a former customer
is a win-back target, not someone to suppress).

Also worth knowing: `Amira_Customer_Status__c` exists but is free text and its
live values include typos (`Cusotmer`, `Cusomter`) and junk (`Pearson`, a bare
date). We are not using it. If it is meant to be authoritative, it needs
cleanup first.

---

## Part 2 — Gong (Neil)

Not yet connected. Gong is where the actual customer conversations are, which
makes it the highest-value source we do not have: it answers *what are districts
actually saying about AI and screen time* rather than what we inferred from news.

**What we need functionally:**

1. **API credentials** — Gong issues an Access Key + Secret for server-to-server
   use. One set for Artemis OS, read-only.
2. **Call metadata read** — date, participants, account/opportunity linkage,
   duration. This alone lets us tie conversations to the districts in the crisis.
3. **Transcript read** — the substance. Without transcripts we can see that a
   conversation happened but not what was said.
4. **Users read** — to resolve internal participants to people.
5. **CRM linkage** — Gong's Salesforce association, so a call joins to the same
   Account we are already reading.

Neil will know the exact permission names in Gong's admin UI better than we can
guess from outside; the four capabilities above are the requirement. Gong scopes
are granular and named roughly `api:calls:read:*` / `api:users:read`, but treat
that as a starting point to confirm rather than a spec.

**Two things worth deciding with Neil rather than assuming:**

- **Recording consent and scope.** Some calls may be restricted by policy or by
  participant consent. We want whatever the compliant subset is, and we would
  rather know the boundary up front than discover it later.
- **Whether transcripts leave Gong.** Our default would be to store derived
  signals (topics, sentiment, district linkage) rather than raw transcripts, to
  keep the data footprint small. Flag if there is a policy requiring that.

---

## Part 3 — HubSpot (Jon self-serves — no request needed)

Jon is HubSpot admin, so this needs no third party.

**Create a Private App** in HubSpot (Settings → Integrations → Private Apps)
with **read-only** scopes:

- `crm.objects.contacts.read` — contacts
- `crm.objects.companies.read` — districts/companies
- `crm.objects.deals.read` — pipeline, if deals live here as well as Salesforce
- `crm.lists.read` — list membership
- `sales-email-read` — **the important one:** email send history, which is the
  other half of the frequency-capping picture alongside Salesforce `Task`
- `crm.schemas.contacts.read` / `crm.schemas.companies.read` — so we can
  introspect custom properties rather than guessing field names (this is exactly
  the guess that nearly caused a 6,000-prospect error in Salesforce)

That yields a single access token. Install it the same way as Salesforce —
the Integrations card in the app, which stores it encrypted — **not** in a
plaintext file, and not pasted into a chat.

**One open question:** the transcript notes HubSpot may or may not be kept.
Worth deciding before building much on it; if it is being retired, the effort
belongs on Salesforce and Gong instead.

---

## What we are NOT asking for

Stated explicitly, because a broad ask invites a slow "no":

- **No write, create, update or delete** on any object in any system.
- **No admin or Modify All Data.** Object-level Read on a single run-as user.
- **No new OAuth scopes on the Salesforce Connected App** — the existing `api`
  scope is sufficient; this is purely profile permissions.
- **No bulk export.** Reads are scoped queries, cached, on a schedule.

## How we will confirm each grant landed

Each of these is verifiable in about a minute, and we will report back per
object rather than saying "it works":

```bash
# Salesforce: per-object readability probe
uv run python -m artemis.ops                      # consolidated health
PYTHONPATH=$PWD .venv/bin/python scripts/salesforce_introspect.py   # read-only field/permission probe
```

For `Task` and `Event` specifically we will check both that the object reads AND
that row counts are plausible — because "View All Activities" missing looks
identical to a successful grant until you count rows.
