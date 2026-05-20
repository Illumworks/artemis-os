**Campaign Signal Spec — v0.1**

# **Purpose of this document**

The purpose of this document is to outline the granular criteria that should fire a campaign (i.e. what constitutes a signal worth pursuing versus noise, and the nuance between districts and states that makes one excite us and another not. This document is the seed for that. It maps to the architecture you laid out:

* **Reason code registry** — read by every Scout (1.1–1.9) and the Qualifier. Each code below becomes one entry.

* **Territory config** — priority\_states, watch\_keywords\_per\_campaign\_type, and deprioritized lists. Seed values below.

* **Qualifier filtering logic** — the boost / suppress / skip rules in §4.

* **Per-scout prompt deltas** — the nuance notes in §3 that should be lifted into the relevant scout's prompt.

# **1\. Territory config — seed values**

These slot directly into the Territory config shared by all scouts.

### **priority\_states**

FL, IN, MD, MO, IL, TX

### **watchlist\_districts**

200–500 districts derived from priority\_states \+ enrollment ≥ 5,000 \+ not on the skip list (§4). Board Minutes Scout and Leadership Transition Scout key off this list.

# **2\. Reason code registry**

These are the codes scouts assign in their signal output. The Qualifier uses them to route to a campaign type (§3) and to apply the boost/suppress rules in §4.

Naming convention follows what's already in the Figma: SCREAMING\_SNAKE\_CASE, prefixed by domain (POLICY\_, FUNDING\_, VENDOR\_, DISTRICT\_, PROCUREMENT\_, TX\_, LEADER\_).

| Code | Plain-English trigger | What the scout looks for | Default urgency |
| :---- | :---- | :---- | :---- |
| POLICY\_LIT\_MANDATE | New state law passes requiring screening or literacy intervention | Bills with screening/intervention/dyslexia/structured-literacy keywords reaching INTRODUCED, PASSED\_CHAMBER, or ENACTED in a priority state | hot at PASSED\_CHAMBER or ENACTED; standard at INTRODUCED |
| POLICY\_EDTECH\_TIME\_LIMIT | Legislation reducing time on ed tech, or public dissatisfaction with screen-time on ed tech | Bills, news, or board commentary citing screen-time caps or ed-tech-time reduction; Amira positioned as low-time / high-impact | standard; hot if bill is statewide and includes K–3 |
| FUNDING\_LITERACY\_GRANT | State publishes a literacy grant or funding announcement for high-impact tutoring | Grants.gov, Federal Register, or state DoE press releases announcing literacy / tutoring / HIT funding | hot if deadline ≤ 30 days; standard if 30–90; enrichment otherwise |
| FUNDING\_DEADLINE\_NEAR | State notification or selection deadline within 90 days | Any active funding signal where days\_until ≤ 90 | hot ≤ 30 days, standard 30–90 |
| FUNDING\_HB2\_ELIA | District publicly discusses HB 2 Early Literacy Intervention Allotment ($250/student, K–3) spend | TX board minutes / budget docs referencing HB 2, ELIA, or Early Literacy Intervention Allotment | enrichment (context only — not a discrete event) |
| VENDOR\_APPROVED\_LIST | State adds Amira to an approved-vendor list | State DoE procurement / approved-vendor list pages mentioning Amira (or category Amira qualifies for) | hot |
| VENDOR\_DISSATISFACTION | Public dissatisfaction with iReady, Lexia, UCSF Multitudes, or Amplify | News, board minutes, or LinkedIn posts naming the competitor with negative valence (efficacy, cost, fit, renewal) | standard; hot if board votes non-renewal or RFP follows |
| DISTRICT\_STRATEGIC\_LITERACY | District strategic plan names literacy as a top priority | Strategic plan PDFs, board adoption of plan with literacy as named pillar | standard |
| DISTRICT\_PROFICIENCY\_GAP | District publicly cites a literacy achievement gap or proficiency drop | Board minutes, press releases, or local news citing reading-proficiency decline, NAEP drop, or named gap | standard; hot if paired with vendor dissatisfaction or RFP |
| DISTRICT\_DLL\_EXPANSION | District announces bilingual or dual-language program expansion | Board votes, press releases, or strategic plan items naming DLL / dual-language / bilingual program expansion | standard |
| DISTRICT\_MTSS\_STRAIN | District announces MTSS or intervention staffing challenges | Board minutes or news citing intervention staffing shortages, MTSS gaps, Tier 2/3 capacity issues | standard |
| PROCUREMENT\_ELA\_ADOPTION | New core ELA adoption cycle opening | Adoption committee formation, public comment windows, ELA materials review on board agenda | standard; hot when RFP posts |
| PROCUREMENT\_LITERACY\_RFP | Active literacy/assessment/curriculum RFP | RFPs/RFIs on statewide portals or district sites; literacy / reading / assessment / tutoring scope | hot if days\_to\_close ≤ 14; standard 15–45; reject \> 45 unless strategic |
| TX\_HB1416\_WAIVER | District pursues or is awarded an HB 1416 tutoring waiver | TEA waiver filings, board discussion of HB 1416 waiver, district press; Amira is TEA-approved for HB 1416 | hot |
| TX\_HB3\_DYSLEXIA\_COMPLIANCE | District flags HB 3 dyslexia reporting compliance challenges | Board minutes / TEA correspondence citing HB 3 dyslexia reporting friction; Amira is TEA-approved | hot |
| LEADER\_TRANSITION\_FORMAL | New superintendent, CAO, or curriculum director formally hired | Two-source confirmed formal hire — board vote OR district press release | hot for 90 days post-hire |
| LEADER\_TRANSITION\_INTERIM | Interim supe / CAO / curriculum lead named | Single-source interim announcement | standard |

**Note on urgency:** default tiers above are starting points. The Qualifier should override based on the signal's own deadline field (per the schema in the Figma — urgency.tier is computed from urgency.days\_until). Where I've written "hot" without a deadline, it means: emit hot regardless of timing, because the event itself is the buying window.

# **3\. Campaign type mapping & watch keywords**

Each signal carries one or more candidate\_campaign\_types (per the schema). This table is the seed mapping — the Qualifier uses it to route, and the watch keywords feed Territory config.

| Campaign type | Reason codes that emit it | Watch keywords (seed) |
| :---- | :---- | :---- |
| **OBC** | POLICY\_LIT\_MANDATE, FUNDING\_LITERACY\_GRANT, PROCUREMENT\_ELA\_ADOPTION, PROCUREMENT\_LITERACY\_RFP, VENDOR\_APPROVED\_LIST | outcomes-based contracting, OBC, pay-for-performance, efficacy-based procurement, RFP, RFI |
| **Dyslexia / structured literacy** | POLICY\_LIT\_MANDATE, TX\_HB3\_DYSLEXIA\_COMPLIANCE, DISTRICT\_PROFICIENCY\_GAP | dyslexia screening, structured literacy, science of reading, decoding, phonics, Tier 2/3 reading |
| **Biliteracy / DLL** | DISTRICT\_DLL\_EXPANSION | biliteracy, dual language, bilingual, DLL, English learner, EL |
| **High-impact tutoring (HIT)** | FUNDING\_LITERACY\_GRANT, TX\_HB1416\_WAIVER, DISTRICT\_MTSS\_STRAIN | high-impact tutoring, HIT, tutoring waiver, intervention staffing, Tier 2 capacity |
| **General growth** | LEADER\_TRANSITION\_FORMAL, DISTRICT\_STRATEGIC\_LITERACY, VENDOR\_DISSATISFACTION | new superintendent, curriculum review, strategic plan, literacy priority |

# **4\. Qualifier rules — boost, suppress, skip**

Three layers. Skip is hard; suppress is contextual; boost moves a signal up a tier.

## **4.1 Hard skip list (no campaign, regardless of trigger strength)**

| Skip rule | Reasoning |
| :---- | :---- |
| **HMH partner districts / Into Reading adopters** | These are channel-conflict accounts. Detection: district board adoption record names HMH Into Reading as current core ELA, OR Salesforce account flag \= HMH partner. If either is true, suppress all signals. |
| **Single-school opportunities** | Below the threshold where our motion fits — sales cycle and pricing assume district-level deployment. Detection: signal geography resolves to a single school, not a district. |
| **Districts under 5,000 students** | Below enrollment threshold for our standard motion. Detection: district\_id maps to enrollment \< 5,000 in the district roster. |

*These are non-negotiable. If a signal resolves to one of these, the Qualifier drops it before it ever reaches the human-gate. Log to* skipped\_signals *for visibility, but don't surface it.*

## **4.2 Suppress (downgrade or hold)**

* **Stale signal.** Same district \+ same reason\_code emitted in the last 30 days → suppress unless material\_change\_check passes (the Starbridge Researcher already has this; extend to all scouts via the shared dedupe logic in the schema).

* **Speculation, not action.** BOARD\_OBC\_DISCUSSION on the agenda without a vote \= standard, not hot. Only BOARD\_OBC\_RFP\_APPROVED or an actual posted RFP earns hot. This distinction matters — both Regional News Scout and Board Minutes Scout flagged it as a failure mode in the Figma.

* **Single-source leader transition.** LinkedIn-only profile change without press or board confirmation → hold 7 days, retry, then downgrade to enrichment only. Two-source rule from Leadership Transition Scout.

* **Paywalled evidence.** If the only evidence quote is partial / behind a paywall, emit with evidence\_quote\_partial flag and downgrade one tier. We want the BDR to be able to cite the source.

## **4.3 Boost (upgrade tier)**

* **Stacked signals.** Two reason codes in the same district within 30 days → upgrade one tier. E.g. DISTRICT\_PROFICIENCY\_GAP \+ VENDOR\_DISSATISFACTION \= hot, not standard. This is the highest-conviction pattern from sales conversations.

* **Leader transition \+ curriculum signal.** LEADER\_TRANSITION\_FORMAL within 90 days, paired with PROCUREMENT\_ELA\_ADOPTION or DISTRICT\_STRATEGIC\_LITERACY → hot. New leader, new mandate.

* **Texas approval signals.** TX\_HB1416\_WAIVER or TX\_HB3\_DYSLEXIA\_COMPLIANCE → always hot. Amira is TEA-approved for both; the district has effectively told us they're shopping.

# **5\. Nuance the scouts should be aware of**

Direct answers to your question "what are the nuances between this state, that state, this district, that district." Lift these into the relevant scout's prompt deltas.

## **Florida**

* OBC is the dominant frame. Watch for outcomes-based contracting language in RFPs and board minutes — FL is leading on this and the language is showing up in adjacent states.

## **Texas**

* HB 1416 (tutoring) and HB 3 (early reading / dyslexia reporting) are the two policy anchors. Amira is TEA-approved for both — waiver activity and compliance friction are direct substitution signals.

* HB 2 ELIA ($250/student, K–3) deployment is enrichment context, not a discrete trigger. Begins SY 2026–27 — districts are budgeting now.

* Skip TX biliteracy for v0.1 (deprioritized — revisit).

## **Indiana, Maryland, Missouri, Michigan, Illinois**

* Watch for the cross-state OBC pattern — Florida's framing is showing up in IN and MD legislation. The Territory config is shared across scouts specifically so this cross-state pattern is visible (the Figma calls this out).

* Dyslexia screening mandates are the second cross-state thread.

## **All states — vendor dissatisfaction**

* **Named competitors:** iReady, Lexia, UCSF Multitudes, Amplify. Add to watch keywords for Regional News Scout and LinkedIn Observer.

* Dissatisfaction language is rarely direct. Look for: "reviewing our options," "considering alternatives," "renewal under review," board efficacy reviews of named products, public RFP releases naming an incumbent.

* Screen-time dissatisfaction is becoming its own thread. Amira's positioning here is strong (low time, high impact) — POLICY\_EDTECH\_TIME\_LIMIT should fire on legislation OR public sentiment, not just bills.

