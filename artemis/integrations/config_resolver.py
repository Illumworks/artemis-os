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
    # J9b: Jon's personal Slack user ID, used to classify direct-mention events.
    # Stored as SLACK_AUTHED_USER_ID env var or in integration_configs under
    # the "slack" provider key "authed_user_id".  May be empty string when not
    # configured — callers must check before using.
    authed_user_id: str
    # P1-hardening: the set of Slack user IDs permitted to hold a conversation
    # with Artemis over the inbound bridge.  The owner (authed_user_id) is always
    # included when set; extra IDs come from integration_configs["slack"]
    # ["allowed_user_ids"] (list) or the SLACK_ALLOWED_USER_IDS env var
    # (comma-separated).  An EMPTY tuple means fail-closed: no one is allowed and
    # no inbound message is routed into an agent session.
    allowed_user_ids: tuple[str, ...] = ()

    def is_user_allowed(self, user_id: str) -> bool:
        """Return True iff this Slack user may converse with Artemis (fail-closed)."""
        return bool(user_id) and user_id in self.allowed_user_ids


def _parse_allowed_user_ids(raw: object) -> list[str]:
    """Normalize an allowlist from a stored list or a comma-separated string."""
    if isinstance(raw, list | tuple):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


async def resolve_slack_config(session: AsyncSession) -> SlackConfig:
    """Resolve Slack credentials: DB per-field, then env per-field fallback.

    Raises MissingProviderConfigError if any required field is absent from both sources.

    authed_user_id is optional — no error is raised when absent; callers receive "".
    """
    stored = await repo.get_provider_config(session, "slack") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "SLACK_CLIENT_SECRET", ""
    )
    signing_secret = str(stored.get("signing_secret") or "") or os.environ.get(
        "SLACK_SIGNING_SECRET", ""
    )
    # Jon's personal Slack user ID — stored in integration_configs["slack"]["authed_user_id"]
    # or SLACK_AUTHED_USER_ID env var.  Used by J9b mention-type classifier.
    authed_user_id = str(stored.get("authed_user_id") or "") or os.environ.get(
        "SLACK_AUTHED_USER_ID", ""
    )

    # P1-hardening: resolve the inbound-conversation allowlist.  The owner
    # (authed_user_id) is always permitted when known; extra IDs come from the DB
    # config or the SLACK_ALLOWED_USER_IDS env var.  Order-preserving + de-duped.
    extras = _parse_allowed_user_ids(stored.get("allowed_user_ids")) or _parse_allowed_user_ids(
        os.environ.get("SLACK_ALLOWED_USER_IDS", "")
    )
    allowed_ordered: list[str] = []
    for candidate in ([authed_user_id] if authed_user_id else []) + extras:
        if candidate and candidate not in allowed_ordered:
            allowed_ordered.append(candidate)

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
        authed_user_id=authed_user_id,
        allowed_user_ids=tuple(allowed_ordered),
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
    team_members = (
        tuple(str(m) for m in _members_raw if m) if isinstance(_members_raw, list) else ()
    )

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


@dataclass(frozen=True)
class SalesforceConfig:
    client_id: str
    client_secret: str
    login_url: str


async def resolve_salesforce_config(session: AsyncSession) -> SalesforceConfig:
    """Resolve Salesforce OAuth 2.0 Client Credentials: DB per-field, then env fallback.

    Same DB-first/env-fallback shape as resolve_jira_config -- Salesforce's Client
    Credentials grant is a single static server-to-server secret (client_id +
    client_secret + login_url), with no per-user OAuth dance and no redirect_uri,
    so it maps onto IntegrationConfig exactly the way Jira's site_url/email/api_token
    does, not onto the per-connection Integration/encrypted_credentials row gcal
    uses for its per-user refresh tokens (Salesforce Client Credentials issues no
    refresh token at all -- see artemis.integrations.salesforce.client).

    login_url defaults to Salesforce production ("https://login.salesforce.com")
    when unset anywhere; Jon can override to a custom My Domain URL or
    "https://test.salesforce.com" for a sandbox org.

    Raises MissingProviderConfigError if client_id/client_secret are absent from
    both sources -- callers (artemis.marketing.salesforce_suppression) must treat
    that exactly like any other Salesforce-unreachable failure: fail closed, never
    "not a customer".

    The env-var fallback exists only for parity with every other resolve_*_config
    function and for tests. SFDC-1's brief is explicit that credentials must never
    live in plaintext .env -- the supported install path is
    POST /api/integrations/providers/salesforce/config (owner-gated), the same
    mechanism Jira's site_url/email/api_token already use.
    """
    stored = await repo.get_provider_config(session, "salesforce") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("SALESFORCE_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "SALESFORCE_CLIENT_SECRET", ""
    )
    login_url = (
        str(stored.get("login_url") or "")
        or os.environ.get("SALESFORCE_LOGIN_URL", "")
        or "https://login.salesforce.com"
    )

    missing = [
        name
        for name, val in [("client_id", client_id), ("client_secret", client_secret)]
        if not val
    ]
    if missing:
        raise MissingProviderConfigError("salesforce", missing)

    return SalesforceConfig(client_id=client_id, client_secret=client_secret, login_url=login_url)


@dataclass(frozen=True)
class GranolaConfig:
    """Granola OAuth client credentials.

    For local-state mode (desktop app), client_id and client_secret may be
    empty strings — connect_local() does not require them.
    """

    client_id: str
    client_secret: str


async def resolve_granola_config(session: AsyncSession) -> GranolaConfig:
    """Resolve Granola credentials: DB per-field, then env per-field fallback.

    Unlike other providers, missing credentials do NOT raise — Granola works
    without OAuth creds via the local-state path. Callers must check whether
    client_id is populated before starting an OAuth flow.
    """
    stored = await repo.get_provider_config(session, "granola") or {}

    client_id = str(stored.get("client_id") or "") or os.environ.get("GRANOLA_CLIENT_ID", "")
    client_secret = str(stored.get("client_secret") or "") or os.environ.get(
        "GRANOLA_CLIENT_SECRET", ""
    )

    return GranolaConfig(client_id=client_id, client_secret=client_secret)


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
