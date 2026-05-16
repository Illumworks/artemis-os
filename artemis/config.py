"""Runtime configuration loaded from environment.

Single source of truth for all env-driven settings. Imported wherever config is needed.
Never read os.environ directly elsewhere — go through `settings`.
"""

from functools import lru_cache
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

    db_url: str = Field(
        default="postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os",
        description="SQLAlchemy async URL for Postgres.",
    )

    token: str | None = Field(default=None, description="Shared-account auth token; None disables.")

    embedding_provider: str = Field(
        default="minilm",
        description="Embedding backend. Only 'minilm' supported in V1.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
