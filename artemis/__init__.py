"""Artemis OS — Marketing Intelligence + Campaign Workflow System."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load env files at package import so API keys + integration secrets are
# available to all downstream modules.
#
# CRITICAL: override=False on BOTH files. An explicit os.environ value
# (e.g. set by tests/conftest.py to redirect ARTEMIS_DB_URL → artemis_test)
# MUST win over .env contents. Otherwise tests write to the live database
# despite the safety guard. This bit us three times on 2026-05-18.
#
# Resolution order (highest → lowest priority):
#   1. Explicit os.environ values already set at import (tests, launchd, shell)
#   2. Project-local .env (./.env)
#   3. User-global ~/.artemis/.env
_HOME = Path(os.path.expanduser("~"))
load_dotenv(Path(__file__).parent.parent / ".env", override=False)
load_dotenv(_HOME / ".artemis" / ".env", override=False)

__version__ = "0.0.1"
