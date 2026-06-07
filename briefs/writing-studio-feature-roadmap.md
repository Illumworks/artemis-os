# Writing Studio — feature roadmap + memory audit (consolidated)

**Status:** ROADMAP (build after the content-node P0 — the engine must write real drafts first). Captured
2026-06-06 with Jon. Touches Writing-Studio memory/rules → Jon-in-the-loop. This consolidates the audit +
the agreed feature set; refine each into worker briefs when scheduled.

## Memory audit (how WS uses its memory today — verified)
One `WritingProfile` ("Amira Marketing Voice", id=1) + **9 sources + 3 rules + examples**, assembled into
the agent prompt by `compose_engine.build_ruleset_grounding_block`.
- **9 sources** (`writing_sources`): 1 Master Prompt · 2 Message Compass · 3 Product Cards · 4 Audience
  Router · 5 Glossary · 6 **Claims Register** · 7 **Proof Pack Index** · 8 **Templates** · 9 Changelog.
- **3 rules** (`writing_rules`): teacher Reading-Suite emails → practical classroom language; always active
  voice for educators; board-action follow-ups → open by naming the board action. (Already audience/asset
  scoped in spirit → validates the tag-scoped-rules design.)
- Key implication: **claims/compliance + templates content ALREADY EXIST as memory** — several "new"
  features below are "wire up / surface what's there," not build-from-scratch.

## Agreed features (Jon likes all; ★ = must / high)

### ★ Learn-from-edits flywheel (MUST)
When a human edits an AI draft or rejects it at Gate-2 with a reason, capture the delta → AI proposes a
tag-scoped rule → human confirms → it joins `writing_rules`. The studio improves with use. Extends the
existing rejection-memory-carryover + the conversational rule capture to EDITS. Uniquely fits the
memory+rules architecture.

### ★ Claims guardrail + LIVING approved-claims registry (WIRE UP + curate the Claims Register / Proof Pack)
Content exists (sources 6 + 7). Build:
- (a) the agent inserts approved claims/proof **verbatim** from the Claims Register / Proof Pack;
- (b) a **check that flags any claim lacking a registered source** before send;
- (c) **the flag is curatable in-flow** — when a claim is flagged as not-registered, the user can **add it
  as an approved claim with one click** ("Add to Claims Register") **or conversationally** ("this is an
  approved claim"), and it immediately becomes registered/usable. (Jon 2026-06-06: *"we're building the
  bible with this tool — it needs flexibility."*)
- **Authority model (the key distinction):** Writing Studio users ARE the authorized approvers, so in-tool
  one-click approve is correct and safe here. The exported company **custom GPT consumes the registry
  READ-ONLY — it can NEVER approve a new claim** (only the WS curators can). So flexibility lives where the
  authority is; downstream is locked.
- **Governance (keep the bible trustworthy + auditable, without slowing the add):** each approval is
  lossless/append — capture **who approved + when**, and *optionally* prompt for a source/evidence link
  (skippable — don't block the add). Gives an audit trail for district-facing claims while staying
  one-click. The Claims Register grows through use; this is also where the learn-from-edits loop feeds new
  approved language.
Hooks into the compliance gate. District-buyer-safe + self-curating = a real moat.

### ★ Custom-GPT export (company-wide, transferable)
**Lives on the Writing Studio MEMORY page (Jon 2026-06-06)** — it acts on the profile/sources/rules, not a
draft, so it belongs there, NOT in the composer. Export the profile to a ChatGPT custom GPT: **Instructions**
← Master Prompt + rules + Message Compass + Audience Router + Glossary; **Knowledge files** ← Product Cards +
Claims Register + Proof Pack + Templates; **starters/examples** ← Writing Examples. Re-export to keep in sync
when memory changes. Gives the whole company the same voice/claims/proof outside the marketing pipeline.

### Templates (CREATE/manage + start-from) — content exists (source 8)
UI to author/manage templates (per asset-type + audience + format) and "start a draft from template."
Pairs with the tagging taxonomy (templates tagged like assets) and campaign lineage (`predecessor_id`).

### Repurposing — one piece → many audiences/platforms
Adapt an approved piece for another audience/platform using its tags (superintendent email → principal
LinkedIn post → one-pager). Force-multiplier for a small team; deterministic via tags + Audience Router.

### Personalization / merge fields + state-aware content
`{{district_name}}`, `{{superintendent_name}}`, and **state-specific swaps** (Field Guide auto-localizes —
SB 1672 for IL, etc.). One draft → N personalized sends, using the targeting district list + contact data.

### Editor / quality
- **Version history + compare + restore** (extend the save-version concept).
- **Variants side-by-side** (2–3 angles/tones; pick/merge).
- **Readability + voice linter** — reading-level/jargon + brand-drift flags (on-brand for a reading co.;
  uses the rules engine).

### Workflow / team
- **Comments / review threads** on a draft, tied to the Gate-2 approval (Angela/Josh mark up before sign-off).
- **Suggested next assets** — after one asset, propose the campaign's follow-ons, pre-tagged.

### Longer-term loop
- **Performance feedback** — once sends have open/reply data (CRM/Starbridge), feed which drafts performed
  back to the agent → learns what works per audience. Closes wrote-it → worked → write-more-like-that.

## Sequencing
Engine first (content-node P0) → composer UX (3-mode editor) → tagging+rules engine → then this set, roughly
in ★ order (learn-from-edits, claims guardrail, custom-GPT export) then templates/repurposing/personalization
then editor-quality/workflow then the performance loop. Jon picks build order when scheduled.

## Constraints
Lossless (memory append/version, never destructive; AI proposes, human confirms anything affecting how we
write — Writing-Studio rules = Jon's call). Reuse the existing profile/sources/rules + compose engine; don't
fork. Org dep rule.
