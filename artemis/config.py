"""Runtime configuration loaded from environment.

Single source of truth for all env-driven settings. Imported wherever config is needed.
Never read os.environ directly elsewhere — go through `settings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ARTEMIS_",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    app_base_url: str = Field(
        default="",
        description="Absolute Artemis app base URL used for Slack deep-links.",
    )
    outbound_send_enabled: bool = Field(
        default=False,
        description="Enable CMP-SEND-2 outbound send surfaces and enqueue-on-approve hook.",
    )
    marketing_campaigns_slack_channel: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_MARKETING_CAMPAIGNS_SLACK_CHANNEL",
            "MARKETING_CAMPAIGNS_SLACK_CHANNEL",
        ),
        description=(
            "Slack channel ID (e.g. C0B8QE17DGQ) where marketing approval gates post a "
            "review notification, in addition to approver DMs. Empty = no channel post."
        ),
    )
    approval_notify_override: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_APPROVAL_NOTIFY_OVERRIDE",
            "APPROVAL_NOTIFY_OVERRIDE",
        ),
        description=(
            "TEST/STAGING: if set to an email, ALL human-gate approval DMs route only to this "
            "person instead of the configured approvers (channel post is unaffected). Empty = "
            "normal routing to the gate's approvers."
        ),
    )

    db_url: str = Field(
        default="postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_os",
        description="SQLAlchemy async URL for Postgres. Use 127.0.0.1, not "
        "localhost: localhost resolves to ::1 first and IPv6 loopback to "
        "Postgres hangs on this machine (4s+ connect stall before IPv4 fallback).",
    )
    db_pool_size: int = Field(
        default=5,
        description="SQLAlchemy pool_size (number of persistent connections). "
        "Keep lean in dev/worktrees; production overrides via ARTEMIS_DB_POOL_SIZE.",
    )
    db_max_overflow: int = Field(
        default=10,
        description="SQLAlchemy max_overflow (burst connections above pool_size). "
        "Keep lean in dev/worktrees; production overrides via ARTEMIS_DB_MAX_OVERFLOW.",
    )
    db_pool_timeout: int = Field(
        default=10,
        description="Seconds to wait for a connection from the pool before raising. "
        "10s is a safe improvement over SQLAlchemy's 30s default.",
    )
    db_pool_recycle: int = Field(
        default=1800,
        description="Seconds after which a connection is recycled to avoid stale "
        "server-side timeouts (30 min is a safe default).",
    )

    token: str | None = Field(default=None, description="Shared-account auth token; None disables.")
    cf_access_enabled: bool = Field(
        default=False,
        description="When true, trust and verify Cloudflare Access JWT headers for identity.",
    )
    cf_access_team_domain: str = Field(
        default="",
        description="Cloudflare Access team domain, e.g. example.cloudflareaccess.com.",
    )
    cf_access_aud: str = Field(
        default="",
        description="Cloudflare Access application audience tag (AUD).",
    )
    google_client_id: str = Field(
        default="",
        description="Google OAuth client id for per-user Docs/Drive access.",
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth client secret for per-user Docs/Drive access.",
    )
    google_redirect_uri: str = Field(
        default="https://app.artemisos.me/api/google/oauth/callback",
        description="Google OAuth redirect URI for the Artemis app.",
    )

    embedding_provider: str = Field(
        default="minilm",
        description="Embedding backend. Only 'minilm' supported in V1.",
    )

    # M1: lossless memory — archive + backup paths and parameters
    archive_dir: Path = Field(
        default=Path.home() / ".artemis" / "archive",
        description="Root directory for cold-tier raw_inputs archives.",
    )
    memory_eval_dir: Path = Field(
        default=Path.home() / ".artemis" / "memory-eval",
        description="Directory for persisted retrieval-eval QA sets and reports.",
    )
    backup_dir: Path = Field(
        default=Path.home() / ".artemis" / "backups",
        description="Directory for nightly pg_dump backups.",
    )
    backup_pg_host: str = Field(default="localhost", description="Postgres host for pg_dump.")
    backup_pg_port: int = Field(default=5432, description="Postgres port for pg_dump.")
    backup_pg_user: str = Field(default="artemis", description="Postgres user for pg_dump.")
    backup_pg_dbname: str = Field(default="artemis_os", description="Database name to back up.")
    backup_pg_bindir: str = Field(
        default="/opt/homebrew/opt/postgresql@17/bin",
        description="Directory containing pg_dump, pg_restore, createdb binaries.",
    )
    backup_retain_days: int = Field(default=30, description="Days to retain pg_dump files.")
    archive_age_days: int = Field(
        default=90, description="Archive raw_inputs rows older than this many days."
    )
    morning_brief_cron: str = Field(
        default="0 8 * * *",
        description="Cron expression for the scheduled Slack morning brief.",
    )
    morning_brief_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the scheduled Slack morning brief.",
    )
    okr_checkin_cron: str = Field(
        default="0 16 * * 5",
        description="Cron expression for the Friday 4pm OKR check-in (default: Fri 16:00).",
    )
    review_escalation_cron: str = Field(
        default="0 17 * * *",
        description=(
            "Cron expression for the daily stale-review escalation sweep "
            "(default: every day at 17:00)."
        ),
    )
    review_escalation_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the stale-review escalation sweep.",
    )
    review_escalation_age_hours: int = Field(
        default=24,
        description="Minimum age in hours before a ready-for-review draft is escalated.",
    )
    commitments_followup_cron: str = Field(
        default="30 9 * * *",
        description="Cron expression for the commitments follow-up sweep.",
    )
    commitments_followup_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the commitments follow-up sweep.",
    )
    commitments_due_soon_hours: int = Field(
        default=48,
        description="How far ahead the follow-up sweep treats commitments as due soon.",
    )
    commitments_renotify_hours: int = Field(
        default=24,
        description="Minimum hours between repeat follow-ups for the same commitment.",
    )
    commitments_default_snooze_hours: int = Field(
        default=48,
        description="Default snooze duration when a follow-up reply omits an explicit window.",
    )
    uvicorn_workers: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "ARTEMIS_UVICORN_WORKERS",
            "UVICORN_WORKERS",
            "WEB_CONCURRENCY",
        ),
        description=(
            "Configured uvicorn worker count. Writing Studio collab is single-process in v1 and "
            "logs a startup warning if this is >1."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
