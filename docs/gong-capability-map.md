# Gong capability map

**Audited 2026-09-08** against the live Gong tenant, read-only, using the credentials already in
`.env`. Every request behind this document was a read (`GET`, or a `POST` to one of Gong's
read-only query endpoints). Nothing was written, and no call, user or setting was modified.

**This document contains no call content.** It describes the shape and volume of what the API
returns, never what anyone said. Two transcripts were fetched to establish the response format
and nothing from them is reproduced here. Accounts and people are referred to as "a district"
and "an internal user". That restraint is the point of the exercise, not a formatting
preference: see "The privacy surface" near the end.

This was a **pre-integration** audit: when it was written there was no Gong client in the repo,
so everything below describes what we *could* build against.

**Built since, 2026-09-08/09** — see "What was built" at the end. The audit itself has not been
re-run and its numbers are as of 2026-09-08; where the two disagree, the code is current and this
document is a snapshot.

---

## The connection

| | |
|---|---|
| Base URL | `https://api.gong.io` |
| Auth | HTTP Basic, access key + secret, sent on every request |
| Credentials | `GONG_ACCESS_KEY` / `GONG_ACCESS_KEY_SECRET` in `.env` (aliases `ARTEMIS_GONG_*`) |
| Defined in | `/Users/artemis/Artemis/artemis-os/artemis/config.py` (fields `gong_access_key`, `gong_access_key_secret`) |
| Consumed by | nothing, as of this audit |
| Workspaces | exactly one, "Initial workspace" (`1251007101643293717`) |
| API server version seen | `PublicApiServer:17.main.20731` |

One workspace means no workspace-scoping decisions to make, and no risk of a query silently
covering only part of the company.

---

## The short version

Four findings, in the order that matters for what Jon asked for.

1. **Calls can be tied to accounts, and richly.** A call carries its Salesforce Account
   (including `Name`, `Industry` and `District_Marketing_Tier__c`) and any related
   Opportunities with 22 fields each, delivered inline on the call record. **92.4% of real
   external meetings are linked**, covering 874 distinct accounts, of which roughly 175 are
   currently active. Per-district notification is possible. The gaps are systematic rather than
   random, and one of them swallows the entire imported history (see section 3).

2. **Gong already scores every call, and the scores contain no words.** Every call comes back
   with 26 "trackers", 17 of which are AI trackers someone at Amira configured, including
   `Customer concerns` (fires on 69% of external meetings), `Customer objections` (58%),
   `Product feedback` (67%), `Amira Solving Problems` (33%) and `Opportunity stalled in Active
   Discussion` (11%). Each is returned as a **count only** with no text attached. This is very
   close to exactly the signal Jon described, at zero content exposure. We very probably do not
   need transcripts at all. The catch is that they fire often enough that presence means little
   and only a per-account baseline turns them into an alert.

3. **There are no private calls, and this credential reads everything.** All 5,516 calls have
   `isPrivate: false`, every permission profile in the tenant grants `callsAccess: all`, and the
   per-call access list is empty. This key can read every rep's conversations, including
   transcripts and signed links to the recordings. There is no subset and no restriction.

4. **Gong gives us no sentiment score of any kind**, and no scorecards are configured. The
   "how does this customer feel" question has to be answered from tracker counts, topic
   durations and talk statistics, not from a number Gong hands us.

---

## What is NOT available

Listed first, deliberately. A capability map that reports only successes gets someone to
promise a feature we cannot build.

| Not available | What we established |
|---|---|
| **Sentiment score** | There is no sentiment field anywhere in the API. Not on the call, not on the transcript, not in the stats endpoints. Gong's product shows sentiment-flavoured views internally; the public API does not expose them. |
| **Scorecards** | `/v2/settings/scorecards` returns an empty list. Nobody has configured any, so `/v2/stats/activity/scorecards` returns 404 "No answered scorecards found". A whole category of structured human judgement is simply absent. |
| **`callOutcome`** | The field is accepted by `/v2/calls/extensive` and returns nothing on this tenant. No call outcomes are being set. |
| **`structure`** | Accepted, returns nothing. |
| **`pointsOfInterest`** | Accepted, returns nothing. Gong deprecated `pointsOfInterest.actionItems` in January 2025 in favour of `highlights`, which is why. Do not build on this field. |
| **AI tracker definitions** | `/v2/settings/trackers` returns only the 13 keyword trackers. The 17 AI ("SMART") trackers are visible per call but **cannot be enumerated, described, or their definitions read through the API**. To know what `Customer concerns` actually matches, someone has to open Gong and look. |
| **Call history before 2025-03-03** | A query for anything earlier returns 404, "No calls found corresponding to the provided filters." The corpus starts there and there is no way to reach further back. |
| **Deals / forecast** | `/v2/deals` and `/v2/forecast` are 404. Gong's deal board is not in the public API. |
| **Emails and activities** | `/v2/emails`, `/v2/activities`, `/v2/engagement`, `/v2/digital-interactions` are all 404, even though 33 users have `emailsImported: true`. Gong holds email activity we cannot query. |
| **A readable rate limit** | Responses carry `x-ratelimit-remaining` and nothing else. No limit, no reset time, no daily counter. We can see how much is left in the current bucket and cannot see the size of the bucket or how much of the daily allowance is gone. |
| **Salesforce link on imported history** | Calls imported from the previous vendor carry no CRM context at all: 0 of 2,582. See section 3. |
| **Four configured trackers that never reach the API** | `Pain points (tracker)`, `Discovery questions (tracker)`, `Economic pulse (by Gong)` and `Next steps (tracker)` are all defined in `/v2/settings/trackers` and appear on **none** of the 5,516 call records. `Pain points` is the one that hurts: it is the tracker whose name best matches "this customer has a problem". |
| **Two fields that exist and are always empty** | The `Business goals (tracker)` keyword tracker has fired on **0 of 5,516 calls**, and the `Moving Forward` topic is **zero seconds on every call**. Both are returned on every response and neither carries information. |

---

## 1. Volume and range

**5,516 calls**, from **2025-03-03** to **2026-09-08** (the audit date). Nothing exists before
2025-03-03; the history is hard-floored there, not merely sparse.

The corpus is two different things stitched together, and treating it as one will produce wrong
answers:

| Source | Calls | Date range | What it is |
|---|---|---|---|
| `Custom Import 1` | 2,582 | 2025-03-03 to 2025-11-24 | Bulk import from the previous conversation-intelligence vendor |
| `Gong Connect` | 1,748 | 2025-11-13 to 2026-09-04 | The dialer. Audio only, median length **1 minute** |
| `Zoom` | 1,077 | 2025-11-11 to 2026-09-08 | Recorded video meetings |
| `Google Meet` | 109 | 2025-11-11 to 2026-09-03 | Recorded video meetings |

Gong itself went live around **2025-11-11**, and the earlier material was imported. The import
and the native recording overlap for two weeks in November 2025 and then the import stops dead.

### Calls per month

| Month | Import | Dialer | Zoom | Meet | Total |
|---|---|---|---|---|---|
| 2025-03 | 144 | 0 | 0 | 0 | 144 |
| 2025-04 | 185 | 0 | 0 | 0 | 185 |
| 2025-05 | 194 | 0 | 0 | 0 | 194 |
| 2025-06 | 229 | 0 | 0 | 0 | 229 |
| 2025-07 | 359 | 0 | 0 | 0 | 359 |
| 2025-08 | 513 | 0 | 0 | 0 | 513 |
| 2025-09 | 498 | 0 | 0 | 0 | 498 |
| 2025-10 | 290 | 0 | 0 | 0 | 290 |
| 2025-11 | 170 | 78 | 79 | 10 | 337 |
| 2025-12 | 0 | 155 | 105 | 12 | 272 |
| 2026-01 | 0 | 298 | 115 | 14 | 427 |
| 2026-02 | 0 | 123 | 134 | 9 | 266 |
| 2026-03 | 0 | 208 | 95 | 6 | 309 |
| 2026-04 | 0 | 314 | 111 | 5 | 430 |
| 2026-05 | 0 | 174 | 81 | 8 | 263 |
| 2026-06 | 0 | 150 | 73 | 6 | 229 |
| 2026-07 | 0 | 134 | 107 | 6 | 247 |
| 2026-08 | 0 | 96 | 142 | 28 | 266 |
| 2026-09 (to the 8th) | 0 | 18 | 35 | 5 | 58 |

**The number that matters for a notification feature is not the total.** It is the ~5.8 real
external meetings per working weekday (1,186 Zoom and Meet calls spread over 203 weekdays since
November 2025). The dialer's 1,748 calls have a median length of one minute and are mostly
outbound attempts, so they carry very little of what Jon is asking about.

### People

76 users exist (63 active, 13 deactivated). Only **32 have `webConferencesRecorded: true`**, and
only **37 distinct users appear as the primary user on any call**. So roughly half the Gong seats
are observers rather than recorded participants. Email domains are 72 `amiralearning.com`, 3
`istation.com`, 1 external consultant.

---

## 2. Which calls we can see, and which we cannot

**We can see all of them. This is the section to read twice.**

- **`isPrivate` is `false` on all 5,516 calls.** Not "mostly". There is not one private call in
  the tenant.
- **`scope`** takes two values: `External` (1,186, all the Zoom and Meet meetings, all of which
  have a calendar event behind them) and `Unknown` (4,330, being the dialer calls and the
  imported history). The value `Internal` never appears. `scope` is a description of how the
  call was captured, not a permission.
- **Every permission profile in the tenant** (Business Admin, RevOps, Manager, Collaborator,
  Standard Team Member) has `callsAccess.permissionLevel: "all"`. Nobody in this Gong instance
  is restricted from anybody else's calls.
- **`/v2/calls/users-access` returns an empty access list**, which is Gong's way of saying no
  call has a restricted audience.

**Plainly: this credential can read every conversation every rep at Amira has had since March
2025, including full transcripts and signed links to the audio and video.** It is not scoped to
a team, a workspace, a user, or a date range. Whatever we build has to impose its own limits,
because Gong is not imposing any.

**One forward-looking caveat.** The API does **not** filter private calls server-side. If someone
marks a call private tomorrow, it will still come back from `/v2/calls` with `isPrivate: true`,
and its transcript will still be fetchable. Third-party connectors handle this by filtering
client-side after the fetch, because Gong requires it for app approval. **Any integration we
build must drop `isPrivate: true` records itself, and that filter is the only thing standing
between us and reading a call somebody deliberately marked private.**

---

## 3. What identifies the account

Calls carry Salesforce context inline, retrieved by `POST /v2/calls/extensive` with
`contentSelector.context = "Extended"`. This is the single most useful finding for per-district
notification.

### At call level

| Object | Fields returned |
|---|---|
| `Account` | `Name`, `Industry`, `District_Marketing_Tier__c`, `OwnerId`, `Website`, `Sales__c`, `CreatedDate` |
| `Opportunity` (one entry per related opportunity) | 22 fields: `Name`, `StageName`, `Amount`, `Probability`, `CloseDate`, `ForecastCategory`, `ForecastCategoryName`, `Type`, `LeadSource`, `RecordTypeId`, `Base_Value__c`, `Prior_Opportunity_Renewing_Revenue__c`, `Meeting_Count__c`, `DashboardsGSP__Days_Since_Last_Stage_Change__c`, `Demo_Scheduled__c`, `Demo_Complete__c`, `CEO_Invited_to_Demo__c`, `Progressing_Past_Demo__c`, `Vision_Meeting__c`, `Vision_LockStrategic_Alignment__c`, `Shared_Owner__c`, `IsDeleted` |

`District_Marketing_Tier__c` arriving free on the call record is worth noting on its own: it
means a notification can be tier-aware without a second Salesforce query.

### At participant level

Each party on a call carries its own Salesforce context: internal people resolve to a `User`,
external people to a `Contact`. Parties also carry `affiliation`, `emailAddress`, `name`, and a
`speakerId` that joins to the transcript.

**`affiliation` has three values, not two:** `Internal`, `External`, and `Unknown`. Any logic
that treats "not Internal" as "the customer" will be wrong on the `Unknown` rows, and they are
not rare. On sampled calls from the imported era and from Google Meet, `Unknown` was the single
largest affiliation group.

### Coverage

**43.3% of all calls (2,386 of 5,516) carry a Salesforce Account.** That headline number is
misleading in both directions, because the misses are entirely systematic:

| Capture system | Linked to an Account |
|---|---|
| Zoom | 1,004 / 1,077 (**93.2%**) |
| Google Meet | 92 / 109 (**84.4%**) |
| Gong Connect (dialer) | 1,290 / 1,748 (73.8%) |
| Imported from the previous vendor | **0 / 2,582 (0.0%)** |

**Every single imported call is unlinked**, and that alone accounts for the entire gap. The
imported era predates the Salesforce integration and no amount of API work will recover it.
Restricted to real external meetings, linkage is **92.4%** (1,096 of 1,186). Month by month since
the integration turned on:

| Month | Linked | Month | Linked |
|---|---|---|---|
| 2025-03 to 2025-10 | 0.0% | 2026-03 | 88.3% |
| 2025-11 | 36.5% | 2026-04 | 88.1% |
| 2025-12 | 84.2% | 2026-05 | 81.7% |
| 2026-01 | 73.8% | 2026-06 | 87.3% |
| 2026-02 | 83.8% | 2026-07 | 73.3% |
| | | 2026-08 | 76.7% |

**For a notification feature this is good enough.** We are working with roughly 75 to 90% of
recent calls attributable to a district, and near-total coverage on the scheduled external
meetings that carry the most signal. The unattributable remainder is mostly dialer activity.

Volume behind that: **874 distinct Salesforce Accounts** have at least one call. The median
account has 2 calls and the busiest has 69. **123 accounts have 5 or more calls**, and 553
accounts have had a call since March 2026, of which 175 have had three or more. So a
per-district notification would be watching on the order of 150 to 200 genuinely active
accounts, not 874.

**Three complications worth designing around.**

- **319 calls link to more than one Account** (up to five). "The account for this call" is not
  always a single answer, and code that takes the first one will occasionally attribute a call
  to the wrong district.
- **Only 29.9% of calls link to an Opportunity.** The rich 22-field opportunity data is real but
  it is present on under a third of calls, so it corroborates and cannot be the spine.
- **`affiliation` is `Unknown` more often than it is anything else.** Across 27,372 party
  records: 11,284 `Unknown`, 8,288 `External`, 7,800 `Internal`. Party-level CRM links resolve to
  `User` (4,387), `Contact` (3,970) and `Lead` (1,156). And only **18,472 of 27,372 parties carry
  a `speakerId`**, so a third of participants cannot be matched to anything they said even if we
  had the transcript.

---

## 4. What Gong already computes for us

This is the good news, and it is better than expected.

### Trackers (26 per call)

Every call returns all 26 configured trackers with a hit count, whether or not they fired. Nine
are keyword trackers and 17 are AI ("SMART") trackers. The AI ones were configured by someone at
Amira and read like a list of the things Jon wants to know:

`Product feedback`, `Customer concerns`, `Customer objections`, `Amira Solving Problems`,
`Opportunity stalled in Active Discussion`, `Buying Indicator`, `Champion`, `Next steps`,
`Decision process`, `Paper process`, `Outcome / Solution`, `Reactions to pricing`,
`Strategic business goals`, `Customer or seller trends`, and three near-identical trackers about
Amira not listening (`Customer states Amira isn't listening`,
`School states Amira not listening`, `Customer stating Amira not listening`).

**Measured fire rates.** Presence of a tracker is not the same as a tracker that works. Across
all 5,516 calls, and separately across the 1,186 external meetings that carry most of the signal:

| Tracker | Type | Fires on external meetings | Fires on all calls |
|---|---|---|---|
| Next steps | AI | 95.4% | 71.4% |
| Pricing (tracker) | keyword | 77.8% | 33.1% |
| Objections (tracker) | keyword | 75.4% | 63.7% |
| Product Names | keyword | 70.5% | 29.5% |
| **Customer concerns** | AI | **69.1%** | 16.0% |
| **Product feedback** | AI | **66.8%** | 15.3% |
| **Customer objections** | AI | **57.8%** | 14.1% |
| Champion | AI | 45.2% | 42.2% |
| Decision process | AI | 41.8% | 51.8% |
| **Buying Indicator** | AI | **39.8%** | 10.4% |
| **Amira Solving Problems** | AI | **32.5%** | 7.5% |
| Outcome / Solution | AI | 27.8% | 49.9% |
| Strategic business goals | AI | 26.6% | 6.0% |
| Budget (tracker) | keyword | 24.6% | 15.4% |
| Paper process | AI | 17.7% | 29.4% |
| Vision Lock | keyword | 17.2% | 10.5% |
| Reactions to pricing | AI | 16.2% | 4.1% |
| Decision process (tracker) | keyword | 14.7% | 4.0% |
| Customer or seller trends | AI | 12.0% | 2.7% |
| **Opportunity stalled in Active Discussion** | AI | **10.5%** | 2.7% |
| Competitors | keyword | 6.3% | 6.0% |
| Discount (tracker) | keyword | 5.1% | 2.1% |
| School states Amira not listening | AI | 0.8% | 0.2% |
| Customer stating Amira not listening | AI | 0.6% | 0.1% |
| Customer states Amira isn't listening | AI | 0.4% | 0.1% |
| **Business goals (tracker)** | keyword | **0.0%** | **0.0%** |

Three things to take from that table.

**`Business goals (tracker)` has never fired once**, on any of 5,516 calls, despite carrying 21
keywords. It is broken or its keywords never occur. Do not build on it, and someone should look
at it in Gong.

**The three "Amira not listening" trackers are effectively dead**, firing on 21 calls between
them in eighteen months. They are the trackers whose names most directly match "a customer has a
problem", and they are the ones that will not tell us. Whoever configured them presumably meant
something quite specific. Anything relying on them will be silent almost always.

**The useful trackers fire too often to be alerts on their own.** `Customer concerns` firing on
69% of external meetings means its presence is unremarkable; concerns come up on most calls, as
you would expect. **The signal is the count and its deviation from that account's own baseline,
not the fact that it fired.** Any alerting has to be built on rolling per-account baselines. A
fixed threshold would either fire on everything or on nothing.

The keyword trackers **can** be read from `/v2/settings/trackers` with their full keyword lists.
That endpoint and the per-call tracker list **disagree in both directions**, which is the single
most confusing thing about trackers in this API:

| Tracker | Whose speech | Keywords | Created | Appears on calls? |
|---|---|---|---|---|
| Budget (tracker) | Anyone | 5 | 2025-11-03 | yes |
| Business goals (tracker) | Company | 21 | 2025-11-03 | yes, but never fires |
| Competitors | NonCompany | 17 | 2025-11-03 | yes |
| Decision process (tracker) | Company | 12 | 2025-11-03 | yes |
| Discount (tracker) | Anyone | 11 | 2025-11-03 | yes |
| **Discovery questions (tracker)** | Company | 29 | 2025-11-03 | **no** |
| **Economic pulse (by Gong)** | NonCompany | 46 | 2025-11-03 | **no** |
| **Next steps (tracker)** | Company | 14 | 2025-11-03 | **no** |
| Objections (tracker) | NonCompany | 36 | 2025-11-03 | yes |
| **Pain points (tracker)** | Company | 26 | 2025-11-03 | **no** |
| Pricing (tracker) | Company | 27 | 2025-11-03 | yes |
| Product Names | Company | 6 | 2025-12-01 | yes |
| Vision Lock | Anyone | 3 | 2026-01-29 | yes |

**Four of the thirteen configured keyword trackers never appear on a single one of the 5,516
call records.** Not "never fire", which would show as a zero count. They are absent from the
response entirely, as though they did not exist. `Pain points (tracker)` is one of them, and it
is the tracker whose name most directly promises "this customer has a problem". Its 26 keywords
are configured, readable, and produce nothing.

Going the other way, the 17 AI trackers appear on every call and are **not listed by
`/v2/settings/trackers` at all**.

**The consequence for anything we build: the per-call tracker list is the only trustworthy
inventory.** Never take `/v2/settings/trackers` as the set of trackers you will receive. Read
one call and enumerate what actually comes back.

**The privacy property that makes this whole approach work:** AI trackers return a **count and
nothing else**. Asking for `trackerOccurrences` on top adds, for keyword trackers only, the
matched phrase (which is the configured keyword, so we already knew it) plus `speakerId` and
`startTime` per hit. **AI trackers returned zero phrases.** So `Customer concerns: 47` is
available to us as a bare integer with no words attached to it, ever.

### Topics

Exactly **six named topics on every one of the 5,516 calls**, each with a duration in seconds.
The taxonomy never varies, which makes it a clean fixed-width numeric feature vector. Durations
only, no text.

| Topic | Non-zero on | Median seconds when non-zero |
|---|---|---|
| Call Setup | 60.9% of calls | 139 |
| Pricing | 47.3% | 221 |
| Wrap-Up | 45.8% | 86 |
| Small Talk | 34.4% | 112 |
| Next Steps | 23.3% | 91 |
| **Moving Forward** | **0.0%** | n/a |

`Moving Forward` is always zero, on every call in the corpus. Like `Business goals (tracker)`, it
is a field that exists and carries no information.

"This call spent 221 seconds on Pricing and zero on Next Steps" is a usable signal that exposes
nothing. Note that `Next Steps` is non-zero on only 23% of calls while the `Next steps` AI
tracker fires on 95% of external meetings; the two measure different things and should not be
conflated.

### Interaction statistics

Per call: `Talk Ratio`, `Longest Monologue`, `Longest Customer Story`, `Interactivity`,
`Patience`, plus a question count split into `companyCount` and `nonCompanyCount`, plus webcam
and screen-share durations broken out by participant type. `POST /v2/stats/interaction` gives the
same statistics aggregated per user over a date range, adding `Question Rate`.

All numbers. No text.

### The things that are content

These also come back from `/v2/calls/extensive`, and they are summaries of what people said:

| Field | Size on a sampled 35-minute call |
|---|---|
| `brief` | 620 characters, one paragraph |
| `keyPoints` | 10 items, 2,011 characters |
| `highlights` | 8 items, 1,009 characters |
| `outline` | 18 sections, 112 items, **22,878 characters** |

`outline` is a section-by-section retelling of the call and is only about 40% smaller than the
transcript itself. Treat it as content, not as metadata. `brief` is the smallest useful summary
by a wide margin.

`collaboration.publicComments` returned empty, meaning nobody is leaving comments on calls in
Gong.

---

## 5. Transcript shape and size

`POST /v2/calls/transcript` with `filter.callIds`. Two transcripts were fetched for this audit
and no further sampling was done, by design.

The response is `callTranscripts[]`, each with a `callId` and a `transcript` array of **monologue
segments**. Each segment has `speakerId`, an optional `topic`, and a `sentences` array of
`{start, end, text}` with millisecond offsets.

| | 35-minute external meeting | 14-second dialer call |
|---|---|---|
| Monologue segments | 130 | 1 |
| Sentences | 542 | 6 |
| Total characters | 37,805 | 272 |
| Distinct speakers | 3 | 1 |
| Segments carrying a topic | 41 of 130 | 0 of 1 |
| Estimated tokens | ~9,450 | ~70 |

**Speakers are identified but not described.** `speakerId` is an opaque id. To learn whether a
speaker is ours or the customer's, you must separately call `/v2/calls/extensive` with
`parties` exposed and join on `parties[].speakerId` to get `affiliation` and name. So
distinguishing internal from external speech **costs a second API call and pulls participant
names and email addresses into the process**, which is worth knowing before designing anything
that needs the distinction.

Transcript density is a steady **17.8 characters per second of call**, which makes cost easy to
forecast:

| Slice | Calls | Median length | Tokens per call | Whole corpus |
|---|---|---|---|---|
| Everything | 5,516 | 23.0 min | ~6,200 | ~37.3M tokens |
| External meetings only | 1,186 | 35.3 min | ~9,450 | ~11.8M tokens |
| Dialer only | 1,748 | 1.0 min | ~280 | ~0.9M tokens |

**Processing every external call's transcript would cost roughly 55,000 input tokens per working
day** (5.8 calls at ~9,450 each), before any output. That is affordable. The reason not to do it
is not cost.

---

## 6. Rate limits

Gong's published defaults are **3 API calls per second and 10,000 per day**, with HTTP 429 and a
`Retry-After` header when exceeded.

What we actually observed differs, and the difference is in our favour but unverifiable:

- The only rate header returned is **`x-ratelimit-remaining`**, which read `17` both when idle
  and during a sustained pull. There is no `x-ratelimit-limit` and no `x-ratelimit-reset`, so
  **we cannot read our actual ceiling or our remaining daily budget from the API.**
- The audit sustained roughly 2.5 requests per second for an extended bulk pull and **received
  zero 429s**, which suggests our tenant's per-second allowance is above the documented default.
  We should not depend on that. Build for 3 per second and back off on 429.

**A realistic daily pull is nowhere near any of these limits.** Fetching one day of calls costs
one request to `/v2/calls` plus one `/v2/calls/extensive` page per 100 calls, so about **3
requests for a normal day** of roughly 20 calls. Even re-pulling the entire 5,516-call history
costs about 60 requests. The daily cap is not a design constraint for this use case; it only
becomes one if we start fetching transcripts per call, which is one request each.

---

## 7. Detecting that a customer has a problem, without reading transcripts

Ranked by signal earned per unit of content exposed. The first four expose no words at all.

**1. AI tracker counts (no content whatsoever).** `Customer concerns` (69% of external meetings),
`Customer objections` (58%), `Product feedback` (67%) and `Opportunity stalled in Active
Discussion` (11%) are, between them, a direct read on Jon's "customers having problems".
`Amira Solving Problems` (33%), `Buying Indicator` (40%) and `Champion` (45%) are the read on
"customers saying good things". These arrive as integers on every call. **This is the answer to
Jon's question, and it needs no transcript access at all.**

Three cautions, all load-bearing.

*Use counts against a baseline, never presence.* At a 69% fire rate, "this call had customer
concerns" describes most calls. The alert is "this district's concern count is well above its own
recent average", which means storing a rolling per-account history before anything can fire.

*Do not use the "not listening" trackers.* All three fired on 21 calls in eighteen months. They
are the trackers whose names best match what we want and they will be silent almost always.

*We cannot see when the trackers change.* AI tracker definitions are not readable through the
API, so **nothing in our code will notice when someone edits what `Customer concerns` matches**,
and our baselines would shift underneath us with no signal. Record the fire rates in this
document as of today so a future drift is at least detectable by comparison.

**2. Keyword tracker counts (no content beyond the keyword lists we can already read).**
`Objections` (75% of external meetings), `Pricing` (78%), `Competitors` (6%), `Budget` (25%),
`Discount` (5%). Blunter than the AI trackers but fully auditable, because
`/v2/settings/trackers` gives us the exact keyword list behind every count, which is the one
thing the AI trackers cannot offer. Note that `Pain points` and `Economic pulse` are **not**
available despite being configured, so the obvious picks here are not the ones on the menu.

**3. Topic durations (no content).** A fixed six-number vector on every call. Seconds spent on
`Pricing`, and `Next Steps` sitting at zero when it is usually non-zero, are meaningful without a
word of text. Ignore `Moving Forward`, which is always zero.

**4. Interaction statistics (no content).** `Longest Customer Story` spiking, `Talk Ratio`
inverting, `Patience` dropping. Weak individually, useful as corroboration.

**5. Call-pattern metadata (no content).** Cadence changes per account: a district that had a
call every fortnight going quiet for six weeks, or three unscheduled calls in a week. Available
from `/v2/calls` metadata plus the Account id, with no `extensive` call at all. With 175 accounts
carrying three or more calls since March 2026, there is enough per-account history for a cadence
baseline to mean something.

**6. Salesforce fields carried on the call (no Gong content).** `StageName`,
`DashboardsGSP__Days_Since_Last_Stage_Change__c` and `ForecastCategory` come along for free with
the call's context and are the cheapest possible corroboration that something is up.

**7. `brief` (620 characters of content).** The first rung that exposes what was said. If a
notification genuinely needs a sentence of "why", this is the smallest thing that provides one,
at roughly 1.6% of the transcript's size.

**8 and below, in descending order of how much we should want them:** `keyPoints` (~2,000
chars), `highlights` (~1,000 chars but usually just next steps), `outline` (~23,000 chars, a
retelling of the call), the raw transcript (~38,000 chars), the signed media URLs (the recording
itself).

**Recommendation.** Everything Jon described is achievable from rungs 1 to 6, which expose no
call content at all. Build the notification on tracker counts plus call cadence plus the
Salesforce fields that arrive alongside them, and let the notification link to Gong so a human
opens the call in the system that is already access-controlled and audit-logged for it.

---

## The privacy surface

**What a maximal integration would expose.** Every word of every conversation every Amira rep
has had since March 2025, roughly 37 million tokens of it. Participant names and email
addresses, internal and external. Signed URLs to the audio and video of each call. The
Salesforce account and opportunity behind each one. All of it reachable by a single key pair in
`.env`, with no per-user, per-team or per-date restriction of any kind, because there are no
private calls and every permission profile grants access to everything.

**What the minimum-exposure design looks like.** Concretely:

- **Never request `transcript`, `outline`, `keyPoints`, `highlights` or `media`.** The
  `contentSelector` is an allowlist, so simply not asking is a real control, not a convention.
  A pull limited to `parties`, `content.trackers`, `content.topics`, `interaction` and
  `context: "Extended"` returns numbers and CRM ids and no sentences. That is exactly the pull
  this audit used for its bulk pass, and it is enough for everything in section 7 rungs 1 to 6.
- **Drop `isPrivate: true` at the client boundary**, before the record reaches storage, since
  Gong will not do it for us.
- **Never store the `audioUrl` / `videoUrl`.** Requesting `media: true` returns two ~1,700
  character signed URLs pointing at the actual recording. The audit did not fetch them and so did
  not establish how long they stay valid, but a URL of that shape is a bearer credential: anyone
  holding it very likely reaches the recording without logging in. The correct durable reference
  is the call's ordinary Gong URL, which does require a login.
- **Store counts, not text.** Persist tracker counts, topic durations, participant *counts* by
  affiliation, and the Salesforce Account id. Do not persist participant names or email
  addresses; the Account id and the Gong call URL are enough to re-resolve anything later.
- **The notification says that something happened, not what was said.** "Four calls at this
  district in eight days, `Customer concerns` firing at three times its usual rate, opportunity
  unchanged in stage for 46 days" is the whole message, plus a link into Gong. That satisfies
  Jon's "something's up with X" without any conversation content leaving Gong.
- **If a summary ever becomes necessary**, use `brief` and only `brief`, and treat it as content
  subject to the same handling rules as the transcript, not as metadata.

**One point in our favour.** Gong keeps an access log, readable at
`GET /v2/logs?logType=AccessLog` (and `UserActivityLog`; other log-type names are rejected). Our
own API reads are auditable by a Gong admin after the fact. Whatever we build is not invisible to
the people whose calls it reads, which is the right property for a system touching this data.

---

## How to re-run this audit

Credentials load from `.env` through `artemis/config.py`. From the repo root:

```bash
PYTHONPATH=. uv run python - <<'PY'
import base64, httpx, artemis
from artemis.config import settings
tok = base64.b64encode(
    f"{settings.gong_access_key}:{settings.gong_access_key_secret}".encode()
).decode()
h = {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}
r = httpx.get("https://api.gong.io/v2/calls",
              params={"fromDateTime": "2010-01-01T00:00:00Z",
                      "toDateTime": "2026-12-31T00:00:00Z"},
              headers=h, timeout=60)
print(r.json()["records"]["totalRecords"])
PY
```

The endpoints this document is built on, all read-only:

| Endpoint | Method | What it answered |
|---|---|---|
| `/v2/workspaces` | GET | one workspace |
| `/v2/users` | GET | 76 users, recording settings, cursor-paginated |
| `/v2/calls` | GET | volume, date range, `isPrivate`, `scope`, capture system |
| `/v2/calls/extensive` | POST | CRM context, parties, trackers, topics, interaction stats, summaries |
| `/v2/calls/transcript` | POST | transcript shape and size |
| `/v2/calls/users-access` | POST | per-call access restrictions (none) |
| `/v2/settings/trackers` | GET | keyword tracker definitions (AI trackers absent) |
| `/v2/settings/scorecards` | GET | empty |
| `/v2/all-permission-profiles?workspaceId=…` | GET | every profile grants `callsAccess: all` |
| `/v2/stats/interaction` | POST | per-user talk statistics (note: `fromDate` / `toDate`, **not** `fromDateTime`) |
| `/v2/stats/activity/aggregate`, `/v2/stats/activity/day-by-day` | POST | per-user activity counts |
| `/v2/logs?logType=AccessLog` | GET | Gong's own audit trail |

### Parameter traps

Three, recorded because each cost real time here and the third cost the most.

1. The `/v2/stats/*` endpoints take **`fromDate` / `toDate`**, while every other endpoint takes
   `fromDateTime` / `toDateTime`. Using the wrong one returns a 400 that names the field it
   wanted, so this one at least fails loudly.
2. `trackerOccurrences` is rejected unless `trackers` is requested alongside it.
3. **On `POST /v2/calls/extensive` the pagination cursor goes at the top level of the request
   body, not inside `filter`.** `GET /v2/calls` takes its cursor as a query parameter and
   `/v2/calls/extensive` looks like it should take it in `filter`, but a cursor placed there is
   **silently ignored** and the endpoint returns page one again, forever. There is no error and
   no warning. A paging loop written that way spins indefinitely, accumulating duplicates, and
   looks exactly like a slow API. The audit lost about twenty minutes to this.

   The robust alternative, and what this audit ultimately used: pull the id list from
   `GET /v2/calls` first, then feed `filter.callIds` to `/v2/calls/extensive` in batches of 100.
   That has no cursor semantics at all, is deterministic, and completed all 5,516 calls in 226
   seconds across 56 requests.

**If you re-run this, keep the same discipline: request the minimum `contentSelector` that
answers the question, and do not paste call content into the result.**

---

## What was built, 2026-09-08/09

Recorded here so the next reader does not have to re-derive it from the tree, and so the gap
between "what Gong could give us" and "what we actually take" stays visible.

| Module | What it does | Which finding above it acts on |
|---|---|---|
| `integrations/gong/client.py` | metadata-only client; **no transcript method exists in it** | §4, §5 — everything useful is metadata, so the content path was never built |
| `integrations/gong/baseline.py` | portfolio tracker rates, and per-account deviation from them | §7 caution 1: "use counts against a baseline, never presence" |
| `integrations/gong/brief_section.py` | the daily brief's "What districts are saying" — concern and advocacy | §7 items 1 and 2 |
| `integrations/gong/snapshots.py` | dated per-district readings, and the trend between them | §7 caution 1: "storing a rolling per-account history before anything can fire" |
| `integrations/gong/survey.py` | one paging pass over the window, shared by every consumer | §"Parameter traps" — the cursor belongs at the TOP LEVEL of the body |
| `floating_artemis/tools/gong_tools.py` | `district_call_signal`, Callie's read-only tool | nothing in the audit; it exists because the signal otherwise had one consumer |

**The baseline's margin is not flat.** The first version required a fixed gap above the portfolio
rate and flagged 48% of accounts, which is not a signal. A district with four calls now has to
clear a much wider margin (0.40) than one with thirty (0.15): one call joining a four-call window
moves its rate 25 points, and the sample sizes here are small enough that this dominates.
Currently 37 of 340 accounts carry a signal.

**Trends compare against a reading at least 21 days old,** not the most recent one. The section
scores a 120-day window, so yesterday's reading shares 119 days of calls with today's — it cannot
move, and comparing against it would report "steady" indefinitely while a district drifted from
40% to 100%. A district with no reading that old is reported as "nothing to compare against yet",
which is deliberately a different statement from "no change".

**Caution 3 above is still unaddressed.** Nothing notices when someone edits what a tracker
matches, and the baselines would shift underneath us silently. The fire rates in §4 are the
record against which a future drift could be detected — by a person, by hand. There is no code
watching for it.

**Asking is now possible, not just being told.** Until 2026-09-09 the signal
appeared only as a line in the daily brief. `district_call_signal` answers the
portfolio question from stored readings (no API call) and a named district live.
It is registered on **Callie's registry only** — not Artemis, Kai or Ares — and
that is a scope decision, not an oversight: this reads a call corpus whose
participants did not consent to us reading it.

### What is still not built

- **Trend on the advocacy half.** Only concern is trended. A district whose enthusiasm is rising
  is arguably a better case-study lead than one that has always been enthusiastic, and nothing
  currently says which it is.
- **Transcripts, for the "where did we leave off" use Josh described in the HubSpot meeting.**
  Not started, and it cannot be built the way the rest of this was — it is the one thing that
  needs call content. Pending a conversation with Jon and Josh about whether a derived,
  non-quoting summary is acceptable, and rule 4 says the default answer is no.
- **Anything keyed to a rep.** Deliberately, permanently. See "The privacy surface".

---

## Concern categories, derived locally (pilot, 2026-09-10)

Carolyn asked in #market-signals whether we could say a district's concerns were
about rostering, or parents, or training. §4 above is why we could not: all 26
trackers are sales-process and all six topics are too. Getting new trackers
configured would have repeated a two-month access wait, so the category is
derived from the call instead — on the Studio, never leaving the network, storing
only the category. `artemis/integrations/gong/concern_classifier.py`.

**Six districts from the brief, three most recent calls each.**

| District | Dominant category | Rest |
|---|---|---|
| Rowland Unified | **implementation, 3 of 3** | training 1, product functionality 1 |
| Pinellas County | **rostering, 3 of 3** | training 2, parent 1, implementation 1, reporting 1, product functionality 1 |
| Madera Unified | **product functionality, 3 of 3** | reporting 2, training 1, implementation 1 |
| Idaho Dept of Education | implementation 2, rostering 2 | product functionality 1, reporting 1 |
| Visions In Education | reporting 2, rostering 2, product functionality 2 | training 1, implementation 1 |
| Blue Ridge Academy | none recurring | four categories, each on one call |

**Repetition is the signal, not the list.** A category on 3 of 3 calls is a
pattern; one on 1 of 3 is a mention. Blue Ridge — four categories, each appearing
once — is the profile of a district with nothing recurring, and it is one the
brief lists as sounding positive. Across repeat runs the dominant category is
stable and the single mentions move, so the top line is the part to trust.

**One independent corroboration.** Rowland came back implementation on every
call. Carolyn's own note, written before any of this ran, says they "did
transition to direct so we've spent a lot of time with them — answering questions
and front loading support". The classifier found from the calls alone what the
person who knows the account already knew.

**The check that nearly did not happen.** The first version reported every topic a
call touched. Madera — a district the brief calls *sounding positive* — came back
with seven of nine concern categories, more than one flagged concern-heavy. Unit
tests were green throughout; only reading the live output against a district
whose character was already known exposed it. The prompt now requires an
expressed problem rather than a mention.

Also worth recording: "sounding positive" means `Amira Solving Problems` fires
often, which means problems are being discussed AND SOLVED. It never meant "has
no concerns", so a positive district showing concern categories is not on its own
evidence of anything.

**Status: awaiting validation.** Posted to #market-signals on 2026-09-10 asking
Carolyn whether the top line matches what she knows. One district corroborates.
Five are unverified, and it should not reach the daily brief until they are.
