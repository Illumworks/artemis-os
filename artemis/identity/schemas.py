"""Identity API schemas."""

from __future__ import annotations

from pydantic import BaseModel


class CurrentUserRead(BaseModel):
    id: int
    email: str
    name: str | None
    # Server-resolved ownership flag.  Fail-closed: absent/False = non-owner.
    # Frontend uses this to hide owner-only nav sections (Personal Workspace,
    # Dev Projects).  The backend gates are the real protection; this is UX.
    is_owner: bool = False
