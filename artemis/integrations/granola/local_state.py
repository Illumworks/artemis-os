"""Read Granola desktop-app tokens from the local supabase.json state file.

The Granola.app on macOS persists WorkOS session tokens at a well-known path.
We read them as the friendly default auth path — no OAuth setup required for
users who already have the desktop app installed and signed in.

Returns None instead of raising on any read/parse failure; caller falls back
to OAuth path when None is returned.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# macOS only; override in tests via _STATE_PATH_OVERRIDE
_DEFAULT_STATE_PATH = Path.home() / "Library" / "Application Support" / "Granola" / "supabase.json"

# Seam for unit tests — set to a Path to bypass the real filesystem
_STATE_PATH_OVERRIDE: Path | None = None


def _state_path() -> Path:
    return _STATE_PATH_OVERRIDE if _STATE_PATH_OVERRIDE is not None else _DEFAULT_STATE_PATH


def read_local_token() -> str | None:
    """Return the Granola desktop-app access token, or None if unavailable.

    Handles two supabase.json shapes observed in the wild:
      - workos_tokens is a JSON-encoded string  (older Granola builds)
      - workos_tokens is a dict                 (newer Granola builds)

    Returns None when:
      - The file does not exist (Granola.app not installed or never signed in)
      - The file is unreadable or malformed
      - workos_tokens is missing or contains no access_token
    """
    path = _state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("Granola supabase.json not found at %s", path)
        return None
    except OSError as exc:
        logger.warning("Could not read Granola supabase.json: %s", exc)
        return None

    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Granola supabase.json parse error: %s", exc)
        return None

    workos_tokens = state.get("workos_tokens")
    if workos_tokens is None:
        logger.debug("Granola supabase.json: no workos_tokens key")
        return None

    # Older builds encode the tokens as a JSON string inside the JSON
    if isinstance(workos_tokens, str):
        try:
            workos_tokens = json.loads(workos_tokens)
        except json.JSONDecodeError:
            logger.warning("Granola supabase.json: workos_tokens is a non-JSON string")
            return None

    if not isinstance(workos_tokens, dict):
        return None

    token = workos_tokens.get("access_token")
    return str(token) if token else None
