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
    "version": 1,
    "favorable_keywords":   [...],   # carve-out / exemption / evidence-based language
    "unfavorable_keywords": [...],   # blanket-restriction language
    "restriction_keywords": [...],   # "is this a restriction at all?" signal
    "exclude_keywords":     [...],   # out-of-scope (cellphone bans) → forces neutral
    "rollup": {                       # how per-signal stances roll up to a state
      "favorable_wins_ties": true
    }
  }

Classification semantics (see ``classifier``):
  - exclude_keywords present                                  → neutral (out of lane)
  - restriction + favorable carve-out                         → favorable
  - blanket restriction, no carve-out                         → unfavorable
  - otherwise / unknown                                       → neutral

These keyword rules also drive a deterministic fallback classification when the
LLM provider is unavailable, so the pipeline never hard-depends on a model call.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CONFIG_NAME = "default"

# v1 default — the definition locked with Jon in the plan.
DEFAULT_STANCE_RULES: dict[str, Any] = {
    "version": 1,
    # Pro-evidence-based / carve-out language → favorable.
    "favorable_keywords": [
        "carve-out",
        "carve out",
        "carveout",
        "exemption",
        "exempt",
        "exempts",
        "exception",
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
    # Blanket-restriction language → unfavorable (unless a carve-out is present).
    "unfavorable_keywords": [
        "blanket",
        "total ban",
        "prohibit all",
        "ban all",
        "eliminate screen",
        "no screens",
        "device-free",
        "screen-free",
        "remove devices",
        "remove technology",
        "minimize screen time",
        "reduce screen time",
    ],
    # Does the item concern an instructional screen-time restriction at all?
    "restriction_keywords": [
        "screen time",
        "screen-time",
        "screentime",
        "device time",
        "limit",
        "limits",
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
