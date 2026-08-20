"""Tunable, config-driven TOPIC-relevance rules for Screen-Time Watch.

This is the data behind the topic gate (see ``filters.passes_topic_gate``) that
runs BEFORE store/classify. It decides whether a finding is genuinely about
**instructional / student screen-time or device-time limits** (and evidence-based
-tool exemptions to such limits) **OR AI-in-schools policy** (adoption, pilots,
guidance, moratoria, bans, guardrails — the "AI use in the classroom" beat) — vs.
generic ed-policy noise (literacy, reading retention, curriculum approval, test
scores) that swamped the first live run.

2026-07-10 broadening: the owner's exec report ("Board Meetings on Screen Time
& the Use of AI") treats screen-time and AI-in-schools policy as ONE "rein in
the technology" story, tracked together. The gate's ``require_any`` now carries
BOTH a screen/device-time anchor set AND an AI-in-schools-policy anchor set —
either family alone is enough to pass. AI anchors are deliberately MULTI-WORD
(e.g. "artificial intelligence", "ai policy", "chatgpt") — a bare "ai" is never
used as an anchor because it substring-matches unrelated words ("email",
"available", "captain", ...) and would flood the gate with false positives.
STANCE tuning for AI-policy items (e.g. whether a ban on open/general chatbots
is actually *favorable* to Amira as a standards-aligned tool) is OUT OF SCOPE
here — that is being reviewed with Angela separately; see the TODO on
``stance_config`` / the classifier. AI findings land with best-effort stance
until that review lands.

Like the stance rules, the require/exclude term sets are **data, not code**, so
Angela can re-tune the gate after seeing real signals — a settings change, never
a deploy.

Resolution order (first hit wins), mirrors ``stance_config``:
  1. DB row ``screentime_stance_config(name='topic')`` — the live, editable source.
  2. ``settings.screentime_topic_rules`` — env/.env override (JSON), if set.
  3. ``DEFAULT_TOPIC_RULES`` baked here — the v1 definition.

Rule schema (``rules`` JSONB):
  {
    "version": 1,
    "require_any":  [...],   # at least one MUST appear → "this is about screen-time"
    "exclude_any":  [...],   # generic ed-policy noise → drop UNLESS a require term
                             #   is ALSO present (mixed signal → LLM tie-break/ drop)
    "llm_tiebreak": false     # optional per-config toggle (settings flag also gates)
  }

Why a require/exclude split (not the stance keywords): the stance
``restriction_keywords`` set is intentionally broad ("limit", "restrict", "cap")
and the ``favorable_keywords`` set includes generic pro-evidence language
("evidence-based", "research-based", "approved program") — exactly the words a
reading-retention or literacy bill uses. So reusing the stance keywords as a
topic gate is what let the noise through. The topic gate instead requires an
explicit SCREEN/DEVICE-time anchor.

PURE / no I/O at import time — the resolver functions do their own lazy imports.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Stored under the shared screentime_stance_config table, distinct name from the
# stance rules row so both live side-by-side and are tuned independently.
TOPIC_CONFIG_NAME = "topic"

# v3 default — screen-time anchors PLUS AI-in-schools-policy anchors (v2 tightened
# the exclude set against health/behavioral "screenings" and budget "study" noise;
# v3 widens require_any to also admit AI-in-schools policy, per the owner's "rein
# in the technology" framing — screen-time and AI-in-schools are ONE story now).
DEFAULT_TOPIC_RULES: dict[str, Any] = {
    "version": 4,
    # At least ONE of these must appear for an item to be relevant. Two anchor
    # families, either is sufficient on its own:
    #   1. explicit screen/device-time anchors — NOT generic "limit"/"evidence
    #      -based" language that any ed-policy item carries.
    #   2. explicit AI-in-schools POLICY anchors — deliberately MULTI-WORD only.
    #      A bare "ai" is NEVER used here: the gate does plain substring
    #      matching (see filters.topic_prescreen), so a 2-letter "ai" anchor
    #      would match inside ordinary words ("email", "available", "captain",
    #      "domain", ...) and defeat the gate's precision. Every AI anchor below
    #      is a multi-word phrase (or "chatgpt", a distinct token) to avoid that.
    "require_any": [
        # -- screen/device-time anchors --
        "screen time",
        "screen-time",
        "screentime",
        "screen use",
        "screen-use",
        "device time",
        "device-time",
        "screens in school",
        "screens in classroom",
        "screens in the classroom",
        "screen exposure",
        "device usage limit",
        "device-usage limit",
        "digital device time",
        "time on screens",
        "time on devices",
        "minutes of screen",
        "screen-based instruction",
        "screen based instruction",
        "instructional screen",
        "student screen",
        # -- AI-in-schools POLICY anchors (2026-07-10 broadening) --
        "artificial intelligence",
        "generative ai",
        "ai policy",
        "ai guidance",
        "ai in schools",
        "ai in the classroom",
        "ai use policy",
        "student use of ai",
        "ai moratorium",
        "ai literacy",
        "chatgpt",
        "responsible ai",
        "ai guardrails",
        "ai in education",
    ],
    # Generic ed-policy themes that produced the first-run noise. An item that
    # hits one of these AND has NO require-term is dropped outright. An item that
    # hits one of these AND a require-term is "mixed" → LLM tie-break (if enabled)
    # else kept (the require-term wins, gate is precision-on-drop not recall-killing).
    "exclude_any": [
        "reading retention",
        "retention of students",
        "third grade reading",
        "3rd grade reading",
        "literacy",
        "dyslexia",
        "biliteracy",
        "phonics",
        "science of reading",
        "curriculum approval",
        "curriculum adoption",
        "approved curriculum",
        "instructional materials adoption",
        "textbook adoption",
        "test scores",
        "standardized test",
        "assessment scores",
        "graduation requirement",
        # Health/behavioral "screenings" that matched "screen" in the first run —
        # NOT screen-time policy. A screenings item with no screen-TIME anchor
        # already lacks a require-term and is dropped; these belt-and-suspenders
        # excludes also drop one that incidentally name-drops a screen-time anchor.
        "behavioral health screening",
        "mental health screening",
        "pediatric screening",
        "vision screening",
        "hearing screening",
        "health screening",
        "developmental screening",
        "screening program",
        "screenings",
        # Pure budget / appropriations "study" items with no screen-time substance.
        "general appropriations",
        "appropriations act",
        "budget bill",
        "commissions a study",
        "feasibility study",
        "funding study",
    ],
    # ── v4: the BRAND lane ────────────────────────────────────────────────────
    # A named ed-tech vendor is ALWAYS topic-relevant: it bypasses both the
    # require-anchor test and the exclude list.  This exists because the NM
    # crisis is a BRAND/procurement story, not a policy story, and the policy
    # gate above structurally cannot see it -- "New Mexico district drops Amira
    # reading program" carries no screen-time anchor and trips the "literacy"
    # exclude.  Zero NM signals were captured during an active NM crisis.
    #
    # DISAMBIGUATION IS MANDATORY, for exactly the reason a bare "ai" anchor is
    # banned above: the gate does plain substring matching.  Several vendors are
    # ordinary English words -- amplify, renaissance, brisk, multitudes -- and a
    # bare token would match "amplify the message", "renaissance fair", "brisk
    # pace".  Every such name below is qualified.  Only genuinely distinctive
    # tokens (lexia, iready, newsela, edmentum, khanmigo) stand alone.
    "brand_any": [
        # -- Amira itself --
        "amira learning",
        "amira reading",
        "amira",  # distinctive enough inside a school-scoped feed
        # -- Tier 1: closest ICP match (their removals lead ours) --
        "i-ready",
        "iready",
        "curriculum associates",
        "amplify reading",
        "amplify education",
        "amplify ckla",
        "amplify literacy",
        "mclass",
        "dibels",
        "renaissance learning",
        "star reading",
        "lexia",
        "magic school ai",
        "magicschool",
        "brisk teaching",
        # -- Tier 2: moderate ICP alignment --
        "imagine learning",
        "fastbridge",
        "acadience",
        # -- Tier 3: less aligned ICP --
        "schoolai",
        "school ai platform",
        "newsela",
        "edmentum",
        "savvas",
        "khanmigo",
        # NOTE: "istation" is deliberately ABSENT. Istation is the company Amira
        # MERGED with, not a competitor -- their shared Salesforce org is
        # istation.my.salesforce.com. It was briefly listed here in error; a
        # story about Istation losing a district would have registered as a
        # COMPETITOR removal, which the risk scoring reads as a leading
        # indicator that Amira is next. Wrong signal, from our own parent.
        "ixl learning",
    ],
    # ── v4: the ENTRANT lane (Mark's "left field" concern) ────────────────────
    # General AI companies count as competitors because they could ship learning
    # software and blindside us from outside the ed-tech category.  Unlike
    # `brand_any` these do NOT stand alone -- an entrant term only counts when an
    # education-context term is ALSO present.  Watching OpenAI or Google on
    # general AI news would bury every other lane within a day.
    "entrant_any": [
        "openai",
        "anthropic",
        "chatgpt",
        "gemini",
        "copilot",
        "khan academy",
        "meta ai",
    ],
    # The education-context proof an `entrant_any` hit requires.
    "entrant_context_any": [
        "school",
        "district",
        "classroom",
        "teacher",
        "student",
        "k-12",
        "k12",
        "curriculum",
        "literacy",
        "reading instruction",
        "education",
    ],
    # Per-config toggle for the cheap LLM tie-break on mixed-signal items. The
    # settings.screentime_topic_llm_tiebreak flag ALSO gates it (both must be on).
    "llm_tiebreak": False,
}


def _merge_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay partial *rules* onto the baked default (shallow, per top-level key)."""
    if not rules:
        return dict(DEFAULT_TOPIC_RULES)
    merged = dict(DEFAULT_TOPIC_RULES)
    merged.update(rules)
    return merged


def default_topic_rules() -> dict[str, Any]:
    """Code-side rules: settings override (if set) merged onto the baked default.

    Pure / no I/O — safe to import at module top elsewhere.
    """
    from artemis.config import settings

    return _merge_rules(settings.screentime_topic_rules or None)


async def load_topic_rules(
    session: AsyncSession,
    *,
    name: str = TOPIC_CONFIG_NAME,
) -> dict[str, Any]:
    """Resolve the live topic rules: DB row → settings → baked default.

    Reading is the live tuning surface — change the DB row and the next run
    re-gates. Never raises on a missing row; falls back gracefully.
    """
    from artemis.screentime.models import ScreentimeStanceConfig

    try:
        row = (
            await session.execute(
                select(ScreentimeStanceConfig).where(ScreentimeStanceConfig.name == name)
            )
        ).scalar_one_or_none()
    except Exception:
        # Failure-safe: a DB hiccup must never break the gate; use code-side rules.
        return default_topic_rules()
    if row is not None and row.rules:
        return _merge_rules(row.rules)
    return default_topic_rules()


async def set_topic_rules(
    session: AsyncSession,
    rules: dict[str, Any],
    *,
    name: str = TOPIC_CONFIG_NAME,
) -> None:
    """Upsert the live (DB) topic rules. Used to tune without a deploy / by tests."""
    from json import dumps

    from sqlalchemy import text as _text

    await session.execute(
        _text(
            """
            INSERT INTO screentime_stance_config (name, rules, updated_at)
            VALUES (:name, CAST(:rules AS jsonb), now())
            ON CONFLICT (name) DO UPDATE
              SET rules = EXCLUDED.rules, updated_at = now()
            """
        ),
        {"name": name, "rules": dumps(rules)},
    )
