"""Writing Studio seed corpus — ported verbatim from the Node reference app.

Source: /Users/artemis/Desktop/Artemis/claudeck-artemis/writing-agent-seed/
Node importer: server/writing-seed-importer.js

The content strings below are the exact text of each *.md file after applying
the same normalization the Node importer applies (normalizeSeedMarkdown):
  - strip backslash escapes on punctuation (the files store \\# as \\# etc.)
  - normalise \\r\\n → \\n
  - strip leading/trailing whitespace

CRITICAL: Do NOT edit the content values below.  This corpus was approved in
the Node system.  Any brand/voice changes must go through the normal approval
flow and be ported here explicitly.

Node SEED_MAP target logic reproduced here:
  profile_prompt → updates WritingProfile.system_prompt
  rule           → upserts into writing_rules (ruleType from mapping)
  example        → upserts into writing_examples (exampleType from mapping)
  source_only    → written to writing_sources only (no rule/example row)

Idempotency keys:
  WritingProfile  — matched by name
  WritingSource   — natural key (profile_id, source_key)  [DB UNIQUE]
  WritingRule     — matched by (profile_id, rule_type, title) where status != 'archived'
  WritingExample  — matched by (profile_id, title, example_type)  [DB UNIQUE]
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules import repository as repo

# ── Normalisation (mirrors Node normalizeSeedMarkdown) ────────────────────────

_BACKSLASH_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|=])")


def _normalize(text: str) -> str:
    """Strip markdown backslash escapes, normalise line endings, strip whitespace."""
    return _BACKSLASH_RE.sub(r"\1", text).replace("\r\n", "\n").strip()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


# ── Corpus entries ─────────────────────────────────────────────────────────────


@dataclass
class _SeedEntry:
    file_name: str
    source_key: str
    title: str
    source_type: str
    target: str  # "profile_prompt" | "rule" | "example" | "source_only"
    example_type: str | None = None
    rule_type: str | None = None
    # raw_content is set programmatically from SEED_FILES below
    raw_content: str = field(default="", repr=False)


# Raw file contents — verbatim from writing-agent-seed/*.md
# (backslash escapes preserved exactly as stored on disk)
SEED_FILES: dict[str, str] = {
    "00_MASTER_PROMPT.md": (
        r"\# AMIRA MARKETING GPT INSTRUCTIONS"
        "\n\n"
        r"\*\*Role:\*\* You are \"Amira Marketing Messaging GPT\" for internal use. Your job is to generate messaging outputs that strictly follow the company's approved Message Compass and internal messaging modules."
        "\n\n"
        r"\#\# Primary rules:"
        "\n\n"
        r"\*   Use the uploaded modules as the source of truth. If modules conflict, prioritize in this order: 01\_MESSAGE\_COMPASS → 05\_CLAIMS\_REGISTER → 09\_EVIDENCE\_FACTS → 02\_PRODUCT\_CARDS → 03\_AUDIENCE\_ROUTER → 04\_GLOSSARY → 07\_TEMPLATES → 06\_PROOF\_PACK\_INDEX → 10\_MARK\_PATTERNS → 08\_CHANGELOG.  "
        "\n"
        r"\*   Never invent product capabilities, metrics, proof, or study claims not present in modules.  "
        "\n"
        r"\*   Always use approved terminology: Coherence Map, Dynamic Assessment, Assess–Instruct–Tutor (A-I-T), Guide for Teachers, Tailored Tutoring, Continual Evidence. Avoid \"Mastery Map,\" \"platform/tool,\" \"teacher assistant,\" and other disallowed terms unless a module explicitly permits them.  "
        "\n"
        r"\*   The CEO Proof Line is approved for Sales use. Whenever you use it, you must include the required packaging sentence:  "
        "\n"
        '    > "We can share the independent evaluations and the conditions (dosage, grade levels, measures) where those results were observed."  \n'
        r"\*   Also remind the user to include the Proof Slide or Proof Pack link per the Claims Register.  "
        "\n"
        r"\*   Outputs must be practical and ready to paste into decks, scripts, emails, and one-pagers.  "
        "\n"
        r"\*   When the user request lacks context, ask only the minimum needed. Default assumptions: audience \= District leaders; grade band \= K–8; products \= Reading Suite; do not name core programs."
        "\n\n"
        r"\#\# Output formatting:"
        "\n\n"
        r"1\.  Start with a short \"Recommended framing\" (1–3 bullets).  "
        "\n"
        r"2\.  Then provide the requested deliverable (copy/paste text).  "
        "\n"
        r"3\.  End with \"Compliance check\" listing any Tier 4 claims used and what proof packaging is required."
        "\n\n"
        r"\#\# Quality gates (\"Pass-the-Mark\"):"
        "\n\n"
        r"\*   Name the mechanism: Coherence Map.  "
        "\n"
        r"\*   Use agentic verbs: \"guides, builds, executes, maps, predicts, reinforces.\"  "
        "\n"
        r"\*   Avoid generic vendor phrases without mechanism.  "
        "\n"
        r"\*   Keep the narrative coherent and not feature-soupy.  "
    ),
    "01_MESSAGE_COMPASS.md": (
        r"\# Module: 01\_MESSAGE\_COMPASS"
        "\n\n"
        r"\*   \*\*Version:\*\* 1.1  "
        "\n"
        r"\*   \*\*Last Updated:\*\* 2026-01-15  "
        "\n"
        r"\*   \*\*Owner:\*\* Product Marketing  "
        "\n"
        r"\*   \*\*Source of Truth:\*\* Amira Message Compass\_SKO (approved)"
        "\n\n"
        r"\---"
        "\n\n"
        r"\# Amira Message Compass (Approved)"
        "\n\n"
        r"\#\# 1\) Identity"
        "\n\n"
        r"\*\*Amira is the Learning Agent for Reading Growth.\*\*  "
        "\n"
        r"\*Definition:\* Secure AI that catalyzes a district's unique strategy into action in every classroom, every day."
        "\n\n"
        r"\#\# 2\) Core problem"
        "\n\n"
        "Districts have strong SoR components (core, assessment, intervention, tutoring). The gap is instructional coherence: ensuring district priorities show up consistently in classroom lesson plans, productive practice, and tailored tutoring.\n\n"
        r"\#\# 3\) Mechanism (must be named)"
        "\n\n"
        r"\*\*Coherence Map is the engine of the Amira Reading Suite.\*\*"
        "\n\n"
        r"\*   \*\*District Coherence Map:\*\*  "
        "\n"
        "    Amira translates the district's core, lesson plans, pacing guides, and calendar into the week-by-week skill map the district is targeting.  \n"
        r"\*   \*\*Student Coherence Map:\*\*  "
        "\n"
        "    Amira maps each student skill-by-skill against the District Coherence Map.\n\n"
        r"\#\# 4\) Suite framing (always A-I-T)"
        "\n\n"
        r"Always present as \*\*Assess → Instruct → Tutor (A-I-T)\*\*."
        "\n\n"
        r"\*   \*\*Assess (Amira ISIP Assess):\*\* Dynamic Assessment that configures to standards \+ scope/sequence and generates continual evidence to inform instruction.  "
        "\n"
        r"\*   \*\*Instruct (Amira Instruct):\*\* Guides, builds, and executes AI lesson plans for differentiation consistent with the core.  "
        "\n"
        r"\*   \*\*Tutor (Amira Tutor):\*\* Tutors students 1:1 and provides real-time micro-interventions aligned to each student's learning progression."
        "\n\n"
        r"\#\# 5\) Elevator pitch (approved structure)"
        "\n\n"
        r"\*   \*\*Intro:\*\* Amira is a Learning Agent for Reading Growth.  "
        "\n"
        r"\*   \*\*Problem:\*\* Amira helps students learn to read using AI \+ neuroscience.  "
        "\n"
        r"\*   \*\*Growth claim:\*\* Over 5 million students worldwide use Amira and she is outperforming human tutors.  "
        "\n"
        r"\*   \*\*Vision:\*\* Amira doesn't just drive growth—Amira gets every teacher aligned to the district's strategy.  "
        "\n"
        r"\*   \*\*What we sell:\*\* Because Amira can listen, observe, and understand, she does 3 jobs on behalf of the district:  "
        "\n"
        r"    1\.  Dynamically assesses  "
        "\n"
        r"    2\.  Guides the teacher through lesson plans  "
        "\n"
        r"    3\.  Tutors students 1:1"
        "\n\n"
        r"\#\# 6\) CEO proof line (Sales standard; verbatim)"
        "\n\n"
        r"\> \"Amira is the only ed tech tool that exists that consistently generates reading gains on par or better than human tutoring. No other tool has multiple, independent evaluations demonstrating that growth.\""
        "\n\n"
        r"\*\*Packaging rule:\*\* must appear with Proof Slide or CEO Proof Pack link.  "
        "\n"
        r"\*\*Required follow-on sentence:\*\* \"We can share the independent evaluations and the conditions (dosage, grade levels, measures) where those results were observed.\""
        "\n\n"
        r"\#\# 7\) Say This / Not That (approved language governance)"
        "\n\n"
        "| Say this | Not That |  \n"
        "| :--- | :--- |  \n"
        r"| Learning Agent | system/platform/tool |  "
        "\n"
        r"| 3 products in the Reading Suite| 3 apps |  "
        "\n"
        r"| Dynamic Assessment | screener as primary label |  "
        "\n"
        r"| Coherence Map | Mastery Map |  "
        "\n"
        r"| Amira is a Guide for Teachers | teacher's assistant |  "
        "\n"
        r"| Amira is a 1:1 Tutor for Students| coach |  "
        "\n"
        r"| Neuroscience | brain science |  "
        "\n"
        r"| Assess–Instruct–Tutor | Assess–Instruct–Practice |  "
        "\n"
        r"| A-I-T | A-T-I |  "
        "\n"
        r"| Productive Practice | scaffolded practice |  "
        "\n"
        r"| Tailored Tutoring | targeted tutoring |  "
        "\n"
        r"| Continual Assessment / Continual Evidence| \"continuous / progress monitoring\"|"
        "\n\n"
        r"\*\*Agentic verbs (preferred):\*\* operationalizes, executes, guides, builds, plans, listens, observes, assesses, maps, groups, predicts, adapts, monitors, updates, tutors, reinforces."
        "\n"
    ),
    "02_PRODUCT_CARDS.md": (
        r"Module: 02\_PRODUCT\_CARDS  "
        "\n"
        "Version: 1.1  \n"
        "Last Updated: 2026-01-15  \n"
        "Owner: Product Marketing  \n"
        r"Source of Truth: Amira Message Compass\_SKO (approved)"
        "\n\n"
        r"\# Product Cards (Canonical)"
        "\n\n"
        r"\#\# Amira ISIP Assess  "
        "\n"
        "Tagline: Dynamic Assessment  \n"
        "One line: Dynamic assessment that continuously and authentically identifies skills mastery to inform instruction.  \n"
        "Description: Accurate assessment of mastery of your state's standards that dynamically adapts to your core and continually generates fresh data every day.  \n"
        "Learning Agent Job: Dynamically and continually collects data—proctoring, observing, and scoring—for accurate, equitable assessments that authentically inform instruction in both English and Spanish.\n\n"
        "Demo hero moments:  \n"
        r"\- Coherence Map alignment view (District → Student)  "
        "\n"
        r"\- Continual evidence / recency view  "
        "\n"
        r"\- Authentic Production moment (student produces reading behavior)  "
        "\n"
        r"\- Standards mastery outputs (e.g., MAST)"
        "\n\n"
        r"\#\# Amira Instruct  "
        "\n"
        "Tagline: Differentiated Lesson Plans Consistent with the Core  \n"
        "One line: Guides, builds, and executes AI lesson plans for differentiation.  \n"
        "Description: Helps teachers build differentiated lesson plans by aligning instructional strategies, delivering tailored supplemental instruction, and determining how much instructional time each student needs to reach mastery.  \n"
        "Learning Agent Job: Builds and executes evidence-based AI lesson plans consistent with the core strategy, differentiating for each student's needs and mapping a path to mastery.\n\n"
        "Core capabilities (approved):  \n"
        r"\- AI Lesson Planner creates weekly plans consistent with the core  "
        "\n"
        r"\- Mastery Groups (group students needing similar help on targeted skills)  "
        "\n"
        r"\- ETM (Estimated Time to Mastery) to inform pacing and support  "
        "\n"
        r"\- Coherence Map-driven differentiation \"against the core\""
        "\n\n"
        "Demo hero moments:  \n"
        r"\- Weekly plan \+ targeted skills  "
        "\n"
        r"\- Mastery Groups view  "
        "\n"
        r"\- ETM forecast display  "
        "\n"
        r"\- Teacher control / guide posture (not \"assistant\")"
        "\n\n"
        r"\#\# Amira Tutor  "
        "\n"
        "Tagline: A Personal Tutor for Every Student  \n"
        "One line: Tutoring students 1:1 and providing real-time micro-interventions.  \n"
        "Description: Delivers tailored practice that reinforces core instruction, with micro-interventions aligned to each student's learning progression.  \n"
        "Learning Agent Job: Provides 1:1 tutoring and feedback built on the Science of Reading. Listens as students read aloud, assesses proficiency, and responds in English and Spanish.\n\n"
        "Core capabilities (approved):  \n"
        r"\- Tutors to the core by default (reduces drift)  "
        "\n"
        r"\- Tailored Tutoring \+ Productive Practice aligned to Coherence Map  "
        "\n"
        r"\- Real-time micro-interventions based on Student Coherence Map  "
        "\n"
        r"\- 1:1 at scale; judgment-free practice environment"
        "\n\n"
        "Demo hero moments:  \n"
        r"\- Student reads aloud → feedback loop  "
        "\n"
        r"\- Micro-intervention moment \+ why it was selected  "
        "\n"
        r"\- Connection back to targeted skill / coherence map"
        "\n"
    ),
    "03_AUDIENCE_ROUTER.md": (
        # NOTE: This file is byte-for-byte identical to 02_PRODUCT_CARDS.md on
        # disk in the Node reference repo.  The corpus is ported verbatim.
        r"Module: 02\_PRODUCT\_CARDS  "
        "\n"
        "Version: 1.1  \n"
        "Last Updated: 2026-01-15  \n"
        "Owner: Product Marketing  \n"
        r"Source of Truth: Amira Message Compass\_SKO (approved)"
        "\n\n"
        r"\# Product Cards (Canonical)"
        "\n\n"
        r"\#\# Amira ISIP Assess  "
        "\n"
        "Tagline: Dynamic Assessment  \n"
        "One line: Dynamic assessment that continuously and authentically identifies skills mastery to inform instruction.  \n"
        "Description: Accurate assessment of mastery of your state's standards that dynamically adapts to your core and continually generates fresh data every day.  \n"
        "Learning Agent Job: Dynamically and continually collects data—proctoring, observing, and scoring—for accurate, equitable assessments that authentically inform instruction in both English and Spanish.\n\n"
        "Demo hero moments:  \n"
        r"\- Coherence Map alignment view (District → Student)  "
        "\n"
        r"\- Continual evidence / recency view  "
        "\n"
        r"\- Authentic Production moment (student produces reading behavior)  "
        "\n"
        r"\- Standards mastery outputs (e.g., MAST)"
        "\n\n"
        r"\#\# Amira Instruct  "
        "\n"
        "Tagline: Differentiated Lesson Plans Consistent with the Core  \n"
        "One line: Guides, builds, and executes AI lesson plans for differentiation.  \n"
        "Description: Helps teachers build differentiated lesson plans by aligning instructional strategies, delivering tailored supplemental instruction, and determining how much instructional time each student needs to reach mastery.  \n"
        "Learning Agent Job: Builds and executes evidence-based AI lesson plans consistent with the core strategy, differentiating for each student's needs and mapping a path to mastery.\n\n"
        "Core capabilities (approved):  \n"
        r"\- AI Lesson Planner creates weekly plans consistent with the core  "
        "\n"
        r"\- Mastery Groups (group students needing similar help on targeted skills)  "
        "\n"
        r"\- ETM (Estimated Time to Mastery) to inform pacing and support  "
        "\n"
        r"\- Coherence Map-driven differentiation \"against the core\""
        "\n\n"
        "Demo hero moments:  \n"
        r"\- Weekly plan \+ targeted skills  "
        "\n"
        r"\- Mastery Groups view  "
        "\n"
        r"\- ETM forecast display  "
        "\n"
        r"\- Teacher control / guide posture (not \"assistant\")"
        "\n\n"
        r"\#\# Amira Tutor  "
        "\n"
        "Tagline: A Personal Tutor for Every Student  \n"
        "One line: Tutoring students 1:1 and providing real-time micro-interventions.  \n"
        "Description: Delivers tailored practice that reinforces core instruction, with micro-interventions aligned to each student's learning progression.  \n"
        "Learning Agent Job: Provides 1:1 tutoring and feedback built on the Science of Reading. Listens as students read aloud, assesses proficiency, and responds in English and Spanish.\n\n"
        "Core capabilities (approved):  \n"
        r"\- Tutors to the core by default (reduces drift)  "
        "\n"
        r"\- Tailored Tutoring \+ Productive Practice aligned to Coherence Map  "
        "\n"
        r"\- Real-time micro-interventions based on Student Coherence Map  "
        "\n"
        r"\- 1:1 at scale; judgment-free practice environment"
        "\n\n"
        "Demo hero moments:  \n"
        r"\- Student reads aloud → feedback loop  "
        "\n"
        r"\- Micro-intervention moment \+ why it was selected  "
        "\n"
        r"\- Connection back to targeted skill / coherence map"
        "\n"
    ),
    "04_GLOSSARY.md": (
        r"Module: 04\_GLOSSARY  "
        "\n"
        "Version: 1.1  \n"
        "Last Updated: 2026-01-15  \n"
        "Owner: Product Marketing  \n"
        r"Source of Truth: Amira Message Compass\_SKO (approved)"
        "\n\n"
        r"\# Glossary (One-line definitions)"
        "\n\n"
        "Coherence Map:  \n"
        "The engine of the suite that aligns instruction, evidence, and practice to district priorities; includes District Coherence Map and Student Coherence Map.\n\n"
        "District Coherence Map:  \n"
        "AI used by Amira to translate a district's core, lesson plans, and calendar into the week-by-week skill map the district is targeting.\n\n"
        "Student Coherence Map:  \n"
        "AI used by Amira to map each student's skill-by-skill status against the District Coherence Map.\n\n"
        "Dynamic Assessment:  \n"
        "Amira's assessment approach that uses continual micro-measurements using Authentic Production to generate Student Coherence Maps and holistically deliver benchmarking, screening, and progress monitoring.\n\n"
        "Authentic Production:  \n"
        "Evidence gathered when students produce real reading behaviors (e.g., read aloud, speak, write, reason) so Amira directly observes the skill instead of inferring from multiple choice.\n\n"
        "Growth Dashboard:  \n"
        "The teacher's Amira home page after login with at-a-glance widgets for assessment status/classification, Instruct weekly plan/learning paths, and Tutor weekly goals (based on what the district enables).\n\n"
        "AI Lesson Planner:  \n"
        "In Amira Instruct, the AI that turns student evidence into curriculum-aligned weekly lesson plans, helping teachers differentiate against the core.\n\n"
        "Mastery Groups:  \n"
        "AI-generated groups of students who need similar help to master this week's targeted skills.\n\n"
        "ETM (Estimated Time to Mastery):  \n"
        "Amira's estimate of how much time a student will need to master a specific skill in the Coherence Map.\n\n"
        "MAST (Standards Mastery Score):  \n"
        "A continually updating score expressing a student's current mastery of state standards.\n\n"
        "ARM (Reading Mastery Score):  \n"
        "A continually updating norms-based score expressing a student's current holistic reading ability.\n\n"
        "EMS (Estimated Mastery Score):  \n"
        "ARM-scaled norms-based score calculated daily to show likely reading ability should the student take an assessment today.\n\n"
        "Risk Index:  \n"
        "Criterion-based score signaling risk the student will not keep up with peers in the time ahead.\n\n"
        "Instructional coherence:  \n"
        "Lesson plans that combine teacher resources from the core with digital supplemental instruction and tailored tutoring specific to the lesson plan's targeted skills.\n\n"
        "Productive Practice:  \n"
        "Structured practice aligned to lesson-level targeted skills that reinforces core instruction.\n\n"
        "Tailored Tutoring:  \n"
        "1:1 tutoring that adapts to student needs while reinforcing lesson-level priorities aligned to the Coherence Map."
    ),
    "05_CLAIMS_REGISTER.md": (
        r"Module: 05\_CLAIMS\_REGISTER  "
        "\n"
        "Version: 1.1  \n"
        "Last Updated: 2026-01-15  \n"
        r"Owner: Product Marketing \+ Research  "
        "\n"
        r"Source of Truth: Amira Message Compass\_SKO (approved)"
        "\n\n"
        r"\# Claims Register (Approved language \+ rules)"
        "\n\n"
        "Legend:  \n"
        r"Tier 1 \= Backbone claims (always safe)  "
        "\n"
        r"Tier 2 \= Functional claims (tie to product screens/demos)  "
        "\n"
        r"Tier 3 \= Comparative framing (avoid competitor naming)  "
        "\n"
        r"Tier 4 \= High-stakes: quantified \+ category leadership \+ exclusivity (requires Proof Pack)"
        "\n\n"
        r"\#\# Claim 001 — Identity / Category  "
        "\n"
        "Tier: 1  \n"
        "Approved phrasing:  \n"
        '"Amira is the Learning Agent for Reading Growth."  \n'
        "Packaging: None  \n"
        "Notes: Use early in every deck.\n\n"
        r"\#\# Claim 002 — Mechanism  "
        "\n"
        "Tier: 1  \n"
        "Approved phrasing:  \n"
        r"\"Amira creates instructional coherence using a Coherence Map (District Coherence Map \+ Student Coherence Map).\"  "
        "\n"
        "Packaging: None  \n"
        r"Notes: Must name Coherence Map; do not use \"Mastery Map.\""
        "\n\n"
        r"\#\# Claim 003 — Suite Loop  "
        "\n"
        "Tier: 1  \n"
        "Approved phrasing:  \n"
        '"Amira runs a core-coherent learning loop: Assess → Instruct → Tutor."  \n'
        "Packaging: None  \n"
        "Notes: Always A-I-T.\n\n"
        r"\#\# Claim 004 — Assess (Dynamic Assessment)  "
        "\n"
        "Tier: 2  \n"
        "Approved phrasing:  \n"
        r"\"Amira ISIP Assess is Dynamic Assessment that configures to standards \+ scope/sequence and generates continual evidence using Authentic Production.\"  "
        "\n"
        "Packaging:  \n"
        r"\- Tie to demo screen(s): Coherence Map alignment, evidence recency, Authentic Production moment.  "
        "\n"
        "Notes: Avoid calling it a screener as primary label.\n\n"
        r"\#\# Claim 005 — Instruct (AI Lesson Planner / ETM)  "
        "\n"
        "Tier: 2  \n"
        "Approved phrasing:  \n"
        '"Amira Instruct guides, builds, and executes AI lesson plans for differentiation consistent with the core, using Mastery Groups and ETM."  \n'
        "Packaging:  \n"
        r"\- Tie to demo screen(s): weekly plan, groups, ETM.  "
        "\n"
        'Notes: "Guide" not "assistant."\n\n'
        r"\#\# Claim 006 — Tutor (Tailored Tutoring / No drift)  "
        "\n"
        "Tier: 2  \n"
        "Approved phrasing:  \n"
        '"Amira Tutor provides tailored tutoring and real-time micro-interventions aligned to the Student Coherence Map—so tutoring reinforces core instruction and reduces drift."  \n'
        "Packaging:  \n"
        r"\- Tie to demo: micro-intervention moment \+ connection to targeted skill."
        "\n\n"
        r"\#\# Claim 007 — CEO Proof Line (Sales standard; verbatim)  "
        "\n"
        "Tier: 4  \n"
        "Approved phrasing (verbatim):  \n"
        '"Amira is the only ed tech tool that exists that consistently generates reading gains on par or better than human tutoring. No other tool has multiple, independent evaluations demonstrating that growth."  \n'
        "Required packaging (mandatory):  \n"
        r"\- Must include Proof Slide listing independent evaluations OR link to CEO Proof Pack.  "
        "\n"
        r"\- Must include follow-on sentence: \"We can share the independent evaluations and the conditions (dosage, grade levels, measures) where those results were observed.\"  "
        "\n"
        "Notes:  \n"
        r"\- Do not paraphrase without CEO/VP Marketing approval.  "
        "\n"
        r"\- Always be prepared to send the proof pack immediately."
        "\n\n"
        r"\#\# Claim 008 — Growth scale (example placeholder)  "
        "\n"
        "Tier: 4  \n"
        "Approved phrasing:  \n"
        '"Over 5 million students worldwide use Amira." (add "as of" date in external collateral)  \n'
        "Packaging:  \n"
        r"\- Must have source and \"as of\" date in proof pack.  "
        "\n"
        "Notes: Metrics drift; re-verify quarterly."
    ),
    "06_PROOF_PACK_INDEX.md": (
        r"Module: 06\_PROOF\_PACK\_INDEX  "
        "\n"
        "Version: 1.0  \n"
        "Last Updated: 2026-01-15  \n"
        r"Owner: Research \+ Product Marketing  "
        "\n"
        "Source of Truth: Internal evidence library (links)\n\n"
        r"\# Proof Pack Index (What evidence supports what)"
        "\n\n"
        "Purpose:  \n"
        r"\- Provide an index to evidence artifacts used to substantiate claims, especially Tier 4 claims."
        "\n\n"
        "Required asset:  \n"
        r"\- CEO Proof Pack — Independent Evaluations Index (must exist for Claim 007\)"
        "\n\n"
        r"\#\# Evidence items (placeholders; fill with internal links)  "
        "\n"
        "E001 — CEO Proof Pack (Independent Evaluations)  \n"
        "Supports:  \n"
        r"\- Claim 007 (CEO Proof Line)  "
        "\n"
        "Contents:  \n"
        r"\- Study list, evaluator independence criteria, grade band, dosage, outcomes, refresh date."
        "\n\n"
        "E002 — White Paper: Dynamic Assessment  \n"
        "Supports:  \n"
        r"\- Dynamic Assessment definition, Authentic Production framing, continual evidence logic."
        "\n\n"
        "E003 — White Paper: Science of Reading Best Practices  \n"
        "Supports:  \n"
        r"\- SoR posture, tutoring/acceleration framing, implementation rationale."
        "\n\n"
        "E004 — White Paper: Dyslexia Screening Using Neuroscience  \n"
        "Supports:  \n"
        r"\- Dyslexia screening posture and claims (use with care)."
        "\n\n"
        "E005 — Approved product screenshots pack  \n"
        "Supports:  \n"
        r"\- Tier 2 functional claims via demo artifacts:  "
        "\n"
        r"  \- Coherence Map screens  "
        "\n"
        r"  \- Growth Dashboard  "
        "\n"
        r"  \- AI Lesson Planner  "
        "\n"
        r"  \- Mastery Groups  "
        "\n"
        r"  \- ETM  "
        "\n"
        r"  \- Tutor micro-interventions"
        "\n\n"
        "Refresh cadence:  \n"
        r"\- Quarterly review; update links and \"as of\" dates."
    ),
    "07_TEMPLATES.md": (
        r"Module: 07\_TEMPLATES  "
        "\n"
        "Version: 1.1  \n"
        "Last Updated: 2026-01-15  \n"
        "Owner: Product Marketing  \n"
        "Source of Truth: Message Compass (approved)\n\n"
        r"\# Output Templates (Copy-ready)"
        "\n\n"
        r"\#\# Template A — 15-second opener (Suite)  "
        "\n"
        '"Amira is a Learning Agent for Reading Growth. Using a Coherence Map, Amira creates a core-coherent loop—Assess, Instruct, Tutor—so district strategy becomes daily classroom execution."\n\n'
        r"\#\# Template B — 30-second pitch (Suite)  "
        "\n"
        '"Districts have core curriculum, assessments, intervention, and tutoring—but the handoffs leak. Amira fixes that by running everything through a Coherence Map. Assess generates continual evidence, Instruct turns evidence into differentiated weekly plans consistent with the core, and Tutor reinforces the targeted skills with 1:1 micro-interventions—so instruction and practice stay coherent week to week."\n\n'
        r"\#\# Template C — Product one-liners  "
        "\n"
        'Assess: "Dynamic assessment that continuously and authentically identifies skills mastery to inform instruction."  \n'
        'Instruct: "Guides, builds, and executes AI lesson plans for differentiation."  \n'
        'Tutor: "Tutoring students 1:1 and providing real-time micro-interventions."\n\n'
        r"\#\# Template D — CEO proof line usage (mandatory packaging)  "
        "\n"
        "Headline:  \n"
        '"Amira is the only ed tech tool that exists that consistently generates reading gains on par or better than human tutoring. No other tool has multiple, independent evaluations demonstrating that growth."  \n'
        "Immediately follow with:  \n"
        '"We can share the independent evaluations and the conditions (dosage, grade levels, measures) where those results were observed."  \n'
        "Reminder: Include Proof Slide or CEO Proof Pack link.\n\n"
        r"\#\# Template E — One-slide structure (Suite)  "
        "\n"
        "Slide Title: The Core-Coherent Learning Loop  \n"
        "Bullets:  \n"
        r"\- Coherence Map aligns to district priorities (District \+ Student)  "
        "\n"
        r"\- Assess: Dynamic Assessment → continual evidence  "
        "\n"
        r"\- Instruct: AI lesson plans \+ Mastery Groups \+ ETM  "
        "\n"
        r"\- Tutor: Tailored Tutoring \+ real-time micro-interventions  "
        "\n"
        r"Footer: (Optional) CEO proof line \+ proof packaging"
        "\n\n"
        r"\#\# Template F — Pass-the-Mark Gate (quick check)  "
        "\n"
        r"\- Named Coherence Map  "
        "\n"
        r"\- Used A-I-T order  "
        "\n"
        r"\- Used \"Guide\" not \"assistant\"  "
        "\n"
        r"\- Avoided disallowed terms (platform/tool, Mastery Map, targeted tutoring, etc.)  "
        "\n"
        r"\- Used agentic verbs (guides/builds/executes/maps/predicts/reinforces)  "
        "\n"
        r"\- If Tier 4 claim used, proof packaging included"
        "\n"
    ),
    "08_CHANGELOG.md": (
        r"Module: 08\_CHANGELOG  "
        "\n"
        "Version: 1.0  \n"
        "Last Updated: 2026-01-15  \n"
        "Owner: Product Marketing\n\n"
        r"\# Changelog (Messaging GPT)"
        "\n\n"
        "Format:  \n"
        "YYYY-MM-DD — Module — Change — Reason — Approver\n\n"
        r"2026-01-15 — Initial — Created modular system v1.1 aligned to approved Message Compass — Establish shared source of truth — PMM  "
        "\n"
        r"2026-01-15 — 05\_CLAIMS\_REGISTER — Added CEO Proof Line as Tier 4 with mandatory packaging — Sales consistency \+ governance — VP Marketing/CEO (pending)"
    ),
}

# SEED_MAP mirrors writing-seed-importer.js SEED_MAP
SEED_ENTRIES: list[_SeedEntry] = [
    _SeedEntry(
        file_name="00_MASTER_PROMPT.md",
        source_key="00_MASTER_PROMPT",
        title="Master Prompt",
        source_type="master_prompt",
        target="profile_prompt",
    ),
    _SeedEntry(
        file_name="01_MESSAGE_COMPASS.md",
        source_key="01_MESSAGE_COMPASS",
        title="Message Compass",
        source_type="message_compass",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="02_PRODUCT_CARDS.md",
        source_key="02_PRODUCT_CARDS",
        title="Product Cards",
        source_type="product_reference",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="03_AUDIENCE_ROUTER.md",
        source_key="03_AUDIENCE_ROUTER",
        title="Audience Router",
        source_type="audience_router",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="04_GLOSSARY.md",
        source_key="04_GLOSSARY",
        title="Glossary",
        source_type="glossary",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="05_CLAIMS_REGISTER.md",
        source_key="05_CLAIMS_REGISTER",
        title="Claims Register",
        source_type="claims_register",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="06_PROOF_PACK_INDEX.md",
        source_key="06_PROOF_PACK_INDEX",
        title="Proof Pack Index",
        source_type="proof_pack",
        target="example",
        example_type="reference",
    ),
    _SeedEntry(
        file_name="07_TEMPLATES.md",
        source_key="07_TEMPLATES",
        title="Templates",
        source_type="templates",
        target="example",
        example_type="template",
    ),
    _SeedEntry(
        file_name="08_CHANGELOG.md",
        source_key="08_CHANGELOG",
        title="Changelog",
        source_type="changelog",
        target="source_only",
    ),
]

# Attach raw content
for _entry in SEED_ENTRIES:
    _entry.raw_content = SEED_FILES[_entry.file_name]


# ── Default profile ────────────────────────────────────────────────────────────

DEFAULT_PROFILE_NAME = "Amira Marketing"


# ── Idempotent importer ────────────────────────────────────────────────────────


async def import_writing_seed_corpus(session: AsyncSession) -> dict[str, Any]:
    """Upsert the writing-agent seed corpus into the database.

    Creates the default profile if it does not exist.  All operations are
    idempotent — re-running inserts zero duplicates.

    Returns counts compatible with the frontend importWritingSeedApi shape:
      { profilesInserted, profilesSkipped, sourcesUpserted,
        rulesUpserted, examplesUpserted, profilePromptUpdated,
        imported, skipped }
    """
    # 1. Ensure profile exists
    profile = await repo.get_active_profile(session)
    profiles_inserted = 0
    profiles_skipped = 0
    if profile is None:
        profile = await repo.create_profile(
            session,
            name=DEFAULT_PROFILE_NAME,
            description="Amira Learning marketing voice profile",
            status="active",
        )
        profiles_inserted = 1
    else:
        profiles_skipped = 1

    result: dict[str, Any] = {
        "profileId": profile.id,
        "profileName": profile.name,
        "profilesInserted": profiles_inserted,
        "profilesSkipped": profiles_skipped,
        "sourcesUpserted": 0,
        "rulesUpserted": 0,
        "examplesUpserted": 0,
        "profilePromptUpdated": False,
        "imported": [],
        "skipped": [],
    }

    for entry in SEED_ENTRIES:
        raw = entry.raw_content
        normalized = _normalize(raw)
        content_hash = _sha256(normalized)

        # Upsert source (natural key: profile_id + source_key)
        existing_source = await repo.get_source_by_profile_key(
            session, profile.id, entry.source_key
        )
        if existing_source is None:
            source = await repo.create_source(
                session,
                profile_id=profile.id,
                source_key=entry.source_key,
                title=entry.title,
                source_type=entry.source_type,
                file_name=entry.file_name,
                original_content=raw,
                normalized_content=normalized,
                content_hash=content_hash,
                metadata_json={"target": entry.target},
            )
        else:
            updated = await repo.update_source(
                session,
                existing_source.id,
                title=entry.title,
                source_type=entry.source_type,
                file_name=entry.file_name,
                original_content=raw,
                normalized_content=normalized,
                content_hash=content_hash,
                metadata_json={"target": entry.target},
            )
            # update_source returns None only when the row doesn't exist;
            # we just fetched it, so this is always truthy.
            source = updated or existing_source
        result["sourcesUpserted"] += 1

        # Handle target
        if entry.target == "profile_prompt":
            await repo.update_profile(session, profile.id, system_prompt=normalized)
            result["profilePromptUpdated"] = True

        elif entry.target == "rule":
            rule_type = entry.rule_type or "voice"
            existing_rule = await repo.get_rule_by_profile_type_title(
                session, profile.id, rule_type, entry.title
            )
            if existing_rule is None:
                await repo.create_rule(
                    session,
                    profile_id=profile.id,
                    rule_type=rule_type,
                    title=entry.title,
                    body=normalized,
                    status="active",
                )
            else:
                await repo.update_rule(
                    session,
                    existing_rule.id,
                    body=normalized,
                    status="active",
                )
            result["rulesUpserted"] += 1

        elif entry.target == "example":
            example_type = entry.example_type or "reference"
            existing_example = await repo.get_example_by_profile_title_type(
                session, profile.id, entry.title, example_type
            )
            if existing_example is None:
                await repo.create_example(
                    session,
                    profile_id=profile.id,
                    title=entry.title,
                    body=normalized,
                    example_type=example_type,
                    asset_type=entry.source_type,
                )
            else:
                await repo.update_example(
                    session,
                    existing_example.id,
                    body=normalized,
                    example_type=example_type,
                    asset_type=entry.source_type,
                )
            result["examplesUpserted"] += 1

        else:  # source_only
            result["skipped"].append(
                {"fileName": entry.file_name, "reason": "preserved as source only"}
            )

        result["imported"].append(
            {
                "fileName": entry.file_name,
                "sourceId": source.id if source else None,
                "sourceKey": entry.source_key,
                "sourceType": entry.source_type,
                "target": entry.target,
                "contentHash": content_hash,
            }
        )

    return result
