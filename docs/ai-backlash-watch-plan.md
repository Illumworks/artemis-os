# Plan — AI-backlash / ed-tech sentiment watch (NM crisis + national early warning)

**Written:** 2026-08-11 by Opus Lead, after auditing what the existing Screen-Time Watch actually
captures versus what this situation needs.

## The situation (Jon, 2026-08-11)

New Mexico is moving **statewide** to remove Amira from schools, with AI as the stated problem;
competitors (iReady et al.) are caught in the same wave. A crisis PR agency is engaged and owns
media relations, social and outreach — **they need nothing from us.** This system is for Amira's
own use.

Two goals, and the second is the durable one:

1. **Immediate:** see everything happening, in NM and everywhere else. **NM is the first domino** —
   the point is to be proactive about the other states, not just track this one.
2. **Long-run:** build the market-strategy and analysis corpus, so the product and positioning get
   better over time. This outlives the crisis.

Today the gap is felt as: *"random coworkers share posts they found — nobody is capturing
everything."*

**Output wanted:** a **daily situational read**, expandable on demand (drill into a particular
news item or bill). Visible to the team.

---

## The finding: we are watching the wrong vocabulary

**Zero NM signals in `screentime_signals` during an active NM crisis.** Not a bug — a design
mismatch.

The per-state news query is:

```
<State> schools ("screen time" OR "device policy" OR "AI policy" OR "artificial intelligence")
```

- **No scout watches any vendor by name.** Not Amira, not iReady, Lexia, Istation or Amplify.
  Verified across the whole scout tree — the only hits are a doc comment and unrelated fixtures.
- **No crisis/procurement vocabulary**: contract, RFP, removal, pause, ban, opt-out, parent
  complaint, board vote to discontinue.
- **The topic gate actively excludes** `literacy`, `science of reading`, `phonics`, `dyslexia` —
  exactly the words an Amira story uses. That exclusion was correct when the mission was
  screen-time *policy* and literacy bills were noise. This crisis is a **brand and procurement**
  story in different language.

A headline like *"New Mexico district drops AI reading program over concerns"* slips straight
through.

## The good news: most of this is already built

| Capability | Status |
|---|---|
| 50-state news collection (`national_news`) | ✅ live, daily |
| National legislative collection (LegiScan) | ✅ live |
| Topic gate, dedup, real-move filter, stance classification, storage | ✅ live |
| **Daily digest** — `post_screentime_digest()` | ✅ **BUILT**: selects unreported real moves, composes a Callie-voiced source-linked digest grouped by stance/state, posts, marks reported so re-runs don't duplicate |
| Report channel | ✅ `ARTEMIS_SCREENTIME_REPORT_CHANNEL=C0BBYM8N26M` already set |
| On-demand drill-down | ✅ Callie's `get_screentime_report` |
| Board-minutes scout | ✅ built, **disabled** (was blocked on a Salesforce customer list) |
| Procurement scouts (Bonfire, USASpending) | ✅ live, but scoped to marketing territory |

**The daily digest is one boolean from running** — `runner.py:253` passes `deliver_alerts=False`,
a deliberate owner decision from when the worry was noise.

⚠ **Sequencing matters: fix sensing BEFORE switching delivery on.** Flipping the flag today
produces a daily digest that cheerfully reports nothing happening while NM burns — worse than
silence, because it looks like coverage.

---

## The work

### Stream A — Crisis sensing (urgent; production; small)

Not a new system — a second **lane** through the existing pipeline.

1. **Vendor lane.** Watch Amira and named competitors (iReady, Lexia, Istation, Amplify, and the
   rest of the set Jon/Josh name) across all 51 states. A brand hit is *always* topic-relevant —
   it must bypass the `literacy`/`phonics` exclusions, which is what would otherwise drop it.
2. **Crisis vocabulary.** contract · RFP · procurement · removal · discontinue · pause · ban ·
   opt-out · parental consent · board vote · petition · "pulled from" · "dropped".
3. **Keep the lanes separate.** Do NOT loosen the existing screen-time policy gate — it is
   precision-tuned and works. Add a parallel gate so each can be tuned without wrecking the other.
4. **Point the existing scouts at this.** Enable the board-minutes scout for NM and the priority
   states (the Salesforce exclusion blocked *peer-validation*, not general board intel), and widen
   the procurement scouts beyond marketing territory for vendor-removal filings.
5. **Capture broadly, report narrowly.** Store everything (lossless — it *is* the market-strategy
   corpus, goal 2). The daily digest already surfaces only `is_real_move` items, so breadth of
   capture does not become noise in the read.

### Stream B — The daily read (small, follows A)

- Turn `deliver_alerts` on for the digest **after** Stream A is landing real signals.
- Add an **NM / escalation section** at the top — the first domino deserves its own block, with
  other states below it.
- Drill-down already works: ask Callie. Confirm `get_screentime_report` can answer "tell me more
  about that bill / that article" and extend it if not — this is the "expandable" half of Jon's
  requirement and it needs no new surface.

### Stream D — State risk scoring: "get smarter over time" (after A and B; the strategic half)

Jon's ask: something that gets smarter and **narrows down where to focus**, rather than 51 states
of undifferentiated feed. This is the piece that turns a crisis tool into the market-strategy
asset (goal 2).

**The key asset is that NM is a labeled example.** We know the outcome. So we can ask the only
question that matters: *what did NM look like three and six months before it broke?* — then look
for that shape elsewhere.

**Design principles, in priority order:**

1. **Transparent beats clever.** Every state score must decompose into "these four signals drove
   it," with the source links. A number Jon cannot interrogate is useless in a crisis and will
   not be trusted twice. No opaque model.
2. **Hand-tuned first, learned later.** v1 is an explicit weighted score over components we can
   name. Do not reach for ML.
3. **Back-test before believing.** Run the score over NM's own history. If it does not rise
   ahead of the crisis, the components are wrong — fix them before shipping the number. This is
   the honest validation and it is available today.

**Candidate components** (each independently visible):
- Signal **velocity** — rate of new items, not just volume. Acceleration is the tell.
- **Stance trend** — is the unfavorable share rising?
- **Legislative movement** — bills introduced → advancing → passed, weighted by stage.
- **Tier-1 competitor removals in-state** — per the ICP tiering, a Closest-ICP competitor being
  pulled is the strongest leading indicator we have that Amira is next.
- **Escalation level** — district → county → state. NM went statewide; that jump is the signal.
- **Amira's own exposure** — a state with 40 Amira districts matters more than one with 2.

⚠ **Dependency:** that last component needs Amira's customer footprint, which is the same
Salesforce/customer-list blocker that has stalled the board peer-validation scout. Build the
score so it works without it and improves with it — do not block on Salesforce.

**How it actually gets smarter (the honest version):**

- **n=1 today.** One labeled example is a hypothesis, not a model. Say so plainly rather than
  implying prediction.
- **Human labels are the real fuel.** When Jon or Josh marks a state "this is real" or "false
  alarm," record it. The reaction-learning mechanism from `callie_push` already does exactly this
  shape (reasoned-reject teaches, silent ignore teaches nothing) — reuse it rather than inventing
  a second one.
- **Re-tune as states resolve.** Each state that moves — or conspicuously does not — is another
  label. The score improves with evidence, not with cleverness.

**What it produces:** a ranked watchlist — "these 5 states are heating up, here is why, here is
what changed this week" — which is the "narrow down where to focus" Jon asked for, and it feeds
straight into the daily read as its top section.

### Stream C — Team-visible surface (Ares's project)

The heat map plus per-item detail, as a standalone app reading the database. See
`docs/artemis-ares-teammate-plan.md` §6 — this replaces the State Policy Tracker as Ares's first
project, and it is strictly better as a test case because it is genuinely needed and has real
feedback attached.

Deliberately **not** on the crisis path: Streams A and B deliver the situational read without it.
Ares builds the thing that makes it good for the whole team, and a slip costs nothing urgent.

---

## Why the split

The sensing (Stream A) is crisis-critical and is production work in artemis-os. It should not
wait on an agent that has never completed a task — you would be finding out during a crisis.
The surface (Stream C) is standalone, visual, judgeable in ten seconds, multi-session, and
carries zero production risk. Both halves are real work; only one is urgent.

## The watch list (from Jon's "Amira Competitors" sheet, 2026-08-11)

Source: `docs.google.com/spreadsheets/d/18uvVHqoSUdcnGUqYjdX9jJ73RgvBe7FupaeWOmg-2PA`

The sheet's **ICP Overlap** column is the monitoring priority — a removal at a Closest-ICP
competitor is a leading indicator for Amira in a way that a Less-Aligned one is not.

**Tier 1 — Closest ICP Match** (watch hardest; their removals predict ours)
i-Ready (Curriculum Associates) · Amplify · Renaissance (Star Reading) · Lexia Learning ·
Magic School AI · Brisk Teaching

**Tier 2 — Moderate ICP Alignment**
Imagine Learning · FastBridge / Acadience

**Tier 3 — Less Aligned ICP**
Multitudes · SchoolAI · Newsela · IXL · Edmentum · Savvas · EPS · Amplify CKLA · Khanmigo

⚠ Rows 12–13 were truncated in the screenshot — confirm the full membership of the
"Newsela / IXL / Edmentum / Savvas" and "EPS / Amplify CKLA / Khanmigo" groups against the sheet
before seeding.

**Tier 0 — the left-field lane (Mark's point).** Mark counts general **AI companies** as
competitors: they could ship learning software and blindside Amira from outside the ed-tech
category. Watch OpenAI, Anthropic, Google/Gemini, Microsoft/Copilot, Meta and Khan Academy —
but **only** on education-entry intent (`k-12`, `schools`, `district`, `literacy`, `reading`,
`teacher`, `curriculum`), never on general AI news, or the lane will drown everything else.

This is cheap to add and genuinely different from the others: Tiers 1–3 answer *"is the backlash
reaching us?"*, Tier 0 answers *"is someone about to enter our market?"*

## Open questions
2. **Which states after NM?** Watching all 51 for brand hits is cheap. If there is a known
   next-most-likely set, they warrant board + procurement coverage too, which costs more.
3. **Social.** The agency owns social, and coworkers are finding posts there. Do we capture it
   for the corpus, or stay out of their lane? Recommendation: stay out for now — news, board and
   procurement is where nobody is looking, which is where our value is.
4. **Who sees the daily read?** Channel `C0BBYM8N26M` is configured — confirm that is the right
   audience now that this is a crisis feed rather than a policy curiosity.
