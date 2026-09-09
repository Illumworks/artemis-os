# Brevo + Typeform capability notes

**Compiled 2026-09-09** from official vendor documentation only.

> **These are vendor claims read from documentation, not tested against a live account.**
> Nothing in this document was verified by making an API call, sending an email, or importing a
> contact. No Brevo or Typeform account was created and no credentials were entered anywhere.
> Every factual claim below carries the URL it came from. Where the documentation is ambiguous,
> self-contradictory, or silent, that is stated explicitly in the answer and collected again in
> **"What we could not confirm"** at the end — a confident wrong answer here costs a purchasing
> decision, so the gaps are part of the finding.

**Why this exists.** Amira Learning leaves HubSpot ~31 Oct 2026. Brevo is the likely replacement
as prospect contact hub *and* email sender. Forms stay in Typeform. Working volume: **~88,000
contacts, emailed roughly twice a month ≈ 176,000 emails/month.** Artemis keeps district-level
signal scoring and campaign assembly; we want Brevo as a contact store + sending pipe, with
engagement data flowing back to us.

**To regenerate:** re-read the URLs cited inline. `help.brevo.com` returns HTTP 403 to fetchers —
those pages must be read in a browser. `developers.brevo.com` is JS-rendered but serves clean
Markdown if you append `.md` to the URL.

---

## The short version

| Question | Answer |
|---|---|
| Pricing metering unit | **Emails sent per month** — but a contact cap rides along with the tier you pick |
| Send path for a resolved recipient list | **Transactional API**, not campaigns |
| Delivery + engagement data back to us | **Yes**, webhooks, per-event configurable |
| Read suppression lists via API | **Yes**, with a page-size caveat and a legal trap |
| Brevo lead scoring | **Not the feature you think it is** — e-commerce scoring, or DIY |
| Typeform → two destinations | **Yes**, multiple webhooks per form, distinguished by tag |

---

# Brevo

## 1. Pricing model — per EMAIL SENT, with a contact cap attached to the tier

**This is the headline finding, and it is more nuanced than "Brevo doesn't charge per contact."**

**The metering unit is monthly email volume.** The pricing configurator's own label is
*"Monthly email volume (campaigns & transactional)"* — you choose a send volume and that choice
sets the price. Note the parenthesis: **campaign and transactional sends draw on the same
monthly allowance.** There is no separate transactional meter.

**But contacts are also capped, and the cap is derived from the email tier you bought.** Brevo's
quota page states it plainly: stored contacts are *"500, 1,500, or 500,000 contacts based on the
number of emails in your plan."* So the answer to "does Brevo price per contact?" is: not
directly, but you cannot store contacts beyond the ceiling your send tier grants you.

| Plan | Monthly email sends | Contacts stored |
|---|---|---|
| Free — $0 | 300/day | up to **100,000** |
| Starter — from $9/mo | 5,000 | up to 500 |
| | 10,000 | up to 1,500 |
| | 15,000 | up to 2,500 |
| | 20,000 – 100,000 | up to **500,000** |
| Standard — from $18/mo | 5,000 / 10,000 / 15,000 | 500 / 1,500 / 2,500 |
| | 20,000 – 500,000 | up to **500,000** |
| Professional — from $499/mo | 150,000 – 10,000,000 | up to **2,000,000** |
| Enterprise — custom | custom | **unlimited** |

Sources: https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans ·
https://help.brevo.com/hc/en-us/articles/9168632514066-What-are-the-different-quotas-applied-in-Brevo ·
https://www.brevo.com/pricing/

**What this means for 88,000 contacts / 176,000 emails a month.** The contact count is not the
binding constraint — any tier at or above 20,000 monthly sends carries a 500,000-contact ceiling,
which is 5.7× our list. **The send volume is the binding constraint.** 176,000/month exceeds
Starter's 100,000 ceiling, so the fit is **Standard in its 20,000–500,000 band**, or Professional
(which begins at 150,000 sends and $499/mo).

**On the "250k contract."** The tier structure is denominated in emails, and 250,000 sits
naturally inside Standard's documented 20,000–500,000 send range. It is **not** a contact-cap
value — the documented contact ceilings are 500 / 1,500 / 2,500 / 500,000 / 2M, and 250,000
appears nowhere among them. On the documentation alone, **"250k" almost certainly means 250,000
emails per month, not 250,000 contacts.** That reading is an inference from the tier structure,
not a quoted vendor statement — confirm it against the actual quote before signing.

**Two cost risks worth naming.**

- The published pricing configurator only exposes volume steps up to 100k (5k, 10k, 15k, 20k,
  30k, 40k, 50k, 60k, 80k, 100k, then "More"). **We could not read a public price for the
  200k–250k Standard band** — see "What we could not confirm."
- Brevo's pricing FAQ carries an entry titled *"What is the auto-upgrade process when reaching
  the contact limit for my plan?"* — i.e. **crossing the contact ceiling triggers an automatic
  plan upgrade.** At 88,000 contacts against a 500,000 ceiling we have headroom, but this
  confirms contact limits are actively enforced and billable, not advisory.

Other quotas that bear on us: **200 contact attributes** max, **300 contact lists**, **10,000
stored email campaigns**, **40 outbound webhooks per account**.
Source: https://help.brevo.com/hc/en-us/articles/9168632514066-What-are-the-different-quotas-applied-in-Brevo

## 2. Transactional vs marketing send — use transactional, and it isn't close

**For a templated email to an explicitly resolved recipient list, the transactional API is the
right tool and the campaigns API is close to unusable.**

`POST /v3/emailCampaigns` accepts recipients **only** as `listIds` / `segmentIds` — there is no
way to pass an explicit array of addresses. Sending to a resolved list of 800 would mean
create-list → populate → send → clean up, per send. Campaigns also fall under the rate-limit
table's "all other endpoints" row: **100 requests/hour** on General.
Source: https://developers.brevo.com/reference/createemailcampaign-1

**Rate limits.** Three tiers — General (all accounts), Advanced (Professional & Enterprise),
Extended (Enterprise only).

| Endpoint | General | Advanced | Extended |
|---|---|---|---|
| `POST /v3/smtp/email`, `GET /v3/smtp/blockedContacts` | 3,600,000 RPH / **1,000 RPS** | 7,200,000 RPH / 2,000 RPS | 6,000 RPS |
| `GET /v3/smtp/emails` (event log) | 7,200 RPH / **2 RPS** | 10,800 RPH / 3 RPS | 18,000 RPH |
| all other `/v3/smtp/…` | 300 RPH | 600 RPH | 1,800 RPH |
| all `/v3/contacts/…` | 36,000 RPH / **10 RPS** | 72,000 RPH / 20 RPS | 60 RPS |
| all other endpoints (**incl. campaigns**) | **100 RPH** | 200 RPH | 600 RPH |

RPS and RPH apply simultaneously; over-limit returns **429** with `x-sib-ratelimit-reset` giving
seconds to wait. No per-day *API* cap is documented — daily/monthly *sending* caps are a plan
matter.
Sources: https://developers.brevo.com/docs/api-limits · https://developers.brevo.com/docs/limit-headers

**Batching.** `messageVersions` is the batch mechanism on `POST /v3/smtp/email`. Documented,
verbatim: *"Maximum total recipients per API request is 2000"*, *"Maximum recipients per message
version is 99"*, individual `params` ≤ **100 KB**, cumulative `params` ≤ **1000 KB**.
Source: https://developers.brevo.com/reference/sendtransacemail

**Fit for our shape: comfortable.** At 2,000 recipients per call, 176,000 emails/month is roughly
**88 API calls a month**. We are nowhere near any transactional limit.

⚠️ Brevo's own pages conflict twice here (batch size and batch call rate) — see "What we could
not confirm". Neither conflict threatens our volume, but neither number should be quoted as
authoritative.

## 3. Contacts API — yes to create, update, custom attributes, and read-back

| Operation | Endpoint |
|---|---|
| Create | `POST /v3/contacts` |
| Update | `PUT /v3/contacts/{identifier}` |
| Read one / all | `GET /v3/contacts/{identifier}` · `GET /v3/contacts` |
| Bulk import (upsert) | `POST /v3/contacts/import` |
| Batch update | `POST /v3/contacts/batch` |
| Async export | `POST /v3/contacts/export` |
| Attribute definition | `GET/POST/PUT/DELETE /v3/contacts/attributes/{category}/{name}` |

**Upsert exists, two ways, with inverted defaults — an easy bug.** `POST /v3/contacts` with
`updateEnabled: true` updates in place; **default is `false`**, which returns 4xx on identifier
conflict. `POST /v3/contacts/import` has `updateExistingContacts` **defaulting to `true`**.
Sources: https://developers.brevo.com/docs/synchronise-contact-lists · https://developers.brevo.com/reference/import-contacts

**Bulk import limits.** *"Maximum allowed file body size is 10MB"* and *"Maximum allowed json body
size is 10MB"*, with a recommended *"safe limit of around 8 MB"*; use `fileUrl` beyond that.
Accepts `fileBody` (semicolon-CSV), `fileUrl`, or `jsonBody`. **Asynchronous** — returns 202 with
a `processId` and calls `notifyUrl` on completion. There is **no documented per-contact count
limit**, only the byte cap.
Source: https://developers.brevo.com/reference/import-contacts

**Read-back.** `GET /v3/contacts` — `limit` max **1000** (default 50), plus `offset`,
`modifiedSince`, `createdSince`, `sort`, `segmentId`, `listIds`. Response carries `id`, `email`,
`attributes`, `listIds`, `createdAt`, `modifiedAt`, `emailBlacklisted`, `smsBlacklisted`.
**`modifiedSince` gives us a clean incremental-pull cursor** for keeping Artemis in sync.
Source: https://developers.brevo.com/reference/getcontacts-1

**Custom attributes** are declared via `POST /v3/contacts/attributes/{category}/{name}`.
Categories: `normal`, `transactional`, `category`, `calculated`, `global`. Types: `text`, `date`,
`float`, `boolean`, `id`, `category`, `multiple-choice`, `user`. Help docs add: text ≤ 10,000
chars, numbers ≤ 15 digits, dates 01/01/1900–31/12/2050. **Cap of 200 attributes per account.**
Sources: https://developers.brevo.com/reference/createattribute-1 ·
https://help.brevo.com/hc/en-us/articles/10582214160274-About-contact-attributes

**Practical note:** at 10 RPS, syncing 88,000 contacts one at a time takes ~2.4 hours; via
`import` it is a handful of calls. Use `import`.

## 4. Webhooks — all the events we asked for, configurable per webhook, two payload shapes

`POST /v3/webhooks` takes a `type` and an explicit `events` array, so **the event set is
configurable per webhook.**

| `type` | Available events |
|---|---|
| `transactional` | `sent`, `request`, `delivered`, `hardBounce`, `softBounce`, `blocked`, `spam`, `invalid`, `deferred`, `click`, `opened`, `uniqueOpened`, `unsubscribed` |
| `marketing` | `spam`, `opened`, `click`, `hardBounce`, `softBounce`, `unsubscribed`, `listAddition`, `delivered`, `contactUpdated`, `contactDeleted` |
| `inbound` | `inboundEmailProcessed`, `reply` |

Source: https://developers.brevo.com/reference/createwebhook

**Every event on our list is available** — delivered, opened, clicked, bounced (hard and soft),
unsubscribed, and spam complaint. Two caveats: `deferred` and `blocked` are **transactional
only**; `uniqueOpened` is transactional only (marketing has plain `opened`).

**Payload fields.**

- *Transactional:* `event`, `email`, `id`, `date`, `ts`, `ts_event`, `ts_epoch`, `message-id`,
  `subject`, `tag`, `tags`, `sending_ip`, `X-Mailin-custom`, `template_id`; plus `reason` on
  bounce/blocked/error and `link` on click.
  ⚠️ *spam* and *error* events **omit** several standard fields including `sending_ip` and
  `ts_epoch` — the parser must tolerate their absence.
  Source: https://developers.brevo.com/docs/transactional-webhooks
- *Marketing:* `event`, `email`, `id`, `camp_id`, `date_sent`, `date_event`, `ts_sent`,
  `ts_event`, `ts`, plus `reason`, `sending_ip`, `URL`, `segment_ids`; `list_id` on
  unsubscribe / contact-deleted / list-addition.
  Source: https://developers.brevo.com/docs/marketing-webhooks

🚩 **Integration cost worth pricing in now:** transactional keys on `message-id` and `ts_epoch`;
marketing keys on `camp_id` / `date_sent` / `ts_sent` and carries **no message id at all**. These
are **two parsers, not one with a flag.** Since we intend to send transactionally (§2), the
transactional shape is the one that matters.

**`X-Mailin-custom` is the useful hook for us** — it lets a send carry our own identifiers
(district id, campaign id, signal id) through to every event webhook, which is how engagement
gets attributed back onto Artemis rows without a lookup table.

**Gap-filling:** `GET /v3/webhooks/export` exports webhook history, and `GET /v3/smtp/emails` is
an event-log fallback — but at **2 RPS / 7,200 RPH** it is a thin reconciliation channel.
Source: https://developers.brevo.com/reference/export-webhooks-history

## 5. Suppression / unsubscribes — readable via API, with one page-size caveat and one legal trap

**Yes, we can read suppression state and keep our own send-suppression logic in sync.**

**Transactional blocklist — full read, and removal:**
`GET /v3/smtp/blockedContacts` returns `contacts[]` of `{blockedAt, email, reason, senderEmail}`
plus `count`. Params: `startDate`, `endDate`, `limit`, `offset`, `senders`, `sort`.
The **`reason.code` enum is exactly what we need to separate a legal unsubscribe from a bounce**:
`unsubscribedViaMA`, `unsubscribedViaEmail`, `adminBlocked`, `unsubscribedViaApi`, `hardBounce`,
`contactFlaggedAsSpam`.
⚠️ **`limit` maxes at 100 per page** — tight for a full sync; page on the date window.
Removal: `DELETE /v3/smtp/blockedContacts/{email}`.
Sources: https://developers.brevo.com/reference/get-transac-blocked-contacts ·
https://developers.brevo.com/reference/unblock-or-resubscribe-a-transactional-contact

**Marketing suppression is a field on the contact, not a list.** `emailBlacklisted` is readable
on `GET /v3/contacts` and writable on `PUT /v3/contacts/{identifier}`. There is **no dedicated
"get unsubscribed marketing contacts" endpoint**; alternatively `POST /v3/contacts/export`
supports `customContactFilter.actionForContacts` ∈ `allContacts` | `subscribed` | `unsubscribed` |
`unsubscribedPerList`, and `actionForEmailCampaigns` ∈ `openers` | `nonOpeners` | `clickers` |
`nonClickers` | `unsubscribed` | `hardBounces` | `softBounces`.
Source: https://developers.brevo.com/reference/request-contact-export

Domain-level suppression also exists: `GET`/`POST`/`DELETE /v3/smtp/blockedDomains`.

🚩 **The legal trap, in Brevo's own words:** *"Unblocking or resubscribing a contact is considered
illegal if the contact has explicitly requested to be blocklisted... This can also lead to account
suspension."* The API exposes the un-blocklist write **with no guardrail**. A naïve two-way sync
that pushes Artemis state over Brevo's will silently resurrect people who unsubscribed in Brevo.
Source: https://help.brevo.com/hc/en-us/articles/5317448358034-Blocklist-unblock-or-resubscribe-contacts

> **Design rule that follows: suppression sync is one-way Brevo → Artemis, plus additive-only
> Artemis → Brevo. Never push `emailBlacklisted: false` from our side.**

## 6. Lead scoring — the feature being described does not exist in the form implied

**A salesperson called it "clunky". The more accurate word is "absent".** Two different things
wear the name:

**(a) "Lead scoring" in the help docs is a DIY recipe, not a product feature.** You create a
Number custom attribute yourself — Brevo's own example says *"Name your attribute (e.g. SCORE)"* —
then build an automation that adds points on triggers like email-opened. It is **entirely
rule-based and the rules are yours.** Brevo supplies the automation canvas, not a model.
Source: https://help.brevo.com/hc/en-us/articles/25723707326738-Create-a-lead-scoring-model-with-an-automation

**(b) "Contact scoring" — the feature listed on the Professional plan — is e-commerce scoring.**
Brevo computes eight `SCORE_*` attributes: five behavioural (`SCORE_TIME_BETWEEN_ORDERS`,
`SCORE_KEY_SHOPPING_PERIODS`, `SCORE_ORDERING_BEHAVIOUR`, `SCORE_RECENCY_FREQUENCY_MONETARY`,
`SCORE_CUSTOMER_LIFETIME_VALUE`) and three predictive (`SCORE_PREDICTIVE_CHURN_RISK`,
`SCORE_PREDICTIVE_PURCHASES_EXPECTED`, `SCORE_PREDICTIVE_CLV`). **All eight derive from order
data.** For a K-12 district pipeline with no orders in Brevo, **all eight compute to nothing.**
Sources: https://help.brevo.com/hc/en-us/articles/30822459476114-About-scores-in-Brevo ·
https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans

**Readable via API?** Structurally yes — the help doc says *"Scores are automatically computed
contact attributes with the prefix `SCORE_`"* and *"Scores behave like standard contact
attributes"*, so they would land in the `attributes` map on `GET /v3/contacts`.
⚠️ **But the developer documentation does not mention scoring anywhere** — no scoring endpoint,
no `SCORE_` attribute in the API reference. The "readable via API" conclusion is **inferred from
a help page, not confirmed by any API page, and not tested.**

**Bearing on the decision:** this is not a reason to reject Brevo, because **scoring is the part
Artemis already does.** We want Brevo as a contact store and sending pipe; district-level signal
scoring stays with us. Brevo's scoring should be treated as absent, and nothing in the purchase
should be justified by it.

## 7. Sending reputation and warm-up — the real risk in this migration

**Dedicated IPs: Professional and Enterprise only.** *"Dedicated IPs are only available as add-ons
on the Professional and Enterprise plans. One dedicated IP is included in the Enterprise plan by
default."* Brevo recommends one if *"You send at least three email campaigns per week to 3,000 or
more contacts, OR you send over 100,000 emails per month, with no gaps longer than a week between
sends."*

⚠️ **We clear the volume threshold (176k/month) but probably fail the cadence one.** Emailing
"about twice a month" is precisely the *"gaps longer than a week"* pattern the guidance warns
against. A dedicated IP mailed twice a month may well perform **worse** than shared, because
reputation decays between sends — and Brevo separately documents that an IP must be **re-warmed
after 30 days of inactivity**.
Source: https://help.brevo.com/hc/en-us/articles/208835449-Introduction-to-dedicated-IPs

**Documented warm-up ramps:**

| Mode | Plan | Ramp |
|---|---|---|
| Manual | legacy Free, Starter, Business | *"start with around 3,000 emails per day and then increas[e] the volume by 15% on the next day's send"* |
| Automatic | Professional | *"volume sent from your dedicated IP increases by 20% each sending day until it reaches full capacity"* |
| Custom | Enterprise | via CSM + deliverability specialists |

Sources: https://help.brevo.com/hc/en-us/articles/115000204270-Warm-up-your-dedicated-IP-manual-warm-up ·
https://help.brevo.com/hc/en-us/articles/33359225915154-Warm-up-your-dedicated-IP-automatic-warm-up

Also documented: target most-engaged contacts first (opened/clicked in last 30 days) and broaden
only as metrics hold; do not suspend or requeue during warm-up (it restarts the ramp); and
*"Brevo will not replace or swap out 'burnt out' dedicated IPs."*
Source: https://help.brevo.com/hc/en-us/articles/209576665-Best-practices-for-managing-a-dedicated-IP

### 🚩 The part that could cost the account

**Suspension thresholds, stated numerically:** *"A hard bounce rate above 2%, an unsubscribe rate
above 1%, or a spam complaint rate above 0.2% can lead to the suspension of your account or email
campaigns."*
Source: https://help.brevo.com/hc/en-us/articles/360020418259-Best-practices-for-email-deliverability

**Purchased / scraped lists are prohibited, and the ban explicitly covers transactional.** From
the Acceptable Use Policy: *"Lists of Contacts that have been scraped on the internet, acquired or
purchased from a third-party... the use of such lists of Contacts is strictly prohibited on our
Software, whether You send transactional or marketing electronic communications."* **We cannot
route around a list-quality problem by calling the send transactional.**
Source: https://www.brevo.com/legal/antispampolicy/

**The two-year staleness rule is the one most likely to bite an 88,000-contact migration.** Brevo
defines a legitimate list as one where consent *"was made within the last two years"*, and their
worked example marks the opposite ❌: *"Contact lists that have not been updated in the last two
years are not considered compliant."* Also flagged ❌ in the same guidance: **role-based addresses**
(`info@`, `admin@` — common in district data) because *"you won't be able to receive explicit and
verifiable consent from a role-based email address"*; and lists *"of a political character
(consular, government lists, etc.)"* — **relevant if any part of the 88,000 came from
public-records sources.**

And, aimed squarely at our scenario: *"A legitimate contact list does not allow you to send mass
communication campaigns to all those contacts. Ensure you segment your contacts database before
sending."*
Source: https://help.brevo.com/hc/en-us/articles/213405965-Build-a-legitimate-contacts-database-for-optimal-deliverability-and-compliance

⚠️ **No page documents an import-then-send throttle or cooling-off period.** Enforcement appears
**reactive** — bounce/complaint thresholds and account review — not a preventive gate. Absence of
a gate is not permission; it means the first bad send is the one that gets caught.

---

# Typeform

## 8. Webhooks — arbitrary HTTPS endpoint, yes; more than one destination, yes

**A Typeform submission can POST to any HTTPS endpoint we control.** *"With our Webhooks API, you
can send every submission straight to any URL or compatible web application as soon as it's
submitted."*
Source: https://www.typeform.com/developers/webhooks/

The API is `PUT https://api.typeform.com/forms/{form_id}/webhooks/{tag}`, with body fields `url`,
`enabled`, `event_types`, `secret`, `verify_ssl`.
Source: https://www.typeform.com/developers/webhooks/reference/create-or-update-webhook/

**More than one destination per form: yes, keyed by `tag`.** `tag` is documented as *"Unique name
you want to use for the webhook"* and is a **path segment**, so `{form_id}` + `{tag}` is a
composite key — two tags on one form are two independent webhook rows. The list endpoint
*"Retrieve all webhooks for the specified typeform"* returns an **array**, each item with its own
`id`, `tag`, `url`, `secret`, `enabled`. The UI has an **"Add a webhook"** button.
Sources: https://www.typeform.com/developers/webhooks/reference/retrieve-webhooks/ ·
https://help.typeform.com/hc/en-us/articles/360029573471-Webhooks

⚠️ **This is an inference from API shape, not a single explicit sentence** — Typeform nowhere
writes "a form may have multiple webhooks" in one line. It is a strong inference, and cheap to
verify empirically (create two tags on a test form, `GET /forms/{id}/webhooks`).

⚠️ **No documented maximum number of webhooks per form** was found in any official source.

**HTTPS is required.** *"All new webhook URLs must use `https`."* Existing `http` webhooks keep
working but cannot be changed to another `http` URL.

**Retry semantics** (same source):

| Response | Behaviour |
|---|---|
| non-2XX / no reply in **30 s** | marked failed, retry policy triggers |
| `410`, `404` | **no retry, webhook disabled immediately** |
| `429`, `408`, `503`, `423` | retry every **2–3 min for 10 hours** |
| any other code | back-off retries at 5 min, 10 min, 20 min, 1 h, 2 h, 3 h, 4 h |

**Auto-disable:** *"If a webhook fails 100% of the time within 24 hours with more than 300 delivery
attempts or within 5 minutes with 100 delivery attempts, we will disable it."*

**Delivery is at-least-once and duplicates are expected** — *"it's better to deliver webhooks
twice, than not deliver them at all"*, and *"you can identify a duplicate or a re-sent entry by
comparing the token."* **Artemis must dedupe on `token`.** Deliveries are viewable for 30 days.
Source: https://help.typeform.com/hc/en-us/articles/12978390412692-Webhooks-Troubleshooting-and-FAQ

Two receiver-design constraints: **no payload or header customization** (*"we do not offer any
customization or modification to our webhooks"* — so no auth token in a header; use the
signature), and **no mutual TLS**.

## 9. Brevo integration — native and first-party, but partner-built, and one form per instance

**There is a genuine native Typeform↔Brevo integration. It does not go through Zapier or Make.**
Typeform classifies it as **partner-built** — *"built and supported by our partners"* — with
**"Brevo (formerly Sendinblue)"** named in that list, meaning **Brevo owns and supports it**, not
Typeform.
Source: https://help.typeform.com/hc/en-us/articles/4408031805972-Partner-built-integrations

Behaviour, from Brevo's own documentation:
- **Free.** *"The Typeform integration for Brevo is free."*
- Set up **inside Brevo** (account → Integrations → App Store → "Typeform contacts integration"),
  authorised over OAuth.
- **Real-time plus backfill:** *"Your existing contacts are imported from Typeform to Brevo when
  the integration is created. Your existing contacts are updated and your new contacts are
  imported when a survey is submitted."*
- Syncs an explicit **field mapping** (Typeform attribute → Brevo attribute) and a **list
  assignment**. *"It is mandatory to map at least the Email or SMS field."*

Source: https://help.brevo.com/hc/en-us/articles/360006908480-Typeform-integration-Import-your-contacts-to-Brevo

🚩 **The constraint that matters most:** *"You can only select one form per Typeform integration
created."* One integration instance = one form. Multiple forms means multiple instances,
hand-configured.

🚩 **Several question types do not sync at all.** Supported: Email, Phone, Website, Number, Date,
Long/Short text, Contact Info, Address, Statement, Picture Choice, NPS, Matrix, Multiple Choice,
**Hidden fields**. **Not supported:** **Yes/No, Opinion Scale, Rating, Calendly, Dropdown, Legal,
Payment, File Upload.** Dropdown and Yes/No are common lead-form fields — anything in that list
must reach Brevo another way.

🚩 **Integration quota:** 100,000 tasks/month across all Brevo integrations, with warnings at 80%
and integrations **stopped** past it.
Source: https://help.brevo.com/hc/en-us/articles/9168632514066-What-are-the-different-quotas-applied-in-Brevo

**The architectural consequence is the good news.** The Brevo integration is **not a webhook** —
it is an OAuth-based sync owned by Brevo. It therefore **does not compete with our webhook at
all.** The clean design is:

> **Brevo native integration (per form) running independently, plus one Typeform webhook to
> Artemis.** Fan-out to both destinations does not depend on the undocumented multi-webhook
> behaviour; multiple webhooks are only needed if we want a *third* HTTPS destination.

## 10. Payload — form identified cleanly, respondent is not, and the signature is real

Source: https://www.typeform.com/developers/webhooks/example-payload/

**Top level:** `event_id`, `event_type` (e.g. `form_response`), `form_response`.
**Inside `form_response`:** `form_id`, `token`, `response_url`, `landed_at`, `submitted_at`,
`definition`, `answers`, `hidden`, `variables`, `calculated`, `ending`.

**Identifies the FORM:** `form_response.form_id`, mirrored as `form_response.definition.id`, with
`definition.title` and `definition.fields[]` (each `id`, `title`, `type`, `ref`) carrying the
question schema.

**Identifies the RESPONDENT — with an important correction.** `form_response.token` is *"Unique ID
for the typeform submission. This is identical to response id in the Responses API."*

⚠️ **There is no `response_id` key; the field is `token`, and it identifies the SUBMISSION, not
the person.** Typeform sends **no stable respondent identity of its own** — an anonymous
respondent filling the form twice produces two unrelated tokens. **Person-level identity must come
from an answer (their email) or from a hidden field we populated.** Use `token` purely as the
idempotency/dedupe key.

**Signature: yes.**
Source: https://www.typeform.com/developers/webhooks/secure-your-webhooks/

| | |
|---|---|
| Header | **`Typeform-Signature`** |
| Algorithm | **HMAC SHA-256**, key = the `secret` set on the webhook |
| Computed over | the **entire raw received payload as binary** |
| Encoding | **base64**, prefixed `sha256=` |
| Final form | `sha256=<base64-hash>` |

Two implementation traps, both from Typeform's own docs:
- **Hash the raw body bytes, never a re-serialized object.** *"make sure that your body payload is
  passed as a string and not as an object. If you pass it as an object or array it will fail."*
  In FastAPI that means `await request.body()`, not the parsed Pydantic model.
- Use a **constant-time comparison** (the doc explicitly recommends secure-compare).
- The signature is present **only if a `secret` is configured** on that webhook — fail closed on a
  missing header only after confirming the secret is set.

**Hidden fields: yes, and this is how our identifiers survive the round trip.**
`form_response.hidden` is a flat key/value object (official example: `"hidden": {"user_id":
"abc123456"}`), populated as query-string parameters on the form URL.
Source: https://www.typeform.com/developers/create/url-parameters/

Two caveats: URL parameters are *"pre-filled when respondents start the form and cannot be
populated with information they enter during the form"* — they carry identifiers we already knew,
not anything derived from the submission. And *"You are responsible for any information you share
with URL parameters"* — they are visible in the URL, so nothing sensitive goes there.

---

# What we could not confirm

Everything in this list is a real gap, not a formatting note. Several would change a build
decision, and two would change a purchasing one.

**Brevo — pricing**
1. **The price of the Standard band that fits us.** The public configurator only exposes volume
   steps to 100k (5k, 10k, 15k, 20k, 30k, 40k, 50k, 60k, 80k, 100k, then "More", which did not
   expand for us). **We could not read a public price for a 176k–250k monthly-send Standard
   plan.** Get it from the quote.
2. **What "250k" means in the proposed contract.** Our reading — 250,000 emails/month — is an
   inference from the tier structure being denominated in emails and from 250,000 matching no
   documented contact ceiling. **Brevo never states this; confirm against the actual quote.**
3. **The auto-upgrade mechanics on hitting a contact ceiling.** The pricing FAQ has an entry
   titled *"What is the auto-upgrade process when reaching the contact limit for my plan?"* but
   the accordion body could not be read. We know contact limits are enforced and trigger an
   upgrade; we do not know the notice, the pricing, or whether it is automatic and unattended.

**Brevo — API**
4. **Batch size, two Brevo pages disagree.** The batch guide says *"Send up to 1000 email messages
   in one API call"*; the API reference says *"Maximum total recipients per API request is 2000."*
   1000 versions × 99 recipients cannot coexist with a 2000 cap. The binding number is almost
   certainly 2,000 recipients/request, but no single page says so.
5. **Batch call rate, the same two pages disagree by ~600×.** The guide says *"up to 6000 times per
   hour (100 times per minute)"*; the rate-limit page says 3,600,000 RPH / 1,000 RPS for
   `POST /v3/smtp/email`. **Neither should be quoted as authoritative.** At ~88 calls/month our
   volume is far below either, so this does not threaten the use case.
6. **No documented maximum for a plain top-level `to` array.** The 2,000 figure appears *only*
   under `messageVersions`. Do not assume `to` accepts 2,000. Relatedly, the docs never state
   whether `to` recipients see each other — treat one-version-per-recipient as the safe pattern.
7. **`messageId` ↔ webhook `message-id` equality is never stated in one sentence**, and the docs do
   not say what is returned when a single request carries many recipients or versions (the
   documented response shows one singular `messageId`). **A batch call returning one id for 2,000
   recipients would break per-recipient attribution.** Verify empirically before building
   attribution on it.
8. **A possible deprecation of `POST /v3/contacts/batch`** (reportedly 30 Oct 2026 — the same month
   HubSpot ends) **could not be confirmed on any Brevo page.** The reference carries no notice and
   the changelog URL 404s, but the endpoint is absent from Brevo's own `llms.txt` index. Treat as
   a live risk. Building the sync on `import` rather than `batch` sidesteps it entirely.
9. **`SCORE_*` attributes being readable via `GET /v3/contacts` is inferred from a help page, not
   confirmed by any API page, and not tested.** The developer documentation does not mention
   scoring anywhere.
10. **No documented import-then-send throttle or cooling-off period.** Enforcement of list quality
    appears reactive (bounce/complaint thresholds, account review) rather than a preventive gate.
    Absence of a gate is not permission.
11. **Dedicated IP add-on pricing** was not readable — the add-ons help article did not load.

**Typeform**
12. **"Multiple webhooks per form" is inferred**, never stated in one explicit sentence (see §8).
    Cheap to verify empirically. Note our recommended design does **not** depend on it.
13. **No documented maximum number of webhooks per form.** "Unlimited" is unconfirmed.
14. **Which surface initiates the Brevo connection** — Typeform's article says the Connect panel,
    Brevo's says Brevo's App Store. We could not load `typeform.com/connect/brevo` well enough to
    resolve it. Brevo's article is more specific and more recent; treat the Brevo-side flow as
    real and verify in-product.
15. **Typeform's own retry doc is internally inconsistent** — it says "five times" and then lists
    seven back-off intervals.

---

## Things to settle before signing

Not vendor claims — our reading of what the above implies. Listed so the purchasing conversation
has an agenda.

1. **Get the metering unit in writing.** Ask Brevo to state on the quote whether 250k is emails
   per month or contacts, and what the contact ceiling is at that tier.
2. **Audit list provenance and recency before committing.** The largest risk in this migration is
   not technical. It is Brevo's **two-year consent rule** and a **0.2% spam-complaint suspension
   threshold** applied to 88,000 contacts of unknown origin, with **role-based addresses**
   (`info@`, `admin@`) and **government/public-records-sourced lists** both named as
   non-compliant. This is a K-12 district list; both patterns are likely present.
3. **Reconcile cadence with the dedicated-IP guidance.** We clear the volume threshold (176k/month)
   but "about twice a month" is the *"gaps longer than a week"* pattern Brevo warns against, and
   an IP needs **re-warming after 30 days idle**. A dedicated IP on a twice-monthly cadence may
   perform worse than shared.
4. **Do not let Brevo's lead scoring carry any weight in the decision.** It is e-commerce scoring
   driven by order data we will not have. District-level signal scoring stays in Artemis, which is
   where it already works.

