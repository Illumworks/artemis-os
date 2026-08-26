"""ORM for the target-account universe. See migration 0121 for the key rationale."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

# Imported for its side effect: the district_id ForeignKey below cannot resolve
# unless the `districts` table is registered in the same metadata. Without this
# the first query raises NoReferencedTableError at mapper-configuration time,
# nowhere near the line that looks wrong.
from artemis.marketing.models import District  # noqa: F401


class TargetAccount(Base):
    """One account Josh sells into.

    Natural key is the RAW ``(state, account_name)``. ``normalized_name`` is a
    matching aid and is NOT unique -- two different PA districts normalize to
    "HEMPFIELD" -- and is empty for names made entirely of generic words.
    """

    __tablename__ = "target_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(8), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    marketing_tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_partner: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_customer: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_parent_account: Mapped[bool] = mapped_column(default=True, nullable=False)
    district_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("districts.id", ondelete="SET NULL"), nullable=True
    )
    match_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imported_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
