"""ORM model for the name→email people directory.

``directory_people`` is a cache of company roster entries (synced from Slack)
that lets agents and the post-meeting scheduler map a person's NAME
("Angela", "Julie K", "Greg Shrader") to their EMAIL, instead of failing with
"couldn't map X to a calendar".

Keyed on a lowercased email (unique). The sync job UPSERTs by email so a
person's row is updated in place across runs rather than duplicated.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class DirectoryPerson(Base):
    """A single person in the company directory (one row per email)."""

    __tablename__ = "directory_people"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Stored lowercased; unique + indexed so UPSERT-by-email is cheap.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)

    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    slack_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="slack")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
