"""CLI binary discovery — env override, common install prefixes, PATH.

Ported from the Node reference: claudeck-artemis/server/providers/bin-path.js.
Python version adds os.access(X_OK) check that the Node version omits.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_cli_binary(name: str, extra_candidates: list[str] | None = None) -> str | None:
    """Return the first executable path for *name*, or None if not found.

    Search order:
      1. ``<NAME_BIN>`` env var (e.g. ``CLAUDE_BIN``, ``CODEX_BIN``)
      2. ``~/.local/bin/<name>``
      3. ``/usr/local/bin/<name>``
      4. ``/opt/homebrew/bin/<name>``
      5. ``shutil.which(name)`` (full PATH scan)
      6. ``extra_candidates`` (caller-supplied extras, checked last)
    """
    env_key = f"{name.upper().replace('-', '_')}_BIN"
    home = Path.home()

    candidates: list[str] = []
    if env_override := os.environ.get(env_key):
        candidates.append(env_override)

    candidates += [
        str(home / ".local" / "bin" / name),
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
    ]

    if path_resolved := shutil.which(name):
        candidates.append(path_resolved)

    if extra_candidates:
        candidates += extra_candidates

    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate

    return None
