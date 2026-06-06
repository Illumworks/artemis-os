"""Seed per-family qualification rulesets + territory config from Josh's spec.

Derives everything from ``artemis.marketing.josh_spec.parse_spec()`` — Josh's
canonical doc is the single source of truth.  Re-runnable when he updates the
spec (idempotent upsert).

## What it does

1.  Parses Josh's spec (§1 territory, §2 reason codes, §3 campaign mappings).
2.  Builds one Ruleset per canonical campaign family (obc / dyslexia / biliteracy
    / hit / general_growth) with version_tag="josh_spec_v1", state="active",
    hard_filters=[] (see note below), and weighted_signals derived faithfully
    from the spec:
      - weight 0.90  if default_urgency contains "hot"
      - weight 0.60  if default_urgency contains "standard" (but not "hot")
      - weight 0.30  if default_urgency contains "enrichment" (or fallback)
    Every entry is tagged source="josh_spec_v1".
3.  Seeds one TerritoryConfig row per family:
      standard_states = spec.territory_config.priority_states  (FL, IN, MD, MO, IL, TX)
      hot_states      = []
      unlisted_multiplier = 0.85  (default — non-priority states get the 15% penalty)
    Priority states get the standard multiplier (1.0); everyone else the 0.85
    unlisted penalty — a faithful soft focus on Josh's six states.
4.  Archives any other active Ruleset rows (e.g. smoke-1 stubs, stale obc drafts)
    so exactly one josh_spec_v1 ruleset per family is active (no double-scoring).
    Lossless: rows are set to state='archived', never deleted.
5.  Upsert semantics: safe to re-run; (family, version_tag) pair is unique.

## Hard-filter note

Josh's §4.1 hard skips (HMH partner districts, single-school, enrollment < 5,000)
are NOT expressible in the current filter engine — they require Salesforce flags,
geography resolution, and NCES enrollment data that the engine does not yet
pipeline at score time.  These are deferred to Phase 3 qualifier logic.

We do NOT add a ``state_not_excluded`` hard filter even though the engine supports
it: that filter would hard-reject every signal from a non-priority state, which
contradicts Josh's intent (priority states are a *multiplier* focus, not an
exclusion list).  Territory focus is applied via the unlisted_multiplier in
TerritoryConfig instead.

## Usage

    # Seed only (default mode):
    uv run python scripts/seed_josh_rulesets.py

    # Seed + rescore all existing signals under new rulesets:
    uv run python scripts/seed_josh_rulesets.py --rescore-all

    # Point at a specific DB (overrides .env):
    ARTEMIS_DB_URL="postgresql+asyncpg://..." uv run python scripts/seed_josh_rulesets.py

Exit codes:
    0  — success
    1  — fatal error
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, TypedDict

# ── sys.path bootstrap ─────────────────────────────────────────────────────────
# When run as ``python scripts/seed_josh_rulesets.py``, sys.path[0] is the
# scripts/ directory, not the repo root.  Insert the repo root so that
# ``import artemis`` resolves correctly regardless of how the script is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import artemis.marketing.models  # noqa: F401 — registers models on Base.metadata
import artemis.pipelines.models  # noqa: F401 — pipeline_runs FK dep of signal_queue
from artemis.config import settings  # use settings.db_url — NOT database_url
from artemis.db import attach_pgvector_codec
from artemis.marketing.josh_spec import normalize_campaign_family, parse_spec
from artemis.marketing.models import Ruleset, SignalQueue, TerritoryConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("seed_josh_rulesets")

# ── TypedDicts ────────────────────────────────────────────────────────────────


class WeightedSignal(TypedDict):
    reason_code: str
    weight: float
    source: str


class RulesetData(TypedDict):
    family: str
    version_tag: str
    state: str
    hard_filters: list[Any]
    weighted_signals: list[WeightedSignal]
    qualitative_rubrics: list[Any]


class TerritoryData(TypedDict):
    family: str
    hot_states: list[str]
    standard_states: list[str]
    unlisted_multiplier: float


# ── Constants ─────────────────────────────────────────────────────────────────

VERSION_TAG = "josh_spec_v1"
SOURCE_TAG = "josh_spec_v1"

# Faithful numeric translation of Josh's urgency tiers (§2).
# The mapping is intentionally explicit so it is reviewable and auditable.
_URGENCY_WEIGHT: dict[str, float] = {
    "hot": 0.90,
    "standard": 0.60,
    "enrichment": 0.30,
}


# ── Weight derivation ─────────────────────────────────────────────────────────


def _derive_weight(default_urgency: str) -> float:
    """Map Josh's default_urgency string to a numeric weight.

    Rules (applied in priority order — first match wins):
      - contains "hot"        → 0.90
      - contains "standard"   → 0.60
      - contains "enrichment" → 0.30
      - fallback              → 0.30  (treat unknown as enrichment)

    This is a faithful translation of Josh's three-tier system.
    """
    u = default_urgency.lower()
    if "hot" in u:
        return _URGENCY_WEIGHT["hot"]
    if "standard" in u:
        return _URGENCY_WEIGHT["standard"]
    if "enrichment" in u:
        return _URGENCY_WEIGHT["enrichment"]
    # Unknown — default to enrichment weight
    log.warning(
        "Unknown urgency tier in spec: %r — defaulting to enrichment weight", default_urgency
    )
    return _URGENCY_WEIGHT["enrichment"]


# ── Build ruleset data from spec ──────────────────────────────────────────────


def build_ruleset_map() -> dict[str, RulesetData]:
    """Parse Josh's spec and build per-family ruleset data.

    Returns a dict mapping canonical family slug → RulesetData.
    """
    spec = parse_spec()
    urgency_by_code: dict[str, str] = {rc.code: rc.default_urgency for rc in spec.reason_codes}

    rulesets: dict[str, RulesetData] = {}

    for mapping in spec.campaign_type_mappings:
        family = normalize_campaign_family(mapping.campaign_type)
        if family is None:
            log.warning(
                "campaign_type %r did not canonicalize to a known family — skipping",
                mapping.campaign_type,
            )
            continue

        weighted_signals: list[WeightedSignal] = []
        for code in mapping.reason_codes:
            urgency = urgency_by_code.get(code, "")
            weight = _derive_weight(urgency)
            weighted_signals.append(
                WeightedSignal(
                    reason_code=code,
                    weight=weight,
                    source=SOURCE_TAG,
                )
            )

        rulesets[family] = RulesetData(
            family=family,
            version_tag=VERSION_TAG,
            state="active",
            hard_filters=[],
            weighted_signals=weighted_signals,
            qualitative_rubrics=[],
        )

    return rulesets


def build_territory_data() -> dict[str, TerritoryData]:
    """Parse Josh's spec and build per-family territory config data.

    Returns a dict mapping canonical family slug → TerritoryData.

    Priority states (FL, IN, MD, MO, IL, TX) become standard_states (1.0×).
    hot_states is left empty — no family currently designates hot states.
    Unlisted states receive the 0.85 multiplier.
    """
    from artemis.marketing.josh_spec import CANONICAL_CAMPAIGN_FAMILIES

    spec = parse_spec()
    priority_states = list(spec.territory_config.priority_states)

    return {
        family: TerritoryData(
            family=family,
            hot_states=[],
            standard_states=priority_states,
            unlisted_multiplier=0.85,
        )
        for family in CANONICAL_CAMPAIGN_FAMILIES
    }


# ── DB operations ─────────────────────────────────────────────────────────────


async def _archive_stale_rulesets(session: AsyncSession, active_families: set[str]) -> int:
    """Archive any active ruleset that is NOT the josh_spec_v1 version.

    Lossless: sets state='archived', never deletes.
    Returns count of archived rows.
    """
    result = await session.execute(
        select(Ruleset).where(
            Ruleset.state == "active",
            Ruleset.version_tag != VERSION_TAG,
        )
    )
    stale = list(result.scalars().all())
    for row in stale:
        row.state = "archived"
        log.info(
            "Archived stale ruleset: family=%s version=%s id=%s",
            row.family,
            row.version_tag,
            row.id,
        )
    return len(stale)


async def _upsert_rulesets(
    session: AsyncSession, rulesets: dict[str, RulesetData]
) -> dict[str, str]:
    """Upsert rulesets by (family, version_tag) — idempotent.

    Returns dict of family → action ("inserted" | "updated").
    """
    actions: dict[str, str] = {}
    for family, data in rulesets.items():
        # Check existing
        result = await session.execute(
            select(Ruleset).where(
                Ruleset.family == family,
                Ruleset.version_tag == VERSION_TAG,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            row = Ruleset(
                family=data["family"],
                version_tag=data["version_tag"],
                state=data["state"],
                hard_filters=data["hard_filters"],
                weighted_signals=data["weighted_signals"],
                qualitative_rubrics=data["qualitative_rubrics"],
            )
            session.add(row)
            actions[family] = "inserted"
            log.info("Inserted ruleset: family=%s version=%s", family, VERSION_TAG)
        else:
            # Update in place (weights may have changed if spec was updated)
            existing.weighted_signals = data["weighted_signals"]
            existing.hard_filters = data["hard_filters"]
            existing.qualitative_rubrics = data["qualitative_rubrics"]
            existing.state = "active"
            actions[family] = "updated"
            log.info(
                "Updated ruleset: family=%s version=%s id=%s", family, VERSION_TAG, existing.id
            )
    return actions


async def _upsert_territory_configs(
    session: AsyncSession, territory: dict[str, TerritoryData]
) -> dict[str, str]:
    """Upsert TerritoryConfig by family — idempotent.

    Returns dict of family → action ("inserted" | "updated").
    """
    actions: dict[str, str] = {}
    for family, data in territory.items():
        result = await session.execute(
            select(TerritoryConfig).where(TerritoryConfig.family == family)
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            row = TerritoryConfig(
                family=data["family"],
                hot_states=data["hot_states"],
                standard_states=data["standard_states"],
                unlisted_multiplier=data["unlisted_multiplier"],
            )
            session.add(row)
            actions[family] = "inserted"
            log.info("Inserted territory_config: family=%s", family)
        else:
            existing.hot_states = data["hot_states"]
            existing.standard_states = data["standard_states"]
            existing.unlisted_multiplier = data["unlisted_multiplier"]
            actions[family] = "updated"
            log.info("Updated territory_config: family=%s id=%s", family, existing.id)
    return actions


async def _rescore_all_signals(session: AsyncSession) -> dict[str, int]:
    """Re-run qualification for ALL signals (any status).

    Returns {"rescored": N, "skipped_no_ruleset": K, "errors": E}.
    """
    from artemis.marketing.qualification import run_and_store_qualification

    result = await session.execute(select(SignalQueue))
    signals = list(result.scalars().all())
    log.info("--rescore-all: found %d signals to rescore", len(signals))

    rescored = 0
    skipped = 0
    errors = 0

    for signal in signals:
        try:
            qual = await run_and_store_qualification(session, signal)
            if qual is None:
                skipped += 1
            else:
                await session.commit()
                await session.refresh(signal)
                rescored += 1
        except Exception:
            log.exception("rescore error for signal id=%s (non-fatal)", signal.id)
            errors += 1
            await session.rollback()

    return {"rescored": rescored, "skipped_no_ruleset": skipped, "errors": errors}


def _print_summary(rulesets: dict[str, RulesetData]) -> None:
    """Print a formatted summary table: family → #weighted_signals → version → state."""
    header = f"{'Family':<20} {'#Signals':>9} {'Version':<16} {'State':<10}"
    separator = "-" * len(header)
    print("\nRuleset seeding summary:")
    print(separator)
    print(header)
    print(separator)
    for family, data in sorted(rulesets.items()):
        n_signals = len(data["weighted_signals"])
        print(f"{family:<20} {n_signals:>9} {data['version_tag']:<16} {data['state']:<10}")
    print(separator)


# ── Main entry point ───────────────────────────────────────────────────────────


async def _run(rescore_all: bool) -> None:
    """Seed rulesets + territory configs; optionally rescore all signals."""
    # Use settings.db_url — NOT settings.database_url (which doesn't exist)
    db_url = str(settings.db_url)
    engine = create_async_engine(db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)

    try:
        rulesets = build_ruleset_map()
        territory = build_territory_data()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            # 1. Archive stale active rulesets (smoke-1 stubs, old drafts)
            archived = await _archive_stale_rulesets(session, set(rulesets.keys()))
            log.info("Archived %d stale active ruleset(s)", archived)

            # 2. Upsert josh_spec_v1 rulesets
            ruleset_actions = await _upsert_rulesets(session, rulesets)

            # 3. Upsert territory configs
            territory_actions = await _upsert_territory_configs(session, territory)

            await session.commit()
            log.info(
                "Seed complete: rulesets=%s territory=%s",
                ruleset_actions,
                territory_actions,
            )

            # 4. Optional rescore
            if rescore_all:
                rescore_stats = await _rescore_all_signals(session)
                log.info("Rescore stats: %s", rescore_stats)
                print(
                    f"\nRescore stats: rescored={rescore_stats['rescored']} "
                    f"skipped={rescore_stats['skipped_no_ruleset']} "
                    f"errors={rescore_stats['errors']}"
                )

        _print_summary(rulesets)

    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seed per-family qualification rulesets + territory config from Josh's spec. "
            "Idempotent — safe to re-run when the spec is updated."
        )
    )
    parser.add_argument(
        "--rescore-all",
        action="store_true",
        default=False,
        help=(
            "After seeding, re-run qualification on ALL signals (not just pending_qualification) "
            "so existing signals get real fitScores under the new rulesets."
        ),
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(rescore_all=args.rescore_all))
    except Exception:
        log.exception("Fatal error during seed_josh_rulesets")
        sys.exit(1)


if __name__ == "__main__":
    main()
