"""Provider credential resolver — DB first, env fallback per field.

Design: fluidity, simplicity, purposefulness, naturalness, spacious, open.

DB value wins when set. Env vars remain valid as a fallback so existing
deployments continue to work without manual DB migration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo


class MissingProviderConfigError(Exception):
    """Raised when required credential fields are absent from both DB and env."""

    def __init__(self, provider: str, missing_fields: list[str]) -> None:
        self.provider = provider
        self.missing_fields = missing_fields
        super().__init__(
            f"Provider {provider!r} is missing required fields: {', '.join(missing_fields)}. "
            "Configure them via /api/integrations/providers/{provider}/config."
        )


@dataclass(frozen=True)
class SlackConfig:
    client_id: str
    client_secret: str
    signing_secret: str


async def resolve_slack_config(session: AsyncSession) -> SlackConfig:
    """Resolve Slack credentials: DB per-field, then env per-field fallback.

    Raises MissingProviderConfigError if any required field is absent from both sources.
    """
    stored = await repo.get_provider_config(session, "slack") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "SLACK_CLIENT_SECRET", ""
    )
    signing_secret = str(stored.get("signing_secret") or "") or os.environ.get(
        "SLACK_SIGNING_SECRET", ""
    )

    missing = [
        name
        for name, val in [
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("signing_secret", signing_secret),
        ]
        if not val
    ]
    if missing:
        raise MissingProviderConfigError("slack", missing)

    return SlackConfig(
        client_id=client_id,
        client_secret=client_secret,
        signing_secret=signing_secret,
    )


@dataclass(frozen=True)
class GCalConfig:
    client_id: str
    client_secret: str


async def resolve_gcal_config(session: AsyncSession) -> GCalConfig:
    """Resolve GCal credentials: DB per-field, then env per-field fallback.

    Raises MissingProviderConfigError if any required field is absent from both sources.
    """
    stored = await repo.get_provider_config(session, "gcal") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "GCAL_CLIENT_SECRET", ""
    )

    missing = [
        name
        for name, val in [
            ("client_id", client_id),
            ("client_secret", client_secret),
        ]
        if not val
    ]
    if missing:
        raise MissingProviderConfigError("gcal", missing)

    return GCalConfig(client_id=client_id, client_secret=client_secret)
