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


@dataclass(frozen=True)
class JiraConfig:
    site_url: str
    email: str
    api_token: str
    project_key: str
    max_items_per_column: int
    team_members: tuple[str, ...]


async def resolve_jira_config(session: AsyncSession) -> JiraConfig:
    """Resolve Jira credentials: DB per-field, then env per-field fallback.

    Raises MissingProviderConfigError if any required field is absent from both sources.
    """
    stored = await repo.get_provider_config(session, "jira") or {}

    site_url = str(stored.get("site_url") or "") or os.environ.get("JIRA_SITE_URL", "")
    email = str(stored.get("email") or "") or os.environ.get("JIRA_EMAIL", "")
    api_token = str(stored.get("api_token") or "") or os.environ.get("JIRA_API_TOKEN", "")
    project_key = str(stored.get("project_key") or "") or os.environ.get("JIRA_PROJECT_KEY", "")
    _max_raw = stored.get("max_items_per_column")
    max_items = int(_max_raw) if isinstance(_max_raw, (int, float, str)) else 20
    _members_raw = stored.get("team_members")
    team_members = tuple(str(m) for m in _members_raw if m) if isinstance(_members_raw, list) else ()

    missing = [
        name
        for name, val in [("site_url", site_url), ("email", email), ("api_token", api_token)]
        if not val
    ]
    if missing:
        raise MissingProviderConfigError("jira", missing)

    return JiraConfig(
        site_url=site_url,
        email=email,
        api_token=api_token,
        project_key=project_key,
        max_items_per_column=max_items,
        team_members=team_members,
    )


# ── LLM provider resolvers (DB-first, env fallback) ──────────────────────────


@dataclass(frozen=True)
class AnthropicConfig:
    api_key: str


async def resolve_anthropic_config(session: AsyncSession) -> AnthropicConfig:
    """Resolve Anthropic API key: DB first, then ANTHROPIC_API_KEY env var.

    Returns AnthropicConfig with the key.  Raises MissingProviderConfigError
    when absent from both sources.
    """
    stored = await repo.get_provider_config(session, "anthropic") or {}

    api_key = str(stored.get("api_key") or "") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        raise MissingProviderConfigError("anthropic", ["api_key"])

    return AnthropicConfig(api_key=api_key)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str


async def resolve_openai_config(session: AsyncSession) -> OpenAIConfig:
    """Resolve OpenAI API key: DB first, then OPENAI_API_KEY env var.

    Returns OpenAIConfig with the key.  Raises MissingProviderConfigError
    when absent from both sources.
    """
    stored = await repo.get_provider_config(session, "openai") or {}

    api_key = str(stored.get("api_key") or "") or os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        raise MissingProviderConfigError("openai", ["api_key"])

    return OpenAIConfig(api_key=api_key)


@dataclass(frozen=True)
class GeminiProviderConfig:
    api_key: str


async def resolve_gemini_config(session: AsyncSession) -> GeminiProviderConfig:
    """Resolve Gemini API key: DB first, then GEMINI_API_KEY env var.

    Returns GeminiProviderConfig with the key.  Raises MissingProviderConfigError
    when absent from both sources.
    """
    stored = await repo.get_provider_config(session, "gemini") or {}

    api_key = str(stored.get("api_key") or "") or os.environ.get("GEMINI_API_KEY", "")

    if not api_key:
        raise MissingProviderConfigError("gemini", ["api_key"])

    return GeminiProviderConfig(api_key=api_key)
