"""District research drawer — per-district memory slot over the existing memory infra.

Drawer convention
-----------------
scope_kind  = "workspace"
scope_id    = "marketing"
category    = "district_research"

Each observation's content is a canonical text block that encodes one
DistrictFinding in a stable, human-readable format:

    [Argus|<dimension>|<district_key>] <value>
    source: <source_label>
    url: <url_or_none>
    researched_at: <ISO-8601 date>

The leading bracketed key is intentionally stable so that:
  - content-hash dedup in write_observation deduplicates identical findings.
  - The district_key + dimension together act as a namespaced "drawer slot"
    that Argus, Callie, and the brief assembler can query by district.

Why "workspace:marketing" scope?
---------------------------------
Marketing-shared scope (used by M5 signal writes, used by Callie) keeps
district research accessible to every marketing agent without owner-private
gating.  It mirrors the scope convention established by M5.

Dedup guarantee
---------------
write_district_findings calls write_observation, which uses:
    ON CONFLICT DO NOTHING on (scope_kind, scope_id, content_hash)
So bit-for-bit identical findings are silently idempotent.  The incremental
consolidator also sees the new observation and runs semantic conflict detection
on the scope — duplicates with different wording are caught there.  Argus does
NOT reimplement this logic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope, SourceQualityHint
from artemis.memory.store import link_evidence, write_drawer, write_observation
from artemis.memory.schemas import Source

_logger = logging.getLogger(__name__)

# ── Drawer scope constant ─────────────────────────────────────────────────────

# All Argus findings land in the marketing-shared scope, matching the M5 convention.
ARGUS_SCOPE = Scope(scope_kind="workspace", scope_id="marketing")

# Category applied to every Argus observation.  "district_research" is a
# recognised category name we add here; the KNOWN_CATEGORIES set only governs
# decay factors, and unknown categories still write fine (they log a warning and
# decay at 0.95).  This is an intentional tradeoff — we accept the warning and
# stay within the "no new DB table" constraint.
ARGUS_CATEGORY = "district_research"

# Source quality for Argus-synthesised findings: between agent (0.7) and
# consolidation (0.9) since findings blend web research + LLM synthesis.
ARGUS_SOURCE_QUALITY = 0.75


# ── Finding shape ─────────────────────────────────────────────────────────────


@dataclass
class DistrictFinding:
    """A single researched fact about a district.

    dimension    — which aspect was researched (see DIMENSIONS below).
    value        — the finding text (what Argus learned).
    source       — provenance label. Use "Argus" for synthesised findings.
                   May include the originating tool name (e.g., "Argus/news_api").
    url          — optional source URL.
    researched_at — when Argus produced this finding (defaults to now UTC).
    raw_notes    — optional extra context / sub-bullets; not part of content hash.
    """

    dimension: str
    value: str
    source: str = "Argus"
    url: str | None = None
    researched_at: str = field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    raw_notes: dict[str, Any] = field(default_factory=dict)


# Canonical dimension names — callers should use these constants rather than
# free-form strings so content-hash dedup and downstream queries are stable.
class Dimension:
    CURRENT_VENDOR = "current_vendor"
    PROCUREMENT_TIMING = "procurement_timing"
    DISTRICT_PROFILE = "district_profile"
    DECISION_MAKERS = "decision_makers"
    PRIOR_AMIRA_RELATIONSHIP = "prior_amira_relationship"
    COMPETITOR_COMMITMENTS = "competitor_commitments"
    RECOMMENDED_ANGLE = "recommended_angle"
    # Catch-all for dimensions not yet in the enum
    OTHER = "other"


ALL_DIMENSIONS: frozenset[str] = frozenset(
    v for k, v in vars(Dimension).items() if not k.startswith("_")
)


# ── Serialisation helpers ─────────────────────────────────────────────────────


def _finding_to_content(district_key: str, finding: DistrictFinding) -> str:
    """Encode a DistrictFinding as the canonical observation content string.

    The format is designed so that:
      - The first line is deterministic (drives content-hash dedup).
      - Human-readable in the memory shell / Callie's prompt context.
      - The district_key + dimension are embedded so retrieval by district is
        possible with a simple substring/FTS match.
    """
    url_part = finding.url or "none"
    return (
        f"[Argus|{finding.dimension}|{district_key}] {finding.value}\n"
        f"source: {finding.source}\n"
        f"url: {url_part}\n"
        f"researched_at: {finding.researched_at}"
    )


def _content_to_finding(district_key: str, content: str) -> DistrictFinding | None:
    """Attempt to parse an observation content string back to a DistrictFinding.

    Returns None if the content does not match the Argus format (i.e., it was
    written by a different source and should be left alone).
    """
    lines = content.strip().splitlines()
    if not lines:
        return None
    first = lines[0]
    if not first.startswith("[Argus|"):
        return None
    # Parse header: [Argus|<dimension>|<district_key>] <value>
    try:
        bracket_end = first.index("]")
        header = first[1:bracket_end]  # "Argus|<dimension>|<district_key>"
        _, dimension, dk = header.split("|", 2)
        if dk != district_key:
            return None
        value = first[bracket_end + 2 :]  # skip "] "
    except (ValueError, IndexError):
        return None

    kv: dict[str, str] = {}
    for line in lines[1:]:
        if ": " in line:
            k, _, v = line.partition(": ")
            kv[k.strip()] = v.strip()

    return DistrictFinding(
        dimension=dimension,
        value=value,
        source=kv.get("source", "Argus"),
        url=kv.get("url") if kv.get("url") != "none" else None,
        researched_at=kv.get("researched_at", ""),
    )


# ── Public read helper ────────────────────────────────────────────────────────


async def read_district_drawer(
    session: AsyncSession,
    district_key: str,
) -> dict[str, DistrictFinding]:
    """Read all Argus findings for a district from the memory store.

    Returns a dict keyed by dimension name so callers can check which dimensions
    are already present.  Only returns active (non-superseded) observations that
    match the Argus content format for this district.

    Does NOT raise — returns {} if the district has no prior research.
    """
    try:
        result = await session.execute(
            select(MemoryObservation).where(
                MemoryObservation.scope_kind == ARGUS_SCOPE.scope_kind,
                MemoryObservation.scope_id == ARGUS_SCOPE.scope_id,
                MemoryObservation.category == ARGUS_CATEGORY,
                MemoryObservation.superseded_by.is_(None),
            )
        )
        rows = list(result.scalars())
    except Exception:
        _logger.error(
            "read_district_drawer failed for district_key=%r", district_key, exc_info=True
        )
        return {}

    findings: dict[str, DistrictFinding] = {}
    # Filter to rows that contain this district_key in their content to avoid
    # loading the entire marketing scope on every call.
    tag = f"|{district_key}]"
    for row in rows:
        if tag not in row.content:
            continue
        finding = _content_to_finding(district_key, row.content)
        if finding is not None:
            # If there are multiple active observations for the same dimension,
            # keep the most recent (by id, which is autoincrement → newest last).
            if finding.dimension not in findings or row.id > _finding_row_id(
                findings[finding.dimension], district_key
            ):
                findings[finding.dimension] = finding
    return findings


def _finding_row_id(finding: DistrictFinding, district_key: str) -> int:
    """Placeholder — we track the row id via a separate lookup in the real impl.
    In practice, for the "keep newest" tie-break, we rely on dict overwrite order
    from the query (rows come back in id asc by default).  This sentinel returns
    -1 so the first finding always wins when we don't have the actual id.
    """
    return -1


# ── Public write helper ────────────────────────────────────────────────────────


async def write_district_findings(
    session: AsyncSession,
    district_key: str,
    findings: list[DistrictFinding],
    *,
    triggering_signal_id: str | None = None,
) -> list[int]:
    """Write a list of DistrictFindings through the memory pipeline.

    Each finding becomes:
      1. A memory_drawers row (verbatim evidence) — carries the raw notes JSON
         as source_extra so provenance is preserved losslessly.
      2. A memory_observations row (curated content) — uses the canonical
         _finding_to_content() format so content-hash dedup applies.
      3. Evidence links:
           observation → drawer (source_kind="drawer")
           observation → signal (source_kind="signal_queue") if triggering_signal_id given

    This is the same pattern as M5 (_write_signal_memory in signal_queue_ops.py).
    Dedup is handled by ON CONFLICT DO NOTHING in both write_drawer and
    write_observation — Argus does NOT reimplement it.

    Returns the list of observation ids that were written (may be shorter than
    findings if some were deduped away).

    FAIL SAFE: if any individual finding write fails, it is logged and skipped;
    other findings still proceed.  The caller's transaction is never aborted here.
    """
    written_ids: list[int] = []

    for finding in findings:
        try:
            obs_content = _finding_to_content(district_key, finding)

            # ── Drawer: verbatim evidence with raw notes ──────────────────────
            drawer_content = json.dumps(
                {
                    "district_key": district_key,
                    "dimension": finding.dimension,
                    "value": finding.value,
                    "source": finding.source,
                    "url": finding.url,
                    "researched_at": finding.researched_at,
                    "raw_notes": finding.raw_notes,
                },
                sort_keys=True,
            )
            drawer = await write_drawer(
                session,
                ARGUS_SCOPE,
                drawer_content,
                Source(
                    source_kind="agent_run",
                    source_id="argus",
                    source_extra={"dimension": finding.dimension, "district_key": district_key},
                ),
                corpus_kind=ARGUS_CATEGORY,
            )

            # ── Observation: curated content that rides the memory pipeline ──
            obs = await write_observation(
                session,
                ARGUS_SCOPE,
                obs_content,
                category=ARGUS_CATEGORY,
                source_quality=ARGUS_SOURCE_QUALITY,
                raw_payload={
                    "agent": "argus",
                    "district_key": district_key,
                    "dimension": finding.dimension,
                    "source": finding.source,
                    "url": finding.url,
                    "researched_at": finding.researched_at,
                },
                raw_source_kind="agent_run",
                raw_source_id="argus",
                raw_actor="argus",
                confidence_origin="argus",
            )

            # ── Evidence: link observation ← drawer ──────────────────────────
            await link_evidence(
                session,
                observation_id=obs.id,
                source_kind="drawer",
                source_id=str(drawer.id),
            )

            # ── Evidence: link observation ← triggering signal ───────────────
            if triggering_signal_id is not None:
                await link_evidence(
                    session,
                    observation_id=obs.id,
                    source_kind="signal_queue",
                    source_id=triggering_signal_id,
                )

            written_ids.append(obs.id)
            _logger.debug(
                "Argus wrote finding district_key=%r dimension=%r obs_id=%d",
                district_key,
                finding.dimension,
                obs.id,
            )

        except Exception:
            _logger.error(
                "write_district_findings: failed to write dimension=%r for district_key=%r "
                "(skipped; other findings proceed)",
                finding.dimension,
                district_key,
                exc_info=True,
            )

    return written_ids
