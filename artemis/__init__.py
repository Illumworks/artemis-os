"""Artemis OS — Marketing Intelligence + Campaign Workflow System."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load env files at package import so API keys + integration secrets are
# available to all downstream modules. User-global first, project-local
# wins on conflict.
_HOME = Path(os.path.expanduser("~"))
load_dotenv(_HOME / ".artemis" / ".env", override=False)
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

__version__ = "0.0.1"
