# Claims Register — extraction from published content (2026-06-08)

> **⚠️ SUPERSEDED (2026-06-08) — read this first.** Jon corrected the approach: these published docs ALREADY
> went through company approval, so the "boasting" claims are **approved** (no legal-hold), and the wording —
> written by Angela & Julie — must be preserved **verbatim** (rewording = working backwards). The buckets +
> reworded phrasings below are therefore NOT authoritative. The **canonical load is verbatim + approved** in
> `scripts/seed_harvested_claims.py` (80 claims, exact wording, loaded as `approved`). Keep this doc only as
> the original analysis (incl. which claims lacked an in-doc citation — useful for proof-pack follow-up), NOT
> as the source of phrasing or the load list.

Harvested by 3 Sonnet subagents reading the 15 published PDFs in Jon's Drive (Research · Product Overviews ·
Enablement). **Every claim is source-cited.** Two buckets: **READY TO PROPOSE** (well-sourced, safe to load
into the register as `proposed` for Jon to approve) and **HOLD** (strong but unsourced/internal/superlative
— do NOT add until sourced or legal-reviewed). Nothing here is auto-approved. The 8 seeded claims (001–008:
Identity, Coherence Map, Assess→Instruct→Tutor loop, etc.) already exist — these are net-new.

## READY TO PROPOSE (well-sourced)

### Identity / mechanism (Tier 1–2)
- Amira is an AI Learning Agent grounded in the Science of Reading — continuously assesses, instructs, and
  tutors in one connected loop. *(EGS, FG, Reading Suite)*
- Amira listens as students read aloud and analyzes oral reading fluency + decoding errors in real time to
  diagnose gaps across Scarborough's Reading Rope. *(Tutor, Assess)*
- Amira delivers micro-interventions the moment an error occurs — 1:1 tutoring at scale, English & Spanish.
  *(Tutor)*
- Reading Error Detection (RED), not speech-to-text (ASR): detects reading skill gaps, not accent/dialect.
  *(Equity WP — equity differentiator)*

### Efficacy — by study (Tier 4, cite the specific study each time)
- **Louisiana** (Shah/Scanlan/Wall, Instructure, June 2025; ~79,084 students, 12 districts, 75% econ-disadv):
  statistically significant reading gains at every grade K–5; high users up to +10 DIBELS pts. *(ESSA Level II)*
- **Texas STAAR** (36,123 students, 2024–25, ~30 min/wk): users **82% more likely** to meet grade-level
  standards; +9/+7/+6 percentile points in Gr 3/4/5 (effect size 0.3–0.4). *(OP_Texas)*
- **New Mexico Summer** (Boden, N=5,464, 4-week program): avoided the typical 7–11pt summer slide; students
  at 5+ stories/wk **47% more likely** to advance a proficiency level; 25% advanced ≥1 level. *(NM Summer)*
- **Middle School** (Boden, 10 districts × 2 years): +6–7 percentile points at 20–30 min/wk, effect size
  0.35–0.40 — replicated across two cohorts. *(Tutor English Middle School)*
- **Psychometrics / fairness** (Equity WP): reliability ≥0.90 most grade×subgroup cells; DIF ≤5% every
  comparison; 96.9% human-Amira scoring agreement (independent TX ISD Spring 2026 study, 301k+ words);
  African-American scoring accuracy 95.6% (≥ overall 95.1%); NM EL/non-EL proficiency gap narrowed 36% YoY.
- **Growth headline:** ~**9 additional weeks** of reading growth in a 36-week year at 30+ min/wk. *(Tutor,
  Reading Suite, EGS)* — **USE THE "average ~9 additional weeks" FRAMING**, not "minimum 45 weeks" (see
  Tensions).

### Credentials / independent approvals (Tier 1–3, cite the body)
- **Georgia** (Sandra Dunagan Deal Center, Nov 2024): Amira ISIP ranked **#1 of all screeners**, 142.6/155
  (next: 139.2, 134.0, 133.2, i-Ready 120.2); selected as the free statewide screener.
- **Oklahoma** (Strong Readers Act, 2025): passed all 17 mandatory criteria; highest score 266 vs 202 DIBELS.
- **California** (2024 K-2 review): the **only screener unanimously approved** across all grades in English &
  Spanish.
- **Michigan** MDE-approved (1 of 3, as of Mar 2026); **Maryland** MSDE-approved (1 of 3, Feb 2026).
- **NCII**: highest ratings for reliability & validity *(verify current rating/version at ncii.intensive
  intervention.org before each use)*.

### Product (Tier 2)
- Assess: full-class universal screening in ~20 min, English & Spanish, authentic spoken assessment (not
  translated print); measures across Scarborough's Reading Rope; norm- + criterion-referenced; dyslexia-risk
  flag (RAN) included in the base session (no add-on).
- Instruct: turns assessment into instruction-ready guidance configurable to a district's scope/sequence,
  pacing, standards — teachers spend less time diagnosing.
- Tutor: 1:1 tutoring at scale; adapts to pace/level/needs incl. English learners, dyslexia, ADHD; supports
  MTSS Tiers 1–3.
- **Lectura** (Tier 1 differentiator): purpose-built for Spanish literacy (lectoescritura) — **original
  Spanish text sets, not translations/adaptations**; EL norms from 52,000+ bilingual students.

## HOLD — do NOT add until sourced / legal-reviewed
These are strong/valuable but unsupported in the docs, internal-only, or superlative without a comparator:
- "**Most widely used** reading screener and tutoring system in the U.S." — no citation. (highest-risk)
- "The **only TEA-approved program** that raises district proficiency rates by **>20%**" — exclusivity, no
  cited study. Legal review.
- "**Industry-leading accuracy**" / "**10x more measurement points**" — superlative/quantified, no defined
  comparator or methodology.
- "Developed in **partnership with Carnegie Mellon University**" — verify the relationship + current status.
- "**5 Million+ students**" — no citation/date/definition (ever-used vs active).
- Site-specific from **internal or press** data: Tara Elementary, Clayton County GA (18.7 wks growth in 15.7
  wks — Amira *internal* data, flagged "illustrative"); Westowne/Baltimore County (+12pp / K +26pp / MLL
  ~+20pp — sourced to a *Baltimore Banner* article, not a study).
- "+5–6 months growth, outperforming **many** human tutoring models" — "many" undefined, no comparison study.
- "**2x more likely** to improve 1+ TELPAS level" — no citation/sample in the doc.

## Tensions to resolve (pick one framing before these go live)
1. **Growth:** "average ~9 additional weeks" (Tutor) vs "minimum 45 weeks" (Reading Suite/EGS). Recommend the
   conservative "average ~9 additional weeks at 30+ min/wk." Confirm against the underlying studies.
2. **Screening time:** "full class in 20 min" (EGS/Illinois/Michigan) vs the Maryland MSDE panel noting
   "small-group or 1:1 administration is most appropriate." Treat as context-specific; don't state both flat.
3. **Dose thresholds differ by study** (30 min/wk · 5 stories/wk · 20 min/wk) — not interchangeable in copy.

## Recommended next step
Load the READY-TO-PROPOSE claims into the register as **`proposed`** (via `POST /api/writing-studio/claims`)
so Jon approves/edits them at his pace in the Writing Studio — the living-bible loop, "AI proposes, human
confirms." Keep the HOLD list OUT of the register (separate sourcing/legal task). The "average ~9 weeks"
framing wins over "minimum 45."
