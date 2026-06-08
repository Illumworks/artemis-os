"""Identity API schemas."""

from __future__ import annotations

from pydantic import BaseModel


class CurrentUserRead(BaseModel):
    id: int
    email: str
    name: str | None
