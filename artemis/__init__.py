"""Artemis OS — Marketing Intelligence + Campaign Workflow System."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load env files at package import so API keys + integration secrets are
# available to all downstream modules.
#
# CRITICAL — PERMANENT INVARIANT: override=False on BOTH files.
# An explicit os.environ value (e.g. set by tests/conftest.py to redirect
# ARTEMIS_DB_URL → artemis_test) MUST win over .env contents. Otherwise
# tests write to the live database despite the safety guard.
#
# Regression history (do not let this rot):
#   - 8f37470 — original fix, after three OKR-data wipes on 2026-05-18
#   - 7ad1598 — fix restored after commit b542dcc silently reverted it
#     during unrelated Granola work (commit message did not mention env)
#
# If you find yourself changing either `override=` to True, stop and read
# both commits above. The .env file points at the live `artemis_os` DB;
# without override=False, any shell-set ARTEMIS_DB_URL=...artemis_test
# is clobbered and TRUNCATE-using fixtures run against production data.
#
# Resolution order (highest → lowest priority):
#   1. Explicit os.environ values already set at import (tests, launchd, shell)
#   2. Project-local .env (./.env)
#   3. User-global ~/.artemis/.env
_HOME = Path(os.path.expanduser("~"))
load_dotenv(Path(__file__).parent.parent / ".env", override=False)
load_dotenv(_HOME / ".artemis" / ".env", override=False)

__version__ = "0.0.1"
