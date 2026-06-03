# Marketing Intelligence Layer — Design (planning doc, evolving)

**Status:** CONCEPT — flushed out with Jon 2026-06-02, not yet built. This is the "intelligence"
half of Artemis OS ("marketing-**intelligence** + campaign-workflow"). The campaign-workflow half
(signal → cluster → propose → initiate → draft → review/edit → approve) is built. This doc captures
the intelligence half before we lose the thread; refine before building.

## The idea (Jon, in his words)
Use the data we store on signals (and eventually campaign performance) to (a) analyze **trends over
time** across states / districts / campaigns / families, and the broader **social + political
ed-tech landscape** (problems, struggles, desires, needs), and (b) **react by creating content** —
social posts, smarter campaign content, and one-off pieces — that a tight team would never have the
man-hours to produce manually.

## Two halves (confirmed framing)
1. **Retrospective intelligence (analyze / trends)** — descriptive: what's happening, over time,
   where. Grounded in data we already store. Hallucination-safe *by construction* when built on real
   aggregations.
2. **Reactive content (see a trend → create content)** — generative: detect a theme → draft
   responsive content. Carries brand/PR/compliance weight → human-gated, claims-grounded.

## Locked principles (aligned 2026-06-02)
- **Trends are computation, not agents.** Counts / time-series / deltas / pgvector clustering are
  deterministic and trustworthy. The LLM only **narrates** computed trends and **generates** content
  off them — it never "discovers" trends as free-form judgment. **Minimize net-new agents.** (A new
  agent is justified only when we need a new *data source* to grab information we don't have.)
- **Every analytic ties to a decision.** No dashboard-for-its-own-sake (BI graveyard). Start from
  the decision the intelligence should change.
- **Decision-enrichment FIRST, discovery SECOND.** Primary near-term win: sharpen the campaigns
  we're *already considering* (lower risk, fast value). Higher-ceiling win: surface campaigns/content
  we'd *never have thought of* (a big deal for a small team) — but built thoughtfully and *after*, so
  it doesn't pollute the grounded decision-enrichment path.
- **Draft-for-human. No auto-publish.** (Auto-posting to social = deep water.) The review/edit/approve
  flow we built is reused.
- **Claims are grounded, never fabricated.** Education efficacy/impact claims are regulated territory
  (ESSA evidence tiers, "proven to improve outcomes"). Content that makes claims must assemble from a
  **vetted, approved claims library** (Writing Studio has some seeded; needs a proper ingestion of our
  existing public documents + a hard restriction against fabricating claims). **Not all content needs
  claims** — clean, simple, on-brand posts ("keep calm and carry on" style) are fine and claim-free.
- **Alerts on meaningful thresholds + a light digest.** Alert only when something crosses a line that
  *should change behavior*; don't ship a digest that exists just to exist.
- **Mine what we have before chasing new sources.** We're likely data-rich/analysis-poor (signals
  already carry state / district / family / urgency / evidence / dates). The genuine gap is the
  qualitative "desires / needs / struggles" landscape (social listening) — noisiest + least
  groundable, so it comes **last and carefully**.
- **Memory keystone is the home for durable insights.** Trend observations persist as memory
  observations (with evidence + embeddings), so they accumulate and can inform future agent/human
  decisions — not a throwaway analytics store.

## Refined phased shape
1. **Trend substrate (deterministic)** over existing signals + districts → persisted as memory
   observations. (Buildable now; no new data.)
2. **Enrich the decision** — the Gate-1 / initiation approval surface gets historical + trend context
   ("TX literacy legislation up this quarter; 3 comparable districts had signals in 90d; here's what a
   similar campaign did"). **This is where we start** — intelligence used at the moment a human is
   already deciding, not admired on a side page.
3. **Alerts + light digest** off the substrate.
4. **Campaign performance** — depends on the outcome-tracking loop (#106) + connecting HubSpot /
   Salesforce / ChurnZero (not yet done). Enables "which campaigns/messages actually work."
5. **Social / political listening → reactive content** — new sources for the qualitative landscape →
   theme detection → human-gated, claims-library-grounded content drafts. Highest ceiling, most risk;
   last.

## The first concrete step (agreed)
**Audit whether the Gate-1 / initiation brief contains enough to make an informed decision** (Jon's
sharp question) — then enrich it with historical + trend context. This is actionable today, a quick
win, and the foundation the whole intelligence layer enriches. Read-only audit first → then the
enrichment build.

## Dependencies / what's not ready
- **Campaign performance metrics** need #106 (outcome capture) + CRM integrations (HubSpot,
  Salesforce, ChurnZero) — Jon hasn't built/connected these yet. Phase 4 waits on them.
- **Claims library** needs a real ingestion of our public documents + a fabrication guard (Writing
  Studio has a seed only).

## Open questions to refine next
- What are the *specific* decisions Phase 1/2 should improve (list them concretely → that defines the
  analytics)?
- What dimensions do current signals actually capture vs. what trend analysis wants (gap analysis)?
- What does "enough info to decide" mean for a brief (the audit answers this)?
- For discovery (Phase 5): how do we keep net-new opportunity surfacing from polluting the grounded
  decision path — separate surface? separate confidence labeling?
- Claims library: where do approved claims live, what's the ingestion source set, what's the
  fabrication guard mechanism?

## Related tasks
Brief-sufficiency audit + enrichment (next step); claims-library ingestion + fabrication guard;
#106 outcome tracking; CRM connectors (HubSpot/Salesforce/ChurnZero); the trend substrate build.
