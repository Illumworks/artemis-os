"""Test-DB wiring for artemis/argus/tests (ARGUS-2).

Every pre-existing test in this directory is fully mocked (no DB). ARGUS-2
adds tests that exercise ``_resolve_district_row``/``_fetch_board_minutes``
against a real ``districts`` row -- and those call ``artemis.db.SessionLocal``
directly (the same call the production code path makes; there is no
injectable-session variant here).

Two problems that would otherwise bite, both already solved elsewhere in
this repo (``artemis/routes/tests/conftest.py``, ``artemis/builders/tests/conftest.py``,
et al. -- this mirrors that established pattern rather than inventing a new
one):

1. Safety: pytest only loads a conftest.py from a collected test's OWN
   ancestor directories, and ``artemis/argus/tests`` is not a descendant of
   ``tests/``. Running the brief's own verification command,
   ``ARTEMIS_TEST_DB_URL=... uv run pytest artemis/argus/tests``, never
   touches ``tests/conftest.py``'s ARTEMIS_DB_URL guard at all. Without a
   copy of that guard here, ``artemis.db.engine`` would bind to whatever
   ``ARTEMIS_DB_URL`` resolves to in the ambient environment -- unset in a
   worktree with no ``.env``, which makes pydantic-settings fall back to
   ``artemis_os``, the LIVE database (see ``artemis/config.py``).
2. Event-loop safety: ``artemis.db.engine`` is a module-level singleton with
   a real connection pool (``pool_pre_ping=True``, default QueuePool),
   created once at import time. pytest-asyncio's function-scoped loops
   (this repo's default -- see pyproject.toml) mean each test function gets
   its OWN event loop; a pooled asyncpg connection checked out under one
   test's loop and reused under the next test's loop raises "Future attached
   to a different loop". The fix already established across this repo's
   other DB-touching conftest.py files is to REPLACE
   ``artemis.db.engine``/``SessionLocal`` with a NullPool-backed pair bound
   to the test DB, so every checkout is a fresh connection.

Must run before ``artemis.db`` is used by any test in this directory --
true here because conftest.py always loads before the modules in its
directory are collected, provided this directory is what's being collected
(as the brief's command does).
"""

from __future__ import annotations

import os

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction. This conftest INSERTs/DELETEs rows
# in `districts`; if ARTEMIS_DB_URL does not resolve to a test database,
# refuse to load.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "artemis/argus/tests inserts/deletes districts rows; running it against "
        "the live DB would corrupt real data. Set "
        "ARTEMIS_TEST_DB_URL=...artemis_test_a (or any *artemis_test* db)."
    )
os.environ["ARTEMIS_DB_URL"] = _db_url

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
