"""Pytest fixtures shared across the suite.

DATA-LOSS SAFETY GUARD (read this — it has saved real data):

Tests TRUNCATE real Postgres tables to enforce per-test isolation. If the
test suite is allowed to run against the live `artemis_os` database, every
`uv run pytest` wipes Slack integrations, OKR data, memory observations, and
anything else the conftest fixtures clear.

To prevent that:
  1. We force-override `ARTEMIS_DB_URL` to point at a safe test DB here,
     BEFORE any artemis module reads its config.
  2. We REFUSE to start the test session if the resolved URL points at the
     live database (name contains 'artemis_os' and not 'artemis_test').
  3. Create the test database once with:
         psql -h localhost -U artemis -d postgres \\
              -c "CREATE DATABASE artemis_test WITH OWNER = artemis"
     and run migrations against it:
         ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test \\
         uv run alembic upgrade head

Async loop management is delegated to pytest-asyncio via the
`asyncio_default_fixture_loop_scope = "session"` setting in pyproject.toml.
"""

import os
import sys

# Force the test database BEFORE anything imports artemis.config or artemis.db.
_TEST_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)
os.environ["ARTEMIS_DB_URL"] = _TEST_DB_URL

# Refuse to run if the resolved URL points at the live database. The
# `artemis_test` name passes; `artemis_os` does not.
_resolved = os.environ["ARTEMIS_DB_URL"]
if "artemis_test" not in _resolved:
    sys.exit(
        f"REFUSING TO RUN TESTS: ARTEMIS_DB_URL={_resolved!r} does not point "
        "at the artemis_test database. Tests TRUNCATE tables and would destroy "
        "real data. Override with ARTEMIS_DB_URL=...artemis_test."
    )

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app via ASGI transport (no real server)."""
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
