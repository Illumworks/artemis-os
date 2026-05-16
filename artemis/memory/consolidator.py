"""Phase B3: LLM-based consolidation engine.

Takes a list of candidate observations from one scope+category, calls Anthropic Haiku
with prompt caching, and returns a ConsolidationProposal list. Applies proposals via
the store write API (lossless: new obs → supersede old → link evidence).

LOSSLESS CONTRACT: consolidation never DELETEs rows. It creates new observations that
supersede the old via superseded_by, then links evidence back to every source row.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.schemas import Observation, Scope
from artemis.memory.store import link_evidence, supersede_observation, write_observation

_logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "consolidate.txt"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Heuristic reject patterns — match any one → skip observation
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s"),          # markdown headers
    re.compile(r"^\s*[-*]\s"),          # bullet / task list items
    re.compile(r"^```"),                # fenced code blocks
    re.compile(r"^={3,}|-{3,}"),        # section dividers
    re.compile(r"^(Result|Output|Response|Error):", re.IGNORECASE),  # tool output openers
]
_MARKDOWN_CHARS = set("#*`_~[]|")
_MIN_LEN = 15
_MAX_LEN = 500
_MARKDOWN_DENSITY_THRESHOLD = 0.15


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


def _parse_proposals(
    raw: str, category: str, input_ids: set[int]
) -> list[ConsolidationProposal]:
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
                supersedes_ids=list(set(evidence_ids) - {evidence_ids[0]} if evidence_ids else set()),
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
    client: anthropic.AsyncAnthropic | None = None,
) -> list[ConsolidationProposal]:
    """Call Haiku to consolidate a list of observations.

    Filters via heuristic_filter first. Returns [] if fewer than 2 observations
    survive filtering. On LLM or JSON failure: one structured retry, then returns [].

    Uses prompt caching on the system block (cache_control: ephemeral).
    """
    candidates = heuristic_filter(observations)
    if len(candidates) < 2:
        return []

    if client is None:
        client = anthropic.AsyncAnthropic()

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

    async def _call() -> str:
        response = await client.messages.create(  # type: ignore[union-attr]
            model=_HAIKU_MODEL,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": payload}],
        )
        return response.content[0].text  # type: ignore[union-attr]

    for attempt in range(2):
        try:
            raw = await _call()
            return _parse_proposals(raw, category, input_ids)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            if attempt == 0:
                _logger.warning("Consolidation parse error (will retry): %s", exc)
                continue
            _logger.error("Consolidation failed after retry: %s", exc)
            return []
        except Exception as exc:
            _logger.error("Consolidation LLM call failed: %s", exc)
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
            await link_evidence(
                session,
                observation_id=new_obs.id,
                source_kind="observation",
                source_id=src_id,
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
