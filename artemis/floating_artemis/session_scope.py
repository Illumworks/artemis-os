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

# Per-agent surface allowlist.  When an agent's id appears here,
# resolve_surface_scope returns all_surfaces ∩ allowlist for that agent.
# Agents NOT listed default to the full surface set.
_AGENT_SURFACE_ALLOWLIST: dict[str, frozenset[str]] = {
    # Callie is a marketing-only analyst agent — she may only see marketing surfaces.
    # To grant Callie a new surface later (e.g. "jira-board", "calendar"), add it here.
    "callie": _MARKETING_SURFACES,
}


def personal_surfaces(all_surfaces: set[str]) -> set[str]:
    """Return the personal-scope surface set for Artemis's Slack DM."""
    return {surface for surface in all_surfaces if surface not in _MARKETING_SURFACES}


def _session_channel_id(session_id: str, metadata: dict[str, Any] | None) -> str | None:
    channel_id = metadata.get("channel_id") if isinstance(metadata, dict) else None
    if isinstance(channel_id, str) and channel_id:
        return channel_id

    if not session_id.startswith("slack-"):
        return None

    parts = session_id.split("-")
    if len(parts) < 4:
        return None
    if len(parts) >= 5:
        return parts[3]
    return parts[2]


def _session_agent_id(session_id: str, metadata: dict[str, Any] | None) -> str:
    agent_id = metadata.get("agent_id") if isinstance(metadata, dict) else None
    if isinstance(agent_id, str) and agent_id.strip():
        return agent_id.strip().lower()

    if not session_id.startswith("slack-"):
        return "artemis"

    parts = session_id.split("-")
    if len(parts) >= 5 and parts[1].strip():
        return parts[1].strip().lower()
    return "artemis"


def is_personal_slack_dm_session(session_id: str, metadata: dict[str, Any] | None) -> bool:
    """True iff the session is Artemis's personal 1:1 Slack DM surface."""
    if not session_id.startswith("slack-"):
        return False
    if not isinstance(metadata, dict) or metadata.get("surface") != "slack":
        return False
    if _session_agent_id(session_id, metadata) != "artemis":
        return False
    channel_id = _session_channel_id(session_id, metadata)
    return isinstance(channel_id, str) and channel_id.startswith("D")


def resolve_surface_scope(
    *,
    all_surfaces: set[str],
    session_id: str,
    metadata: dict[str, Any] | None,
) -> set[str]:
    """Resolve the surface set available to this session.

    Priority order:
    1. Artemis personal Slack DM → personal_surfaces (marketing stripped).
    2. Agent with an allowlist entry → all_surfaces ∩ allowlist.
    3. All other sessions → full all_surfaces.
    """
    if is_personal_slack_dm_session(session_id, metadata):
        return personal_surfaces(all_surfaces)
    agent_id = _session_agent_id(session_id, metadata)
    allowlist = _AGENT_SURFACE_ALLOWLIST.get(agent_id)
    if allowlist is not None:
        return all_surfaces & allowlist
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
