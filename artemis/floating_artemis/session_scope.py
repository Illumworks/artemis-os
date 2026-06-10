"""Session-scoped helpers for Floating Artemis surface and history behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_MARKETING_SURFACES = frozenset(
    {
        "scouts",
        "signal-queue",
        "signal-criteria",
        "campaign-ops",
        "campaign-deliverables",
        "content-assets",
        "approvals",
        "writing-studio",
        "marketing-os",
    }
)


def personal_surfaces(all_surfaces: set[str]) -> set[str]:
    """Return the personal-scope surface set for Artemis's Slack DM."""
    return {surface for surface in all_surfaces if surface not in _MARKETING_SURFACES}


def _session_channel_id(session_id: str, metadata: dict[str, Any] | None) -> str | None:
    channel_id = metadata.get("channel_id") if isinstance(metadata, dict) else None
    if isinstance(channel_id, str) and channel_id:
        return channel_id

    if not session_id.startswith("slack-"):
        return None

    parts = session_id.split("-", 3)
    if len(parts) < 4:
        return None
    return parts[2]


def is_personal_slack_dm_session(session_id: str, metadata: dict[str, Any] | None) -> bool:
    """True iff the session is Artemis's personal 1:1 Slack DM surface."""
    if not session_id.startswith("slack-"):
        return False
    if not isinstance(metadata, dict) or metadata.get("surface") != "slack":
        return False
    channel_id = _session_channel_id(session_id, metadata)
    return isinstance(channel_id, str) and channel_id.startswith("D")


def resolve_surface_scope(
    *,
    all_surfaces: set[str],
    session_id: str,
    metadata: dict[str, Any] | None,
) -> set[str]:
    """Resolve the surface set available to this session."""
    if is_personal_slack_dm_session(session_id, metadata):
        return personal_surfaces(all_surfaces)
    return set(all_surfaces)


def parse_history_cutover_at(metadata: dict[str, Any] | None) -> datetime | None:
    """Parse metadata.history_cutover_at, returning an aware UTC datetime."""
    # Retired pre-cutover DM history stays on the same session for later Callie handoff.
    if not isinstance(metadata, dict):
        return None

    raw = metadata.get("history_cutover_at")
    if not isinstance(raw, str) or not raw.strip():
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
