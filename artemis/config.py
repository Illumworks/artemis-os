"""Runtime configuration loaded from environment.

Single source of truth for all env-driven settings. Imported wherever config is needed.
Never read os.environ directly elsewhere — go through `settings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
        description=(
            "Slack channel ID (e.g. C0B8QE17DGQ) where marketing approval gates post a "
            "review notification, in addition to approver DMs. Empty = no channel post."
        ),
    )
    approval_notify_override: str = Field(
        default="",
        description=(
            "TEST/STAGING: if set to an email, ALL human-gate approval DMs route only to this "
            "person instead of the configured approvers (channel post is unaffected). Empty = "
            "normal routing to the gate's approvers."
        ),
    )

    db_url: str = Field(
        default="postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os",
        description="SQLAlchemy async URL for Postgres.",
    )

    token: str | None = Field(default=None, description="Shared-account auth token; None disables.")

    embedding_provider: str = Field(
        default="minilm",
        description="Embedding backend. Only 'minilm' supported in V1.",
    )

    # M1: lossless memory — archive + backup paths and parameters
    archive_dir: Path = Field(
        default=Path.home() / ".artemis" / "archive",
        description="Root directory for cold-tier raw_inputs archives.",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
