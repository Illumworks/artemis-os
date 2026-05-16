"""Pydantic 2.x DTOs for Writing Studio rules + scaffolding domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


# ── Writing Profile ───────────────────────────────────────────────────────────


class WritingProfileCreate(_Base):
    name: str
    description: str | None = None
    status: str = "active"
    default_model_provider: str | None = Field(default=None, alias="defaultModelProvider")
    default_model_id: str | None = Field(default=None, alias="defaultModelId")
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class WritingProfileRead(_Base):
    id: int
    name: str
    description: str | None = None
    status: str
    default_model_provider: str | None = Field(default=None, alias="defaultModelProvider")
    default_model_id: str | None = Field(default=None, alias="defaultModelId")
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class WritingProfileUpdate(_Base):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    default_model_provider: str | None = Field(default=None, alias="defaultModelProvider")
    default_model_id: str | None = Field(default=None, alias="defaultModelId")
    system_prompt: str | None = Field(default=None, alias="systemPrompt")


# ── Writing Folder ────────────────────────────────────────────────────────────


class WritingFolderCreate(_Base):
    name: str
    profile_id: int | None = Field(default=None, alias="profileId")
    parent_folder_id: int | None = Field(default=None, alias="parentFolderId")
    description: str | None = None
    campaign_id: str | None = Field(default=None, alias="campaignId")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")
    sync_id: str | None = Field(default=None, alias="syncId")


class WritingFolderRead(_Base):
    id: int
    sync_id: str | None = Field(default=None, alias="syncId")
    profile_id: int | None = Field(default=None, alias="profileId")
    parent_folder_id: int | None = Field(default=None, alias="parentFolderId")
    name: str
    description: str | None = None
    campaign_id: str | None = Field(default=None, alias="campaignId")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class WritingFolderUpdate(_Base):
    name: str | None = None
    description: str | None = None
    parent_folder_id: int | None = Field(default=None, alias="parentFolderId")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")


# ── Writing Rule ──────────────────────────────────────────────────────────────


class WritingRuleCreate(_Base):
    profile_id: int | None = Field(default=None, alias="profileId")
    rule_type: str = Field(default="voice", alias="ruleType")
    title: str
    body: str
    status: str = "active"


class WritingRuleRead(_Base):
    id: int
    profile_id: int | None = Field(default=None, alias="profileId")
    rule_type: str = Field(alias="ruleType")
    title: str
    body: str
    source_candidate_id: int | None = Field(default=None, alias="sourceCandidateId")
    status: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class WritingRuleUpdate(_Base):
    rule_type: str | None = Field(default=None, alias="ruleType")
    title: str | None = None
    body: str | None = None
    status: str | None = None


# ── Writing Example ───────────────────────────────────────────────────────────


class WritingExampleCreate(_Base):
    profile_id: int | None = Field(default=None, alias="profileId")
    title: str
    body: str
    example_type: str = Field(default="reference", alias="exampleType")
    asset_type: str | None = Field(default=None, alias="assetType")
    channel: str | None = None


class WritingExampleRead(_Base):
    id: int
    profile_id: int | None = Field(default=None, alias="profileId")
    title: str
    body: str
    example_type: str = Field(alias="exampleType")
    asset_type: str | None = Field(default=None, alias="assetType")
    channel: str | None = None
    source_candidate_id: int | None = Field(default=None, alias="sourceCandidateId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class WritingExampleUpdate(_Base):
    title: str | None = None
    body: str | None = None
    example_type: str | None = Field(default=None, alias="exampleType")
    asset_type: str | None = Field(default=None, alias="assetType")
    channel: str | None = None


# ── Writing Source ────────────────────────────────────────────────────────────


class WritingSourceCreate(_Base):
    profile_id: int | None = Field(default=None, alias="profileId")
    source_key: str = Field(alias="sourceKey")
    title: str
    source_type: str = Field(default="reference", alias="sourceType")
    file_name: str | None = Field(default=None, alias="fileName")
    original_content: str = Field(alias="originalContent")
    normalized_content: str = Field(alias="normalizedContent")
    content_hash: str | None = Field(default=None, alias="contentHash")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")


class WritingSourceRead(_Base):
    id: int
    profile_id: int | None = Field(default=None, alias="profileId")
    source_key: str = Field(alias="sourceKey")
    title: str
    source_type: str = Field(alias="sourceType")
    file_name: str | None = Field(default=None, alias="fileName")
    original_content: str = Field(alias="originalContent")
    normalized_content: str = Field(alias="normalizedContent")
    content_hash: str | None = Field(default=None, alias="contentHash")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")
    imported_at: datetime = Field(alias="importedAt")
    updated_at: datetime = Field(alias="updatedAt")


class WritingSourceUpdate(_Base):
    title: str | None = None
    source_type: str | None = Field(default=None, alias="sourceType")
    file_name: str | None = Field(default=None, alias="fileName")
    original_content: str | None = Field(default=None, alias="originalContent")
    normalized_content: str | None = Field(default=None, alias="normalizedContent")
    content_hash: str | None = Field(default=None, alias="contentHash")
    metadata_json: dict[str, Any] | None = Field(default=None, alias="metadataJson")


# ── Migration / dry-run DTOs ──────────────────────────────────────────────────


class WritingProfileRow(_Base):
    """Validates a raw SQLite row for writing_profiles before migration."""

    id: int
    name: str
    description: str | None = None
    status: str = "active"
    default_model_provider: str | None = None
    default_model_id: str | None = None
    system_prompt: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class WritingFolderRow(_Base):
    """Validates a raw SQLite row for writing_folders before migration."""

    id: int
    sync_id: str | None = None
    profile_id: int | None = None
    parent_folder_id: int | None = None
    name: str
    description: str | None = None
    campaign_id: str | None = None
    metadata_json: str | None = None  # JSON-in-TEXT in source
    created_at: int | None = None
    updated_at: int | None = None


class WritingRuleRow(_Base):
    """Validates a raw SQLite row for writing_rules before migration."""

    id: int
    profile_id: int | None = None
    rule_type: str = "voice"
    title: str
    body: str
    source_candidate_id: int | None = None
    status: str = "active"
    created_at: int | None = None
    updated_at: int | None = None


class WritingExampleRow(_Base):
    """Validates a raw SQLite row for writing_examples before migration."""

    id: int
    profile_id: int | None = None
    title: str
    body: str
    example_type: str = "reference"
    asset_type: str | None = None
    channel: str | None = None
    source_candidate_id: int | None = None
    created_at: int | None = None
    updated_at: int | None = None


class WritingSourceRow(_Base):
    """Validates a raw SQLite row for writing_sources before migration."""

    id: int
    profile_id: int | None = None
    source_key: str
    title: str
    source_type: str = "reference"
    file_name: str | None = None
    original_content: str
    normalized_content: str
    content_hash: str | None = None
    metadata_json: str | None = None  # JSON-in-TEXT in source
    imported_at: int | None = None
    updated_at: int | None = None
