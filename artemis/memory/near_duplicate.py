"""Memory M1b — near-duplicate (clone) consolidation.

Conversational observations ingested one-per-message (the Callie history handoff,
``callie_history_handoff.ingest_session_messages``) carry an ISO-timestamp prefix
*inside* their content (``[2026-06-06T16:11:24+00:00] [USER] Still an echo.``).
That prefix defeats the content-hash dedup in ``write_observation``: two
byte-identical messages sent at different times hash differently and both persist.
The result is clone clusters ("Holding." ×7, "Still an echo. Not responding." ×N)
that bury the canonical answer in retrieval — the dominant driver of the R@1 gap
diagnosed in the M2 harness (5/6 baseline misses are near-duplicate clustering).

This module collapses TRUE clones losslessly. Within a single scope, observations
whose *normalized body* — timestamp prefix, a single leading ``[ROLE]`` tag,
collapsed whitespace and case removed — is byte-identical are merged into one
canonical observation; every other member is superseded (``superseded_by`` →
canonical) and linked back as ``observation`` evidence. Nothing is deleted; the
raw_inputs hashchain is untouched; each superseded clone stays fully recoverable.

PRECISION-FIRST / time-series safety (the sharp line from the brief):
    Normalization removes ONLY the prefix, role tag, whitespace and case. Any
    difference in the body itself — including the numeric values that distinguish
    momentum-snapshot time-series points (``current=6`` vs ``current=16``) —
    yields a different normalized body and is therefore NEVER collapsed. We do
    not use embedding similarity here: "merely similar" is not "a clone". When
    two observations are not byte-identical after normalization, we keep both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Observation
from artemis.memory.store import (
    link_evidence,
    list_evidence_for_observation,
    supersede_observation,
)

# Leading ISO-8601 timestamp wrapped in brackets, e.g. "[2026-06-06T16:11:24.23+00:00] "
_TS_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*")
# A single leading role/speaker tag, e.g. "[USER] " / "[ASSISTANT] " / "[SPEAKER] "
_ROLE_TAG = re.compile(r"^\[[A-Z_]+\]\s*")
_WS = re.compile(r"\s+")

# Defense-in-depth: bodies matching a known time-series signature are excluded
# from collapse entirely, even in the (impossible-by-construction) case that two
# of them normalized identically. Distinct snapshots already differ in body, so
# this is belt-and-suspenders around the lossless time-series guarantee.
_TIME_SERIES_SIGNATURES = (re.compile(r"^Momentum snapshot\b", re.IGNORECASE),)


def normalize_body(content: str) -> str:
    """Return the clone-comparison key for an observation's content.

    Strips a leading ISO-timestamp bracket, a single leading ``[ROLE]`` tag,
    collapses internal whitespace, and casefolds. Two observations are clones
    iff their normalized bodies are equal.
    """
    s = _TS_PREFIX.sub("", content.strip())
    s = _ROLE_TAG.sub("", s, count=1)
    s = _WS.sub(" ", s).strip().casefold()
    return s


def _is_time_series(content: str) -> bool:
    head = _TS_PREFIX.sub("", content.strip())
    return any(p.search(head) for p in _TIME_SERIES_SIGNATURES)


@dataclass
class CloneCluster:
    """A set of byte-identical-after-normalization observations in one scope."""

    scope_kind: str
    scope_id: str
    normalized: str
    canonical_id: int
    duplicate_ids: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return 1 + len(self.duplicate_ids)


def _choose_canonical(members: list[Observation]) -> Observation:
    """Pick the observation to keep for a clone cluster.

    Deterministic preference order:
      1. highest source_quality (most trusted provenance)
      2. highest evidence_count (most corroborated)
      3. earliest created_at (the original occurrence; repeats are the clones)
      4. lowest id (stable tie-break)
    """
    return sorted(
        members,
        key=lambda o: (
            -o.source_quality,
            -o.evidence_count,
            o.created_at,
            o.id,
        ),
    )[0]


def plan_clone_clusters(observations: list[Observation]) -> list[CloneCluster]:
    """Group active observations into clone clusters (pure — no DB access).

    Only ACTIVE observations (``superseded_by is None``) are considered. Groups
    of size 1, empty normalized bodies, and time-series-signature bodies are
    skipped. Callers pre-fetch the observation set.
    """
    groups: dict[tuple[str, str, str], list[Observation]] = {}
    for obs in observations:
        if obs.superseded_by is not None:
            continue
        if _is_time_series(obs.content):
            continue
        norm = normalize_body(obs.content)
        if not norm:
            continue
        groups.setdefault((obs.scope_kind, obs.scope_id, norm), []).append(obs)

    clusters: list[CloneCluster] = []
    for (scope_kind, scope_id, norm), members in groups.items():
        if len(members) < 2:
            continue
        canonical = _choose_canonical(members)
        clusters.append(
            CloneCluster(
                scope_kind=scope_kind,
                scope_id=scope_id,
                normalized=norm,
                canonical_id=canonical.id,
                duplicate_ids=sorted(m.id for m in members if m.id != canonical.id),
            )
        )
    return clusters


@dataclass
class ConsolidationStats:
    clusters: int = 0
    observations_superseded: int = 0
    evidence_links_created: int = 0


async def apply_clone_consolidation(
    session: AsyncSession,
    clusters: list[CloneCluster],
) -> ConsolidationStats:
    """Collapse clone clusters losslessly inside the caller's transaction.

    For each duplicate in a cluster:
      1. Link it as ``observation`` evidence on the canonical (records the merge,
         carries a content quote for provenance).
      2. Forward any ``drawer`` evidence from the duplicate to the canonical at
         0.9× weight (mirrors ``apply_consolidation``), so the canonical inherits
         the duplicate's source provenance.
      3. Supersede the duplicate (``superseded_by`` → canonical). It drops out of
         active retrieval but stays in the table, fully recoverable.

    Never deletes. Never touches the canonical's own ``superseded_by``.
    """
    stats = ConsolidationStats()
    for cluster in clusters:
        canonical = await session.get(MemoryObservation, cluster.canonical_id)
        if canonical is None or canonical.superseded_by is not None:
            # Canonical vanished or was itself retired since planning — skip the
            # whole cluster rather than risk superseding into a dead target.
            continue
        stats.clusters += 1
        for dup_id in cluster.duplicate_ids:
            dup = await session.get(MemoryObservation, dup_id)
            if dup is None or dup.superseded_by is not None:
                continue
            quote = dup.content[:500]
            await link_evidence(
                session,
                observation_id=cluster.canonical_id,
                source_kind="observation",
                source_id=str(dup_id),
                source_quote=quote,
                weight=1.0,
            )
            stats.evidence_links_created += 1

            for ev in await list_evidence_for_observation(session, dup_id):
                if ev.source_kind == "drawer":
                    await link_evidence(
                        session,
                        observation_id=cluster.canonical_id,
                        source_kind="drawer",
                        source_id=ev.source_id,
                        source_quote=ev.source_quote,
                        weight=round(ev.weight * 0.9, 4),
                    )
                    stats.evidence_links_created += 1

            await supersede_observation(session, dup_id, cluster.canonical_id)
            stats.observations_superseded += 1

    return stats


async def consolidate_near_duplicates(
    session: AsyncSession,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
) -> ConsolidationStats:
    """Backfill entry point: plan + apply clone consolidation over active obs.

    Optionally restricted to one scope. The caller owns the transaction/commit.
    """
    stmt = select(MemoryObservation).where(MemoryObservation.superseded_by.is_(None))
    if scope_kind is not None:
        stmt = stmt.where(MemoryObservation.scope_kind == scope_kind)
    if scope_id is not None:
        stmt = stmt.where(MemoryObservation.scope_id == scope_id)
    rows = list((await session.execute(stmt)).scalars())
    observations = [Observation.model_validate(r) for r in rows]
    clusters = plan_clone_clusters(observations)
    return await apply_clone_consolidation(session, clusters)
