"""Connector kind registry.

Each kind has a human-readable label and the list of credential fields
the connector stores. OAuth-based connectors (slack, gcal, granola) are
listed for completeness so they can be referenced by agents; their
credential management remains in the integrations module.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConnectorKind:
    id: str
    label: str
    fields: list[str] = field(default_factory=list)
    # If True, credentials are managed by the integrations OAuth flow, not
    # directly through the connectors CRUD API.
    oauth_managed: bool = False


# v1 supported kinds — add entries here as new sources are onboarded.
CONNECTOR_KINDS: dict[str, ConnectorKind] = {
    "starbridge": ConnectorKind(
        id="starbridge",
        label="Starbridge",
        fields=["api_key", "api_url"],
    ),
    "openai": ConnectorKind(
        id="openai",
        label="OpenAI",
        fields=["api_key", "organization"],  # organization is optional
    ),
    "anthropic": ConnectorKind(
        id="anthropic",
        label="Anthropic",
        fields=["api_key"],
    ),
    "gemini": ConnectorKind(
        id="gemini",
        label="Google Gemini",
        fields=["api_key"],
    ),
    "tavily": ConnectorKind(
        id="tavily",
        label="Tavily",
        fields=["api_key"],
    ),
    # OAuth-managed — listed for agent linking, not for direct CRUD
    "slack": ConnectorKind(
        id="slack",
        label="Slack",
        fields=[],
        oauth_managed=True,
    ),
    "google_calendar": ConnectorKind(
        id="google_calendar",
        label="Google Calendar",
        fields=[],
        oauth_managed=True,
    ),
    "granola": ConnectorKind(
        id="granola",
        label="Granola",
        fields=[],
        oauth_managed=True,
    ),
}

KNOWN_KIND_IDS: frozenset[str] = frozenset(CONNECTOR_KINDS)
API_MANAGED_KIND_IDS: frozenset[str] = frozenset(
    k for k, v in CONNECTOR_KINDS.items() if not v.oauth_managed
)
