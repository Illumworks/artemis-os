"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

Lifecycle:
- Engine is module-level singleton, created on first import.
- Sessions are per-request, yielded by `get_session()` for FastAPI Depends.
- Tests override the engine via `artemis.db.engine` monkeypatching or by
  setting `ARTEMIS_DB_URL` to a test database before import.

pgvector wiring:
- asyncpg has no built-in knowledge of the `vector` type. Without registering
  the pgvector codec on each new connection, parameter binding for `Vector`
  columns silently fails (errors look like '[parameters: [{}]]' in logs).
  We hook the engine's `connect` event to register the codec on every new
  connection via `dbapi_conn.run_async(...)`. The same helper is exported
  for the test engine in `artemis/memory/tests/conftest.py`.
"""

from collections.abc import AsyncIterator
from typing import Any

import pgvector.asyncpg  # type: ignore[import-untyped]
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from artemis.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models. Memory keystone models will inherit from this."""


def _register_pgvector_codec(dbapi_conn: Any, _connection_record: Any) -> None:
    """SQLAlchemy 'connect' listener — registers pgvector on each asyncpg conn.

    SQLAlchemy wraps the raw asyncpg connection in an AsyncAdapt_asyncpg_connection.
    Its `run_async()` schedules an async callable on the connection's loop, which
    is the only safe way to call asyncpg's async `register_vector` from the
    sync `connect` event.
    """
    dbapi_conn.run_async(pgvector.asyncpg.register_vector)


def attach_pgvector_codec(engine: AsyncEngine) -> None:
    """Wire `_register_pgvector_codec` to an engine's sync handle.

    Idempotent: SQLAlchemy's event system tolerates duplicate listener attaches
    on the same target+event, but we still gate to avoid noise in test reruns.
    """
    sync_engine: Engine = engine.sync_engine
    if not event.contains(sync_engine, "connect", _register_pgvector_codec):
        event.listen(sync_engine, "connect", _register_pgvector_codec)


engine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
)
attach_pgvector_codec(engine)


SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a per-request session."""
    async with SessionLocal() as session:
        yield session
