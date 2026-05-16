"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

Lifecycle:
- Engine is module-level singleton, created on first import.
- Sessions are per-request, yielded by `get_session()` for FastAPI Depends.
- Tests override the engine via `artemis.db.engine` monkeypatching or by
  setting `ARTEMIS_DB_URL` to a test database before import.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from artemis.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models. Memory keystone models will inherit from this."""


engine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a per-request session."""
    async with SessionLocal() as session:
        yield session
