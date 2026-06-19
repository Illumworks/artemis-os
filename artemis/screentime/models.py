"""ORM models for the isolated Screen-Time Watch namespace.

Three tables, all prefixed ``screentime_`` and registered on the shared
``Base.metadata`` (so alembic autogenerate + the env import pick them up):

  screentime_signals        — one row per discovered "real move".
  screentime_state_stance   — per-state rollup for the heat map (state = PK).
  screentime_stance_config  — tunable stance rules (singleton-ish; keyed by name).

NONE of these tables overlap with the marketing ``SignalQueue`` / campaign
tables. Wiping later = truncate these three tables only (see
``repository.purge_screentime_data``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

# Canonical list of the screentime_* tables — the single source of truth for
# the purge action so it can never accidentally touch a non-screentime table.
SCREENTIME_TABLES: tuple[str, ...] = (
    "screentime_signals",
    "screentime_state_stance",
    "screentime_stance_config",
)

# Vocabularies (kept as plain str columns — no PG enum, to stay tunable).
STANCE_FAVORABLE = "favorable"
STANCE_UNFAVORABLE = "unfavorable"
STANCE_NEUTRAL = "neutral"
STANCE_NO_INFO = "no_info"

LEVEL_STATE = "state"
LEVEL_DISTRICT = "district"


class ScreentimeSignal(Base):
    """One discovered screen-time policy/legislation move."""

    __tablename__ = "screentime_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    state: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, server_default=LEVEL_STATE)
    district_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # proposed | passed | amended | guidance | news
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="news")

    # favorable | unfavorable | neutral
    stance: Mapped[str] = mapped_column(Text, nullable=False, server_default=STANCE_NEUTRAL)
    amira_angle: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # legislative | state_doe | board_minutes | regional_news
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="regional_news")

    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    is_real_move: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # UNIQUE — the dedup key. Computed from source_type + source_url + title.
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    raw: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class ScreentimeStateStance(Base):
    """Per-state rollup recomputed from the signals (state = PK)."""

    __tablename__ = "screentime_state_stance"

    state: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    # favorable | unfavorable | neutral | no_info
    stance: Mapped[str] = mapped_column(Text, nullable=False, server_default=STANCE_NO_INFO)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_updated: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ScreentimeStanceConfig(Base):
    """Tunable stance-classification rules, editable WITHOUT a code change.

    A small key/value table; the active row is ``name = 'default'``. ``rules`` is
    a JSONB blob (see ``stance_config`` for the v1 default + schema). Updating the
    row (or the config.py settings fallback) re-tunes classification on the next
    run — no deploy required.
    """

    __tablename__ = "screentime_stance_config"

    name: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    rules: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
