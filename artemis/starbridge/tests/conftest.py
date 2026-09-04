"""A rollback-scoped session for the routing tests.

Deliberately NOT the TRUNCATE-per-test pattern used elsewhere. TRUNCATE on these
tables deadlocks against any other session holding them -- including a leaked
idle-in-transaction fixture inside a single serial run -- and these tests only
ever need their own writes to be invisible afterwards. `route_delivery` flushes
rather than commits, so a transaction rolled back at teardown is both sufficient
and free of contention.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

_db_url = os.getenv("ARTEMIS_TEST_DB_URL") or os.getenv(
    "ARTEMIS_DB_URL", "postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a"
)
_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()


@pytest.fixture(autouse=True)
def _no_live_bridge_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the routing tests off the network.

    `route_delivery` resolves a bridge's type from the Starbridge API. Left
    unstubbed the suite made a real HTTP call per test, which is slow, needs a
    key, and fails in CI. Tests that care about the type override this.
    """
    import artemis.starbridge.router as router_mod

    async def _unresolved(_bridge_id: str) -> str:
        return ""

    monkeypatch.setattr(router_mod, "resolve_bridge_type", _unresolved)
