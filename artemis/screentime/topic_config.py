"""Tunable, config-driven TOPIC-relevance rules for Screen-Time Watch.

This is the data behind the topic gate (see ``filters.passes_topic_gate``) that
runs BEFORE store/classify. It decides whether a finding is genuinely about
**instructional / student screen-time or device-time limits** (and evidence-based
-tool exemptions to such limits) — vs. generic ed-policy noise (literacy, reading
retention, curriculum approval, test scores) that swamped the first live run.

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

# v2 default — the screen-time topic anchor (v2 tightens the exclude set against
# health/behavioral "screenings" and budget "study" noise from the first run).
DEFAULT_TOPIC_RULES: dict[str, Any] = {
    "version": 2,
    # At least ONE of these must appear for an item to be screen-time-relevant.
    # All are explicit screen/device-time anchors — NOT generic "limit"/"evidence
    # -based" language that any ed-policy item carries.
    "require_any": [
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
