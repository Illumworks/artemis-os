# Callie's capability vs Josh's five-item workflow

**Audited 2026-09-09** against the live registry, the live Salesforce org and live Gong,
read-only. Every figure below came from running the thing, not from reading its docstring.

Josh stated a five-item workflow, in his own priority order:

1. **Customer status** — is this district a customer? ("at the very least")
2. **Site-level licences** — which schools/sites are licensed
3. **Opportunity history** — past and open opportunities
4. **Prior conversations** — where did we leave off with this district
5. **Messaging** built from all four

This document answers, honestly, which of those she can serve today. Read
["What she cannot do"](#what-she-cannot-do) before promising anything on the back of it —
a capability map that lists only successes reads as complete and is worse than none.

---

## The short version

She can answer **1 and 4 well, 3 partially, 5 for the inputs but not the assembly, and 2 not
at all.**

The single most useful correction in here: **item 2 is not blocked by a permission.** The
site roster is sitting in Salesforce behind the credential she already uses, one SOQL query
away. No tool asks for it. The seat *counts*, separately, are genuinely not there — the
fields exist and are empty.

A second finding that cuts across everything: **she has 40 tools registered and 30 of them
at runtime.** See [The 10 tools she does not actually have](#the-10-tools-she-does-not-actually-have).

---

## Josh's five items

| # | Item | Verdict | Tool | What actually comes back | Gap |
|---|---|---|---|---|---|
| 1 | Customer status | **Served** | `check_salesforce_activity` | `Account.Customer_Status__c` verbatim, plus a customer/pilot/prospect reading of it. Live: Houston ISD → `status: Customer`. Fails closed — an unreachable Salesforce reports "UNKNOWN", never "not a customer" | Picklist only. No `Customer_Since__c`, no contract expiry, no ARR |
| 2 | Site-level licences | **Not served** | *(none)* | Nothing. No tool in her registry selects `ParentId` or any `*_Licenses_Scoreboard__c` field | Two separate gaps: the site **roster** is readable and simply never queried; the seat **counts** are a field that exists and is unpopulated |
| 3 | Opportunity history | **Partly served** | `check_salesforce_activity` | One integer: `10 open opportunities`. Nothing else | No names, stages, amounts, close dates, owners. **No closed/past opportunities at all.** `salesforce_pipeline` is portfolio-wide and takes no account filter |
| 4 | Prior conversations | **Served** | `check_salesforce_activity` (folds in Gong) + `district_call_signal` | Call count, dates, durations, external-party counts, which trackers fired, opportunity stage, days-in-stage; plus per-district tracker rates and a worsening/improving trend | Only calls Gong **linked** to a Salesforce account. Email, meetings and tasks are invisible — `Task`/`Event`/`OpenActivity` all 400. Contact "last touched" dates are **due** dates and some are future bookings |
| 5 | Messaging from all four | **Partly served** | `get_message_compass`, `search_claims_register`, `list_writing_rules`, `list_content_assets` | Real, populated inputs — 3,556-char Message Compass, 91 approved claims, house style rules, 5 content assets | She drafts in conversation. `assemble_brief` persists whatever the model hands it and is one of the 10 tools missing at runtime |

---

## Item 1 — Customer status: served

`check_salesforce_activity` → `lookup_district` → `find_accounts_by_name`, which selects
`Id, Name, Customer_Status__c` and reads it against the configured truthy set
`Customer,Child,Parent,Pilot` (10,795 accounts org-wide).

This is the right field. The capability map records that the two obvious alternatives are
traps: `Is_Customer__c` is portfolio-wide in a shared Amira/Istation org and would suppress
~6,000 genuine Amira prospects, and `Amira_Customer_Status__c` is free text carrying live
typos ("Cusotmer", "Cusomter").

Three properties worth keeping:

- **It abstains.** Several name matches are reported as several, never resolved by picking
  the first.
- **It fails closed and says which failure.** An unreachable Salesforce is "UNKNOWN, not
  confirmed absent"; a genuine miss is "a real absence, not a lookup failure".
- **It flags the contradiction.** If Salesforce carries the account as anything but a
  prospect while it also sits on Josh's new-business target list, the answer says so.

Live, unedited:

```
Salesforce: Ypsilanti Community Schools — status: Customer — 2 open opportunities
```

---

## Item 2 — Site-level licences: not served

**No tool she has can answer "which sites are licensed."** This is the plainest gap in the
set, and it is the one Josh ranked second.

Nothing in her registry selects `ParentId`, and nothing selects any licence field. A repo-wide
grep for `ParentId` returns no Salesforce use at all. Our own `districts` table (13,466 rows)
has no school, site, campus or licence column, and no such table exists anywhere in the schema.

But the two halves of the question fail for *different* reasons, and the difference is the
actionable part.

### The site roster is readable and simply never asked for

The Account hierarchy carries it. `Customer_Status__c = 'Child'` marks a site licensed under
a parent district — 5,631 such accounts org-wide. Probed live:

```
PARENT: Ypsilanti Community Schools | Type: District | Customer_Status__c: Customer
licensed sites under that parent: 10
   - Beatty Early Learning Center            | Public School
   - Holmes Elementary School                | Public School
   - Erickson Elementary School              | Public School
   - Estabrook Elementary School             | Public School
   - Perry Child Development Center          | Public School
   - Washtenaw International Middle Academy  | Public School
   - Ypsilanti International Elementary School | Public School
   - Achieving College and Career Education (Acce) | (no type)
```

That is Josh's item 2, answered, by one SOQL query against the credential the app already
holds. **This is a missing query, not a missing permission.**

### The seat counts are genuinely not there

The `(AML)`-suffixed licence fields exist on Account and are readable. They are also empty:

| Field | Accounts with a value > 0 (of 168,552) |
|---|---|
| `Practice_Licenses_Scoreboard__c` | 1,255 |
| `Assessment_Licenses_Scoreboard__c` | 1,176 |
| `Dyslexia_Licenses_Scoreboard__c` | 1,113 |
| `Suite_Licenses_Scoreboard__c` | 753 |
| `Teacher_Licenses_Scoreboard__c` | 670 |

And at the site level they are effectively absent: of the **5,631** accounts marked
`Customer_Status__c = 'Child'`, exactly **1** has `Suite_Licenses_Scoreboard__c > 0`.

Houston ISD — a Customer with 10 open opportunities — reads `0.0` on every licence field at
the district record, and `None` on most of its school records. `Expiration_Date__c` was null
on every row probed.

**So: a tool could be built today that names the licensed sites. A tool that reports how many
seats each holds would be reporting nulls.** Anyone scoping this work should build the first
and not promise the second.

---

## Item 3 — Opportunity history: partly served

She gets a count and nothing more. The entire implementation is:

```python
rows = await client.query(
    "SELECT Id FROM Opportunity WHERE AccountId = "
    f"'{_soql_escape(account_id)}' AND IsClosed = false LIMIT 50"
)
return len(rows)
```

Two consequences:

- **Past opportunities are not returned at all.** `IsClosed = false` excludes them.
- **Open ones are a number.** No name, stage, amount, close date or owner.

The data is rich and fully readable. Probed live on the same account whose tool output says
"2 open opportunities":

```
2027-06-30 | Renewal Opp Auto-Generated | $6,618.75  | open
2027-06-30 | Renewal Opp Auto-Generated | $11,206.25 | open
2026-06-18 | Closed Won                 | $12,138.61
2025-06-24 | Closed Won                 | $12,240.16
2024-08-13 | Closed Won                 | $15,333.00
2022-07-27 | Closed Won                 | $40,776.00
```

Ten opportunities back to 2022, with stages and amounts, thrown away by the tool that fetched
them. `salesforce_pipeline` does not fill the gap: all seven of its prepared questions are
portfolio-wide and none accepts an account or district filter.

Same shape of fix as item 2 — widen an existing query, no new integration.

---

## Item 4 — Prior conversations: served, with a stated ceiling

Two tools, deliberately different in kind.

**`recent_contact_summary` is folded into `check_salesforce_activity` rather than being its
own tool.** That is the right call and the reasoning is recorded at the call site: Callie was
drafting *cold* outreach for Grosse Pointe while calls sat in Gong firing an Objections
tracker. Nobody would have thought to ask, so a tool she must remember to call would not have
caught it. It rides on the call she is already required to make before drafting outreach.

It returns, for the last 180 days: how many linked calls, each one's date, duration,
external-party count and which trackers fired, plus the newest call's opportunity stage and
days-in-stage. Never a word anyone said — CLAUDE.md rule 4 holds, and holds structurally,
because the client underneath has no transcript method.

**`district_call_signal` (registered today, 2026-09-09)** makes the tracker signal something
she can be *asked*, which it previously was not — it existed only as one line in the daily
brief. Verified live, both paths:

- No arguments → the portfolio shortlist from stored readings, no API call. 37 districts,
  split into "sounding positive (case-study leads)" and "raising more than usual".
- `district_name` → a fresh live read in ~15s. Pinellas County Schools: `Product feedback on
  100% of calls, above the norm`, `Customer concerns on 90%`, `Customer objections on 70%`.

It is written against hallucination as much as for retrieval: Gong unreachable is UNKNOWN and
explicitly "not the same as a quiet district", an ambiguous name lists candidates rather than
picking one, and "nothing unusual" says out loud that it is not a clean bill of health.

**The ceiling, which must travel with the answer:** only calls Gong *linked* to a Salesforce
account are visible, and calls imported from the previous vendor carry no account link at all.
Absence of a result means "no linked call", never "no contact" — both tools say so in their
own output. Everything non-call is dark: `Task`, `Event` and `OpenActivity` all return 400 for
our run-as user, so emails and meetings are invisible.

**And `LastActivityDate` does not mean what its label says.** A fix landed in
`salesforce_account_lookup.py` on 2026-09-09, mid-audit, and it changes how item 4 should be
described. Salesforce's `LastActivityDate` is a **due** date, not a completed one — the most
recent Event's `ActivityDate`, counted whether or not the meeting has happened. So a meeting
booked for next March renders as "last touched 2027-03-29".

That is 233 contacts across 184 accounts, and the 0.087% rate wildly understates the damage:
`fetch_account_contacts` sorts `LastActivityDate DESC`, so future dates sort to the **top**, and
on those 184 accounts a rounding-error field problem is a 100% error on the first rows anyone
reads. Asked about Pinellas, Callie was handed two of them as rows one and two, reported them
as past contact, and called the list "warm".

It now reads *"a meeting is SCHEDULED for <date> (not yet happened) — Salesforce cannot tell us
when this person was last actually contacted"*. The reasoning for saying it rather than
filtering it is recorded at the call site and is right: filtering swaps one false statement for
another and throws away a booked meeting, which is a thing demand gen wants to know. Worth
noting the finding is strong inference plus vendor documentation, not direct observation —
the source Activity objects cannot be read to confirm it.

Practical consequence for Josh: **the contact roster's dates are a mix of past contact and
future bookings, and only the tool's own phrasing distinguishes them.** Anyone quoting a date
out of that list without the surrounding sentence can restate the original bug.

---

## Item 5 — Messaging built from all four: partly served

The **inputs are real and populated.** Run live today:

| Tool | Live result |
|---|---|
| `get_message_compass` | 3,556 chars, `01_MESSAGE_COMPASS` v1.1, owner Product Marketing |
| `search_claims_register` | 91 approved claims, tiered, searchable |
| `list_writing_rules` | House rules and voice guidance |
| `list_content_assets` | 5 assets — and several are visibly CI test fixtures |

So she can cite approved language in approved style. The **assembly** is where it thins out:

- `assemble_brief` persists whatever `content` the model passes it, defaulting to `{}`, then
  reports `"Brief assembled for candidate N"`. The real assembler
  (`artemis/marketing/brief_assembler.py`) is never invoked. Called with no content it writes
  an empty row and reports success.
- It is layer 3, so on her actual runtime path it is not there at all (below).
- `submit_draft_for_review` writes a real approval row, but the external submit goes to an
  in-memory `StubWritingStudio` unless `ARTEMIS_WRITING_STUDIO_URL`/`_TOKEN` are set; its ids
  are `stub-approval-N` and vanish when the turn's subprocess exits.

In practice item 5 happens **in conversation** — she reads the compass and the claims and
writes the message in her reply. That works. It is just not a pipeline, and nothing durable
records what she produced.

---

## The 10 tools she does not actually have

`build_floating_artemis_tool_set` skips every entry with `layer > 2`
(`artemis/tools/mcp_server.py:353`), because layer 3 suspends the loop for an operator
confirmation that the subprocess path has no way to serve.

Callie's registry is 21 layer-1, 9 layer-2 and **10 layer-3** tools. Her last 7 days of
`agent_traces` are **11 turns, all on `claude-code`** — the subprocess path. So the working
number is **30 of 40**.

Absent at runtime: `approve_signal`, `reject_signal`, `assemble_brief`,
`submit_draft_for_review`, `decide_approval`, `propose_ruleset_change`, `link_content_asset`,
`post_analyst_message`, `send_slack_message`, `react_to_slack_message`.

For Josh's workflow this costs only `assemble_brief` — every tool serving items 1–4 is
layer 1. But "Callie has 40 tools" is not a true sentence about the running system, and
`post_analyst_message` and `send_slack_message` being missing is worth knowing separately.

---

## Her full tool list, by purpose

40 registered. Flags: **REAL** — queries a live DB or API. **PARTLY** — real code, thin or
conditional data. **HOLLOW** — returns canned text or persists whatever it was handed.
`layer 3` = absent at runtime, per above.

### Salesforce, read-only (2)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `check_salesforce_activity` | 1 | REAL | Customer status, open-opp **count**, up to 25 contacts with titles, active-outreach conflicts, and folded-in Gong context. Verified live |
| `salesforce_pipeline` | 1 | REAL | Seven prepared questions, no free SOQL. Portfolio-wide only — **no account filter**. Returns `UNAVAILABLE`, never zero, on failure |

### Gong, read-only (1) — added 2026-09-09
| Tool | Layer | Status | Note |
|---|---|---|---|
| `district_call_signal` | 1 | REAL | Verified live on both paths. Counts by account only, never a rep, never a quote |

### Signals and the marketing queue (14)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `list_signals` / `get_signal` / `list_candidates` / `list_scout_runs` / `get_active_rulesets` | 1 | REAL | `signal_queue` holds 4,679 rows |
| `list_target_signals` | 1 | REAL | Reads `target_accounts` (1,287 rows) — the right tool for "my accounts" |
| `find_by_keyword` | 1 | REAL | |
| `get_campaign_performance` | 1 | PARTLY | Status, age, volume, pipeline state. **No opens/clicks/conversions** — honestly labelled "not aggregated KPIs" in both output and description |
| `qualify_signal` / `snooze_signal` | 2 | REAL | |
| `fire_scout` | 2 | **HOLLOW — description contradicts implementation** | See below |
| `approve_signal` / `reject_signal` / `propose_ruleset_change` | 3 | REAL | Absent at runtime |

### Content and messaging (6)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `get_message_compass` | 1 | REAL | 3,556 chars live |
| `search_claims_register` | 1 | REAL | 91 approved claims live |
| `list_writing_rules` | 1 | REAL | Read only — she cannot propose rule changes, by design |
| `list_content_assets` | 1 | PARTLY | 5 rows, several CI fixtures. Only writer is one REST route |
| `link_content_asset` | 3 | REAL | Absent at runtime |
| `assemble_brief` | 3 | **HOLLOW** | Persists whatever it is handed; real assembler never invoked. Absent at runtime |

### Approvals and drafts (2)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `submit_draft_for_review` | 3 | **PARTLY / stub-backed** | Approval row real; external submit is an in-memory stub unless Writing Studio env is set. Absent at runtime |
| `decide_approval` | 3 | REAL | Absent at runtime |

### Research (1)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `dispatch_research` | 1 | REAL | Enqueue-only, three truthful statuses, **no false success** — rebuilt after the five-week Argus failure |

### District and people lookup (3)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `get_district_contacts` | 1 | PARTLY | Real query against `district_contacts` — **which holds 7 rows.** Honest on empty. The real contact answer comes from `check_salesforce_activity` |
| `resolve_person` | 1 | REAL | Internal Amira staff only; never merged with district contacts |
| `read_web_page` | 1 | REAL | SSRF-guarded, returns text labelled untrusted |

### Slack and outbound (6)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `list_slack_channels` / `read_slack_channel` | 2 | PARTLY | Use the **most recently connected** Slack integration row, not Callie's — compare `post_analyst_message`, which selects `agent_id == "callie"` explicitly |
| `send_guarded_dm` | 2, identity-gated | REAL | Both ends allowlisted from the inbound Slack id; every attempt logged. Failure path refuses to claim delivery |
| `send_slack_message` / `react_to_slack_message` / `post_analyst_message` | 3 | REAL | Absent at runtime |

### Screen-time (2)
| `get_screentime_report` (1) / `record_screentime_feedback` (2) | REAL |

### Memory and admin (3)
| Tool | Layer | Status | Note |
|---|---|---|---|
| `query_memory` | 1 | REAL | Scope-gated to Callie's allowance — the M3 control. Must not be swapped for the ungated variant |
| `write_memory` | 2 | REAL | |
| `import_target_accounts` | 2, identity-gated | REAL | Replaces Josh's target list from a posted spreadsheet |

### Stubs, called out explicitly

- **`fire_scout` is the one tool whose card lies.** Its implementation is exemplary — it
  refuses, explains that scouts run as subprocesses of the long-lived app and a task started
  in the per-turn MCP subprocess would be killed mid-flight, and returns
  `{"status": "not_started"}` with the schedule. But the description the model reads is
  `"Trigger a scout run immediately."` The model will call it believing it fires a scout.
  Fix the description, not the code.
- **`assemble_brief`** — hollow success path, as above.
- **`submit_draft_for_review`** — stub-backed unless Writing Studio env is configured.
- **`artemis/tools/starbridge.py` is stubbed but Callie cannot reach it.** Verified: her
  registry is an early return that never falls through, and the starbridge stubs live in the
  separate `artemis.tools.registry` served to pipeline agents. `get_district_contacts` imports
  `artemis.tools.district_resolve`, which *registers* those stubs as a side effect of package
  import — registration into the other registry, never exposed to her. Same for the
  `memory_layer`, `linkedin_scraper`, `unresolved_signals` and `legiscan` stubs.
- Indirectly: 3 of the 9 scout sources feeding `signal_queue` (`regional_news`,
  `linkedin_observer`, `starbridge`) are null adapters producing nothing. Her signal tools are
  real; part of their input is not.

---

## What she cannot do

As prominent as what she can. Every line here was checked, not assumed.

**On Josh's five items**

- **Name the licensed schools or sites in a district.** No tool queries the account hierarchy.
  The data is there; nothing asks for it.
- **Report seat or licence counts, anywhere.** The fields exist and are empty — 1 of 5,631
  site-level accounts carries a Suite licence count. Do not promise this even after item 2 is
  built.
- **Report a licence expiry or renewal date at site level.** `Expiration_Date__c` was null on
  every row probed.
- **Name a single opportunity, past or open.** She reports an integer for open deals and
  nothing for closed ones.
- **Give any pipeline figure scoped to one district.** `salesforce_pipeline` is portfolio-wide
  by design.
- **Assemble a durable campaign brief.** The tool is hollow and absent at runtime.

**On Salesforce generally** (from the 2026-09-04 capability audit, still current)

- **No `Task`, `Event` or `OpenActivity`** — all three return 400 for our run-as user. Not one
  email, call log, meeting or activity record. "Last touched" comes from
  `Contact.LastActivityDate` and nothing finer — **and that field is a due date**, so some of
  those dates are meetings that have not happened yet (see item 4).
- **No `Lead`.** No `Campaign` or `CampaignMember`, so no campaign attribution: she cannot say
  which contacts a campaign touched or which opportunities it influenced.
- **No `OpportunityLineItem`** — the products on a deal are invisible; only the total amount.
- **No `Contract`, `Order`, `Asset`, `Case`.**
- **No `UserRole`/`Profile`**, so no org-chart or territory reasoning from CRM data.
- **She cannot write to Salesforce.** Structurally — the client exposes only `describe_sobject`
  and `query`, both GET, and a test asserts that method set.

**On Gong** (CLAUDE.md rule 4, enforced in code, not prompt)

- **Never what anyone said.** No transcript, quote or paraphrase, ever. The client has no
  transcript method.
- **Never anything about a rep.** No performance view, no ranking, no "who was on the call".
  Aggregation is by account only.
- **Only linked calls.** Imported calls from the previous vendor carry no account link;
  "no calls" always means "no *linked* call".

**On her own data**

- `district_contacts` holds **7 rows**. `content_assets` holds **5**, some of them CI fixtures.
  Tools reading these are real and near-empty, and say so rather than guessing.
- **She cannot fire a scout**, whatever `fire_scout`'s description claims.
- **She cannot change house writing rules or OKR rows** — deliberate, both are owner-judgment
  surfaces.
- No Gmail, Google Calendar, Granola transcripts, Jira, builder tools, system tools or file
  reading. All deliberate.

**Two stale statements found in passing** (not fixed here — read-only audit)

- `docs/FUNCTIONALITY-MAP.md` heads Callie's section "Her 39 tools". The table below it lists
  40 correctly; only the count is stale.
- `pipeline_intel.NOT_COVERED` tells the model Salesforce records no loss reasons — "whether
  Salesforce records loss reasons (it does not)" — while `loss_reasons()` in the same file
  reads `Reason__c`, populated on ~28,600 closed-lost opportunities. The escape-hatch text
  contradicts the working tool three functions above it.

---

## How to regenerate

The tool list:

```bash
cd /Users/artemis/Artemis/artemis-os
PYTHONPATH=. uv run python -c "
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
r = build_authorized_tool_registry(set(), agent_id='callie')
for e in r.all_entries(): print(e.tool.name, '| layer', e.layer)
"
```

Which of those survive to runtime (the layer > 2 filter):

```bash
PYTHONPATH=. uv run python -c "
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
r = build_authorized_tool_registry(set(), agent_id='callie')
print(sorted(e.tool.name for e in r.all_entries() if e.layer <= 2))
"
```

Which provider path she is actually on (decides whether that filter applies):

```sql
SELECT provider, count(*) FROM agent_traces
WHERE agent_id = 'callie' AND created_at > now() - interval '7 days'
GROUP BY provider;
```

The licence-field population figures, live and read-only:

```bash
PYTHONPATH=. uv run python -c "
import asyncio, artemis.db as db
async def main():
    from artemis.marketing.salesforce_suppression import _get_client
    async with db.SessionLocal() as s: c = await _get_client(s)
    for f in ['Assessment','Suite','Teacher','Practice','Dyslexia']:
        r = await c.query(f'SELECT COUNT(Id) n FROM Account WHERE {f}_Licenses_Scoreboard__c > 0')
        print(f, r[0]['n'])
asyncio.run(main())
"
```

Related, and authoritative where they overlap:

- `docs/salesforce-capability-map.md` — audited 2026-09-04. Field-level truth for the org,
  including the "What we cannot read" section this document leans on.
- `docs/gong-capability-map.md` — tracker definitions and coverage.
- `docs/FUNCTIONALITY-MAP.md` — the full per-agent tool tables (note the stale "39").
