"""Phase B4: Graph extraction engine.

Triggered by the incremental consolidator after consolidation completes for a
scope. Scans for memory_observations with graph_status IS NULL (or failed but
retryable) and calls Haiku to extract entities/relations, persisting them via
the graph.py helpers.

Backoff schedule on failure (same as Node reference):
  attempt 1 → 0 s (immediate)
  attempt 2 → 60 s
  attempt 3 → 300 s
  attempt 4 → 1 800 s
  attempt 5 → 7 200 s
  after 5 attempts → permanent 'failed'

Per-observation in-flight guard prevents concurrent extractions for the same
observation. Extraction errors never propagate to the consolidation path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.costs.events import record_cost_event
from artemis.memory.graph import (
    VALID_ENTITY_KINDS,
    VALID_PREDICATES,
    record_alias,
    record_mention,
    upsert_entity,
    upsert_relation,
)
from artemis.memory.models import MemoryObservation
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter_async

_logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "memory-graph.yaml"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# System prompt (static — prompt-cached by Anthropic)
_SYSTEM_PROMPT = """You are a named entity and relation extractor for a marketing intelligence system.
Extract entities and relations from memory observations.

ENTITY KINDS (use the best fit only):
- person     — a named individual human (e.g. "Angela", "Josh")
- brand      — a company, product, or external platform (e.g. "LinkedIn", "Amira Learning")
- campaign   — a named marketing campaign stated explicitly (e.g. "Spring 2026"); NOT generic phrases
- project    — an internal tool or process (e.g. "Writing Studio", "Signal Scout")
- post       — a specific named piece of content (blog post, article, webinar)
- channel    — a generic channel type such as "email" or "social media"; NOT company names
- other      — anything that does not clearly fit the above

RELATION PREDICATES (use exactly one from this list):
works_on, owns, publishes_to, belongs_to, posted_on, runs_campaign, authored_by, mentioned_with, related_to

Rules:
- Extract only entities explicitly mentioned by name (no pronouns, no "the team")
- Only use entity kinds and predicates from the lists above
- Aliases: surface forms that refer to the same entity; leave empty array if none
- Relations: subject/object must be entity names from your entities list
- Return ONLY a JSON object — no explanation, no markdown fences

Output format:
{"entities": [{"kind": "person", "name": "Angela", "aliases": []}], "relations": [{"subject": "Angela", "predicate": "runs_campaign", "object": "Spring 2026"}]}

If no entities exist, return: {"entities": [], "relations": []}"""

# ── Backoff ───────────────────────────────────────────────────────────────────

_BACKOFF_SECONDS = [0, 60, 300, 1800, 7200]


def _backoff(attempt_count: int) -> float:
    """Return delay seconds for attempt_count (1-based)."""
    idx = min(attempt_count - 1, len(_BACKOFF_SECONDS) - 1)
    return float(_BACKOFF_SECONDS[idx])


# ── Config ────────────────────────────────────────────────────────────────────


def _extraction_model() -> str:
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open() as f:
            cfg: dict[str, Any] = yaml.safe_load(f) or {}
        return str(cfg.get("extraction_model", _DEFAULT_MODEL))
    return _DEFAULT_MODEL


# ── Prompt parser ─────────────────────────────────────────────────────────────


def _parse_extraction_output(raw: str) -> dict[str, Any] | None:
    """Extract JSON from the model response. Handles markdown fences."""
    if not raw:
        return None
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw) or re.search(r"(\{[\s\S]*\})", raw)
    json_str = match.group(1).strip() if match else raw.strip()
    try:
        parsed: dict[str, Any] = json.loads(json_str)
        if not isinstance(parsed.get("entities"), list):
            return None
        return parsed
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


# ── In-flight guard ───────────────────────────────────────────────────────────

_INFLIGHT: set[int] = set()


def _peek_inflight() -> frozenset[int]:
    return frozenset(_INFLIGHT)


# ── Injectable deps (model call + session factory) ────────────────────────────

_call_model_fn: Any = None  # set by tests via _set_call_model_for_tests
_session_factory_fn: Any = None  # set by tests via _set_session_factory_for_tests


def _get_session_factory() -> Any:
    if _session_factory_fn is not None:
        return _session_factory_fn
    from artemis.db import SessionLocal

    return SessionLocal


async def _default_call_model(content: str, model: str) -> str:
    """Call the provider abstraction (claude-code cascade) with the static system prompt.

    Routes through resolve_adapter("claude-code") — no raw Anthropic SDK / no
    ANTHROPIC_API_KEY required when the claude-code CLI is available. Mirrors the
    pattern used by artemis/memory/consolidator.py (C3 pattern).
    """
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    try:
        factory = _get_session_factory()
        async with factory() as _override_session:
            adapter = await resolve_adapter_async(
                provider="claude-code",
                feature_tag="memory_graph_extraction",
                session=_override_session,
            )
    except NoProviderAvailableError as exc:
        raise RuntimeError(f"Graph extraction: no provider available: {exc}") from exc

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=f'Observation: "{content}"')])],
        system=_SYSTEM_PROMPT,
        model=model,
        max_tokens=512,
        cache_system=True,
    )
    response = await adapter.complete(request)
    # Record cost — failure must never propagate to the caller.
    try:
        from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

        _provider = "claude-code" if isinstance(adapter, ClaudeCodeAdapter) else "anthropic"
        _path = "cli" if isinstance(adapter, ClaudeCodeAdapter) else "api"
        factory = _get_session_factory()
        async with factory() as _cost_session:
            await record_cost_event(
                _cost_session,
                provider=_provider,
                model=model,
                provider_path=_path,
                feature_tag="memory_graph_extraction",
                input_tokens=getattr(response.usage, "input_tokens", 0),
                output_tokens=getattr(response.usage, "output_tokens", 0),
                cache_creation_input_tokens=getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
                cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
            )
            await _cost_session.commit()
    except Exception:
        _logger.warning("cost_event recording failed in graph_extractor", exc_info=True)
    parts = [b.text for b in response.message.content if isinstance(b, TextBlock)]
    return "".join(parts)


async def _call_model(content: str, model: str) -> str:
    fn = _call_model_fn or _default_call_model
    return await fn(content, model)


# ── DB helpers ────────────────────────────────────────────────────────────────


async def _set_graph_status(
    session: AsyncSession,
    obs_id: int,
    status: str,
    attempt_count: int,
) -> None:
    from datetime import UTC, datetime

    stmt = (
        update(MemoryObservation)
        .where(MemoryObservation.id == obs_id)
        .values(
            graph_status=status,
            graph_attempt_count=attempt_count,
            graph_last_attempt_at=datetime.now(UTC),
        )
    )
    await session.execute(stmt)


async def _get_pending_observations(
    session: AsyncSession,
    scope_kind: str,
    scope_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return observations that need graph extraction (status IS NULL or retryable failed)."""
    sql = text("""
        SELECT id, content, scope_kind, scope_id, graph_attempt_count
        FROM memory_observations
        WHERE scope_kind = :sk AND scope_id = :si
          AND superseded_by IS NULL
          AND (graph_status IS NULL
               OR (graph_status = 'failed' AND graph_attempt_count < 5))
        LIMIT :lim
    """)
    result = await session.execute(sql, {"sk": scope_kind, "si": scope_id, "lim": limit})
    return [dict(row._mapping) for row in result]


# ── Core extraction ───────────────────────────────────────────────────────────


async def extract_for_observation(
    obs_id: int,
    scope_kind: str,
    scope_id: str,
) -> None:
    """Extract entities/relations for one observation and persist the results.

    Never raises — all errors are logged and recorded as graph_status='failed'.
    Retry scheduling uses asyncio.get_running_loop().call_later with backoff.
    """
    if obs_id in _INFLIGHT:
        return
    _INFLIGHT.add(obs_id)

    try:
        _sf = _get_session_factory()

        # Fetch obs, guard against already-ok, and mark pending — all in one transaction
        async with _sf() as session, session.begin():
            check = await session.execute(
                text(
                    "SELECT content, graph_status, graph_attempt_count FROM memory_observations WHERE id = :id AND superseded_by IS NULL"
                ),
                {"id": obs_id},
            )
            row = check.one_or_none()
            if row is None:
                return
            if row.graph_status == "ok":
                return

            content: str = str(row.content)
            attempt_count = int(row.graph_attempt_count or 0) + 1
            await _set_graph_status(session, obs_id, "pending", attempt_count)

        # Call model (outside transaction — can be slow)
        model = _extraction_model()
        try:
            raw = await _call_model(content, model)
            parsed = _parse_extraction_output(raw)
            if parsed is None:
                raise ValueError(f"parse failed: {raw[:120]!r}")
        except Exception as call_err:
            _logger.warning("Graph extraction model call failed for obs %d: %s", obs_id, call_err)
            await _handle_failure(obs_id, scope_kind, scope_id, attempt_count)
            return

        # Persist entities, aliases, mentions, and relations
        try:
            async with _sf() as session, session.begin():
                entity_map: dict[str, int] = {}  # name → entity_id
                for ent in parsed.get("entities") or []:
                    if not ent or not ent.get("name") or ent.get("kind") not in VALID_ENTITY_KINDS:
                        continue
                    try:
                        entity = await upsert_entity(
                            session,
                            kind=ent["kind"],
                            name=ent["name"],
                            scope_kind=scope_kind,
                            scope_id=scope_id,
                            confidence=0.9,
                        )
                        entity_map[ent["name"]] = entity.id
                        for alias in ent.get("aliases") or []:
                            if alias and alias != ent["name"]:
                                await record_alias(session, entity.id, alias)
                        await record_mention(
                            session,
                            entity_id=entity.id,
                            source_kind="observation",
                            source_id=obs_id,
                        )
                    except Exception as e:
                        _logger.debug("Skip entity %r: %s", ent.get("name"), e)

                for rel in parsed.get("relations") or []:
                    if (
                        not rel
                        or not rel.get("subject")
                        or not rel.get("object")
                        or not rel.get("predicate")
                    ):
                        continue
                    if rel["predicate"] not in VALID_PREDICATES:
                        continue
                    sub_id = entity_map.get(rel["subject"])
                    obj_id = entity_map.get(rel["object"])
                    if sub_id is None or obj_id is None:
                        continue
                    await upsert_relation(
                        session,
                        subject_id=sub_id,
                        predicate=rel["predicate"],
                        object_id=obj_id,
                        evidence_observation_id=obs_id,
                    )

                await _set_graph_status(session, obs_id, "ok", attempt_count)

            _logger.info(
                "Graph extraction ok: obs %d → %d entities, %d relations",
                obs_id,
                len(entity_map),
                len(parsed.get("relations") or []),
            )
        except Exception as persist_err:
            _logger.warning("Graph extraction persist failed for obs %d: %s", obs_id, persist_err)
            await _handle_failure(obs_id, scope_kind, scope_id, attempt_count)
    finally:
        _INFLIGHT.discard(obs_id)


async def _handle_failure(
    obs_id: int,
    scope_kind: str,
    scope_id: str,
    attempt_count: int,
) -> None:
    """Record failure and schedule a retry with backoff (if retries remain)."""
    try:
        async with _get_session_factory()() as session, session.begin():
            await _set_graph_status(session, obs_id, "failed", attempt_count)
    except Exception:
        _logger.debug("Could not record failure for obs %d", obs_id, exc_info=True)

    if attempt_count < 5:
        delay = _backoff(attempt_count + 1)
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(
                delay,
                lambda: asyncio.ensure_future(
                    extract_for_observation(obs_id, scope_kind, scope_id)
                ),
            )
        except RuntimeError:
            pass  # no running loop; test context


# ── Scope-level trigger ───────────────────────────────────────────────────────


def notify_consolidation_complete(scope_kind: str, scope_id: str) -> None:
    """Called by the incremental consolidator after consolidation completes.

    Fires extraction for every pending observation in the scope. Fire-and-forget.
    Silently skipped when ARTEMIS_GRAPH_EXTRACTION_DISABLED=1.
    """
    if os.environ.get("ARTEMIS_GRAPH_EXTRACTION_DISABLED") == "1":
        return

    async def _run() -> None:
        try:
            async with _get_session_factory()() as session:
                pending = await _get_pending_observations(session, scope_kind, scope_id)
            for obs in pending:
                asyncio.ensure_future(extract_for_observation(int(obs["id"]), scope_kind, scope_id))
        except Exception:
            _logger.exception(
                "notify_consolidation_complete failed for %s/%s", scope_kind, scope_id
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass  # no running loop; test context


# ── Test helpers ──────────────────────────────────────────────────────────────


def _set_call_model_for_tests(fn: Any) -> None:
    global _call_model_fn
    _call_model_fn = fn


def _set_session_factory_for_tests(sf: Any) -> None:
    global _session_factory_fn
    _session_factory_fn = sf


def _reset_for_tests() -> None:
    global _call_model_fn, _session_factory_fn
    _call_model_fn = None
    _session_factory_fn = None
    _INFLIGHT.clear()
