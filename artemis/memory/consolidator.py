"""Phase B3: LLM-based consolidation engine.

Takes a list of candidate observations from one scope+category, calls the
provider-abstraction LLM (claude-code by default), with claude-haiku-4-5 when the
provider supports model selection, and returns a ConsolidationProposal list. Applies
proposals via the store write API (lossless: new obs → supersede old → link evidence).

LOSSLESS CONTRACT: consolidation never DELETEs rows. It creates new observations that
supersede the old via superseded_by, then links evidence back to every source row.

M2 — Confidence semantics (set at write time by the writer):
  ┌──────────────────────────────────────────────────────────────────┐
  │ Source                                          │ confidence     │
  ├──────────────────────────────────────────────────────────────────┤
  │ Direct user statement ("my email is X")         │ 0.95           │
  │ Tool / API result (calendar, CRM)               │ 0.90           │
  │ LLM inference from observed text                │ 0.50 – 0.70    │
  │ LLM speculation without direct evidence         │ 0.30 – 0.50    │
  └──────────────────────────────────────────────────────────────────┘

Auto-resolution threshold: confidence delta > 0.3 AND (newer has > 2× evidence_count
compared to existing) → old observation is automatically retired; a memory_conflicts
row is written with resolution='auto'. Otherwise both observations persist and a
memory_conflicts row is written with resolution=NULL for operator review.

Corroboration formula: when a new raw_input restates the same claim,
  evidence_count += 1
  confidence = min(0.99, current + (1 - current) * 0.3)
This is asymptotic toward 1.0 and never reaches it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionRequest, ModelAdapter
from artemis.agent.types import Message, TextBlock
from artemis.costs.events import record_cost_event
from artemis.memory.conflict_detector import detect_conflicts
from artemis.memory.models import MemoryConflict, MemoryObservation
from artemis.memory.schemas import Observation, Scope
from artemis.memory.store import link_evidence, supersede_observation, write_observation
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter_async

_logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "consolidate.txt"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Heuristic reject patterns — match any one → skip observation
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s"),  # markdown headers
    re.compile(r"^\s*[-*]\s"),  # bullet / task list items
    re.compile(r"^```"),  # fenced code blocks
    re.compile(r"^={3,}|-{3,}"),  # section dividers
    re.compile(r"^(Result|Output|Response|Error):", re.IGNORECASE),  # tool output openers
]
_MARKDOWN_CHARS = set("#*`_~[]|")
_MIN_LEN = 15
_MAX_LEN = 500
_MARKDOWN_DENSITY_THRESHOLD = 0.15

# ── Observability counters ────────────────────────────────────────────────────

CONSOLIDATION_FAILURE_COUNTERS: dict[str, int] = {
    "llm_call": 0,
    "parse": 0,
    "no_provider": 0,
}


# ── Heuristic filter ─────────────────────────────────────────────────────────


def heuristic_filter(observations: list[Observation]) -> list[Observation]:
    """Return observations that are worth consolidating.

    Rejects: too short, too long, noise patterns, or high markdown density.
    Passing observations are suitable LLM input.
    """
    kept: list[Observation] = []
    for obs in observations:
        content = obs.content
        if len(content) < _MIN_LEN or len(content) > _MAX_LEN:
            continue
        if any(p.search(content) for p in _NOISE_PATTERNS):
            continue
        density = sum(1 for c in content if c in _MARKDOWN_CHARS) / len(content)
        if density > _MARKDOWN_DENSITY_THRESHOLD:
            continue
        kept.append(obs)
    return kept


# ── Proposal dataclass ───────────────────────────────────────────────────────


@dataclass
class ConsolidationProposal:
    category: str
    content: str
    evidence_from_ids: list[int]
    # ids fully absorbed by this proposal (will be superseded)
    supersedes_ids: list[int] = field(default_factory=list)
    source_quality: float = 0.9


# ── LLM call ─────────────────────────────────────────────────────────────────


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


def _parse_proposals(raw: str, category: str, input_ids: set[int]) -> list[ConsolidationProposal]:
    """Parse LLM JSON output into ConsolidationProposal list.

    Validates that every input id appears in at least one evidence list or
    removed_ids. Raises ValueError on structural failures so the caller can retry.
    """
    data: dict[str, Any] = json.loads(raw)
    optimized: list[dict[str, Any]] = data.get("optimized", [])
    removed_ids: list[int] = [int(x) for x in data.get("removed_ids", [])]

    seen_ids: set[int] = set(removed_ids)
    proposals: list[ConsolidationProposal] = []

    for entry in optimized:
        content = str(entry["content"]).strip()
        evidence_ids = [int(x) for x in entry.get("evidence_from_ids", [])]
        seen_ids.update(evidence_ids)

        # Supersedes = evidence_ids that are being replaced (all of them here —
        # apply_consolidation will create a new observation that supersedes each).
        proposals.append(
            ConsolidationProposal(
                category=str(entry.get("category", category)),
                content=content,
                evidence_from_ids=evidence_ids,
                supersedes_ids=list(
                    set(evidence_ids) - {evidence_ids[0]} if evidence_ids else set()
                ),
                source_quality=0.9,
            )
        )

    # Verify coverage — every input id must be accounted for
    missing = input_ids - seen_ids
    if missing:
        raise ValueError(f"LLM output dropped input ids: {missing}")

    return proposals


async def consolidate_observations(
    observations: list[Observation],
    *,
    adapter: ModelAdapter | None = None,
) -> list[ConsolidationProposal]:
    """Call the provider-abstraction LLM to consolidate a list of observations.

    Filters via heuristic_filter first. Returns [] if fewer than 2 observations
    survive filtering. On LLM or JSON failure: one structured retry, then returns [].

    Uses prompt caching on the system block via cache_system=True (handled by the
    adapter). The adapter resolves the claude-code → codex → lm-studio → anthropic
    cascade so no ANTHROPIC_API_KEY is required when the claude-code CLI is available.

    Failure modes are loud (ERROR log + counter increment) rather than silent.
    """
    candidates = heuristic_filter(observations)
    if len(candidates) < 2:
        return []

    # Resolve adapter once per call — not per attempt.
    if adapter is None:
        try:
            from artemis.db import SessionLocal

            async with SessionLocal() as _override_session:
                adapter = await resolve_adapter_async(
                    provider="claude-code",
                    feature_tag="memory_consolidation",
                    session=_override_session,
                )
        except NoProviderAvailableError as exc:
            _logger.error(
                "Consolidation LLM call failed: no provider available: %s", exc, exc_info=True
            )
            CONSOLIDATION_FAILURE_COUNTERS["no_provider"] += 1
            return []

    system_prompt = _load_system_prompt()
    input_ids = {obs.id for obs in candidates}
    category = candidates[0].category

    payload = json.dumps(
        [
            {
                "id": obs.id,
                "content": obs.content,
                "created_at": obs.created_at.isoformat() if obs.created_at else None,
                "source_quality": obs.source_quality,
            }
            for obs in candidates
        ],
        indent=2,
    )

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=payload)])],
        system=system_prompt,
        model=_HAIKU_MODEL,
        max_tokens=2048,
        cache_system=True,
    )

    async def _call() -> tuple[str, object]:
        response = await adapter.complete(request)
        text_parts: list[str] = []
        for block in response.message.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
        return "".join(text_parts), response.usage

    for attempt in range(2):
        try:
            raw, _usage = await _call()
            # Record cost event in a separate session — failure must never propagate.
            try:
                import artemis.db as _db
                from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

                _provider = "claude-code" if isinstance(adapter, ClaudeCodeAdapter) else "anthropic"
                _path = "cli" if isinstance(adapter, ClaudeCodeAdapter) else "api"
                async with _db.SessionLocal() as _cost_session:
                    await record_cost_event(
                        _cost_session,
                        provider=_provider,
                        model=_HAIKU_MODEL,
                        provider_path=_path,
                        feature_tag="memory_consolidation",
                        input_tokens=getattr(_usage, "input_tokens", 0),
                        output_tokens=getattr(_usage, "output_tokens", 0),
                        cache_creation_input_tokens=getattr(
                            _usage, "cache_creation_input_tokens", 0
                        ),
                        cache_read_input_tokens=getattr(_usage, "cache_read_input_tokens", 0),
                    )
                    await _cost_session.commit()
            except Exception:
                _logger.warning("cost_event recording failed in consolidator", exc_info=True)
            return _parse_proposals(raw, category, input_ids)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            if attempt == 0:
                _logger.warning("Consolidation parse error (will retry): %s", exc)
                continue
            _logger.error("Consolidation failed after retry: %s", exc)
            CONSOLIDATION_FAILURE_COUNTERS["parse"] += 1
            return []
        except Exception as exc:
            _logger.error("Consolidation LLM call failed: %s", exc, exc_info=True)
            CONSOLIDATION_FAILURE_COUNTERS["llm_call"] += 1
            return []

    return []


# ── Apply proposals (lossless) ────────────────────────────────────────────────


async def apply_consolidation(
    session: AsyncSession,
    scope: Scope,
    proposals: list[ConsolidationProposal],
    source_observations: dict[int, Observation],
) -> list[Observation]:
    """Persist ConsolidationProposals to the database.

    For each proposal:
    1. Write a new observation (source_quality=0.9).
    2. Supersede each evidence_from_id with the new observation's id.
    3. Link each evidence_from_id as Evidence on the new observation.
    4. Forward any drawer evidence from superseded observations at 0.9× weight.

    All writes happen inside the caller-managed transaction.
    Returns the list of newly created observations.
    """
    from artemis.memory.store import list_evidence_for_observation  # avoid circular at top

    created: list[Observation] = []

    for proposal in proposals:
        new_obs = await write_observation(
            session,
            scope,
            proposal.content,
            category=proposal.category,
            source_quality=proposal.source_quality,
        )
        created.append(new_obs)

        for src_id in proposal.evidence_from_ids:
            # Link the source observation as evidence
            # CC28: link_evidence now takes source_id: str
            await link_evidence(
                session,
                observation_id=new_obs.id,
                source_kind="observation",
                source_id=str(src_id),
                weight=1.0,
            )
            # Supersede the source observation
            await supersede_observation(session, src_id, new_obs.id)

            # Forward drawer evidence from the source at discounted weight
            src_obs = source_observations.get(src_id)
            if src_obs is not None:
                existing_evidence = await list_evidence_for_observation(session, src_id)
                for ev in existing_evidence:
                    if ev.source_kind == "drawer":
                        await link_evidence(
                            session,
                            observation_id=new_obs.id,
                            source_kind="drawer",
                            source_id=ev.source_id,
                            source_quote=ev.source_quote,
                            weight=round(ev.weight * 0.9, 4),
                        )

    return created


# ── M2: confidence corroboration formula ──────────────────────────────────────


def corroborate_confidence(current: float, current_count: int) -> tuple[float, int]:
    """Apply the corroboration formula when a new raw_input restates the same claim.

    Returns (new_confidence, new_evidence_count).
    Formula: confidence = min(0.99, current + (1 - current) * 0.3)
    Asymptotic toward 1.0, never reaching it.
    """
    new_confidence = min(0.99, current + (1.0 - current) * 0.3)
    return new_confidence, current_count + 1


# ── M2: conflict-aware observation writer ────────────────────────────────────

_AUTO_RESOLVE_CONFIDENCE_DELTA = 0.3
_AUTO_RESOLVE_EVIDENCE_RATIO = 2.0


async def write_observation_with_conflict_check(
    session: AsyncSession,
    scope: Scope,
    content: str,
    category: str = "discovery",
    confidence: float = 0.5,
    source_quality: float = 0.5,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    owner_user_id: int | None = None,
) -> Observation:
    """Write an observation and run M2 conflict detection in the same transaction.

    1. Writes the new observation via write_observation().
    2. Pre-fetches candidate observations in scope with active validity windows.
    3. Runs detect_conflicts(new_obs, candidates).
    4. For each conflict:
       - Auto-resolvable (confidence delta > 0.3 AND new has > 2× evidence_count):
         set old.valid_until=now, old.supersedes=new.id, write conflict row (resolution='auto').
       - Otherwise: write conflict row (resolution=NULL) for operator review.
    5. Sets confidence on the newly written observation.

    All writes are inside the caller's transaction.
    """
    # Write the observation first (gets an id)
    new_obs = await write_observation(
        session,
        scope,
        content,
        category=category,
        source_quality=source_quality,
        valid_from=valid_from,
        valid_until=valid_until,
        owner_user_id=owner_user_id,
    )

    # Set M2 confidence on the new row
    await session.execute(
        update(MemoryObservation)
        .where(MemoryObservation.id == new_obs.id)
        .values(confidence=confidence)
    )

    # Pre-fetch active candidates in scope (exclude the just-written observation)
    result = await session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == scope.scope_kind,
            MemoryObservation.scope_id == scope.scope_id,
            MemoryObservation.superseded_by.is_(None),
            MemoryObservation.id != new_obs.id,
        )
    )
    candidates_rows = list(result.scalars())
    candidates = [Observation.model_validate(row) for row in candidates_rows]

    # Attach M2 fields for the new observation
    new_obs_with_m2 = new_obs.model_copy(
        update={"confidence": confidence, "supersedes": None, "evidence_count": 1}
    )

    conflicts = detect_conflicts(new_obs_with_m2, candidates)
    now = datetime.now(UTC)

    # Track which existing obs ids are already handled by rule-based detectors
    # so we don't double-write a memory_conflicts row.
    rule_conflict_ids: set[int] = {c.existing_id for c in conflicts}

    for conflict_candidate in conflicts:
        existing_id = conflict_candidate.existing_id
        existing_row = next((r for r in candidates_rows if r.id == existing_id), None)
        if existing_row is None:
            continue

        existing_confidence: float = getattr(existing_row, "confidence", 0.5)
        existing_evidence: int = getattr(existing_row, "evidence_count", 1)

        confidence_delta = confidence - existing_confidence
        evidence_ratio = (new_obs.evidence_count or 1) / max(existing_evidence, 1)

        auto_resolvable = (
            confidence_delta > _AUTO_RESOLVE_CONFIDENCE_DELTA
            and evidence_ratio > _AUTO_RESOLVE_EVIDENCE_RATIO
        )

        if auto_resolvable:
            # Retire the existing observation
            await session.execute(
                update(MemoryObservation)
                .where(
                    MemoryObservation.id == existing_id,
                    MemoryObservation.valid_until.is_(None),
                )
                .values(valid_until=now, supersedes=new_obs.id)
            )
            resolution = "auto"
        else:
            resolution = None

        # Normalise pair: always store (min, max)
        obs_a = min(new_obs.id, existing_id)
        obs_b = max(new_obs.id, existing_id)

        conflict_row = MemoryConflict(
            scope_id=scope.scope_id,
            observation_a_id=obs_a,
            observation_b_id=obs_b,
            conflict_type=conflict_candidate.conflict_type,
            detected_at=now,
            resolution=resolution,
            resolved_at=now if auto_resolvable else None,
            resolved_by="auto" if auto_resolvable else None,
        )
        session.add(conflict_row)

    # M1 semantic conflict detection — runs AFTER rule-based detectors.
    # FAIL SAFE: any error here must not crash the transaction.
    try:
        from artemis.memory.semantic_conflict_detector import detect_semantic_conflicts

        semantic_candidates = await detect_semantic_conflicts(
            new_obs_with_m2, candidates, session
        )
        for sem_cand in semantic_candidates:
            if sem_cand.existing_id in rule_conflict_ids:
                # Already handled by a rule-based detector; skip to avoid duplicate rows.
                continue

            existing_row = next(
                (r for r in candidates_rows if r.id == sem_cand.existing_id), None
            )
            if existing_row is None:
                continue

            if sem_cand.auto_resolve:
                # High-confidence semantic contradiction → supersede the existing obs.
                await supersede_observation(session, sem_cand.existing_id, new_obs.id)
                sem_resolution = "auto"
            else:
                # Borderline → write review row; leave both observations active.
                sem_resolution = None

            obs_a = min(new_obs.id, sem_cand.existing_id)
            obs_b = max(new_obs.id, sem_cand.existing_id)
            sem_conflict_row = MemoryConflict(
                scope_id=scope.scope_id,
                observation_a_id=obs_a,
                observation_b_id=obs_b,
                conflict_type=sem_cand.conflict_type,
                detected_at=now,
                resolution=sem_resolution,
                resolution_reason=sem_cand.reason or None,
                resolved_at=now if sem_cand.auto_resolve else None,
                resolved_by="auto" if sem_cand.auto_resolve else None,
            )
            session.add(sem_conflict_row)
    except Exception:
        _logger.error(
            "M1 semantic conflict detection failed (non-fatal); observation still written",
            exc_info=True,
        )

    await session.flush()

    # Re-read with updated confidence
    refreshed = await session.execute(
        select(MemoryObservation).where(MemoryObservation.id == new_obs.id)
    )
    return Observation.model_validate(refreshed.scalar_one())
