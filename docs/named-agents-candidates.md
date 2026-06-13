# Named Agents — Candidate Roster (planning)

**Status:** PLANNING / PARKED (2026-06-13). **Do NOT build ahead** — each rides on the multi-team foundation
(scope-aware data/targeting + MCP/fabric fit + memory M1–M3; plus the data-credibility app for #3, P4 delegate
for #4). The **Named Agent Standard** applies to each (persona + avatar, scoped memory, proactivity,
agency-behind-the-gate, Slack presence, orchestration-awareness, defined domain, self-improving). Workers stay
faceless. **Theme:** Greek mythology — Artemis (Olympian goddess), Calliope (a Muse). New names stay in-family.

## Existing
- **Artemis** — personal PA + overseer (1:1 DM). The hub; everyone else reports up.
- **Calliope / "Callie"** — marketing analyst (Muse of eloquence). The analyst, not the ticker.

---

## 1. Sales / lead-gen agent  *(AE-facing)*
- **Name — proposed: `Tyche`** (goddess of fortune, opportunity, prosperity — she surfaces the *right*
  opportunity and drives the win). **Alts:** `Peitho` (goddess of persuasion — "the closer"); `Hermes`
  (god of commerce/the dealmaker — thematically perfect **but collides with the competitor "Hermes Agent" we're
  beating**, so avoid unless you want the irony).
- **Audience:** AEs / sales (Josh's lane).
- **Purpose / directive:** the *sales* read of the signal engine. Surface the few opportunities that matter to
  **this AE's territory + pipeline**, with the so-what and a suggested play — never a raw-signal parrot. Account
  intel, outreach prep, time-sensitive "act now" nudges.
- **Owns / reports:** sales-lane signals + outreach prep; reports up to Artemis, peers with Callie.
- **Gate:** Salesforce territory/pipeline scoping (or it's spam — Mark's #1) + MCP fit.

## 2. Customer Success / renewals agent  *(CSM / impact-director-facing)*
- **Name — proposed: `Hestia`** (goddess of the hearth, home, hospitality — warmth, loyalty, keeping customers
  "in the family," the steady long-term relationship). **Alts:** `Demeter` (nurture/harvest → growing accounts
  to renewal); `Harmonia` (concord/relationship harmony).
- **Audience:** CSMs / impact directors.
- **Purpose / directive:** the *retention* read — account health, renewal-risk signals, relationship cues,
  lifecycle nudges ("usage dipped on this account," "renewal in 60 days — here's the impact story to lead
  with"). A different lens on the same data than Sales.
- **Owns / reports:** success-lane signals + renewal prep; reports up to Artemis.
- **Gate:** account-scoping + Churn-Zero/Gong/Salesforce data + MCP fit.

## 3. Research / Growth-Credibility agent  *(company-wide; RFPs + skeptical districts)*
- **Name — proposed: `Clio`** (Muse of history — the chronicler, keeper of the record + evidence; stays in the
  Muse family with Calliope). **Strong alt: `Aletheia` ("Thea")** — goddess of *truth* (almost too on-the-nose
  for "make our growth believable").
- **Audience:** anyone presenting Amira's impact — sales, success, marketing, leadership.
- **Purpose / directive:** own the "explain our growth credibly" problem from the Jun-12 meeting. Pick the right
  metric lens (percentile / effect-size / weeks-of-growth / return-on-minutes) + a plain-English explainer +
  clear sourcing (independent vs Amira) + comparability ("the clouds") for the audience. The **agent-face of the
  data-credibility app**; musters research/decks/claims for RFPs (Texas/Idaho/Hawaii).
- **Owns / reports:** the growth-story/explainer/sourcing domain + the claims register; reports up to Artemis.
- **Gate:** the data-credibility app (`docs/product-data-credibility-app.md`) + the claims register.

## 4. Personal research / analyst agent  *(Jon's — delegated deep-dives)*
- **Name — proposed: `Metis`** (Titaness of wisdom, deep thought, prudent counsel — the deep-research analyst
  who *advises*, not just searches). **Alts:** `Mnemosyne` ("Nemo" — memory/knowledge, ties to the keystone);
  `Pheme` (report/renown — "delivers the findings").
- **Audience:** Jon (personal).
- **Purpose / directive:** Artemis's go-to for heavy research. She delegates a deep-dive (a topic, person,
  market, decision) and gets back a **synthesized, sourced briefing** — keeping Artemis snappy by handing off
  depth. Wise counsel, not a search box.
- **Owns / reports:** deep-research-on-demand for Jon; **delegated by Artemis** (this is the P4
  "delegate-to-a-named-specialist" pattern — the one named agent that's a *delegate*, not a peer).
- **Gate:** P4 orchestration (delegate primitive) + a deep-research capability.

---

## Sequencing (planning only)
Bring each online **as its lane's foundation lands**, not before:
- **Tyche (Sales)** + **Hestia (Success)** → after the OS multi-team expansion (scope-aware data + MCP fit).
- **Clio (Research/Credibility)** → after / alongside the data-credibility app.
- **Metis (Personal analyst)** → after P4's delegate primitive is real.
Order by priority when the time comes. Companion docs: `docs/agent-slack-architecture.md` (the standard),
`docs/os-multi-team-expansion.md`, `docs/product-data-credibility-app.md`.
