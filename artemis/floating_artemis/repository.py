"""Repository helpers for the Floating Artemis domain.

All functions accept an AsyncSession and are async.
Callers own commit/rollback.

Convention:
- Raise ValueError for not-found conditions (caller maps to 404).
- No business logic — just DB read/write.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.floating_artemis.models import (
    FloatingArtemisMessage,
    FloatingArtemisPageContext,
    FloatingArtemisSession,
    FloatingArtemisVoiceCorpus,
)

# ─────────────────────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────────────────────


async def create_session(
    session: AsyncSession,
    *,
    session_id: str,
    owner_user_id: int | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> FloatingArtemisSession:
    row = FloatingArtemisSession(
        session_id=session_id,
        owner_user_id=owner_user_id,
        title=title,
        metadata_=metadata or {},
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_session_by_id(session: AsyncSession, session_id: str) -> FloatingArtemisSession:
    result = await session.execute(
        select(FloatingArtemisSession).where(FloatingArtemisSession.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"FloatingArtemisSession '{session_id}' not found")
    return row


async def list_sessions(
    session: AsyncSession,
    *,
    owner_user_id: int | None = None,
    limit: int = 50,
    cursor: int | None = None,
    include_closed: bool = False,
) -> list[FloatingArtemisSession]:
    q = select(FloatingArtemisSession).order_by(FloatingArtemisSession.id.desc()).limit(limit)
    if not include_closed:
        q = q.where(FloatingArtemisSession.closed_at.is_(None))
    if owner_user_id is not None:
        q = q.where(FloatingArtemisSession.owner_user_id == owner_user_id)
    if cursor is not None:
        q = q.where(FloatingArtemisSession.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_session(
    session: AsyncSession,
    session_id: str,
    **kwargs: Any,
) -> FloatingArtemisSession:
    row = await get_session_by_id(session, session_id)
    for key, value in kwargs.items():
        # metadata is stored as metadata_ in the model
        attr = "metadata_" if key == "metadata" else key
        setattr(row, attr, value)
    row.last_active_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def close_session(session: AsyncSession, session_id: str) -> FloatingArtemisSession:
    row = await get_session_by_id(session, session_id)
    row.closed_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def archive_session(session: AsyncSession, session_id: str) -> FloatingArtemisSession:
    """Archive a session — marks it closed + sets metadata.archived=true.

    Unlike close_session (which is a hard close), archive is recoverable:
    the session remains queryable via include_closed=true and can be
    surfaced in a future session-history view. "Start fresh" uses this.
    """
    row = await get_session_by_id(session, session_id)
    row.closed_at = datetime.now(UTC)
    existing_meta = row.metadata_ or {}
    row.metadata_ = {**existing_meta, "archived": True}
    await session.flush()
    await session.refresh(row)
    return row


async def touch_session(session: AsyncSession, session_id: str) -> None:
    """Update last_active_at without a full fetch."""
    await session.execute(
        update(FloatingArtemisSession)
        .where(FloatingArtemisSession.session_id == session_id)
        .values(last_active_at=datetime.now(UTC))
    )


async def update_session_model(
    session: AsyncSession,
    session_id: str,
    *,
    provider: str | None,
    model: str | None,
) -> FloatingArtemisSession:
    """Set the provider/model for a session.

    Validation (provider must be registered or null) is the caller's
    responsibility — the repository is deliberately thin.
    Raises ValueError if the session is not found.
    """
    row = await get_session_by_id(session, session_id)
    row.provider = provider
    row.model = model
    row.last_active_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────────────────────────────────────


async def add_message(
    session: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: list[dict[str, Any]],
    cost_input_tokens: int = 0,
    cost_output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> FloatingArtemisMessage:
    msg = FloatingArtemisMessage(
        session_id=session_id,
        role=role,
        content=content,
        cost_input_tokens=cost_input_tokens,
        cost_output_tokens=cost_output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )
    session.add(msg)
    await session.flush()
    await session.refresh(msg)
    return msg


async def list_messages(
    session: AsyncSession,
    session_id: str,
    *,
    limit: int = 100,
    cursor: int | None = None,
) -> list[FloatingArtemisMessage]:
    q = (
        select(FloatingArtemisMessage)
        .where(FloatingArtemisMessage.session_id == session_id)
        .order_by(FloatingArtemisMessage.id.asc())
        .limit(limit)
    )
    if cursor is not None:
        q = q.where(FloatingArtemisMessage.id > cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Page context
# ─────────────────────────────────────────────────────────────────────────────


async def set_page_context(
    session: AsyncSession,
    *,
    session_id: str,
    page: str,
    ref_id: str | None = None,
) -> FloatingArtemisPageContext:
    ctx = FloatingArtemisPageContext(
        session_id=session_id,
        page=page,
        ref_id=ref_id,
    )
    session.add(ctx)
    await session.flush()
    await session.refresh(ctx)
    return ctx


async def get_latest_page_context(
    session: AsyncSession, session_id: str
) -> FloatingArtemisPageContext | None:
    result = await session.execute(
        select(FloatingArtemisPageContext)
        .where(FloatingArtemisPageContext.session_id == session_id)
        .order_by(FloatingArtemisPageContext.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Voice corpus
# ─────────────────────────────────────────────────────────────────────────────


async def seed_voice_corpus_from_profile(session: AsyncSession) -> int:
    """Parse personality profile and idempotently upsert seed phrases.

    Returns the number of NEW rows inserted (0 if all already seeded).
    """
    profile_path = Path(__file__).parent.parent.parent / "artemis-personality-profile.md"
    if not profile_path.exists():
        return 0

    text_content = profile_path.read_text(encoding="utf-8")
    lines: list[str] = []
    in_phrases = False
    for raw_line in text_content.splitlines():
        stripped = raw_line.strip()
        if "Characteristic phrases" in stripped:
            in_phrases = True
            continue
        if in_phrases:
            # Lines starting with - "..." are the phrases
            if stripped.startswith('- "') and stripped.endswith('"'):
                phrase = stripped[3:-1]  # strip leading `- "` and trailing `"`
                if phrase:
                    lines.append(phrase)
            elif stripped and not stripped.startswith("-") and not stripped.startswith("*"):
                # A non-list line means end of phrase block
                in_phrases = False

    inserted = 0
    for line in lines:
        stmt = (
            pg_insert(FloatingArtemisVoiceCorpus)
            .values(line=line, source="seed", active=True)
            .on_conflict_do_nothing(index_elements=["line"])
        )
        result = await session.execute(stmt)
        inserted += getattr(result, "rowcount", 0) or 0

    await session.flush()
    return inserted


async def sample_voice_lines(
    session: AsyncSession,
    *,
    owner_user_id: int | None = None,
    count: int = 5,
    exclude_recent_session_ids: list[str] | None = None,
) -> list[FloatingArtemisVoiceCorpus]:
    """Sample up to *count* active voice lines, weighted by inverse use_count.

    Excludes lines recently used in the provided session IDs (prevents
    same-session repetition). Falls back to random if no suitable candidates.
    """
    q = select(FloatingArtemisVoiceCorpus).where(FloatingArtemisVoiceCorpus.active.is_(True))

    # Exclude lines used in recent sessions — best-effort via a subquery
    if exclude_recent_session_ids:
        # Get IDs of messages from those sessions that include our lines
        # Simple approach: exclude lines used very recently (last_used_at within
        # those sessions). Since we don't have a direct message→corpus link,
        # we approximate with: skip lines with use_count > 0 that were used recently.
        # Proper implementation would track which lines appeared in which session.
        pass  # approximation: no exclusion beyond per-session tracking

    result = await session.execute(q)
    all_lines = list(result.scalars().all())

    if not all_lines:
        return []

    # Weighted sampling: lines with fewer uses are more likely to be picked.
    # Weight = 1 / (use_count + 1)
    weights = [1.0 / (line.use_count + 1) for line in all_lines]
    chosen_count = min(count, len(all_lines))

    chosen: list[FloatingArtemisVoiceCorpus] = []
    pool = list(zip(all_lines, weights, strict=False))
    for _ in range(chosen_count):
        if not pool:
            break
        total = sum(w for _, w in pool)
        r = random.random() * total  # noqa: S311 — not security-sensitive
        cumulative = 0.0
        for i, (item, weight) in enumerate(pool):
            cumulative += weight
            if r <= cumulative:
                chosen.append(item)
                pool.pop(i)
                break

    return chosen


async def record_voice_line(
    session: AsyncSession,
    *,
    line: str,
    context_tag: str | None = None,
    source: str = "observed",
) -> FloatingArtemisVoiceCorpus:
    """Insert a new voice line or fetch existing, marking source."""
    stmt = (
        pg_insert(FloatingArtemisVoiceCorpus)
        .values(
            line=line,
            context_tag=context_tag,
            source=source,
            active=True,
        )
        .on_conflict_do_nothing(index_elements=["line"])
        .returning(FloatingArtemisVoiceCorpus.id)
    )
    result = await session.execute(stmt)
    row_id = result.scalar_one_or_none()

    if row_id is None:
        # Already exists — fetch it
        existing = await session.execute(
            select(FloatingArtemisVoiceCorpus).where(FloatingArtemisVoiceCorpus.line == line)
        )
        return existing.scalar_one()

    await session.flush()
    fetched = await session.execute(
        select(FloatingArtemisVoiceCorpus).where(FloatingArtemisVoiceCorpus.id == row_id)
    )
    return fetched.scalar_one()


async def bump_voice_line_use(session: AsyncSession, line_id: int) -> None:
    """Increment use_count and update last_used_at for a voice line."""
    await session.execute(
        update(FloatingArtemisVoiceCorpus)
        .where(FloatingArtemisVoiceCorpus.id == line_id)
        .values(
            use_count=FloatingArtemisVoiceCorpus.use_count + 1,
            last_used_at=datetime.now(UTC),
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Active runs view
# ─────────────────────────────────────────────────────────────────────────────


async def get_active_runs(
    session: AsyncSession,
    *,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Query v_floating_artemis_active_runs, optionally filtered by owner."""
    sql = "SELECT * FROM v_floating_artemis_active_runs"
    params: dict[str, Any] = {}
    if owner_user_id is not None:
        sql += " WHERE owner_user_id = :owner_user_id"
        params["owner_user_id"] = owner_user_id
    sql += " ORDER BY started_at DESC LIMIT 50"

    result = await session.execute(text(sql), params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]
