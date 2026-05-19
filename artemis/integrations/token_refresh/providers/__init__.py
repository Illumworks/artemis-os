"""Per-provider refresher registry."""

from __future__ import annotations

from artemis.integrations.token_refresh.base import TokenRefresher
from artemis.integrations.token_refresh.providers.gcal import GCalTokenRefresher
from artemis.integrations.token_refresh.providers.granola import GranolaTokenRefresher
from artemis.integrations.token_refresh.providers.slack import SlackTokenRefresher

REFRESHERS: dict[str, TokenRefresher] = {
    "granola": GranolaTokenRefresher(),
    "gcal": GCalTokenRefresher(),
    "slack": SlackTokenRefresher(),
}

__all__ = ["REFRESHERS", "GCalTokenRefresher", "GranolaTokenRefresher", "SlackTokenRefresher"]
