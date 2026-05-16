"""Pytest fixtures shared across the suite.

Async loop management is delegated to pytest-asyncio via the
`asyncio_default_fixture_loop_scope = "session"` setting in pyproject.toml.
Do not define a custom `event_loop` fixture here — doing so causes
RuntimeError: Event loop is closed during async resource teardown.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app via ASGI transport (no real server)."""
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
