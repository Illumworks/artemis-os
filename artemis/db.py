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

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pgvector.asyncpg  # type: ignore[import-untyped]
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from artemis.config import settings

logger = logging.getLogger(__name__)


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
    # Bound asyncpg connection ESTABLISHMENT. asyncpg's default is 60s, so a single
    # stuck connect (the instability-bug failure mode) hangs a request for a full
    # minute before pool_pre_ping/retry can recover. 10s fails fast and lets the
    # pool fall back to a healthy connection. See
    # briefs/instability-asyncpg-connect-timeout.md.
    connect_args={"timeout": 10},
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


# ---------------------------------------------------------------------------
# Postgres startup-race guard
# ---------------------------------------------------------------------------

# Errors that indicate Postgres is still initialising (not-yet-ready, not
# a permanent misconfiguration).  We cast a broad net so asyncpg's
# CannotConnectNowError (wrapped by SQLAlchemy) and plain OS-level
# connection-refused are both caught.
_TRANSIENT_EXC = (OperationalError, InterfaceError, DBAPIError, OSError)


async def wait_for_db_ready(
    *,
    max_wait: float = 45.0,
    initial_delay: float = 0.5,
    max_delay: float = 5.0,
    _ping: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> bool:
    """Wait until Postgres accepts connections, retrying with exponential back-off.

    Called from the FastAPI lifespan BEFORE any schedulers or background tasks
    start, so that a cold-DB startup race (Mac wakes from sleep / Postgres
    restarts mid-boot) doesn't propagate into scheduler abort cascades.

    Parameters
    ----------
    max_wait:
        Total seconds to keep retrying before giving up (default 45).
    initial_delay:
        First sleep interval in seconds (default 0.5).
    max_delay:
        Upper bound on sleep interval in seconds (default 5).
    _ping:
        Async callable that performs the readiness probe.  Defaults to a
        ``SELECT 1`` against the module-level engine.  Inject a fake in tests
        to run without a real DB and without real sleeps.

    Returns
    -------
    True
        Postgres is ready.

    Raises
    ------
    RuntimeError
        After ``max_wait`` seconds without a successful connection.
    """

    async def _default_ping() -> None:
        # Use a fresh raw connection — bypasses pool and session machinery so
        # we can probe before any pool warmup.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    probe = _ping if _ping is not None else _default_ping

    delay = initial_delay
    elapsed = 0.0
    attempt = 0

    while True:
        attempt += 1
        try:
            await probe()
            logger.info("Postgres is ready (attempt %d, elapsed %.1fs).", attempt, elapsed)
            return True
        except _TRANSIENT_EXC as exc:
            remaining = max_wait - elapsed
            if remaining <= 0:
                logger.critical(
                    "Postgres not ready after %.1fs (%d attempts); last error: %s. "
                    "Aborting startup — launchd KeepAlive will relaunch.",
                    max_wait,
                    attempt,
                    exc,
                )
                raise RuntimeError(
                    f"Postgres not ready after {max_wait}s ({attempt} attempts): {exc}"
                ) from exc
            logger.info(
                "Waiting for Postgres to accept connections, attempt %d "
                "(%.1fs elapsed, %.1fs remaining): %s",
                attempt,
                elapsed,
                remaining,
                exc,
            )
            sleep_for = min(delay, remaining)
            await asyncio.sleep(sleep_for)
            elapsed += sleep_for
            delay = min(delay * 2, max_delay)
