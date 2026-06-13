# Product Plan — Growth Credibility / "Growth Story" App (standalone)

**Status:** PLANNING / PARKED (captured 2026-06-13 from the Jun-12 "Research data alignment + Marketing OS"
meeting: Jon, Amy Scholz, Angela Miata, Kelly Boden, Mark Angel). **Not sequenced — not being built yet.**
**Framing (Jon):** this is its own **standalone app/product** that anyone at the company can use — not a
marketing-only feature. Distinct from the OS expansion (see `docs/os-multi-team-expansion.md`).

## The problem (verbatim from the meeting)
Districts **don't believe or don't understand** Amira's growth data — even when it's strong. Recurring pains:
- **Disbelief of big numbers.** Skeptical districts (Houston, Hillsborough, NYC, Miami, San Diego) are *more*
  impressed by competitors' *smaller* growth because it "feels honest." Strong Amira data is met with "really,
  this is it?" instead of awe.
- **Amira-specific jargon.** "ARM growth," "weeks of growth" are unique to Amira — the audience is hearing
  them for the first time and has no anchor. Dr. Graves (San Diego) asked ~30 questions drilling into what the
  metrics even mean — "an object lesson in the struggle to understand growth numbers."
- **No single way that works.** Weeks-of-growth lands for some, percentile rank (PR) for others, effect size
  for the technical few — "we need a couple-three rabbits in the hat, and for each, explain the trick well
  enough that they get the magic." Audience code-switching (deep-technical vs. not) is constant.
- **Sourcing/credibility is invisible.** Independent/peer-reviewed research and "Amira just saying it" **look
  identical on the slides**, so people get confused about what's credible (Dr. Graves thought *everything* was
  Amira-sourced after one mixed deck). The negative-pushback funnel ("not peer reviewed / not published / not
  truly independent") erodes trust.
- **No comparability ("the clouds").** Mark's Aviator analogy: dogfights only look fast once you add clouds for
  relative motion. The numbers need **context/baseline** to land. Competitor framing matters (Houston loves how
  **Zearn** shows impact; the "1 lesson vs 4 stories" usage mismatch confuses comparisons). Idea: **"return on
  minutes"** (growth per instructional minute) as a comparability frame — not yet visualized.

## The product idea (Jon's "filter")
A single interactive app: **"Here is the growth (the data). Pick how you want to see it"** → it produces a
breakdown in that lens **with a built-in explainer**.
- **Metric lenses** (the "trifecta+"): **Percentile Rank**, **Effect Size**, **Weeks of Growth**, and
  **Return on Minutes** — templatized, consistent presentation.
- **An elegant ~90-second explainer per lens** ("PR 0 is good," "staying the same *is* growing," what effect
  size means, what's in the norm) so a presenter can set a baseline before the data lands.
- **Clear sourcing/credibility treatment** — a distinct visual language for "independently validated" vs
  "Amira-reported," so credibility is never ambiguous.
- **Comparability built in** — show the number against a baseline/competitor frame (the "clouds"); a
  return-on-minutes view.
- **Dual output:** interactive tool (filters) **and** exportable to a flat artifact (PDF) for "send me
  something" requests, **and** a public-facing website view (top-level numbers only). Same data source.

## Design notes / constraints
- **Data-in must support every lens** up front (the gating requirement Jon flagged) — PR, effect size, weeks,
  minutes all derivable from the same underlying analysis.
- Must reconcile with the **product's own reports** — a live credibility risk today is that marketing's
  numbers and the in-product reports don't match (Albuquerque, Houston). Single source of truth matters.
- Audience-aware: let the presenter choose depth (technical vs. plain).
- Amy wants a **focus group / research on what actually resonates** before over-investing in one lens.

## Who uses it
Company-wide: Sales (AEs in RFPs/meetings), Customer Success (renewals), Marketing, Leadership — anyone who
has to present Amira's impact credibly. That breadth is why Jon sees it as its own product.

## Open questions
- Which lenses to ship first (pending Amy's resonance research)?
- Interactive-first vs. export-first (the meeting flagged a real pull toward "just send a PDF")?
- How it sources from / stays consistent with the in-product reporting + the research/publication pipeline.
