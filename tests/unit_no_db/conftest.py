"""Minimal conftest for pure-unit tests that do NOT require a database.

These tests mock all DB session interactions and are safe to run without a
ARTEMIS_TEST_DB_URL (e.g. in a worktree that has no .env).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Provide a fake DB URL so artemis.config doesn't blow up on import.
# The tests mock the session so this URL is never actually used.
os.environ.setdefault(
    "ARTEMIS_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/artemis_test_unit"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-real")
os.environ.setdefault("FERNET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
