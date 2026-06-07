"""Pydantic DTOs for the Writing Studio tag registry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


class TagValueCreate(_Base):
    dimension_key: str = Field(alias="dimensionKey")
    value: str
    label: str
    parent_value: str | None = Field(default=None, alias="parentValue")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TagValueUpdate(_Base):
    label: str | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, alias="sortOrder")
    metadata: dict[str, Any] | None = None


class TagValueRead(_Base):
    id: int
    dimension_key: str = Field(alias="dimensionKey")
    value: str
    label: str
    parent_value: str | None = Field(default=None, alias="parentValue")
    active: bool
    sort_order: int = Field(alias="sortOrder")
    metadata: dict[str, Any]
    created_at: datetime = Field(alias="createdAt")
    children: list[TagValueRead] = Field(default_factory=list)


class TagDimensionCreate(_Base):
    key: str
    label: str


class TagDimensionUpdate(_Base):
    label: str | None = None
    active: bool | None = None


class TagDimensionRead(_Base):
    id: int
    key: str
    label: str
    active: bool
    sort_order: int = Field(alias="sortOrder")
    created_at: datetime = Field(alias="createdAt")
    values: list[TagValueRead] = Field(default_factory=list)


class TagRegistryRead(_Base):
    dimensions: list[TagDimensionRead]


TagValueRead.model_rebuild()
