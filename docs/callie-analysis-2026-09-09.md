# Callie: what she is, where she breaks, what would make her better

**Written 2026-09-09**, grounded in one live test Jon ran that morning rather than
on a reading of the code. Repo state: commit `fd9a3c9`.

Companion documents, both produced the same day and both narrower than this one:
`callie-capability-vs-josh-workflow.md` (what her tools actually return) and
`salesforce-last-touched-audit.md` (the date field that lied). This document is
the judgement; those two are the evidence.

---

## 1. The test

Jon, in Slack, 11:05:

> Callie what's going on with Pinellas?

She answered in 36 seconds, having called `find_by_keyword`,
`check_salesforce_activity`, `get_signal` and `read_web_page`. Abridged:

> The RFP portal returned 403, so I can't pull the specifics of what's being bid.
>
> **Pinellas County Schools is a current customer.** 5 open opportunities, 25
> contacts on file, nobody in active outreach right now. Kelly Austin (Director
> of Elementary Education) was last touched in November, and Michael Feeney
> (Executive Director of Middle School Education) in March. The contact list is
> active and warm.
>
> **There's an open Instructional Services RFP** (Signal 3645, tagged
> PROCUREMENT_LITERACY_RFP) […] **The broader signal cluster is significant.** New
> Strategic Plan approved for 2026-29, a new CTO appointed, MTSS and dyslexia
> screening in motion, and they adopted Lexia for K-5 ELA intervention (Signal
> 2097, worth knowing, that's a competitor).
>
> The Lexia adoption is the wrinkle. If they're running Lexia for intervention
> and Amira for something else, the RFP could be a renewal, an expansion, or a
> competitive displacement play. The account owner needs to tell you which.
>
> My read: this is an expansion or competitive protection moment, not a cold
> outreach situation. The account team should be in front of this RFP, not
> marketing. Who owns Pinellas in Salesforce?

---

## 2. What she got right, which is the half that matters

I checked every factual claim against the database rather than reading them
sympathetically.

| Claim | Verdict |
|---|---|
| Signal 3645, Instructional Services RFP | **True.** Row exists, created 2026-09-04 |
| Tagged `PROCUREMENT_LITERACY_RFP` | **True**, quoted exactly |
| Strategic Plan approved 2026-29 | **True.** Signal 3428 |
| New CTO appointed | **True.** Signal 2835 |
| MTSS and dyslexia screening | **True.** Signal 2098 |
| Lexia adopted for K-5 ELA, Signal 2097, competitor | **True**, and correctly framed |
| Customer, 5 open opportunities, 25 contacts, 0 in outreach | **True.** Matches the tool byte for byte |
| RFP portal 403 | **True.** She tried, it failed, she said so |

**Nothing was invented.** Not a name, not a number, not a signal id, not a tag.
For an agent whose main risk is confident fabrication, that is the result you
want, and it did not happen by luck — she called four tools and reported what
they returned.

Three things beyond accuracy are worth naming, because they are the hard part and
she did them unprompted:

**She refused to resolve an ambiguity she could not resolve.** The Lexia
adoption genuinely could mean renewal, expansion, or displacement. She named all
three, said the account owner has to settle it, and did not pick the flattering
one.

**She argued against her own lane.** "The account team should be in front of this
RFP, not marketing" is a marketing agent telling the head of marketing this is not
a marketing job. That is the single most valuable sentence in the answer.

**She ended by asking the one question that would unblock the next step.** And
she was right to ask rather than look: there is no `Owner` or `OwnerId` field
anywhere in our Salesforce integration. She could not have looked it up.

This is not the failure mode we have been fighting all week. It is the opposite.

---

## 3. The four failures, none of which were hers

### 3.1 She was handed two dates that had not happened yet

The tool gave her this, top of the contact list:

```
Michael Feeney — Executive Director of Middle School Education — last touched 2027-03-29
Kelly Austin  — Director of Elementary Education             — last touched 2026-11-30
```

Today is 2026-09-09. Feeney's date is nearly seven months in the future. She
reported both as past events, dropped the years, and concluded the list was
"active and warm".

Four things had to line up, and only the last is about her:

1. **`Contact.LastActivityDate` is a DUE date.** Salesforce takes the most recent
   Event's `ActivityDate`, and an Event counts whether or not it has happened. A
   meeting booked for March 2027 becomes "last activity".
2. **We prefixed it with the words "last touched"** and shipped it.
3. **The query sorts `LastActivityDate DESC`.** So the impossible rows sort to the
   **top**. 233 contacts across 184 accounts carry a future date — 0.087% of the
   field, which wildly understates the damage, because on those 184 accounts it is
   a 100% error on the first rows anyone reads. On Pinellas it hit the two most
   senior people on the account.
4. **Her system prompt was 20,273 characters and contained no date at all.** No
   year, no ISO date, nothing. She had no way to know 2027-03-29 was in the
   future.

She then did the reasonable thing with the information available: a field called
"last touched" is by its name a past event, so March must mean last March.

**Fixed today** (`dd772c0`, `fd9a3c9`): the agents now get a clock, and are told
that a future date on a past-tense field is a finding to state, not a detail to
smooth over. The render says a meeting is scheduled and that the field cannot say
when the person was last actually contacted — which is *more* useful than the
sentence it replaced, since a booked meeting with the Executive Director is real
intelligence. The send-suppression path was already blocking correctly (its
comparison is a lower bound, so future dates pass it) but its stated reason was a
false sentence, and that string is what a human reads when they ask why a send
was blocked.

**Not fixed, and it should be flagged to Neil:** `Task`, `Event` and
`OpenActivity` all return 400 for our run-as user, so we cannot read the activity
record itself. The diagnosis above rests on strong inference — of the 233 future
dates, 232 fall Monday to Friday, where wrong-year typos would land uniformly and
predict about 67 weekend dates — plus Salesforce's own documentation. It is not a
direct observation, and the code says so.

### 3.2 She was told, in writing, something false about our own CRM

When no prepared pipeline question fits what someone asked, `pipeline_intel`
hands the model an escape-hatch string. Until today it ended:

> "…and whether Salesforce records loss reasons (it does not)."

It does. `Opportunity.Reason__c`, populated on roughly 28,600 closed-lost
opportunities, and the function twenty lines above reads it.

This one is mine and worth recording precisely, because of *how* it survived. I
established the correct fact four days ago and fixed it where the fact is
**computed**. I missed it where the fact is **asserted**, three hundred lines
below, in the string handed to the model when nothing else fits — so the single
path that fires when Callie is *least* certain what to say was the last one still
telling her the wrong thing. When a fact turns out to be wrong, grep for the
sentence, not the function. Fixed in `fd9a3c9`.

### 3.3 A tool whose description promised what its handler refuses

`fire_scout`'s implementation is exemplary: it explains that a subprocess-spawned
scout would be killed mid-flight, returns `not_started`, and instructs the model
to say plainly that nothing was started. Its description read *"Trigger a scout
run immediately."*

A description is what the model reads when it decides **whether to call**. So it
was promising exactly the capability the handler exists to refuse, and the
correction arrived only after the model had been told the thing was possible.
Fixed in `fd9a3c9`.

### 3.4 The thing she could not say, because it did not exist yet

The most decision-relevant fact about Pinellas was not in her answer:

> **Pinellas is the most concern-heavy account in the entire Gong portfolio.**
> Product feedback on 100% of 10 calls, Customer concerns 90%, Customer
> objections 70% — *and* Amira Solving Problems on 60%.

That changes the read materially. Her conclusion was "expansion or competitive
protection moment". With the call signal it is closer to: *an account with an
active competitor in the building, an open RFP, and the highest concern rate we
have — which is also solving real problems with us.* Same recommendation
(account team, not marketing), considerably more urgency.

She could not have known. The tool that answers it (`district_call_signal`)
landed at 10:27 and the service last restarted at 22:32 the night before. **This
one resolves on the next restart** and needs no further work.

---

## 4. Josh's five items, honestly

His stated priority order, against what she can actually do today.

| # | What Josh asked for | Status | Reality |
|---|---|---|---|
| 1 | Customer status ("at the very least") | **Served** | Reads `Account.Customer_Status__c` across 10,795 accounts. Abstains on ambiguity, fails to UNKNOWN rather than "not a customer" |
| 2 | Site-level licences | **Not served** | No tool reads site data at all |
| 3 | Opportunity history | **Partly** | She gets *one integer* |
| 4 | Prior conversations | **Served**, with a ceiling | Gong metadata, folded into the pre-outreach check |
| 5 | Messaging from all four | **Partly** | The inputs are real; the assembler is not invoked |

Three of those deserve more than a row.

**Item 2 is the cheapest win in this document.** The site *roster* is readable
right now, on the credential she already has: `Customer_Status__c = 'Child'`
under a `ParentId` returns the licensed schools, verified live — 10 named schools
under Ypsilanti in a single query. That is a **missing query, not a missing
permission**. The seat *counts* are genuinely absent: of 5,631 site-level child
accounts, exactly one has a licence count above zero. So build the roster and do
not promise the counts.

**Item 3 is smaller than it sounds.** `count_open_opportunities` runs
`SELECT Id … WHERE IsClosed = false` and returns `len(rows)`. Adding
`StageName, Amount, CloseDate` to that same query and rendering them is close to
free, and turns "5 open opportunities" into the deal history Josh actually asked
for. Closed opportunities are excluded entirely, which is why she can tell you
Pinellas has five open deals but nothing about the ones we lost.

**Item 4's ceiling is worth stating plainly** so nobody over-promises it: only
Gong-*linked* calls are visible, and calls imported from the previous vendor
carry no account link at all. "No linked calls" is not "no contact", and the code
says so on every path.

---

## 5. "More natural" is mostly about cadence, not prose

Her writing is already good. The Pinellas answer is well-structured, appropriately
hedged, and sounds like a colleague. That is not the gap.

Her last 30 days, 66 turns:

| | seconds |
|---|---|
| median | **29.1** |
| 90th percentile | **75.6** |
| slowest | **134.0** |

A median of 29 seconds is a long time to watch a Slack channel do nothing. At the
90th percentile someone has switched context and come back. The Pinellas answer
took 36 seconds and arrived as a finished essay.

A person answering that question would say "let me look" in two seconds and the
findings a minute later. **We have the second half and not the first.** The
highest-value naturalness work is not better prose, it is an immediate
acknowledgement that names what she is about to check, followed by the answer —
which also makes the 75-second p90 socially survivable rather than something to
engineer away.

Related, and cheaper: she answers everything at full length. "Is Pinellas a
customer?" and "what's going on with Pinellas?" get the same nine-paragraph
treatment. Matching length to the question is a prompt change, not a build.

---

## 6. What would actually make her smarter

Ranked by how much each would change an answer, not by how interesting it is.

**1. Context she does not have to be asked for.** The Pinellas answer is the
argument. Jon asked an open question; the Gong signal was the most decision-
relevant fact available and would not have surfaced from any question he thought
to ask. `recent_contact_summary` is already folded into the pre-outreach check for
exactly this reason — because nobody would have thought to ask whether Grosse
Pointe had five calls in Gong. Extend that pattern: a district question should
pull the call signal, the open RFPs and the competitor adoptions **without being
asked**, because the value is in what the person did not know to ask about.

**2. Numbers she cannot explain should be findings, not details.** The whole date
failure is one instance of a general shape: she smoothed over an anomaly instead
of reporting it. "Feeney's last-touch date is in 2027, which can't be right" is a
better sentence than anything she actually wrote, and it is available to any agent
that treats an impossible value as interesting. The prompt now says this
explicitly; whether it holds is the thing to watch on the next few turns.

**3. She can report but she cannot act.** She has 40 tools registered and **30 at
runtime** — `build_floating_artemis_tool_set` deliberately drops layer-3 tools
because the claude-code subprocess path cannot run their operator-confirmation
flow. The ten that vanish are precisely the side-effecting ones:
`approve_signal`, `reject_signal`, `submit_draft_for_review`, `send_slack_message`,
`post_analyst_message`, `assemble_brief` and four more. So signal triage — a
plausible reading of her core job — is unavailable in her actual production path.
This is documented and intentional, not a bug, but the consequence is that "she
should suggest things and act on them" runs into a wall nobody has priced. It is
an architecture decision to revisit deliberately, not a fix to slip in.

**4. Grounding her recommendations in what happened last time.** She has no
memory of whether her previous read on an account proved right. The Gong snapshot
work committed this morning is the first thing in the system that stores a dated
verdict it can be judged against later. That is the seed of a feedback loop and
worth extending before anything more exotic.

---

## 7. Where this lands against Hermes

`NORTH-STAR-vs-agent-frameworks.md` sets the bet: we win on **governance and
domain depth**, and we are behind on **self-improvement**. The Pinellas test is a
useful check on whether that is still the right bet.

Hermes' pitch is that it gets more capable the longer it runs. The failure mode of
that pitch is visible in this test, from the other side: **an agent that learns
from its own operation learns from data like `last touched 2027-03-29`.** Callie
did not fabricate anything — she faithfully propagated a false thing she was
handed, and then a confident conclusion on top of it. A self-improvement loop
running on this stack last week would have learned that Pinellas is warm.

That is the sharpest form of the argument for our approach, and it is also a
warning: **our moat is only real if "I don't know" and "that number is impossible"
are things the system actually produces.** Today, one of those two works. She
said "the RFP portal returned 403, so I can't pull the specifics" — a clean,
unhedged admission of a gap. She did not say "that date is in the future". The
first is built; the second was not, until this morning.

So the honest scorecard from this one test:

- **Doesn't fabricate** — demonstrated, eight for eight on checkable claims.
- **Says when it cannot see something** — demonstrated (the 403).
- **Says when what it can see is wrong** — *was absent*, now attempted in the
  prompt, unproven.
- **Gets more capable as it runs** — not yet, and the snapshot work is the first
  brick.
- **Acts, rather than reporting** — blocked by the layer-3 runtime gap above.

Two of five demonstrated, one newly attempted, two open. That is a real position
rather than a marketing one, and the two open items are architecture rather than
polish.

---

## 8. What I would do next, in order

| | Work | Size | Why this order |
|---|---|---|---|
| 1 | **Restart the service** | minutes | Four fixes and the Gong tool are committed and none are live. The running process is from 22:32 on 08 Sep |
| 2 | Watch the next few district answers | — | The clock fix is a prompt instruction, and CLAUDE.md is explicit that a rule in a prompt is not a gate in code. If she smooths over an impossible date again, the next fix is refusing to render one at all |
| 3 | Site-roster tool (Josh item 2) | small | His #2 priority, one query, credential already in hand. Roster only — never promise seat counts |
| 4 | Opportunity detail (Josh item 3) | small | Three fields on a query that already runs |
| 5 | "Let me look" acknowledgement | small | Halves the *perceived* latency without touching the real 29s median |
| 6 | Auto-context on district questions | medium | The Pinellas Gong signal should not have needed asking for |
| 7 | Decide about layer-3 at runtime | medium, architectural | Whether Callie acts or only reports is a real product decision, and right now it has been made by a subprocess limitation rather than by anyone |

Items 3 and 4 together are perhaps a morning's work and would move Josh from two
of five served to four of five.

---

## 8b. What was actually done, same day

Jon read this document and asked for all of it. Status as of commit `a9d17af`,
with the service restarted so every item below is live.

| | Item | Done | Commit |
|---|---|---|---|
| 1 | Restart | yes — twice, the second after the upgrades | — |
| 2 | Watch the next answers | **outstanding, and it is Jon's to judge** | — |
| 3 | Site roster (Josh #2) | yes — `district_sites`, plus a one-line summary folded into the district brief | `a9d17af` |
| 4 | Opportunity detail (Josh #3) | yes — open/won/lost with amounts, dates and loss reasons | `a9d17af` |
| 5 | Acknowledgement | yes — a reaction on receipt, before the turn starts | `2f4a1c6` |
| 6 | Auto-context on district questions | yes — deal history, site roster and the stored call signal all arrive unasked | `a9d17af` |
| 7 | Layer-3 at runtime | **not done, deliberately** — a product decision, not a fix | — |

Three further bugs surfaced while doing the work, all the same shape as the four
in §3:

- **`gong_lines` was computed at line 316 and one branch returned at 286.** For
  any district Salesforce knows and our own index does not, the conversation
  context was never fetched — silently, on exactly the districts most likely to
  be treated as cold, which is the failure the fold-in was built to prevent.
  Grosse Pointe was that case. Pinellas hit it; Ypsilanti, being in our index,
  did not, which is why nobody saw it.
- **`recent_contact_summary` fetched five calls and reported five as the total.**
  Pinellas has 22 in 180 days, the most recent yesterday, and a renewal sitting
  140 days in stage. A cap wearing a count's clothes.
- **A test sliced `inspect.getsource` by character offset** and broke because a
  comment moved. Replaced with one that makes the Gong call fail and checks the
  Salesforce answer survives.

**What the same question returns now.** Where the tested answer had "5 open
opportunities" and "the contact list is active and warm", the tool now hands her:
a $731,625 renewal closed won five weeks ago and another open for July 2027; 15
closed-lost deals with reasons; two contacts correctly described as having
meetings *scheduled* rather than having been touched; 1 of 169 sites marked, with
a caution that the district does not maintain the marker; 22 linked calls, most
recent yesterday; a renewal 140 days in stage; and the highest concern rate in
the Gong portfolio.

None of that changes her recommendation — the account team should still be in
front of this RFP. It changes how quickly.

## 9. The uncomfortable summary

The agent was the most reliable component in this test.

Every wrong thing in that answer came from something we built and handed her: a
field prefixed with the wrong verb, a sort that floated the bad rows to the top, a
prompt with no clock in it, and a hard-coded sentence asserting the opposite of
what our own code reads. She then reasoned correctly from all of it.

That is a good problem to have, and it points the work somewhere specific: for a
while yet, the returns are in **what we hand her**, not in how she thinks. The one
place where her own behaviour needs to change is the third bullet in §7 — saying
out loud when a number cannot be true. Everything else on this list is our side of
the line.

**How to re-run any of this.** The turn: `select tools_used, latency_ms from
agent_traces where agent_id='callie' order by id desc limit 5;`. Her real tool
list and layers: `build_authorized_tool_registry(set(), agent_id='callie')` and
read `.all_entries()`. The date audit regeneration is in
`salesforce-last-touched-audit.md`.
