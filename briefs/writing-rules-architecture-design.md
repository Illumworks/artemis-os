# Design topic: Writing Studio rules — scaling rule application + tagging (NOT a build yet)

Raised by Jon 2026-06-16: as the team uses Writing Studio, the agent's instructions/rules will **bloat over
time**; we need to think about **how new rules get applied** and **how rules are tagged to certain document
types** — from a **practicality** and **UX** standpoint. "This is a bear." Think it through before building.

## What already exists (so we design WITH it, not from scratch)
- `WritingProfile` (status active) → groups rules. `WritingFolder`. `Template`. `Claim` (proposed/approved/
  retired). `WritingExample`, `WritingSource`.
- **`WritingRule`** (`artemis/writing_rules/models.py:128`) already has `rule_type`, `title`, **`tag_scope:
  dict[str, list[str]]`** (rules are ALREADY taggable to scopes), and `status` (active/archived).
- Compose already builds an **"Approved rules" grounding block** (`compose_engine.build_ruleset_grounding_
  block`) **capped at `PROMPT_RULE_LIMIT`** — so the prompt can't grow unbounded; rules beyond the cap are
  dropped (which is itself a problem: which rules win when capped?).
- The Angela-writing-memory review (38 proposed voice rules, approve/reject per item) is the existing
  human-gated rule-intake flow.

## The real questions to work through (practicality + UX)
1. **Rule → doc-type application.** `tag_scope` exists but how is it used at compose time? Should a rule apply
   to ALL docs, or only docs of a tagged type (one-pager vs email vs blog vs event flyer …)? Define the
   doc-type taxonomy and make compose select rules by the current draft's type/tags, so each doc only gets the
   rules that matter to it (keeps the prompt lean + relevant).
2. **Bloat / the prompt cap.** With many rules, `PROMPT_RULE_LIMIT` drops some — silently. Need: rule
   prioritization/ranking (which rules are load-bearing vs nice-to-have), dedup/merge of overlapping rules,
   and visibility into what got applied vs dropped for a given draft.
3. **How new rules get added without mess.** Intake path (Angela-style approve/reject), dedup against existing
   rules, tagging on intake (which doc types / scopes), and a lifecycle (active → superseded/archived) so old
   rules don't accumulate. Tie into the learning loop (rules proposed from edits/feedback, human-gated).
4. **UX for managing rules.** Owner/marketing view to browse rules by tag/doc-type, see which apply where,
   edit/retire, and preview "what rules will this draft get." Avoid a flat ever-growing list.
5. **Ownership/permissions.** Who can edit which rules (ties to the Signal Playbook / role-access pin —
   marketing editors vs owner).

## Recommendation
Do a dedicated design pass: (a) map the current writing_rules application path end-to-end (how `tag_scope` +
the cap actually behave at compose time today), (b) propose a doc-type taxonomy + rule-selection model +
intake/lifecycle + the management UX, (c) review with Jon before any build. Pairs with the
[[writing-studio-team-feedback-2026-06-16]] pins (Signal Playbook role-access). Not urgent; schedule when
ready.
