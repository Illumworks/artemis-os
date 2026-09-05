# Salesforce capability map

**Audited 2026-09-04** against the live org, read-only, using the credentials the app itself
uses. Every call behind this document was a `GET` (SOQL `SELECT` or `describe`). Nothing was
written.

This is a map of what **our connection** can read, which is not the same as what is in
Salesforce. The org holds objects our integration user cannot see at all, and the difference
matters more than the inventory of what works. Read the "What we cannot read" section before
you plan anything that depends on CRM data.

---

## The connection

| | |
|---|---|
| Instance | `https://istation.my.salesforce.com` |
| Org id | `00D400000007wOFEAY` |
| Integration user | `artemis.integration@amiralearning.com` ("Artemis Integration", owner contact neil.martin@) |
| Auth | OAuth 2.0 Client Credentials, no refresh token, fresh token per call |
| Client | `artemis/integrations/salesforce/client.py`, pinned to REST `v59.0` |
| Org's latest advertised version | `v67.0` (Summer '26) |

The client is structurally read-only. It exposes exactly `describe_sobject`, `query` and
`query_all`, all GET, and a test asserts that public method set so nobody can quietly add a
write path. The only POST it ever issues is the token exchange.

**This is a shared Amira / Istation org.** That single fact causes more analytical mistakes
than anything else in this document. The company merged, the CRM did not split, and a large
majority of the rows here are Istation's. Any count you take without an Amira filter is a
portfolio-wide count. See "Telling Amira from Istation" below.

### API version note

The audit ran on `v60.0`; the app runs on `v59.0`. Findings are the same on both. `v60.0`
reports 324 queryable objects against `v59.0`'s 319, and one extra field on Account (513 vs
512). Every object reported inaccessible below is inaccessible on both versions, so none of
the blind spots are an artifact of the pinned version. Moving to a newer version would not
recover them.

---

## The short version

We have deep, historical read access to the **account, contact and opportunity core**, going
back to 2008. We have **no access whatsoever** to leads, activities, campaigns, cases, email,
or products-on-opportunities. In practice that means we can analyse the pipeline and the
customer base in detail, and we cannot see a single call, meeting, email, task, campaign
membership or support ticket.

The most useful surfaces are Opportunity (106,072 rows with 460 fields, including a real
loss-reason picklist), the two history objects (`OpportunityHistory` for stage snapshots and
`OpportunityFieldHistory` for field-level changes, together 1.58M rows back to 2008, which is
enough to compute stage velocity and slippage properly), and Account (168,552 rows, 513
fields, including Amira-specific customer status and license counts).

---

## What we cannot read

This section is deliberately first. A capability map that lists only successes will get
someone to promise a feature we cannot build.

Each of these returns HTTP **404 on describe** and **`INVALID_TYPE` on SOQL**, with the
message "sObject type 'X' is not supported." That is Salesforce saying the integration user's
profile grants no access at all, so the object does not even appear in the global sobject
listing. It is not a permissions nuance we can work around with a better query. It needs a
profile change in Salesforce.

| Object | What we lose |
|---|---|
| `Lead` | The entire pre-conversion funnel. No MQL/SQL analysis, no lead-to-opportunity conversion rate, no lead source attribution at the lead level. |
| `LeadHistory` | Lead status change history. |
| `Task` | **Every logged call, email and to-do.** This is the single biggest gap. No activity counts, no "last touched" from the system of record, no rep-effort analysis. |
| `Event` | Every meeting and calendar entry. No meeting counts, no demo tracking. |
| `EventRelation` / `TaskRelation` | Who attended or was involved in an activity. |
| `ActivityHistory` / `OpenActivity` | The aggregate activity rollups that would have partly substituted for Task and Event. |
| `Campaign` | All campaign structure. No campaign ROI, no cost, no campaign hierarchy. |
| `CampaignMember` | **Campaign attribution.** We cannot tell which contacts were touched by which campaign, or which opportunities a campaign influenced. |
| `Case` / `CaseHistory` | All support tickets and their history. No churn-risk signal from support volume. |
| `EmailMessage` / `EmailStatus` | Email bodies, send/open/click status. |
| `OpportunityLineItem` | **Which products are on which deal.** We can see the product catalogue and a deal's total amount, but not the line items that make it up. |
| `Pricebook2` / `PricebookEntry` | Price books and list prices. |
| `Quote` | Quotes. (Salesforce CPQ `SBQQ__` fields exist on Account and Opportunity, but the CPQ objects themselves are unreachable.) |
| `Contract` | Contracts. |
| `Order`, `Asset`, `Solution`, `Idea` | Standard objects, all closed to us. |
| `Territory2`, `Forecast` | Territory management and forecasting objects. `ForecastingFact` is readable but empty. |
| `Individual`, `ContactPointEmail` | Consent and privacy objects. |
| `UserRole`, `Profile` | **The role hierarchy and profile definitions.** We can read `User` but cannot resolve a user's role name or profile, which makes org-chart and territory reasoning from CRM data impossible. |

A second, softer category: objects that describe fine but refuse a plain count.

- `Attachment` and `ProcessInstance` return `OPERATION_TOO_LARGE` ("exceeded 100000 distinct
  ids") on `COUNT(Id)`. They are readable, just not countable in one shot. Filter first.
- `ContentDocumentLink` requires a filter on a single `ContentDocumentId` or `LinkedEntityId`.
  You cannot enumerate it. This means you cannot ask "which files are attached to accounts"
  in general, only "which files are attached to *this* account".
- `FeedItem` rejects `COUNT(Id)` entirely (Salesforce does not allow the aggregate on that
  field), and `FeedComment` refuses direct querying for non-admin users.
- `TopicAssignment`, `Announcement` and `OauthToken` similarly reject unfiltered aggregation.

---

## Object inventory

The org exposes 390 sobjects, of which **324 are queryable** for us. The overwhelming majority
are Salesforce platform and setup metadata (`FlowOrchestration*`, `LightningUsage*`,
`OmniSupervisorConfig*`, `NetworkMember*`, `Presence*`, `WorkBadge*`, and so on) with no
business meaning. 66 are not queryable, and almost all of those are change-data-capture
platform events (`AccountChangeEvent`, `ContactChangeEvent`, ...) which are streaming
notifications rather than stored data, so their absence costs us nothing.

**The org has essentially no custom objects.** Only two queryable objects are custom:
`Gong__Gong_Custom_Settings__c` (a managed-package settings holder, 0 rows) and
`Opportunity__hd` (Salesforce's built-in historical-trending shadow of Opportunity). All the
customisation in this org lives in *fields* on the standard objects, not in new objects. That
is a genuinely useful thing to know: there is no hidden custom data model to discover.

### Business-relevant objects, readable

| Object | Rows | Fields | Notes |
|---|---:|---:|---|
| `Account` | 168,552 | 513 | The core. 456 custom fields. |
| `Contact` | 792,871 | 174 | 110 custom fields. 706,374 have an email. |
| `Opportunity` | 106,072 | 460 | 418 custom fields. |
| `OpportunityHistory` | 663,618 | 14 | Stage/amount/close-date snapshots, from 2008-06-09. |
| `OpportunityFieldHistory` | 918,785 | 9 | Field-level change log, from 2008-07-10. |
| `Opportunity__hd` | 145,009 | 22 | Historical trending snapshots. |
| `AccountHistory` | 7,606,589 | 9 | From 2008-07-09. |
| `ContactHistory` | 3,436,875 | 9 | From 2008-07-08. |
| `AccountContactRelation` | 1,156,189 | 13 | The many-to-many contact/account map. |
| `AccountTeamMember` | 245,023 | 16 | |
| `OpportunityTeamMember` | 134,143 | 14 | |
| `OpportunityTeamMemberHistory` | 64,209 | 9 | |
| `OpportunitySplit` | 119,878 | 15 | Credit splits. |
| `OpportunitySplitHistory` | 42,176 | 9 | |
| `OpportunityContactRole` | 55,545 | 12 | **The only contact-to-deal link we have.** |
| `AccountContactRole` | 2,069 | 11 | Legacy, largely superseded by AccountContactRelation. |
| `OpportunityRelatedDeleteLog` | 93,688 | 14 | |
| `Product2` | 733 | 140 | The catalogue. Note: unlinkable to deals, see OpportunityLineItem above. |
| `Product2History` | 667 | 9 | |
| `User` | 766 | 206 | 194 active. Role and profile names are **not** resolvable. |
| `OpportunityStage` | 64 | 16 | Authoritative open/closed/won mapping. |
| `RecordType` | 125 | 13 | |
| `Note` | 16,388 | 12 | Classic notes. |
| `ContentDocument` | 331 | 24 | Files. Mostly PNG (225) and PDF (35). |
| `ContentVersion` | 338 | 43 | |
| `Group` / `GroupMember` | 1,656 / 379 | | Public groups and queues. |
| `OpportunityShare` | 882,870 | 8 | Sharing rows, not business data. |
| `AccountShare` | 2,338,328 | 11 | Sharing rows, not business data. |
| `Conversation` | 11,321 | 11 | Web-chat sessions. See the Gong section. |
| `ConversationEntry` | 163,883 | 28 | Chat turns. Message bodies read as null. |
| `ConversationParticipant` | 47,106 | 17 | |
| `AgentWork` | 25,007 | 42 | Omni-channel work assignment records. |
| `VoiceCallMetrics` | 829 | 31 | Daily Service Cloud Voice aggregates, not calls. |

### Business-relevant objects that are readable but empty

Worth stating explicitly, because "the object exists" reads as "the data exists" to anyone
skimming: `OpportunityCompetitor` (0), `OpportunityPartner` (0), `VoiceCall` (0),
`CallCoachingMediaProvider` (0), `Gong__Gong_Custom_Settings__c` (0), `ForecastingFact` (0),
`ContactShare` (0), `SocialPersona` (0), `ExternalSocialAccount` (0), `Recommendation` (0),
`PipelineInspectionListView` (0), `Holiday` (0).

`OpportunityCompetitor` being empty is the notable one. Salesforce's dedicated competitor
object is unused; competitor data lives in picklist fields on Opportunity instead
(`Competitors__c`, populated on 9,635 deals). Do not build against `OpportunityCompetitor`.

`AccountPartner` and `Partner` have 4 rows each, which is effectively empty. Channel/reseller
structure is not modelled there; it is carried on Opportunity record types
(`Reseller New`, `Reseller Renewal`, `Reseller Expansion`) and on
`Account.Amira_Channel_Partner__c`.

---

## Fields on the three core objects

Account has 456 custom fields, Contact 110, Opportunity 418. Listing all of them here would
be noise. What follows is the shape of each, the fields that carry business meaning, and the
traps.

### Managed packages present

The custom fields are not all ours. Namespace prefixes tell you who owns what, and a
prefixed field is only as alive as that vendor's integration.

| Namespace | Account | Contact | Opportunity | What it is |
|---|---:|---:|---:|---|
| *(native, no prefix)* | 363 | 62 | 378 | Ours and Istation's |
| `agileed` | 22 | 13 | 0 | ConnectLink (education data) |
| `fferpcore` / `ffbf` / `ffex` / `ffaci` | 27 | 0 | 0 | FinancialForce ERP |
| `DaScoopComposer` | 13 | 12 | 1 | Groove (outreach) |
| `SBQQ` | 11 | 0 | 6 | Salesforce CPQ (objects unreachable) |
| `AVA_SFCPQ` / `AVA_SFCORE` / `AVA_MAPPER` | 14 | 4 | 18 | Avalara tax |
| `SalesLoft1` | 2 | 4 | 3 | SalesLoft (outreach) |
| `Gong` | 1 | 14 | 2 | Gong |
| `HubSpot_Inc` | 1 | 1 | 0 | HubSpot |
| `Geo_Location` | 2 | 0 | 0 | |
| `DashboardsGSP` / `GSP_LeadDash` | 0 | 0 | 10 | |

### Account: the fields that matter

**Customer status.** This is the one to get right, and the obvious-looking field is the wrong
one.

- `Customer_Status__c` (picklist, label "Customer Status (AML)") is the **Amira** customer
  field and the one `artemis/config.py` is configured to use. Current distribution: `Child`
  5,631, `Customer` 4,932, `Loss` 871, `Parent` 144, `Pilot` 88, `Write Off` 39, and 156,847
  null. The configured truthy set (`Customer,Child,Parent,Pilot`) therefore matches 10,795
  accounts. It is a real picklist, filterable and groupable, and it is trustworthy.
- `Is_Customer__c` (boolean formula) currently returns true for 10,208 accounts and is
  **wrong for Amira purposes**: in a shared org it means "customer of anything in the
  portfolio", Istation included. The config comment records that using it would have
  suppressed roughly 6,000 genuine Amira prospects as existing customers. It filters and
  groups correctly, so nothing will fail loudly. It will just be quietly wrong.
- `Amira_Customer_Status__c` looks like the right field and is not. It is free text, and the
  live data carries typos ("Cusotmer", "Cusomter") plus junk values. Do not use it.
- Product-line variants exist and are trustworthy booleans: `Is_Customer_Reading__c` (5,283),
  `Is_Customer_History__c` (4,318), `Is_Customer_Math__c` (469), `Is_Customer_Spanish__c`
  (332), `Is_Customer_State__c` (855), plus `Is_Customer_Reading_Assess__c`,
  `Is_Customer_Reading_Instruct__c`, `Is_Customer_Reading_Tutor__c`.
- `Active_Customer__c` (20,618) is broader again and portfolio-wide.

**Segmentation and firmographics.** `Enrollment__c` and `Enrollment_in_District__c`,
`NCES_ID__c`, `State_ID__c`, `domain__c` / `Email_Domain__c` / `Customer_Domain_AML__c`,
`Type` (a real picklist: Public School 92,781, Private School 15,119, District 14,181,
Catholic School 6,344, State School 3,685, Vendor 2,901, Charter School 1,827, plus 25,254
null), `Marketing_Tier__c`, `Customer_Segmentation__c`, `Success_Region__c`,
`Sales_Region__c`, `Region_Area__c`, `Dialect_Region__c`, `State_Controlled__c`.

**Amira commercial state (the `(AML)` suffix marks these as ours).**
`Assessment_Licenses_Scoreboard__c`, `Suite_Licenses_Scoreboard__c`,
`Teacher_Licenses_Scoreboard__c`, `Practice_Licenses_Scoreboard__c`,
`Dyslexia_Licenses_Scoreboard__c`, `Customer_Since__c`, `Expiration_Date__c`,
`Renewal_Status__c`, `Renewals_Specialist__c`, `Customer_Success__c` (Success Manager),
`CZ_Health_Score__c`, `Amira_Channel_Partner__c`, `District_Implementation_Meetings__c`.

**Intent signals**, apparently from a Qualified.com-style tool: `q_Score__c`,
`q_Trend__c`, `q_Condition__c`, `q_Visitor_Count__c`, `q_Meetings_Booked__c`,
`Signals_Research_State__c`, `Signals_Research_Score__c`. `q_Score__c` is the second
most-changed field in `AccountHistory` (1,225,462 changes), so it is actively written.

**BDR territory claims.** `BDR_Target_Claim_Date__c`, `BDR_Target_Products__c`,
`BDR_Targeting__c`, plus the derived `BDR_Claim_Expired__c`, `BDR_Expiration_Date__c`,
`Targeting_BDR_is_Active__c`.

**Revenue rollups.** `ARR_Reading__c`, `ARR_Math__c`, `ARR_Spanish__c`, `ARR_History__c`,
`All_District_ARR__c`, `Total_Account_Renewal_Amount_CY__c`, `YTD_Sales__c`, `PY_Sales__c`,
`PY2_Sales__c`. All currency, all non-groupable (see below).

**Dead fields.** `NPS_Score__c` is null on all 168,552 accounts and `NPS_Count__c` is 0 on all
of them. The NPS rollup exists and has never been populated. Do not promise NPS analysis.

### Contact: the fields that matter

`Title` is populated on 741,741 of 792,871 contacts, which is unusually good coverage and
makes title-based persona work viable. `Primary_K12_Role__c` (1,753) and `Contact_roles__c`
(1,822), which look like the structured version of the same thing, are almost entirely empty,
so title parsing is the only route.

Suppression-relevant, and all trustworthy real fields: `HasOptedOutOfEmail` (43,705 true),
`IsEmailBounced` (1,953 true), `Email` (706,374 populated), `DoNotCall` (**0 true**, the field
is simply unused, so do not treat it as a signal).

Marketing state: `Latest_Source__c` (537,730 populated), `Latest_Source_Drill_Down_1__c`,
`Latest_Source_Drilldown_2__c`, `MQL_Source__c` (6,287), `Lead_Score__c` (537,073),
`Master_Hubspot_ID__c` (291,309), `HubSpot_Record_ID__c` (82, effectively dead),
`Product_Interest__c` (multipicklist), `Marketing_Notes__c`. `Lead_Status__c` is populated on
**0** contacts.

Champion/advocacy flags: `Ambassador__c`, `Amira_Certified__c`, `Champion_SY25_26__c`,
`X24_25_Champion__c`, `X23_24_Champions__c`.

**Three generations of outreach tooling coexist on Contact**, and only one is live:

| Tool | Fields | Coverage | Last activity |
|---|---|---:|---|
| Groove (`DaScoopComposer__`, `Groove_*`) | 12 + 18 | `Groove_Last_Touch__c` on 243,806 | **2025-10-15, dead** |
| SalesLoft (`SalesLoft1__`) | 4 | cadence name on 18,799 | stale |
| Gong Engage (`Gong__`) | 14 | 8,169 ever enrolled | **2026-09-03, live** |

`Days_since_latest_touch__c` is a formula over the **Groove** fields, so it has been frozen
since October 2025. It reads as a live recency metric and is not one. Use the Gong Engage
fields for current outreach state.

### Opportunity: the fields that matter

Beyond the standard `Amount` (82,091 deals with a positive amount), `StageName`, `CloseDate`,
`IsClosed`, `IsWon`, `ForecastCategoryName`, `NextStep` (732), `Description` (long text):

**Revenue decomposition**, all formula, all non-groupable: `Annualized_Recurring_Revenue__c`
(48,093 deals > 0), `Recurring_Subscription_Revenue__c`, `Non_Subscription_Revenue__c`,
`Renewal_ARR__c`, `Renewing_ARR__c`, `Upsell_Revenue__c`, `Downsell_Revenue__c`,
`GRR_Renewing_Revenue__c`, `GRR_Percentage__c`, per-product `ARR_Reading__c` / `ARR_Math__c` /
`ARR_Spanish__c` / `ARR_History__c` and the `PORR_*` equivalents.

**Term and renewal chain:** `Term_months__c`, `Term_years__c`, `Prior_Contract_End_Date__c`,
`Projected_Renewal_Date__c`, `Parent_Opportunity_Id__c`, `Parent_Opp_Start_Date__c`,
`Parent_Opp_End_Date__c`, `Prior_Opp_Amount__c`, `Renewal_Opportunity__c`. The parent/prior
links make renewal-cohort analysis possible without OpportunityLineItem.

**Loss and competition:** covered in its own section below.

**Contacts on the deal:** `Primary_Implementation_Contact_Email__c`,
`District_Data_Contact_Email__c`, `District_Technology_Contact_Email__c`,
`Training_Contact_Email__c`, all formula pull-throughs.

---

## Formula fields, and which ones you can trust

137 of Opportunity's fields, 90 of Account's and 15 of Contact's are formula fields
(`calculated: true`). They are not uniformly untrustworthy, and the real rule is narrower and
more useful than "avoid formulas".

**`groupable` is honest. `filterable` is not.**

Every single formula field on all three objects reports `filterable: true` in its describe.
That flag means nothing here: Salesforce will accept your `WHERE` clause and return a
confidently wrong answer rather than an error. By contrast `groupable: false` is accurate and
enforced: 54 of Account's formula fields, 114 of Opportunity's and 13 of Contact's are
non-groupable, and a `GROUP BY` on one fails loudly with "field 'X' can not be grouped in a
query call". A loud failure is the good case. Design around the group-by restriction, and
never assume a formula filter is sound because the metadata said it was filterable.

**Most formula filters do in fact work.** Tested on live data:

| Field | Test | Result | Verdict |
|---|---|---|---|
| `Open_Opportunity_Count__c` | `> 0` / `> 3` / `= 0` | 2,569 / 140 / 165,983 | Trustworthy. The two partitions sum to exactly 168,552. |
| `Days_Since_Last_Activity_Qualified__c` | `< 30` | 1,911 | Trustworthy, plausible. |
| `Region__c` | `= 'West'` / `!= null` | 92 / 89,476 | Filters correctly, but the *data* is junk: values include "Regional Manager", "West Partnership Mgr", "Territory Mgr". The field is misnamed, not broken. |
| `Is_Customer__c` | `= true` group by | 10,208 / 158,344 | Trustworthy (boolean formulas are groupable and behave). |
| `Active_Customer__c`, `Strategic_Account__c` | `= true` | 20,618 / 11,086 | Trustworthy. |
| `Annualized_Recurring_Revenue__c` | `> 0` | 48,093 | Trustworthy. |
| `NPS_Score__c` | `> 0` / `!= null` | 0 / 0 | Filter is fine; the field has never been populated. |

**The one genuinely poisonous field, and it is worse than reported.**

`Gong__Gong_Count__c` (on both Account and Opportunity, `double`, formula) behaves exactly as
warned: `> 0` returns every row (168,552), `= 0` returns none, and `GROUP BY` errors. But the
cause is not a broken filter. Reading the describe metadata gives the answer:

```
Gong__Gong_Count__c  calculatedFormula = '1'
```

**The formula is the literal constant `1`.** Confirmed against the data:
`SELECT MAX(...), MIN(...) FROM Account` returns max 1.0 and min 1.0, and the sum is exactly
168,552, one per row. Every filter result is internally consistent (`>0` all, `>=1` all,
`=1` all, `=2` none, `<1` none) because the value really is 1 everywhere.

So the correction worth recording: this field is not unreliable, it is **empty of
information**. It is a placeholder the Gong package shipped without wiring up. No amount of
careful querying will extract a call count from it, because there is no call count in it.
Treat any Gong-call metric derived from this field as fabricated.

**Long text areas are a separate trap.** `textarea` fields with a length over 255 report
`filterable: false`, `sortable: false`, `groupable: false`, and SOQL rejects them in a `WHERE`
clause outright with `INVALID_FIELD`. On Opportunity this covers `Closed_Lost_Notes__c`,
`Competition__c`, `Reversal_Reason__c`, `Reason_for_Delay__c` and `Description`. They can be
**selected** normally, and they do contain real content (in a sample of recent Closed Lost
deals, `Closed_Lost_Notes__c` was populated on every row and `Description` on 183 of 250).
You just cannot filter, sort, group or count on them. Pull them and process in Python.

One mechanical consequence: **a query that selects a long text area is capped at 250 rows per
batch** regardless of your `LIMIT`. A `LIMIT 2000` returned 250 records. Use `query_all` and
follow `nextRecordsUrl`, or you will silently analyse the first 250 rows and call it the
population.

---

## Gong

Plainly: **we cannot read a single Gong call, transcript, recording or conversation.** There
is no Gong call object, no transcript object, and no field anywhere in the org that holds
conversation content. A scan of the fields of all 324 queryable objects found Gong fields on
exactly four objects, 22 fields in total.

### Every Gong field in the org

**`Contact`, 14 fields. These are Gong ENGAGE, not Gong conversation intelligence.** Engage is
Gong's outreach-sequencing product, the SalesLoft/Outreach competitor. These fields describe
which email sequence a contact is sitting in. They say nothing about calls.

| Field | Type | Meaning |
|---|---|---|
| `Gong__Actively_Being_in_a_Flow__c` | boolean | Currently in an Engage flow |
| `Gong__Flow_Status__c` | picklist | Flow outcome |
| `Gong__Current_Flow_Name__c` | string | Flow name |
| `Gong__Current_Flow_ID__c` | string | Flow id |
| `Gong__Active_Engage_Flow_Names__c` | textarea | All active flow names |
| `Gong__Number_of_Active_Engage_Flows__c` | double | Count of active flows |
| `Gong__Added_to_Flow_Date__c` | date | Date enrolled |
| `Gong__Current_Flow_Step_Number__c` | string | Step number |
| `Gong__Current_Flow_Step_Type__c` | string | Step type |
| `Gong__Current_Flow_Task_Due_Date__c` | date | Next step due |
| `Gong__Current_Flow_User_Name__c` | string | Who enrolled them |
| `Gong__Engage_Flow_Owner__c` | string | Flow owner |
| `Gong__Engage_Last_Step_Completed_Date__c` | date | Last step completed |
| `Gong__Flow_Execution_ID__c` | string | Step attempt id |

None of these are formula fields, all are filterable and groupable, and they are trustworthy.

**`Account`, 1 field:** `Gong__Gong_Count__c`. Hardcoded to `1`. Worthless, see above.

**`Opportunity`, 2 fields:** `Gong__Gong_Count__c` (same hardcoded `1`, returns all 106,072
rows for `> 0`) and `Gong__MainCompetitors__c` (string, "Main Competitor(s)"). The competitor
field is a real, non-formula field and would be genuinely valuable, but it is **populated on 0
of 106,072 opportunities.** Gong is not writing it.

**`Gong__Gong_Custom_Settings__c`, 5 fields:** an org-settings object holding trigger on/off
switches (`Gong__Update_Related_Opportunities_Trigger_Off__c` and similar). **0 rows.**

### Gong Engage: what is actually populated

Engage is live and current. The most recent enrolment is 2026-09-03, the day before this
audit.

- 8,169 contacts have ever been added to a flow (1.0% of 792,871 contacts).
- **722 contacts are currently in an active flow.**
- 2,636 contacts were added in the last 90 days.

Flow status distribution across the 8,169:

| Status | Contacts |
|---|---:|
| Finished (no reply) | 4,884 |
| Finished (removed manually) | 1,585 |
| In progress | 722 |
| Bounced | 367 |
| To-do expired | 210 |
| Paused | 190 |
| Flow deleted | 102 |
| Opted Out | 55 |
| Finished (replied) | 38 |
| Finished (meeting booked) | 16 |

That reply rate is worth noting when anyone asks what Engage is producing: 38 replies and 16
booked meetings against 4,884 sequences that finished with no reply.

Flows are run by five people, very unevenly: Ann-Marie Meyn (6,041 contacts), Bonnie Landry
(1,352), Liana Bonilla (758), Kathleen Rief (16), Torey Page (2). Flow names are campaign-
shaped and readable, for example "AA Take 2 - Biliteracy Webinar correction" (784),
"Webinars - 3/24 & 3/25 2026" (711), "ADAM/Stop by our Booth" (623), "Texas/HB 1416" (536).
With `Campaign` and `CampaignMember` unreachable, **`Gong__Current_Flow_Name__c` is the closest
thing to campaign attribution we can read**, for the 1% of contacts it covers.

### Is any call or conversation data reachable at all?

Three candidates, all dead ends.

- **`VoiceCall`** is Salesforce's own call object, with the fields you would want
  (`CallDurationInSeconds`, `CallDisposition`, `IsRecorded`, `TranscribedLanguage`,
  `AgentSentimentScore`, `CustomerSentimentScore`). It has **0 rows.**
- **`VoiceCallMetrics`** has 829 rows, which looks promising until you read the fields:
  `MetricsDate`, `NumSCVInboundCalls`, `NumSCVOutboundCalls`, `AverageSCVCallDuration`. These
  are **daily aggregate counters for Service Cloud Voice**, one row per day, with no link to
  an account, contact or opportunity. Not per-call data.
- **`Conversation` / `ConversationEntry` / `ConversationParticipant`** (11,321 / 163,883 /
  47,106 rows) are **Service Cloud web-chat sessions, not sales calls.** The participant
  `AppType` is `iamessage` (In-App and Web Messaging), roles are Router / EndUser / System /
  Agent / Supervisor, and every one of the 163,883 entries has `EntryType = 'Text'`. These
  are support chats on the Istation side. Volume: 133,711 entries in 2025, 30,172 in 2026.
  Critically, **the `Message` field reads as null through the API**, so even the chat text is
  not available to us. `ActorName` for end users is an anonymous token
  (`v2/iamessage/UNAUTH/...`), so the chats cannot be joined to contacts either.
- No object anywhere in the org matches transcript, recording, meeting or conversation
  insight naming. `CallCoachingMediaProvider` exists with 0 rows.

**Conclusion: if you need Gong call data, it must come from the Gong API directly. Salesforce
does not have it.**

---

## Opportunity stages

`Opportunity.StageName` offers **36 active picklist values**, and `OpportunityStage` (64 rows)
carries the authoritative `IsClosed` / `IsWon` / probability mapping. Always take open/closed
from `IsClosed`, never by string-matching stage names, because the mapping is not intuitive.

Two counterintuitive points:

- **`Credit` is an OPEN stage** despite the 100% probability, and there are 54 opportunities
  sitting in it.
- **`Void` and `Closed Merged` are CLOSED but not won**, which is why "closed lost" depends on
  how you ask. `StageName = 'Closed Lost'` gives 40,146. `IsClosed = true AND IsWon = false`
  gives 42,150, the difference being Void (1,247) and Closed Merged (757). Say which one you
  mean.

Totals: **6,528 open**, 99,544 closed, 57,394 won.

### Open opportunities by stage

| Stage | Open | Probability | Forecast category |
|---|---:|---:|---|
| Renewal Opp Auto-Generated | 3,857 | 0% | Best Case |
| Renewal Health Assessment | 844 | 85% | Pipeline |
| Renewal Created | 341 | 80% | Best Case |
| Renewal Commit | 194 | 95% | Pipeline |
| Target - AE Cold | 185 | 0% | Pipeline |
| Renewal Active Discussion | 166 | 80% | Best Case |
| Target - AE Warm | 142 | 0% | Pipeline |
| Proposal Sent | 141 | 60% | Most Likely |
| Active Discussion | 141 | 40% | Best Case |
| Renewal Proposal | 118 | 90% | Pipeline |
| Commit | 92 | 80% | Pipeline |
| At Risk | 85 | 25% | Omitted |
| Early Start | 55 | 90% | Commit |
| Credit | 54 | 100% | Commit |
| Pilot | 49 | 80% | Best Case |
| SAL and Demo | 30 | 20% | Pipeline |
| Target - BDR | 21 | 0% | Pipeline |
| Planning | 9 | 0% | Omitted |
| Stage 0 | 4 | 0% | Omitted |
| **Total open** | **6,528** | | |

Renewals dominate the open pipeline: 5,520 of 6,528 open deals (85%) are in a Renewal-prefixed
stage, and 3,857 of those are auto-generated placeholders at 0% probability. Anyone reading
"6,528 open opportunities" as active selling motion will be badly wrong. Net of the
auto-generated renewals it is 2,671, and net of all renewal stages it is 1,008.

Closed history: Closed Won 57,394, Closed Lost 40,146, Void 1,247, Closed Merged 757.

**28 of the 64 stage rows are inactive** (`Renewal Quoted`, `Value Proposition`,
`Proposal/Price Quote`, `Closed/Lost`, `Needs Analysis`, `Qualification` and so on) and carry
no current opportunities. They exist because historical rows once used them. If you group
historical opportunities by stage you will meet them.

---

## Loss reasons: yes, definitively

**A loss-reason field exists.** None of the names in the brief are it.

Probed and **absent from the org** (all return `INVALID_FIELD`, "No such column"):
`Loss_Reason__c`, `Reason_Lost__c`, `Closed_Lost_Reason__c`, `Loss_Reason_Detail__c`,
`Lost_Reason__c`, `Reason_for_Loss__c`, `Win_Loss_Reason__c`, `Closed_Reason__c`,
`Loss_Notes__c`, `Competitor__c`, `Primary_Competitor__c`, `Lost_To__c`,
`Lost_to_Competitor__c`, `Churn_Reason__c`, `Non_Renewal_Reason__c`.

**The field is `Opportunity.Reason__c`**, a picklist labelled simply "Reason". It is
filterable, sortable and groupable, and it is populated on **28,710 opportunities** overall
and on 28,647 of the 40,146 Closed Lost deals (71%).

### The trap in `Reason__c`

The describe advertises **14 active picklist values**. The data contains **36 distinct
values**. Twenty-three values present in real rows are not in the active picklist, including
most of the high-volume ones. The active list was clearly rewritten recently and the history
was not migrated.

Values in the data but **not** active in describe: `Timing`, `Undisclosed reason`,
`Poor Qualification`, `Budget Constraints/Price`, `Product Doesn't fit Customer need`,
`Technical Issues`, `New Decision maker`, `Lead not qualified`, `Product Gaps`,
`Amira Deprioritized`, `Customer requested competitive quote`, `Credit`, `Budget concerns`,
`Competitor under evaluation`, `Customer is unresponsive`, `Istation removed from state list`,
`Level 1 - Possible Loss`, `Level 2 - Verbal Trepidation`, `Negative`, `Neutral`, `Promising`,
`Said "NO" - attempting to mitigate`, `Usage is low for expected use case(s)`.

Only one active value has no rows: `Decision Maker Departed`.

**So: do not build a loss-reason report from the describe's picklist.** Query the distinct
values from the data.

### Closed Lost by reason, all time (40,146 deals)

| Reason | Deals |
|---|---:|
| *(blank)* | 11,499 |
| Merged with another Opp | 6,740 |
| Timing | 4,819 |
| Undisclosed reason | 3,621 |
| Competitors | 3,464 |
| Poor Qualification | 3,445 |
| Budget Constraints/Price | 3,240 |
| Product Doesn't fit Customer need | 1,628 |
| Technical Issues | 370 |
| New Decision maker | 261 |
| Lead not qualified | 258 |
| Product Gaps | 213 |
| Other | 148 |
| Amira Deprioritized | 135 |
| Customer requested competitive quote | 66 |
| Timing Misalignment | 60 |
| Insufficient Access to Key Decision Makers | 41 |
| Credit | 32 |
| Compliance or Procurement Issue | 29 |
| School Closed | 25 |
| AI and Solution Fit Not Conclusively Demonstrated | 22 |
| *(8 more, each under 10)* | 30 |

### Closed Lost by reason, last 730 days

The recent picture is different enough to be worth stating separately, and the new taxonomy
is visibly taking hold.

| Reason | Deals |
|---|---:|
| Undisclosed reason | 1,329 |
| Competitors | 713 |
| *(blank)* | 482 |
| Timing | 416 |
| Budget Constraints/Price | 290 |
| Merged with another Opp | 238 |
| Other | 148 |
| Amira Deprioritized | 135 |
| Lead not qualified | 129 |
| Product Gaps | 75 |
| Timing Misalignment | 60 |
| New Decision maker | 46 |
| Insufficient Access to Key Decision Makers | 41 |
| Technical Issues | 39 |
| Compliance or Procurement Issue | 29 |
| AI and Solution Fit Not Conclusively Demonstrated | 22 |
| School Closed | 16 |
| Customer requested competitive quote | 13 |
| Insufficient Proof of Differentiated Impact | 6 |
| Product and GTM Readiness Gap at Time of Evaluation | 5 |
| Insufficient Differentiation vs. Channel Alternatives | 4 |
| Trial or Pilot Success Criteria Not Met | 4 |
| Product Feature Missing | 4 |

Coverage improved: 11% blank in the recent window (482 of 4,244) against 29% all time. But
"Undisclosed reason" is now the largest single category on its own, so blank plus undisclosed
together still leave 43% of recent losses with no usable reason.

### The supporting loss fields

| Field | Type | Populated (all / Closed Lost) | Notes |
|---|---|---|---|
| `Reason__c` | picklist | 28,710 / 28,647 | The primary field. |
| `Closed_Lost_Notes__c` | textarea(32k) | not countable / dense | **Free-text why-we-lost, and it is real.** Populated on every row of a 250-row sample of recent Closed Lost deals. Cannot be filtered, only selected. This is the richest loss signal we have. |
| `Competitors__c` | picklist(37) | 9,635 / 9,041 | Named competitor. Heavily polluted: 6,358 of 9,635 are `Unknown` and 314 are `N/A`. Real signal in the remaining ~2,900: IReady 737, Amplify 434, Renaissance 241, NWEA 192, STAR 152, Imagine Learning 152, Lexia 152, IXL 119, MAP 111, HMH 101, Curriculum Associates 89. |
| `Reason_for_Churn__c` | picklist(13) | 561 / 417 | Renewal-specific. Top values: Amira Competitor 96, Product 87, Not State-Approved 66, Implementation Fidelity 62, No decision-maker relationship 60, Switching Vendors 45. |
| `Reason_for_Churn_Description__c` | text(255) | 564 / 445 | Filterable, unlike the other free-text fields. |
| `Downsell_Reason__c` | picklist(6) | 208 / 52 | |
| `Competition__c` | textarea(32k) | 0 in sample | Present but appears unused. |
| `Displaced_Competitor__c` | picklist(37) | 2 | Effectively unused. |
| `Competitor_Installed__c` | picklist | 2 | Effectively unused. |
| `Competitor_Displaced__c` | picklist | 18 | Effectively unused. |
| `Reversal_Reason__c` | textarea | not filterable | |

The practical recommendation: for loss analysis, pull `Reason__c` for the structured cut and
`Closed_Lost_Notes__c` for the substance. The competitor picklists look like the obvious
structured source and are two-thirds `Unknown`, so any competitor share derived from
`Competitors__c` needs the `Unknown`/`N/A` rows excluded and the resulting sample size stated.

---

## Telling Amira from Istation

There is no single clean flag. The three usable routes, in order of reliability:

1. **`Account.Customer_Status__c`** for customer state. It is the AML (Amira Learning) field.
2. **`Opportunity.RecordType.Name`** for deals. Record types split by motion, not by brand
   (`Direct New` 54,027, `Direct Renewal` 31,988, `Migration` 6,456, `Reseller Renewal` 5,500,
   `Reseller New` 1,754, plus State and Expansion variants). Two record types named `Amira`
   (5,943) and `Amira Renewal` (6) exist but are **inactive**, so they are historical only and
   are not a current Amira filter.
3. **The `(AML)` field-label suffix** on Account. Fields whose describe label ends in "(AML)"
   are Amira-specific: `Customer_Status__c`, `Customer_Domain_AML__c`, the `*_Scoreboard__c`
   license counts, `Renewals_Specialist__c`, `Success_Region__c`, `Customer_Segmentation__c`,
   `State_Reporting__c`, `Future_Owner__c`, `District_Implementation_Meetings__c`.

Anything without one of those is portfolio-wide. The Opportunity open-pipeline breakdown above
(3,460 open Reseller Renewals against 673 open Direct New) reflects the combined business, not
Amira alone.

---

## Query mechanics worth knowing

- **`client.query()` returns only the first page.** It does not follow `nextRecordsUrl`. Use
  `client.query_all()` for anything that could exceed one batch. This has already bitten:
  Amira has over 10,000 customer school accounts across more than 2,000 districts, and both a
  plain query and a `GROUP BY` hit the single-batch ceiling.
- **Aggregate queries cannot page at all.** `GROUP BY` fails with "Aggregate query does not
  support queryMore()" once it exceeds a batch. For high-cardinality groupings, filter down
  first or pull raw rows and aggregate in Python.
- **Selecting a long text area caps the batch at 250 rows** regardless of `LIMIT`.
- **`COUNT(Id)` fails with `OPERATION_TOO_LARGE` above 100,000 distinct ids** on some objects
  (`Attachment`, `ProcessInstance`). Add a filter.
- **SOQL has no field aliases outside aggregates.** `SELECT Foo__c v FROM Account` is accepted
  but the result key is still `Foo__c`, not `v`. This silently produces empty parsing results
  if you key on the alias. Aliases only bind in aggregate queries.
- **`ContentDocumentLink` cannot be enumerated**, only queried by a single
  `ContentDocumentId` or `LinkedEntityId`.
- Date literals like `LAST_N_DAYS:730` and `CALENDAR_YEAR(CreatedDate)` work and are the
  cheapest way to window a large object.

### History objects are the underused asset

`OpportunityHistory` (663,618 rows) holds stage/amount/close-date snapshots from 2008-06-09 to
now, and `OpportunityFieldHistory` (918,785 rows) holds field-level changes from 2008-07-10.
Together they support stage-velocity, slippage and amount-drift analysis that nothing else
here can give, and they are the best substitute available for the missing `Task` data because
a field change is at least evidence a rep touched the record.

Fields tracked in `OpportunityFieldHistory`, by volume: `Amount` (237,834), `Owner` (172,826),
`created` (98,170), `StageName` (84,343), `ForecastCategoryName` (64,742), `locked` (58,369),
`CloseDate` (54,270), `Base_Value__c` (48,444), `Update_Forecast_Category__c` (43,512),
`unlocked` (15,173), `Lead_Qualified_By__c` (13,256), `Accepted_by_Accounting__c` (9,070),
`opportunityCreatedFromLead` (7,878), `Shared_Owner__c` (6,456), `Pilot_End_Date__c` (2,582),
`Date_Time_Qualified__c` (1,728), `Success_Manager_Account__c` (132).

Note what is **not** tracked: no history on `Reason__c`, `Competitors__c` or any of the loss
fields. You can see when a deal moved to Closed Lost but not when or how often the reason was
edited.

`AccountHistory` (7.6M rows) is dominated by `Owner` (3,073,008) and `q_Score__c` (1,225,462),
the latter confirming the intent-signal integration writes continuously.

### Data freshness

The org is live and actively used. In the 30 days to 2026-09-04: 3,014 opportunities and
9,956 accounts modified. In the last 365 days: 6,445 opportunities and 45,192 contacts
created. The most recent `OpportunityHistory` row and the most recent `ContactHistory` row
were both written on the audit date.

---

## How to re-run this audit

From `/Users/artemis/Artemis/artemis-os`, with `PYTHONPATH=. uv run python ...`:

```python
import asyncio, httpx, json
import artemis.db as _db
from artemis.marketing.salesforce_suppression import _get_client

async def main():
    async with _db.SessionLocal() as s:
        client = await _get_client(s)
    H = {"Authorization": f"Bearer {client._access_token}", "Accept": "application/json"}

    # 1. every object the connection can see
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.get(f"{client._base}/services/data/v60.0/sobjects/", headers=H)
    queryable = [o["name"] for o in r.json()["sobjects"] if o["queryable"]]

    # 2. per object: describe (404 == not accessible) and row count
    #    client.describe_sobject(name) raises SalesforceAPIError on 404
    #    client.query("SELECT COUNT(Id) n FROM <obj>") -> [{"n": ...}]

    # 3. formula-field trust check: read calculatedFormula from the describe,
    #    then verify a filter partitions the table (a > 0 / = 0 split must
    #    sum to the total row count). Never trust the `filterable` flag.

asyncio.run(main())
```

`scripts/salesforce_introspect.py` already exists in this repo and covers part of this ground.

Re-run this audit when any of the following happens, because each invalidates a section:

- The integration user's Salesforce profile changes. Everything in "What we cannot read"
  depends on it, and a profile change is the only thing that will recover Task, Event,
  Campaign or Lead.
- Gong's package is reconfigured or a Gong conversation integration is added.
- The `Reason__c` picklist is edited again, since the describe and the data are already out
  of sync by 23 values.
- Anyone proposes building on a field this document flags as empty (`NPS_Score__c`,
  `Gong__MainCompetitors__c`, `Gong__Gong_Count__c`, `Lead_Status__c`, `DoNotCall`,
  `OpportunityCompetitor`) or as frozen (`Days_since_latest_touch__c` and the Groove fields).

---

## Corrections to the assumptions this audit started from

Recorded because they matter more than the confirmations.

1. **`Lead`, `Task`, `Event`, `Campaign`, `CampaignMember`, `Case`, `EmailMessage`,
   `OpportunityLineItem` and `Pricebook2` are not merely restricted, they are entirely
   invisible.** They do not appear in the sobject listing, describe 404s, and SOQL calls them
   unsupported types.
2. **`Gong__Gong_Count__c` is not a formula field with unreliable filtering. Its formula is
   the literal constant `1`.** The strange filter results are arithmetically correct answers
   about a field containing no information.
3. **Formula fields are mostly fine.** The blanket warning is too strong. The precise rules
   are that `groupable: false` is real and enforced, and `filterable: true` is meaningless
   for formula fields and must never be relied on.
4. **The loss-reason field exists and is none of the probed names.** It is `Reason__c`,
   populated on 71% of Closed Lost deals, and its describe picklist omits 23 values that are
   present in the data.
5. **The 14 Gong fields on Contact are Engage, as expected, and they are the org's only live
   outreach signal.** Groove, which covers 30 times more contacts, stopped writing on
   2025-10-15, which also freezes `Days_since_latest_touch__c`.
6. **`OpportunityCompetitor` is readable and empty.** Competitor data is on Opportunity
   picklists instead, and is two-thirds `Unknown`.
7. **The org has almost no custom objects.** All customisation is in fields on standard
   objects, so there is no hidden data model to go looking for.
