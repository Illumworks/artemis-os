"""Memory M1 — Semantic conflict detector.

Detects contradictions that the rule-based detectors miss: paraphrased or
implied conflicts where the observations don't share an exact attribute prefix
or keyword pattern.

Architecture:
  1. Embedding similarity shortlist — use pgvector cosine distance to select
     the top-N most semantically similar candidates before any LLM call.
     "Merely similar" ≠ "contradictory" — the shortlist is a cheap gate, NOT
     the conflict signal.
  2. LLM contradiction judge — send each shortlisted pair to the provider-
     abstraction resolver (claude-code → codex → lm-studio → anthropic cascade).
     NEVER instantiate AnthropicAdapter directly; there is no ANTHROPIC_API_KEY
     in this environment.
  3. Precision-first routing:
       - CONTRADICT + confidence >= HIGH_THRESHOLD  → auto-supersede (existing
         machinery: superseded_by + memory_conflicts resolution='auto').
       - CONTRADICT + confidence < HIGH_THRESHOLD   → memory_conflicts row with
         resolution=NULL for human review; BOTH observations stay active.
       - REFINE / UNRELATED / any error            → no action.
  4. FAIL SAFE: on NoProviderAvailableError, any LLM/JSON failure, or uncertain
     confidence, do NOT auto-supersede. Never crash the consolidator.

Callers:
  detect_semantic_conflicts(new_obs, existing_obs_with_embeddings, session)
    → list[SemanticConflictCandidate]

  Each SemanticConflictCandidate has:
    .existing_id      — the conflicting observation's id
    .conflict_type    — "semantic_contradiction"
    .auto_resolve     — True only when judge confidence >= HIGH_THRESHOLD
    .reason           — short LLM explanation (for the memory_conflicts row)

The top-level detect_conflicts() in conflict_detector.py is NOT changed here;
instead, write_observation_with_conflict_check (consolidator.py) calls this
module after the rule-based detectors.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.memory.schemas import Observation
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter_async

_logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Embedding cosine similarity must be >= this value for a pair to be sent to
# the LLM judge.  Set conservatively: contradictions in short plain-text
# observations typically score 0.65+.  Irrelevant pairs usually score < 0.55.
_EMBEDDING_SHORTLIST_THRESHOLD: float = 0.60

# Maximum number of candidates to pass to the LLM judge per new observation.
# Keeps latency/cost bounded.
_MAX_LLM_CANDIDATES: int = 5

# LLM judge confidence must be >= this value to auto-supersede (HIGH precision).
# Below this: write review row only.
_AUTO_SUPERSEDE_THRESHOLD: float = 0.85

# Model for the contradiction judge — same as consolidator (cheapest capable).
_JUDGE_MODEL = "claude-haiku-4-5-20251001"

# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class SemanticConflictCandidate:
    """A semantically detected contradiction between new_obs and an existing obs."""

    existing_id: int
    conflict_type: str = "semantic_contradiction"
    auto_resolve: bool = False  # True only on HIGH-confidence judgment
    reason: str = ""  # short LLM explanation for the conflicts row


# ── Observability counters ────────────────────────────────────────────────────

SEMANTIC_DETECTOR_COUNTERS: dict[str, int] = {
    "shortlisted": 0,
    "judged": 0,
    "contradictions_auto": 0,
    "contradictions_review": 0,
    "no_provider": 0,
    "llm_error": 0,
    "parse_error": 0,
}


# ── Cosine similarity helper (pure Python, no numpy required) ────────────────


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two unit-normalized embedding vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a < 1e-9 or mag_b < 1e-9:
        return 0.0
    return dot / (mag_a * mag_b)


# ── LLM judge ────────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """You are a precise contradiction-detection assistant for a memory system.

Given two observations, determine if they DIRECTLY CONTRADICT each other — meaning
one asserts a fact that the other explicitly or strongly implies is false.

IMPORTANT: Be conservative. Only mark CONTRADICT when you are highly confident.
- Near-duplicates that say the same thing slightly differently: UNRELATED.
- One observation adds detail to another (A refines/expands B): REFINE.
- Topically related but not contradictory: UNRELATED.
- Temporally sequential updates (A was true, B is the current state): REFINE (not CONTRADICT).
- Genuine contradiction (A says X is Y, B says X is NOT Y, or X is Z≠Y): CONTRADICT.

Respond with ONLY a JSON object — no markdown, no explanation outside JSON:
{
  "verdict": "CONTRADICT" | "REFINE" | "UNRELATED",
  "confidence": 0.0–1.0,
  "reason": "<one sentence, max 120 chars>"
}"""


async def _judge_pair(
    adapter: object,
    new_content: str,
    existing_content: str,
) -> tuple[str, float, str]:
    """Ask the LLM to judge one pair.  Returns (verdict, confidence, reason).

    On any failure returns ("UNRELATED", 0.0, "judge failed").
    """
    payload = json.dumps(
        {"observation_a": new_content[:500], "observation_b": existing_content[:500]},
        ensure_ascii=False,
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=payload)])],
        system=_JUDGE_SYSTEM,
        model=_JUDGE_MODEL,
        max_tokens=256,
        cache_system=True,
    )
    try:
        response = await adapter.complete(request)  # type: ignore[attr-defined]
        parts: list[str] = [
            block.text for block in response.message.content if isinstance(block, TextBlock)
        ]
        raw = "".join(parts).strip()
        if raw.startswith("```"):
            import re

            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        verdict = str(data.get("verdict", "UNRELATED")).upper().strip()
        if verdict not in {"CONTRADICT", "REFINE", "UNRELATED"}:
            verdict = "UNRELATED"
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        reason = str(data.get("reason", ""))[:200]
        return verdict, confidence, reason
    except json.JSONDecodeError:
        SEMANTIC_DETECTOR_COUNTERS["parse_error"] += 1
        _logger.warning("Semantic conflict judge parse error for pair", exc_info=True)
        return "UNRELATED", 0.0, "judge parse failed"
    except Exception:
        SEMANTIC_DETECTOR_COUNTERS["llm_error"] += 1
        _logger.warning("Semantic conflict judge LLM error for pair", exc_info=True)
        return "UNRELATED", 0.0, "judge failed"


# ── Embedding shortlister ────────────────────────────────────────────────────


async def _shortlist_by_embedding(
    session: AsyncSession,
    new_obs: Observation,
    candidates: list[Observation],
    *,
    threshold: float = _EMBEDDING_SHORTLIST_THRESHOLD,
    max_results: int = _MAX_LLM_CANDIDATES,
) -> list[tuple[Observation, float]]:
    """Fetch embeddings for new_obs and candidates via the DB; return sorted
    (candidate, similarity) pairs with similarity >= threshold.

    Falls back gracefully: if embeddings are unavailable for new_obs or a
    candidate, that candidate is skipped (not an error).
    """
    from sqlalchemy import text as sql_text

    # Fetch embedding for new_obs
    new_emb_result = await session.execute(
        sql_text(
            "SELECT embedding FROM memory_embeddings "
            "WHERE target_table = 'observation' AND target_id = :obs_id "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"obs_id": new_obs.id},
    )
    new_emb_row = new_emb_result.fetchone()
    if new_emb_row is None:
        # No embedding for new_obs yet; cannot shortlist
        return []

    new_vec = new_emb_row[0]
    # Convert pgvector type to list[float] if needed
    if hasattr(new_vec, "tolist"):
        new_vec = new_vec.tolist()
    new_vec = [float(x) for x in new_vec]

    if not candidates:
        return []

    candidate_ids = [c.id for c in candidates]
    # Fetch all candidate embeddings in one query
    emb_result = await session.execute(
        sql_text(
            "SELECT target_id, embedding FROM memory_embeddings "
            "WHERE target_table = 'observation' AND target_id = ANY(:ids) "
            "ORDER BY target_id"
        ),
        {"ids": candidate_ids},
    )
    emb_by_id: dict[int, list[float]] = {}
    for row in emb_result.fetchall():
        vec = row[1]
        if hasattr(vec, "tolist"):
            vec = vec.tolist()
        emb_by_id[int(row[0])] = [float(x) for x in vec]

    scored: list[tuple[Observation, float]] = []
    for cand in candidates:
        cand_vec = emb_by_id.get(cand.id)
        if cand_vec is None:
            continue
        sim = _cosine_sim(new_vec, cand_vec)
        if sim >= threshold:
            scored.append((cand, sim))

    # Sort descending by similarity, take top-N
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:max_results]


# ── Public entry point ────────────────────────────────────────────────────────


async def detect_semantic_conflicts(
    new_obs: Observation,
    existing_observations: list[Observation],
    session: AsyncSession,
) -> list[SemanticConflictCandidate]:
    """Detect semantic (paraphrased/implied) contradictions between new_obs and
    existing_observations.

    Steps:
      1. Filter candidates: same scope, not already superseded, not the same obs.
      2. Embedding shortlist: only pass candidates with cosine similarity >=
         _EMBEDDING_SHORTLIST_THRESHOLD to the LLM.
      3. LLM judge: for each shortlisted pair, ask if they contradict.
      4. Route result:
           CONTRADICT + confidence >= _AUTO_SUPERSEDE_THRESHOLD → auto_resolve=True
           CONTRADICT + lower confidence                          → auto_resolve=False (review)
           REFINE / UNRELATED                                     → skip

    FAIL SAFE: NoProviderAvailableError or any LLM failure → return [] (never crash).
    Returns list of SemanticConflictCandidates (may be empty).
    """
    # Step 1: filter candidates
    scope_candidates = [
        obs
        for obs in existing_observations
        if obs.id != new_obs.id
        and obs.superseded_by is None
        and obs.scope_kind == new_obs.scope_kind
        and obs.scope_id == new_obs.scope_id
    ]
    if not scope_candidates:
        return []

    # Step 2: embedding shortlist
    shortlisted = await _shortlist_by_embedding(session, new_obs, scope_candidates)
    if not shortlisted:
        return []

    SEMANTIC_DETECTOR_COUNTERS["shortlisted"] += len(shortlisted)

    # Step 3: resolve LLM adapter once
    try:
        from artemis.db import SessionLocal

        async with SessionLocal() as _resolver_session:
            adapter = await resolve_adapter_async(
                provider="claude-code",
                feature_tag="memory_semantic_conflict",
                session=_resolver_session,
            )
    except NoProviderAvailableError as exc:
        SEMANTIC_DETECTOR_COUNTERS["no_provider"] += 1
        _logger.warning(
            "Semantic conflict detector: no LLM provider available; skipping semantic check: %s",
            exc,
        )
        return []

    # Step 4: judge each pair
    results: list[SemanticConflictCandidate] = []
    for cand, _sim in shortlisted:
        SEMANTIC_DETECTOR_COUNTERS["judged"] += 1
        verdict, confidence, reason = await _judge_pair(adapter, new_obs.content, cand.content)
        if verdict != "CONTRADICT":
            continue

        if confidence >= _AUTO_SUPERSEDE_THRESHOLD:
            SEMANTIC_DETECTOR_COUNTERS["contradictions_auto"] += 1
            results.append(
                SemanticConflictCandidate(
                    existing_id=cand.id,
                    auto_resolve=True,
                    reason=reason,
                )
            )
        else:
            # Borderline — write review row only, don't auto-supersede
            SEMANTIC_DETECTOR_COUNTERS["contradictions_review"] += 1
            results.append(
                SemanticConflictCandidate(
                    existing_id=cand.id,
                    auto_resolve=False,
                    reason=reason,
                )
            )

    return results
