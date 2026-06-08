"""Pydantic schemas for Writing Studio draft comments."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from artemis.identity.schemas import CurrentUserRead


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


class CommentCreate(_Base):
    body: str
    anchor_start: int | None = Field(default=None, alias="anchorStart", ge=0)
    anchor_end: int | None = Field(default=None, alias="anchorEnd", ge=0)
    anchored_text: str | None = Field(default=None, alias="anchoredText")
    parent_id: int | None = Field(default=None, alias="parentId")
    mentions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_anchor_range(self) -> CommentCreate:
        if not self.body.strip():
            raise ValueError("body must be a non-empty string")
        if (self.anchor_start is None) != (self.anchor_end is None):
            raise ValueError("anchorStart and anchorEnd must both be provided together")
        if (
            self.anchor_start is not None
            and self.anchor_end is not None
            and self.anchor_end < self.anchor_start
        ):
            raise ValueError("anchorEnd must be greater than or equal to anchorStart")
        if self.anchored_text is not None:
            value = self.anchored_text.strip()
            self.anchored_text = value or None
        return self


class CommentUpdate(_Base):
    body: str

    @model_validator(mode="after")
    def _validate_body(self) -> CommentUpdate:
        if not self.body.strip():
            raise ValueError("body must be a non-empty string")
        return self


class CommentRead(_Base):
    id: int
    draft_id: int = Field(alias="draftId")
    author_user_id: int = Field(alias="authorUserId")
    parent_id: int | None = Field(default=None, alias="parentId")
    body: str
    anchor_start: int | None = Field(default=None, alias="anchorStart")
    anchor_end: int | None = Field(default=None, alias="anchorEnd")
    anchored_text: str | None = Field(default=None, alias="anchoredText")
    status: str
    mentions: list[str] = Field(default_factory=list)
    author: CurrentUserRead
    resolved_by_user_id: int | None = Field(default=None, alias="resolvedByUserId")
    resolved_by: CurrentUserRead | None = Field(default=None, alias="resolvedBy")
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    replies: list[CommentRead] = Field(default_factory=list)


CommentRead.model_rebuild()
