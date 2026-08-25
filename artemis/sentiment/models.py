"""ORM model for the Brand Signals corpus.

One table, ``brand_signal_findings``: one row per distinct story we have ever
seen, kept forever.

Why this exists
---------------
The first version of Brand Signals held no state at all. Every run re-scanned
the same 120-day window from scratch, so each morning's brief re-listed the
same ~30 stories with counts wobbling a point or two because Google's feed
returns slightly different results per call. A daily brief that mostly repeats
yesterday is one nobody reads by Thursday, and nothing was accumulating for the
market-strategy corpus this feed was also meant to build.

Two consequences of persisting:

* ``reported_at`` is the "already briefed" marker, so a brief can say *what is
  new since yesterday* rather than re-reading the whole window. Set only after
  Slack has actually accepted the post -- a failed post leaves rows unreported
  so the next run picks them up rather than losing them.
* The corpus outlives the 120-day query window. Rows are never deleted; a story
  that ages out of the feed stays here.

Dedup key
---------
``content_hash`` is the normalized TITLE, not the link. Google News RSS links
are opaque redirect blobs, and if Google regenerates them the same story would
land as a new row every morning -- exactly the repetition this table exists to
stop. Titles are stable, and hashing the title also collapses the same story
arriving under several different state queries, which the previous version
handled with a per-run ``seen`` set that did not survive the process.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

BRAND_SIGNAL_TABLES: tuple[str, ...] = ("brand_signal_findings",)

LANE_VENDOR = "vendor"
LANE_CATEGORY = "category"

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def content_hash_for(title: str) -> str:
    """Stable dedup key for a story, from its title alone.

    Normalization is deliberately aggressive (case, punctuation, whitespace) so
    that the same headline reproduced with a different dash or quote style is
    one story, not two.
    """
    normalized = _WS.sub(" ", _PUNCT.sub(" ", (title or "").lower())).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class BrandSignalFinding(Base):
    """One distinct story in the Brand Signals corpus."""

    __tablename__ = "brand_signal_findings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # UNIQUE — the dedup key. See the module docstring for why it is the title.
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False)

    # vendor | category
    lane: Mapped[str] = mapped_column(Text, nullable=False, server_default=LANE_CATEGORY)
    # Two-letter state, or 'US' for national/unattributable. Never guessed —
    # see artemis.screentime.gazetteer for why abstaining beats guessing.
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="US")
    themes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    names_amira: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped every time the story comes back in a scan. Distinguishes "still
    # being written about" from "seen once in June".
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = not yet in any brief. Set only after Slack accepts the post.
    reported_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        # The two hot queries: "what have I not reported yet" and the
        # per-state/window rollup for the standing picture.
        Index("ix_brand_signal_findings_unreported", "reported_at"),
        Index("ix_brand_signal_findings_published", "published_at"),
        Index("ix_brand_signal_findings_state", "state"),
    )
