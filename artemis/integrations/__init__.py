"""Integration surfaces — Slack, Google Cal, Gmail, Jira, Granola, etc.

IntegrationProvider is the base ABC all J* providers implement. Each provider
module lives in its own sub-package (slack/, gcal/, gmail/, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from artemis.integrations.models import Integration


class IntegrationProvider(ABC):
    """Contract for OAuth-based integration providers."""

    @abstractmethod
    async def connect(self, code: str) -> Integration:
        """Exchange an OAuth code for credentials; store and return the Integration row."""
        ...

    @abstractmethod
    async def verify(self, integration: Integration) -> bool:
        """Ping the provider API to confirm the credential is still valid."""
        ...

    @abstractmethod
    async def revoke(self, integration: Integration) -> None:
        """Revoke the credential at the provider and mark status='revoked' locally."""
        ...
