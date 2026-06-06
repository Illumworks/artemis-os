# Roadmap design — Writing Studio tagging taxonomy + tag-scoped rules engine

**Status:** ROADMAP design (significant, phased). Captured 2026-06-06 with Jon's decisions. Part of the
"S-class Writing Studio" effort. Touches Writing-Studio rules + the Campaign Brief → keep Jon in the loop
on vocabulary + rule decisions (his domain). Refine into phased worker briefs when scheduled.

## Vision
Tag each content asset upfront (at campaign initiation) across a set of dimensions; key Writing Studio
RULES to those same tags; the writing agent drafts an asset by pulling the rules whose scope matches the
asset's tags + the brand voice. Tags are the join key between "what we're making" and "how we write it."
**The system must be flexible — dimensions and values get added over time, and adding them propagates into
the Writing Studio agent's memory/instructions so newly-captured rules (manual or conversational) route to
the right place automatically.**

## 1. Tag Registry — data-driven + extensible (the backbone)
- **Dimensions are data, not hardcoded.** Start with: `asset_type`, `audience`, `platform`, `intent`,
  `topic`, `geography`. Adding a NEW dimension is a supported admin action.
- **Values are data.** Each dimension has values; add values anytime (UI + API). Lossless: deprecate
  (active=false), never hard-delete — existing tagged assets/rules keep resolving.
- **Propagation:** adding a dimension/value updates the Writing Studio agent's instruction/memory context
  (the agent always knows the current vocabulary) AND the rule-scoping options in the UI. One registry,
  consumed by: the initiation tagging UI, the rule editor's scope picker, and the draft agent's prompt.
- Tables (sketch): `tag_dimensions(key, label, active, order)`, `tag_values(dimension_key, value, label,
  active, metadata jsonb)`. `metadata` carries things like applicability (below).

## 2. Starter vocabulary (Jon, 2026-06-06 — editable; these are the v1 values)
- **audience:** superintendent · district leader · curriculum director · principal · board member ·
  special-ed director · teacher · **parent (social only — see applicability)**. *We do NOT do outreach
  (email) to parents.*
- **asset_type:** outreach email · email sequence ⚠️ · social post · blog · long form ⚠️ · product paper ·
  one pager · landing page · webpage · impact story. *(NOT "case study" — Jon: use "impact story".)*
  ⚠️ = too generic, needs subtypes (§3).
- **platform:** email · social (LinkedIn / X / Facebook / Instagram) · web/landing · print.
- **intent:** awareness · consideration · decision · expansion (starter).
- **topic / geography:** inherited from the campaign (family) + targeting (state / tier).
- More values WILL be added — the registry (§1) makes that a data edit, not a code change.

## 3. Generic types need subtypes (Jon flagged "email sequence" + "long form" as too generic)
Add an optional `subtype` to `asset_type` (hierarchical value). Examples to seed WITH Jon:
- email sequence → welcome · nurture · re-engagement · event follow-up · …
- long form → buyer's guide · whitepaper · research report · explainer · …
Rules can target the subtype (more specific) or the parent type (broader). Subtypes are registry values too
(extensible).

## 4. Platform-scoped applicability (the "parents" nuance)
Some values only make sense on some platforms (parent = social only, not email). Store soft applicability in
`tag_values.metadata` (e.g., `parent.applicable_platforms = ["social"]`). The AI suggester + UI respect it;
**soft guidance, not a hard block** ("Parents aren't an outreach-email audience — did you mean social?").
Keep it advisory in v1.

## 5. Asset tagging at initiation
When deliverables/assets are selected for a campaign (Campaign Brief step), each asset is tagged across the
dimensions. **AI suggests values from the signal/campaign context (audience, type, platform, intent);
human confirms/adjusts** (same "AI proposes, human confirms" principle used elsewhere). Tags stored on the
asset/deliverable + surfaced on the Campaign Brief. They flow to the draft agent and into the WS draft's
metadata (filter/organize drafts by audience/type/platform).

## 6. Tag-scoped rules
- A rule has a **scope** = tag matchers (e.g., `audience ∈ {superintendent, board} AND platform = email`).
- At draft time the agent gathers ALL rules whose scope matches the asset's tags + the global brand voice,
  and composes against them. Most-specific can win on conflicts (define precedence when built).

## 7. Rule capture — manual AND conversational (route to tags)
- **Manual:** author a rule + pick its scope from the registry.
- **Conversational:** during a compose chat in the studio, when a rule surfaces ("always open
  superintendent emails with the student-outcome stat"), the system captures it as a rule and **AI routes
  it to the right scope** — inferred from the current draft's tags (this draft is audience=superintendent,
  type=email → propose that scope) — **human confirms**. This is the "rules uncovered through natural
  conversation get routed properly" requirement.
- Either way the rule lands in the registry-keyed rule store and applies to future matching drafts.

## 8. Draft agent integration
The writing agent's context = brand voice + current tag vocabulary (from the registry) + the rules matching
THIS asset's tags. So extending the taxonomy or adding a rule immediately changes what the agent knows/does,
no code change.

## Phasing (this is big — build in slices)
1. **Tag Registry** (tables + CRUD API + "add dimension/value" admin UI) — the extensible backbone.
2. **Initiation tagging** (tag assets on the Campaign Brief; AI-suggest + confirm).
3. **Rule scope** (add tag-scope to rules; rule editor scope picker; agent loads matching rules).
4. **Conversational rule capture + routing** (capture from compose chat; AI-propose scope; confirm).
5. **Memory/instruction propagation polish** (registry feeds agent context cleanly; staleness/coverage view).

## Constraints
- Lossless: registry + rules append/deprecate, never hard-delete; existing tagged assets/rules keep
  resolving. Controlled vocabulary (no free-text tags — rule matching depends on it). AI suggests values +
  rule scopes; **human confirms anything that changes how we write** (Writing-Studio-rules = Jon's call).
  Org dep rule. Extensible-by-design (adding a dimension/value/subtype is a data edit, not code).

## Open questions (decide when scheduled)
- Subtype lists for email-sequence + long-form (need Jon).
- Rule conflict precedence (most-specific wins? explicit priority?).
- Where the "add a dimension/value" admin UI lives (Writing Studio settings? a taxonomy panel?).
- Should `intent`/`topic` be required tags or optional?
