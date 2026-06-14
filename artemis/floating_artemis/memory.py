"""M3+M4 memory helpers for Floating Artemis.

M3 (original): write_turn_drawer — auto-write a memory drawer after every turn.
M4: inject_memory_context — auto-read observations into the system prompt.
Cache: 5-second per-session retrieval cache to avoid hot-loop DB calls.

M3 (access-control): agent READ paths are constrained to the agent's allowed
scopes via ``allowed_scopes_for_agent``.  Callie may NEVER retrieve
``agent:artemis`` or any ``personal:*`` observations, even if a scope_set
is passed by a caller.  Fail-closed: unknown agent → deny.

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

from artemis.identity.scope_policy import allowed_scopes_for_agent
from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Scope, Source
from artemis.memory.store import write_drawer

logger = logging.getLogger(__name__)

# Default scope for Artemis (backward compat)
_FA_SCOPE = Scope(scope_kind="agent", scope_id="floating-artemis")


def _enforce_agent_scope_set(agent_id: str, scope_set: list[Scope]) -> list[Scope]:
    """Filter scope_set to only scopes permitted by the agent's allowance.

    M3 fail-closed: if the allowance is denied or any scope in the set is
    outside the allowance, that scope is silently dropped.  If all scopes are
    dropped the caller gets an empty list → no results (correct for deny path).

    This is the critical gate that prevents Callie from reading agent:artemis
    or personal:* even if a caller passes them in scope_set.
    """
    allowance = allowed_scopes_for_agent(agent_id)
    if allowance.denied:
        return []
    if allowance.allow_all:
        return scope_set
    return [s for s in scope_set if allowance.permits(s.scope_kind, s.scope_id)]


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
    speaker_name: str | None = None,
    speaker_id: str | None = None,
) -> None:
    """Write a memory drawer capturing this turn. Failure-isolated.

    Uses a fresh SessionLocal per M1's session pattern so that memory writes
    never share a transaction with the chat flow and cannot cause deadlocks.

    ``agent_id`` controls the target scope: ``"callie"`` writes to
    ``agent:callie``; any other value uses ``agent:floating-artemis``.

    When ``speaker_name`` or ``speaker_id`` is provided the drawer content is
    prefixed with a structured ``[SPEAKER]`` line so that the speaker attribution
    survives into the searchable observation text after consolidation.  The same
    information is also stored in ``source_extra`` for structured, lossless
    metadata access.  When both are absent the output is byte-for-byte identical
    to the previous behaviour (backward compat / web UI path).
    """
    try:
        import artemis.db as _db

        # Build content — prepend speaker attribution when known.
        if speaker_name or speaker_id:
            speaker_label = speaker_name or ""
            speaker_suffix = f" ({speaker_id})" if speaker_id else ""
            content = (
                f"[SPEAKER] {speaker_label}{speaker_suffix}\n"
                f"[USER] {user_text}\n[ASSISTANT] {assistant_text}"
            )
            source_extra: dict[str, Any] | None = {
                k: v
                for k, v in {
                    "speaker_id": speaker_id,
                    "speaker_name": speaker_name,
                }.items()
                if v is not None
            }
        else:
            content = f"[USER] {user_text}\n[ASSISTANT] {assistant_text}"
            source_extra = None

        source = Source(
            source_kind="floating_artemis_message",
            source_id=str(user_msg_id),
            source_extra=source_extra,
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
    speaker_name: str | None = None,
    speaker_id: str | None = None,
) -> str:
    """Inject relevant memory observations into the system prompt. Failure-isolated.

    Opens a fresh SessionLocal for memory reads (per M1's session pattern, to
    avoid SAVEPOINT collisions with the chat flow session).

    Returns the original prompt unchanged if retrieval fails or finds nothing.

    ``agent_id`` controls the retrieval scope: ``"callie"`` queries
    ``agent:callie``; any other value queries ``agent:floating-artemis``.

    When ``speaker_name`` is supplied the retrieval query is biased toward that
    person's attributed observations (the speaker name is appended to the query
    text so FTS/semantic ranking naturally favours ``[SPEAKER] {name}`` drawers).
    An additional cheap lookup (limit 3) keyed on the speaker name is used to
    build a short ``## What I know about {speaker_name}`` digest that is
    prepended to the general memory block.  Both the bias and the per-person
    digest respect the existing 5-second retrieval cache.  Any failure in the
    per-person lookup is silently swallowed — it must never break chat.
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
        base_query = user_msg + ("\n" + "\n".join(history_texts) if history_texts else "")

        # Bias toward this speaker's attributed observations when identity is known.
        query = f"{base_query}\n{speaker_name}" if speaker_name else base_query

        # Check cache first
        cached = _get_cached(session_id, query)
        if cached is not None:
            results = cached
        else:
            scope = _scope_for_agent(agent_id)
            # M3: enforce agent scope allowance — filter the scope_set to only
            # scopes this agent is permitted to read.  Callie will never see
            # agent:artemis or personal:* even if scope_set were expanded.
            enforced_scope_set = _enforce_agent_scope_set(agent_id, [scope])
            if not enforced_scope_set:
                results = []
            else:
                async with _db.SessionLocal() as session:
                    results = await search_observations(
                        session,
                        scope_set=enforced_scope_set,
                        query=query,
                        limit=5,
                    )
            _put_cache(session_id, query, results)

        # Per-person recall digest — cheap extra lookup keyed on speaker name.
        person_results: list[Any] = []
        if speaker_name:
            person_cache_key = f"__person__{session_id}__{speaker_name}"
            cached_person = _get_cached(person_cache_key, speaker_name)
            if cached_person is not None:
                person_results = cached_person
            else:
                try:
                    scope = _scope_for_agent(agent_id)
                    # M3: enforce scope allowance for per-person digest too.
                    enforced_person_scope_set = _enforce_agent_scope_set(agent_id, [scope])
                    if not enforced_person_scope_set:
                        person_results = []
                    else:
                        async with _db.SessionLocal() as session:
                            person_results = await search_observations(
                                session,
                                scope_set=enforced_person_scope_set,
                                query=speaker_name,
                                limit=3,
                            )
                    _put_cache(person_cache_key, speaker_name, person_results)
                except Exception:
                    logger.warning(
                        "Per-person recall lookup failed for speaker=%s",
                        speaker_name,
                        exc_info=True,
                    )
                    person_results = []

        if not results and not person_results:
            return prompt

        memory_block = (
            "\n\n## Recent memory (LLM-curated observations from prior conversations)\n\n"
            "These are observations the platform has recorded across past conversations. "
            "Use them for continuity but verify before acting on specific claims.\n\n"
        )

        # Prepend per-person digest when available (deduped against general results).
        general_ids = {getattr(obs, "id", None) for obs in results}
        unique_person = [o for o in person_results if getattr(o, "id", None) not in general_ids]
        if speaker_name and unique_person:
            memory_block += f"### What I know about {speaker_name}\n\n"
            for obs in unique_person:
                content_preview = obs.content[:500] if len(obs.content) > 500 else obs.content
                memory_block += f"- {content_preview}\n"
            memory_block += "\n"

        for obs in results:
            content_preview = obs.content[:500] if len(obs.content) > 500 else obs.content
            memory_block += f"- {content_preview}\n"

        return prompt + memory_block

    except Exception:
        logger.warning("Floating Artemis memory injection failed", exc_info=True)
        return prompt
