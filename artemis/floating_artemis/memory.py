"""M3+M4 memory helpers for Floating Artemis.

M3: write_turn_drawer — auto-write a memory drawer after every turn.
M4: inject_memory_context — auto-read observations into the system prompt.
Cache: 5-second per-session retrieval cache to avoid hot-loop DB calls.

Failure isolation is the load-bearing constraint: every public function is
wrapped in try/except. Memory failures NEVER break chat.

Agent scoping:
  Each named agent (e.g. "callie") gets its own ``agent:<agent_id>`` memory
  scope so observations from different agents are retrievable in isolation.
  The Artemis default is ``agent:floating-artemis`` for backward compat.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Scope, Source
from artemis.memory.store import write_drawer

logger = logging.getLogger(__name__)

# Default scope for Artemis (backward compat)
_FA_SCOPE = Scope(scope_kind="agent", scope_id="floating-artemis")


def _scope_for_agent(agent_id: str) -> Scope:
    """Return the memory scope for a named agent.

    - ``"artemis"`` (or any unrecognised value) → ``agent:floating-artemis``
      (the legacy Artemis scope; preserves backward compat).
    - ``"callie"`` → ``agent:callie`` (Callie's isolated marketing scope).
    """
    normalized = (agent_id or "").strip().lower()
    if normalized and normalized != "artemis":
        return Scope(scope_kind="agent", scope_id=normalized)
    return _FA_SCOPE

# ── 5-second retrieval cache ─────────────────────────────────────────────────
# Key: (session_id, query_prefix)  Value: (timestamp, results)
_retrieval_cache: dict[tuple[str, str], tuple[float, list[Any]]] = {}
_CACHE_TTL_SECONDS = 5.0


def _cache_key(session_id: str, query: str) -> tuple[str, str]:
    # Use first 200 chars of query as the cache discriminator — enough to
    # distinguish turns without unbounded key growth.
    return (session_id, query[:200])


def _get_cached(session_id: str, query: str) -> list[Any] | None:
    key = _cache_key(session_id, query)
    entry = _retrieval_cache.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        del _retrieval_cache[key]
        return None
    return results


def _put_cache(session_id: str, query: str, results: list[Any]) -> None:
    key = _cache_key(session_id, query)
    _retrieval_cache[key] = (time.monotonic(), results)


# ── M3: auto-write conversation drawer ───────────────────────────────────────


async def write_turn_drawer(
    user_msg_id: int,
    user_text: str,
    assistant_text: str,
    *,
    agent_id: str = "artemis",
) -> None:
    """Write a memory drawer capturing this turn. Failure-isolated.

    Uses a fresh SessionLocal per M1's session pattern so that memory writes
    never share a transaction with the chat flow and cannot cause deadlocks.

    ``agent_id`` controls the target scope: ``"callie"`` writes to
    ``agent:callie``; any other value uses ``agent:floating-artemis``.
    """
    try:
        import artemis.db as _db

        content = f"[USER] {user_text}\n[ASSISTANT] {assistant_text}"
        source = Source(
            source_kind="floating_artemis_message",
            source_id=str(user_msg_id),
        )
        scope = _scope_for_agent(agent_id)
        async with _db.SessionLocal() as session:
            await write_drawer(
                session,
                scope=scope,
                content=content,
                source=source,
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Floating Artemis turn-drawer write failed for msg_id=%s",
            user_msg_id,
            exc_info=True,
        )


# ── M4: auto-read memory injection ───────────────────────────────────────────


async def inject_memory_context(
    prompt: str,
    user_msg: str,
    history: list[Any],
    session_id: str,
    *,
    agent_id: str = "artemis",
) -> str:
    """Inject relevant memory observations into the system prompt. Failure-isolated.

    Opens a fresh SessionLocal for memory reads (per M1's session pattern, to
    avoid SAVEPOINT collisions with the chat flow session).

    Returns the original prompt unchanged if retrieval fails or finds nothing.

    ``agent_id`` controls the retrieval scope: ``"callie"`` queries
    ``agent:callie``; any other value queries ``agent:floating-artemis``.
    """
    try:
        import artemis.db as _db

        # Build retrieval query from user message + last 3 history turns.
        history_texts: list[str] = []
        for h in history[-3:]:
            for block in getattr(h, "content", []):
                text = getattr(block, "text", None)
                if text:
                    history_texts.append(text)
        query = user_msg + ("\n" + "\n".join(history_texts) if history_texts else "")

        # Check cache first
        cached = _get_cached(session_id, query)
        if cached is not None:
            results = cached
        else:
            scope = _scope_for_agent(agent_id)
            async with _db.SessionLocal() as session:
                results = await search_observations(
                    session,
                    scope_set=[scope],
                    query=query,
                    limit=5,
                )
            _put_cache(session_id, query, results)

        if not results:
            return prompt

        memory_block = (
            "\n\n## Recent memory (LLM-curated observations from prior conversations)\n\n"
            "These are observations the platform has recorded across past conversations. "
            "Use them for continuity but verify before acting on specific claims.\n\n"
        )
        for obs in results:
            content_preview = obs.content[:500] if len(obs.content) > 500 else obs.content
            memory_block += f"- {content_preview}\n"

        return prompt + memory_block

    except Exception:
        logger.warning("Floating Artemis memory injection failed", exc_info=True)
        return prompt
