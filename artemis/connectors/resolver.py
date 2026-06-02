"""Runtime credential resolver.

Used by agent executors and source adapters to obtain credentials at
runtime without knowing the storage details.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.connectors.models import AgentConnector, Connector
from artemis.connectors.repository import get_decrypted_credentials


class ConnectorNotConfigured(Exception):  # noqa: N818 — name prescribed by spec
    """Raised when no active connector is linked for the requested namespace."""

    def __init__(self, agent_id: int, tool_namespace: str) -> None:
        self.agent_id = agent_id
        self.tool_namespace = tool_namespace
        super().__init__(
            f"No connector configured for agent {agent_id!r} "
            f"with tool namespace {tool_namespace!r}. "
            "Link a connector via Settings → Integrations → API Connectors."
        )


async def get_credentials_for_tool(
    session: AsyncSession,
    agent_id: int,
    tool_namespace: str,
) -> dict[str, str]:
    """Return decrypted credentials for the connector linked to this agent + namespace.

    Raises ConnectorNotConfigured if no active connector is linked.
    """
    # Find the agent_connectors row
    link = (
        await session.execute(
            select(AgentConnector).where(
                AgentConnector.agent_id == agent_id,
                AgentConnector.tool_namespace == tool_namespace,
            )
        )
    ).scalar_one_or_none()

    if link is None:
        raise ConnectorNotConfigured(agent_id, tool_namespace)

    # Fetch the connector itself
    connector = (
        await session.execute(
            select(Connector).where(
                Connector.id == link.connector_id,
                Connector.status == "active",
            )
        )
    ).scalar_one_or_none()

    if connector is None:
        raise ConnectorNotConfigured(agent_id, tool_namespace)

    return get_decrypted_credentials(connector)
