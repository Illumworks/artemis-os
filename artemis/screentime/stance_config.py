"""Tunable, config-driven stance rules for Screen-Time Watch.

The favorable / unfavorable / neutral definition is **data**, not code, so
Angela can re-tune it after seeing real signals — a settings change, never a
deploy.

Resolution order (first hit wins):
  1. DB row ``screentime_stance_config(name='default')`` — the live, editable source.
  2. ``settings.screentime_stance_rules`` — env/.env override (JSON), if set.
  3. ``DEFAULT_STANCE_RULES`` baked here — the v1 definition from the plan.

Rule schema (``rules`` JSONB):
  {
    "version": 2,
    "favorable_keywords":   [...],   # carve-out / exemption / evidence-based language
    "unfavorable_keywords": [...],   # *blanket* restriction language (total ban, etc.)
    "restriction_action_keywords": [...],  # explicit restrictive ACTION on screen/
                                     #   device time ("prohibit", "limit", "ban") —
                                     #   unfavorable on its own (no carve-out needed)
    "restriction_keywords": [...],   # broad screen-time ANCHOR ("screen time", "cap")
                                     #   — topicality only, NOT unfavorable on its own
    "exclude_keywords":     [...],   # out-of-scope (cellphone bans) → forces neutral
    "rollup": {                       # how per-signal stances roll up to a state
      "favorable_wins_ties": true
    }
  }

Classification semantics (see ``classifier``):
  - not screen-time relevant / exclude_keywords present       → neutral (out of lane)
  - any restriction (blanket OR action OR anchor) + carve-out → favorable
  - blanket OR restrictive-action keyword, no carve-out       → unfavorable
  - anchor-only (e.g. a "standards" framework, a study)       → neutral
  - otherwise / unknown                                       → neutral

The split between ``restriction_action_keywords`` (a real restrictive action →
unfavorable on its own) and the broad ``restriction_keywords`` anchor (topicality
only) is what lets "Screen time prohibited in preschool" read 🔴 while "Student
Screen-Time Standards Act" — a standards framework with no restrictive action —
stays ⚪ neutral.

These keyword rules also drive a deterministic fallback classification when the
LLM provider is unavailable, so the pipeline never hard-depends on a model call.

TODO(2026-07-10, AI-in-schools broadening): the topic gate (topic_config.py,
v3) now also admits AI-in-schools POLICY findings (not just screen/device-time),
per the owner's "rein in the technology" framing. These STANCE keywords were
NOT retuned for AI here — that is deliberately deferred to a review with
Angela, because AI-policy stance has nuance the generic restriction/favorable
keywords below don't capture (e.g. per the exec report, a ban on open/general
chatbots is NOT unfavorable to Amira, which sits in the standards-aligned,
purpose-built-tool carve-out — the opposite of how "ban" reads for screen-time).
Until that review lands, AI-policy findings get classified with this same
best-effort generic ruleset (e.g. "prohibit"/"ban" reads unfavorable even for
an open-chatbot ban that wouldn't actually hurt Amira) — a known, accepted gap.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CONFIG_NAME = "default"

# v2 default — the definition locked with Jon, tuned after the first real bills.
DEFAULT_STANCE_RULES: dict[str, Any] = {
    "version": 2,
    # Pro-evidence-based / carve-out language → favorable.
    "favorable_keywords": [
        "carve-out",
        "carve out",
        "carveout",
        "exemption",
        "exempt",
        "exempts",
        "exempting",
        "exception",
        "exclude evidence-based",
        "evidence-based",
        "evidence based",
        "purpose-built",
        "purpose built",
        "instructional software",
        "educational software",
        "approved program",
        "approved tool",
        "high-quality instructional",
        "research-based",
        "adaptive learning",
    ],
    # *Blanket* / sweeping restriction language → unfavorable on its own (unless a
    # carve-out is present). These are the strongest restriction signals.
    "unfavorable_keywords": [
        "blanket",
        "total ban",
        "prohibit all",
        "ban all",
        "eliminate screen",
        "no screens",
        "no screen time",
        "device-free",
        "screen-free",
        "remove devices",
        "remove technology",
        "minimize screen time",
        "reduce screen time",
    ],
    # Explicit restrictive ACTION on screen / device / instructional time. A match
    # here (on a screen-time-topical item) is unfavorable on its own — no carve-out
    # needed — because it actively restricts screen/device use in instruction. This
    # is what makes "Screen time prohibited", "limiting screen time", and
    # "screen-based instruction limited" read 🔴 (they were neutral under v1, which
    # only fired unfavorable on the narrow "blanket" set). The negation-aware
    # carve-out check still flips these to 🟢 when an evidence-based exemption is
    # present. NOTE: kept distinct from the broad anchor below so a non-restrictive
    # "Standards Act" / study does NOT become unfavorable.
    "restriction_action_keywords": [
        "prohibit",
        "prohibits",
        "prohibited",
        "prohibiting",
        "prohibition",
        "ban screen",
        "banned",
        "banning",
        "limit screen",
        "limits screen",
        "limiting screen",
        "limit instructional",
        "limit device",
        "limits device",
        "limiting device",
        "restrict screen",
        "restricts screen",
        "restricting screen",
        "restrict device",
        "screen time limited",
        "screen-based instruction limited",
        "screen based instruction limited",
        "instruction limited",
        "screen time prohibited",
        "screen-time prohibited",
        "cap on screen",
        "capping screen",
        "no device use",
        "device use prohibited",
        "device-use prohibited",
        "devices prohibited",
        "ban on devices",
    ],
    # Broad screen-time ANCHOR — establishes "is this about screen-time at all?"
    # for topicality. Intentionally broad ("screen time", "cap", "limit") — these
    # alone are NOT unfavorable (a "Standards Act" mentions screen time without
    # restricting it). Restrictive intent comes from restriction_action_keywords.
    "restriction_keywords": [
        "screen time",
        "screen-time",
        "screentime",
        "screen-based instruction",
        "screen based instruction",
        "device time",
        "limit",
        "limits",
        "limiting",
        "limited",
        "restrict",
        "restriction",
        "cap",
        "minutes per day",
        "instructional technology",
        "digital learning",
    ],
    # Out of lane — cellphone-ban policy is a DIFFERENT project. Force neutral.
    "exclude_keywords": [
        "cellphone",
        "cell phone",
        "cell-phone",
        "smartphone",
        "phone ban",
        "phones in school",
        "personal device ban",
        "bell to bell",
        "bell-to-bell",
    ],
    "rollup": {"favorable_wins_ties": True},
}


def _merge_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay partial *rules* onto the baked default (shallow, per top-level key)."""
    if not rules:
        return dict(DEFAULT_STANCE_RULES)
    merged = dict(DEFAULT_STANCE_RULES)
    merged.update(rules)
    return merged


def default_stance_rules() -> dict[str, Any]:
    """Code-side rules: settings override (if set) merged onto the baked default.

    Pure / no I/O — safe to import at module top elsewhere.
    """
    from artemis.config import settings

    return _merge_rules(settings.screentime_stance_rules or None)


async def load_stance_rules(
    session: AsyncSession,
    *,
    name: str = DEFAULT_CONFIG_NAME,
) -> dict[str, Any]:
    """Resolve the live stance rules: DB row → settings → baked default.

    Reading is the live tuning surface — change the DB row and the next run
    reclassifies. Never raises on a missing row; falls back gracefully.
    """
    from artemis.screentime.models import ScreentimeStanceConfig

    row = (
        await session.execute(
            select(ScreentimeStanceConfig).where(ScreentimeStanceConfig.name == name)
        )
    ).scalar_one_or_none()
    if row is not None and row.rules:
        return _merge_rules(row.rules)
    return default_stance_rules()


async def set_stance_rules(
    session: AsyncSession,
    rules: dict[str, Any],
    *,
    name: str = DEFAULT_CONFIG_NAME,
) -> None:
    """Upsert the live (DB) stance rules. Used to tune without a deploy / by tests."""
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
