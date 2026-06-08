#!/usr/bin/env python
"""Seed the Claims Register with claims harvested VERBATIM from Amira's published content.

Source: 15 published PDFs in Jon's Drive (Research / Product Overviews / Enablement),
read 2026-06-08. **Phrasings are verbatim — written + approved by the marketing team
(Angela/Julie). Do NOT reword them.** Jon confirmed these are approved company claims
(incl. superlative/quantified ones); they load as status='approved'. De-duplicated across
docs (repeats collapsed, sources joined); pure data-tables / methodology / section headers
were dropped (not register-worthy claims).

Idempotent: skips a claim if the exact approved_phrasing already exists for the profile.
Run:  uv run python scripts/seed_harvested_claims.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from artemis.db import SessionLocal
from artemis.writing_rules.models import Claim, WritingProfile

# (approved_phrasing VERBATIM, category, tier, source)
HARVESTED: list[tuple[str, str, int, str]] = [
    # ── Identity / brand ──────────────────────────────────────────────────────
    (
        "Working 1:1 with students, Amira brings the power of one-on-one tutoring to every single student.",
        "Identity",
        1,
        "Louisiana_RB",
    ),
    (
        "Amira helps students develop essential foundational literacy skills for long-term academic success.",
        "Identity",
        1,
        "Louisiana_RB",
    ),
    ('Amira doesn\'t just provide "practice"; it drives proficiency.', "Identity", 1, "OP_Texas"),
    (
        "Amira allows the teacher to multiply herself in the classroom.",
        "Identity / Testimonial",
        1,
        "OP_Texas — Dr. Faviola Cantu, Aldine ISD",
    ),
    (
        "Amira was created for one purpose: to couple the Science of Reading with AI, giving every child a pathway to the power of reading.",
        "Identity",
        1,
        "NM Summer; Tutor English Middle School",
    ),
    (
        "Amira is an AI Learning Agent grounded in the Science of Reading—designed to continuously assess, instruct, and tutor students through a connected learning loop.",
        "Identity",
        1,
        "FG (Funding Guide)",
    ),
    (
        "Equity in Amira-powered assessment is a design discipline, not a marketing claim.",
        "Identity / Equity",
        1,
        "Equity Whitepaper",
    ),
    (
        "Amira is a Reading Error Detection (RED) system.",
        "Mechanism / Equity",
        1,
        "Equity Whitepaper",
    ),
    # ── Product — Assess ──────────────────────────────────────────────────────
    (
        "Amira ISIP Assess is a dynamic reading assessment that listens as students read aloud —capturing authentic evidence of learning to measure literacy skills with unprecedented accuracy.",
        "Product / Assess",
        2,
        "Amira_Assess",
    ),
    (
        "It delivers universal screening, benchmarking, and progress monitoring—in English and Spanish.",
        "Product / Assess",
        2,
        "Amira_Assess",
    ),
    (
        "Powered by AI and the Science of Reading, Amira captures 10x more measurement points than traditional assessments, delivering deeper insight into student reading ability in 20 minutes or less.",
        "Quantified / Assess",
        4,
        "Amira_Assess",
    ),
    (
        "Skills are measured across Scarborough's Reading Rope, to pinpoint the underlying causes of reading challenges.",
        "Product / Assess",
        2,
        "Amira_Assess",
    ),
    (
        "Amira provides norm-referenced insight into reading ability and criterion- referenced measurement of mastery toward state standards, assessing skills in the context of your district's core instruction.",
        "Product / Assess",
        2,
        "Amira_Assess",
    ),
    (
        "Amira has earned the highest ratings from the National Center on Intensive Intervention (NCII) and measures reading performance and growth in both English and Spanish with industry-leading accuracy.",
        "Comparative / Validation",
        4,
        "Amira_Assess; EGS (NCII)",
    ),
    # ── Product — Tutor ───────────────────────────────────────────────────────
    (
        "Amira, the Learning Agent for Reading Growth, delivers 1:1 tutoring at scale, ensuring every student gets the right practice, every day.",
        "Product / Tutor",
        1,
        "Amira_Tutor",
    ),
    (
        "Amira listens as students read aloud and continuously assesses proficiency in real time-analyzing oral reading fluency and decoding errors to diagnose gaps across Scarborough's Reading Rope.",
        "Product / Tutor",
        2,
        "Amira_Tutor",
    ),
    (
        "Micro- interventions address errors the moment they occur-replicating high-quality 1:1 tutoring with consistency, district-wide, in English and Spanish.",
        "Product / Tutor",
        2,
        "Amira_Tutor",
    ),
    (
        "Instruction adapts to each student's pace, level, and learning needs, including English learners and students with dyslexia or ADHD-creating a judgment-free learning environment that builds confidence, motivation, and mastery.",
        "Product / Tutor",
        2,
        "Amira_Tutor",
    ),
    (
        "Multiple independent studies show that students using Amira for 30+ minutes per week gain an average of 9 additional weeks of reading growth in a typical, 36-week single school year.",
        "Quantified / Growth",
        4,
        "Amira_Tutor",
    ),
    # ── Product — Reading Suite / Instruct ────────────────────────────────────
    (
        "Amira is The Learning Agent for Reading Growth connecting assessment, instruction, and tutoring, so your district can move from strategic vision to aligned action in every classroom.",
        "Product / Suite",
        1,
        "Reading Suite",
    ),
    (
        "Amira Reading Suite uses a Coherence Map, based on your core, to pinpoint where each student is, identify what's most likely to unlock progress, and drive tailored instruction:",
        "Product / Suite",
        2,
        "Reading Suite",
    ),
    (
        "Multiple, independent studies show that on average, students reading with Amira at dosage of at least 30 min per week achieve a minimum of 45 weeks of growth in a typical 36-week school year.",
        "Quantified / Growth",
        4,
        "Reading Suite; EGS",
    ),
    (
        "Amira Instruct turns dynamic assessment into instruction-ready guidance so district priorities, classroom instruction, and tutoring reinforce the same skill pathway to drive growth.",
        "Product / Instruct",
        1,
        "Instruct",
    ),
    (
        "Amira Instruct can be configured to reflect your district's existing priorities, providing instructional guidance consistent with your district literacy plan—your scope & sequence, your pacing and your standards to establish a custom coherence plan for the district.",
        "Product / Instruct",
        2,
        "Instruct",
    ),
    (
        "Amira Instruct responds to real-time student evidence and guides teachers with clear next steps that connect evidence to specific instructional focus areas and next steps, so teachers spend less time diagnosing and more time teaching.",
        "Product / Instruct",
        2,
        "Instruct",
    ),
    (
        "Make your literacy work more coherent—by connecting Assess → Instruct → Tutor into one evidence-based loop to drive student growth.",
        "Product / Suite",
        1,
        "Instruct",
    ),
    # ── Product — Lectura (Spanish) ───────────────────────────────────────────
    (
        "Amira is the Learning Agent for Reading Growth, purpose-built for how students actually learn to read in Spanish.",
        "Product / Lectura",
        1,
        "Amira_Lectura",
    ),
    (
        "Designed for dual language, two-way immersion, one-way immersion, transitional bilingual, and maintenance models, Amira honors the linguistic structure, developmental progression, and cultural richness of Spanish literacy from the ground up.",
        "Product / Lectura",
        1,
        "Amira_Lectura",
    ),
    (
        "Grounded in the lectoescritura process, the authentic pathway to Spanish literacy.",
        "Product / Lectura",
        2,
        "Amira_Lectura",
    ),
    (
        "Reflects Spanish's transparent orthography and syllabic structure.",
        "Product / Lectura",
        2,
        "Amira_Lectura",
    ),
    (
        "All original works — not translations or adaptations.",
        "Exclusivity / Lectura",
        4,
        "Amira_Lectura",
    ),
    (
        "Students in bilingual programs deserve more than translated tools.",
        "Positioning / Lectura",
        3,
        "Amira_Lectura",
    ),
    (
        "Amira also developed EL-specific national norms using data from more than 52,000 Spanish-English bilingual students, allowing teachers to compare EL students against relevant peers rather than the general population.",
        "Product / Equity",
        2,
        "Equity Whitepaper",
    ),
    # ── Efficacy — Louisiana ──────────────────────────────────────────────────
    (
        "The study involved nearly 80,000 students, and results clearly demonstrated significant literacy improvements for students in Kindergarten through Grade 5, but was particularly impactful for early literacy in Grades K-3.",
        "Efficacy / Louisiana",
        4,
        "Louisiana_RB — Instructure 2023-24, N≈80,000",
    ),
    (
        "High usage of Amira led up to a 10 point increase for Louisiana students compared to matched non-users.",
        "Efficacy / Louisiana",
        4,
        "Louisiana_RB — Instructure 2023-24",
    ),
    (
        "Students experience measurable benefits with moderate use and even greater growth with higher engagement.",
        "Efficacy / Louisiana",
        2,
        "Louisiana_RB",
    ),
    (
        "Students using Amira showed statistically significant reading gains at every grade level from kindergarten through grade 5.",
        "Efficacy / ESSA Level II",
        4,
        "Illinois FG; Michigan FG; Maryland FG — Shah/Scanlan/Wall, Instructure, June 2025, N=79,084, 12 LA districts",
    ),
    # ── Efficacy — Texas ──────────────────────────────────────────────────────
    (
        "In a study of 36,123 Texas students, those using Amira's Al Tutor outperformed the state average, delivering significant gains in proficiency and scale scores.",
        "Efficacy / Texas",
        4,
        "OP_Texas — 2024-25, N=36,123",
    ),
    (
        'New research from the 2024-2025 school year reveals that Texas students using Amira at "At Dosage" (approx. 30 mins/week) were 82% more likely to meet grade-level standards on the STAAR Reading assessment than non-users.',
        "Efficacy / Texas",
        4,
        "OP_Texas — 2024-25, N=36,123",
    ),
    (
        "In a longitudinal study of 24,000+ students, Amira users saw dramatic scale score advantages over non-users",
        "Efficacy / Texas",
        4,
        "OP_Texas — longitudinal, N=24,000+",
    ),
    (
        "Amira is the only TEA-approved program that raises district proficiency rates by more than 20% while simplifying compliance for school leaders.",
        "Comparative / Exclusivity / Texas",
        4,
        "OP_Texas",
    ),
    (
        "HB 1416 (Tutoring): TEA Approved for HB 1416; Amira accelerates an additional +5-6 months of growth, outperforming many high-dosage human tutoring models.",
        "Comparative / Texas",
        3,
        "OP_Texas",
    ),
    (
        "TELPAS: Students using Amira are 2x more likely to improve by 1+ TELPAS level.",
        "Efficacy / Texas / EL",
        4,
        "OP_Texas",
    ),
    (
        "In Texas, Amira users gained 36 STAAR scale score points more than non-users, translating to approximately a 9-percentile-rank improvement with an effect size of 0.45.",
        "Efficacy / Texas",
        4,
        "Tutor English Middle School — TEA Efficacy Study 2023",
    ),
    # ── Efficacy — New Mexico (summer + equity) ───────────────────────────────
    (
        "Key findings demonstrate that students not only avoided the expected summer slide of 7–11 scale score points, but also achieved positive growth across all grade levels.",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD, N=5,464",
    ),
    (
        "Students who met Amira's recommended usage guidelines of reading 5+ stories per week showed significantly higher gains, with a 47% greater likelihood of advancing to a higher literacy level compared to their peers reading fewer stories.",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD, ANCOVA, N=5,464",
    ),
    (
        "Students reading 5+ stories per week significantly outperformed those reading fewer stories: 4.7 percentile rank points higher growth (p < .001)",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD",
    ),
    (
        "28% of high-usage students advanced reading proficiency levels vs. 19% of low usage students",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD",
    ),
    (
        "Overall, 25% of participants (n = 1,366) advanced at least one proficiency level during the four-week intervention period.",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD",
    ),
    (
        "Fourth and fifth graders gained over 21 ISIP scale score points, compared to expected losses of 10–11 points.",
        "Efficacy / NM Summer",
        4,
        "NM Summer — Boden PhD",
    ),
    (
        "In New Mexico, where the question of voice scoring and English learner performance has been directly tested, English learner (EL) proficiency parity with non-English Learners (non-EL) improved by 36% year-over-year after the introduction of Amira.",
        "Efficacy / NM / Equity",
        4,
        "Equity Whitepaper — R. Liu internal analysis May 2026",
    ),
    # ── Efficacy — Middle School + multi-state ────────────────────────────────
    (
        "Sixth-grade students using Amira for 20–30+ minutes per week gained 6–7 percentile points more than low-usage peers across two consecutive years, even after controlling for starting reading levels.",
        "Efficacy / Middle School",
        4,
        "Tutor English Middle School — Boden PhD, 10 districts x 2 yrs",
    ),
    (
        'The positive effects of Amira were replicated in both the 2023–2024 and 2024–2025 school years, with effect sizes of 0.35–0.40— comparable to high-quality one-on-one tutoring and well within the "Zone of Desired Effects" for educational interventions.',
        "Comparative / Efficacy",
        3,
        "Tutor English Middle School — Boden PhD; Hattie",
    ),
    (
        "Students meeting recommended usage achieved approximately 70% more growth than minimal users.",
        "Efficacy / Middle School",
        4,
        "Tutor English Middle School — Boden PhD",
    ),
    (
        "A statewide study in North Dakota found that students who used Amira regularly demonstrated significantly larger gains on the 2024 state reading assessment (NDSA) than non-users.",
        "Efficacy / Multi-State",
        4,
        "Tutor English Middle School — North Dakota DPI 2024",
    ),
    (
        "Third graders engaging with Amira for more than 20 minutes per week scored 15 points higher on average than low-usage peers, while fourth graders showed a 17-point advantage and fifth graders showed a 10-point advantage.",
        "Efficacy / Multi-State",
        4,
        "Tutor English Middle School — North Dakota DPI 2024",
    ),
    (
        "Utah's state education department reported effect sizes exceeding 0.4, placing Amira within John Hattie's \"Zone of Desired Effects.\"",
        "Comparative / Utah",
        3,
        "Tutor English Middle School — Utah State Board 2022",
    ),
    (
        "Developed in collaboration with leading reading researchers like Dr. Nell K. Duke, the platform provides structured practice that reinforces classroom instruction and accelerates skill development.",
        "Identity / Provenance",
        1,
        "Tutor English Middle School",
    ),
    # ── Psychometric / fairness (Equity Whitepaper) ───────────────────────────
    (
        "Amira is the most widely used reading screener and tutoring system in the United States.",
        "Identity / Market position",
        4,
        "Equity Whitepaper",
    ),
    (
        "Amira is designed for equity, trained for equity, validated for equity, and empirically demonstrates equity.",
        "Identity / Equity",
        1,
        "Equity Whitepaper",
    ),
    (
        "Amira ISIP's reliability estimates meet or exceed 0.90 — well above the threshold — for virtually every grade-by-student subgroup combination evaluated.",
        "Psychometric",
        4,
        "Equity Whitepaper — Amira ISIP Technical Guide 2025-26",
    ),
    (
        "DIF rates remain at or below 5% across all demographic comparisons at every grade level, and items showing DIF are removed or recalibrated annually.",
        "Psychometric",
        4,
        "Equity Whitepaper — Amira ISIP Technical Guide 2025-26",
    ),
    (
        "Independent district-level scoring validation studies — most recently a Spring 2026 study in Texas ISD spanning 1,827 oral reading fluency activities and over 301,000 words scored — confirm 96.9% overall human-Amira agreement, with 95.8% accuracy for non-English native language speakers.",
        "Psychometric",
        4,
        "Equity Whitepaper — Texas ISD Spring 2026 validation",
    ),
    (
        "Amira's word-level scoring accuracy for African American students is higher than its accuracy for the overall population (95.6% vs. 95.1%).",
        "Psychometric / Equity",
        4,
        "Equity Whitepaper — RED reserved test set",
    ),
    (
        "The Amira RED model is trained on a corpus of more than 100,000 hours of children's speech captured during real assessment sessions in real classrooms.",
        "Mechanism / Equity",
        4,
        "Equity Whitepaper",
    ),
    # ── Independent approvals / credentials ───────────────────────────────────
    (
        "In an independent review conducted by the Sandra Dunagan Deal Center for Early Language and Literacy for the State of Georgia (November 2024), Amira ISIP ranked first among all screeners reviewed, with an overall score of 142.6 out of 155 — significantly above any other tool.",
        "Comparative / Georgia",
        4,
        "Equity Whitepaper — Deal Center, Nov 2024",
    ),
    (
        "In an independent evaluation conducted by the Oklahoma State Department of Education under the Strong Readers Act (2025), Amira passed all 17 mandatory screening criteria and earned the highest score among all evaluated screeners on the full evaluation rubric — 266 points compared to 202 for DIBELS.",
        "Comparative / Oklahoma",
        4,
        "Equity Whitepaper — OK State Dept of Ed 2025",
    ),
    (
        "In the 2024 California K-2 screener evaluation, Amira was the only screener unanimously approved by all expert reviewers across all grades in both English and Spanish.",
        "Comparative / Exclusivity / California",
        4,
        "Equity Whitepaper — CA K-2 evaluation 2024",
    ),
    (
        "Amira ISIP Assess is one of three MDE-approved screeners.",
        "State approval / Michigan",
        3,
        "Michigan FG — MDE list March 2026",
    ),
    (
        "Amira ISIP Assess is one of three MSDE-approved universal reading screeners for 2025-26.",
        "State approval / Maryland",
        3,
        "Maryland FG — MSDE report Feb 2026",
    ),
    (
        "The panel reported that reliability evidence is strong, with coefficients exceeding 0.80 across internal consistency, test-retest, and inter-rater measures, and that validity evidence including sensitivity and specificity for early reading risk is well documented.",
        "Psychometric / Maryland",
        4,
        "Maryland FG — MSDE report Feb 2026",
    ),
    # ── Product feature claims (field guides) ─────────────────────────────────
    (
        "Amira ISIP Assess includes RAN and a research-based dyslexia risk flag in the base product with no separate purchase required.",
        "Product / Feature",
        2,
        "Illinois FG; Michigan FG",
    ),
    (
        "A full class can be screened at the same time without one-on-one teacher administration.",
        "Product / Feature",
        2,
        "Illinois FG",
    ),
    (
        "Amira assesses in both English and Spanish using spoken language rather than translated print materials, providing what Amira describes as an authentic oral Spanish assessment.",
        "Product / Bilingual",
        2,
        "Illinois FG",
    ),
    (
        "Developed in partnership with Carnegie Mellon University, Science of Reading experts, and neuroscientists, Amira is an learning agent - continuously assessing, instructing, and tutoring each student to accelerate reading growth.",
        "Identity / Provenance",
        1,
        "EGS",
    ),
    (
        "Amira screens an entire class in 20 minutes or less by listening to students read aloud, capturing and analyzing over 10x more measurement points than traditional assessments.",
        "Quantified / Efficiency",
        3,
        "EGS",
    ),
    ("Growth for 5 Million+ Students", "Scale / Reach", 4, "EGS"),
    # ── Site-specific outcomes ────────────────────────────────────────────────
    (
        "At Tara Elementary in Clayton County, Georgia, students using Amira at the recommended dosage gained 18.7 weeks of reading growth in just 15.7 weeks of instruction.",
        "Efficacy / Site-specific",
        4,
        "Michigan FG — Amira internal outcome data, Tara Elementary",
    ),
    (
        "According to a November 2024 Baltimore Banner report, all 110 elementary schools in Baltimore County use Amira for kindergarten through grade 3 students.",
        "Adoption / Site-specific",
        4,
        "Maryland FG — Baltimore Banner, Nov 25 2024",
    ),
    (
        "Westowne Elementary showed a 12 percentage point increase in reading proficiency over the 2023-24 school year, with the largest gains in kindergarten where students made a 26 percentage point improvement.",
        "Efficacy / Site-specific",
        4,
        "Maryland FG — Baltimore Banner, Nov 25 2024",
    ),
]


async def main() -> None:
    inserted = skipped = 0
    async with SessionLocal() as session:
        profile = (
            await session.execute(select(WritingProfile).order_by(WritingProfile.id).limit(1))
        ).scalar_one_or_none()
        if profile is None:
            raise SystemExit("No WritingProfile found — seed the corpus first.")
        pid = profile.id
        existing = {
            row.approved_phrasing
            for row in (
                await session.execute(select(Claim).where(Claim.profile_id == pid))
            ).scalars()
        }
        n = 0
        for phrasing, category, tier, source in HARVESTED:
            if phrasing in existing:
                skipped += 1
                continue
            n += 1
            session.add(
                Claim(
                    profile_id=pid,
                    claim_code=f"H{n:03d}",
                    category=category,
                    tier=tier,
                    approved_phrasing=phrasing,
                    source=source,
                    status="approved",
                )
            )
            inserted += 1
        await session.commit()
    print(
        f"profile_id={pid}  inserted={inserted}  skipped(existing)={skipped}  total_candidates={len(HARVESTED)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
