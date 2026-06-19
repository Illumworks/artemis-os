"""Name→email directory.

A self-contained module mapping a person's NAME to their EMAIL, backed by a
``directory_people`` cache synced from Slack. Used by agents (via the
``resolve_person`` tool) and the post-meeting scheduler.

These exports do NOT import the providers/LLM stack, so eager imports here are
safe (no circular-import risk at app boot).
"""

from __future__ import annotations

from artemis.directory.resolver import DirectoryMatch, resolve_one, resolve_people
from artemis.directory.sync import (
    sync_directory,
    sync_directory_from_calendar,
    sync_directory_from_slack,
)

__all__ = [
    "DirectoryMatch",
    "resolve_one",
    "resolve_people",
    "sync_directory",
    "sync_directory_from_calendar",
    "sync_directory_from_slack",
]
