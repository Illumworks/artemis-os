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
- **Name — open (pronunciation matters here).** `Tyche` (goddess of fortune/opportunity) is the best *meaning*
  fit but reads as "TY-kee" — non-obvious — so either **keep it and go by "Ty"**, or switch to a cleaner name.
  Candidates: **`Nike`** (goddess of *victory* — "winning deals," easy to say, but shares the shoe brand);
  `Tyche`/"Ty" (fortune/opportunity); `Plutus` (wealth/prosperity); `Peitho` (persuasion — "the closer").
  Avoid `Hermes` (perfect for commerce **but it's the competitor we're beating**). **Lead options: "Ty" or Nike.**
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

## 4. Personal research / analyst agent  *(Jon's — delegated deep-dives)*  ★ Jon's favorite direction
- **Name — `Ares` (locked).** Greek god of war (on-theme, sits beside Artemis). The **war-god stigma IS the
  persona**: like the Tron: Ares arc Jon loves, he *defies* the name — not a brute, but the loyal, methodical
  scout you **dispatch on a mission** who returns with the intel. Reframes "war" as **expeditions / recon**,
  not combat. **Alts (fallback only):** `Metis` (wisdom/counsel); `Mnemosyne` ("Nemo" — memory, ties to the
  keystone).
- **Persona seed:** named for war, defined by restraint. Thorough, dependable, slightly stoic; takes a tasking,
  goes deep, comes back with a sourced briefing and a clear read — no drama, no padding.
- **Audience:** Jon (personal).
- **Purpose / directive:** Artemis's go-to for **two kinds of dispatched mission — research AND building**.
  Same primitive ("tasked → goes deep → returns a result"); the result is a *briefing* OR a *built artifact*
  (a doc, prototype, analysis, small tool, project code). Jon (via Artemis) sends him off; he comes back with
  the goods. Keeps Artemis snappy by handing off depth.
- **Scope discipline — where the output lands (NOT research-vs-build):**
  - ✅ Ares builds **Jon's project artifacts** — his initiatives, prototypes, docs, analyses, project code.
  - 🚫 Ares does **not** freely commit to the production **Artemis OS** itself — that stays under the governed
    Lead/worker flow (worktrees, review, merge gates).
- **★ Ares is the bridge — the point of the whole exercise.** Today Jon lives in two worlds that don't share a
  brain: the **build world** (Claude Code in the terminal — knows the codebase/roadmap, but has *no link to
  Artemis's memory*) and the **assistant world** (Artemis — knows meetings/commitments, but is *blind to what
  Jon is building*). Ask Artemis "what am I working on" and she can't answer about the day's build. Making Ares
  a **maker who lives inside the family + shares Artemis's memory** merges the two: she knows what's being built
  because her own teammate builds it. **Caveat:** the name alone doesn't close the gap — it closes when the
  build work and Artemis **share memory/context** (see gate). **Early win:** a session→memory bridge that writes
  "what Jon & Claude Code are working on" into the keystone can close the disconnect *before* full Ares exists.
- **Owns / reports:** deep-research-on-demand for Jon; **delegated by Artemis** (this is the P4
  "delegate-to-a-named-specialist" pattern — the one named agent that's a *delegate*, not a peer).
- **Gate:** P4 orchestration (delegate primitive) + a deep-research capability.

---

## Sequencing (planning only)
Bring each online **as its lane's foundation lands**, not before:
- **Tyche (Sales)** + **Hestia (Success)** → after the OS multi-team expansion (scope-aware data + MCP fit).
- **Clio (Research/Credibility)** → after / alongside the data-credibility app.
- **Ares (Personal research + maker)** → after P4's delegate primitive + scope-aware memory (M3). *(Jon's
  favored direction — bump priority when P4 lands.)* **Early win available sooner:** a session→memory bridge so
  Artemis can see the build world ahead of full Ares.
Order by priority when the time comes. Companion docs: `docs/agent-slack-architecture.md` (the standard),
`docs/os-multi-team-expansion.md`, `docs/product-data-credibility-app.md`.
