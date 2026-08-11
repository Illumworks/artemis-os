"""Pydantic model for a parsed crisis-content review card.

See ``docs/crisis-content-approval-pipeline.md`` for the design this
implements, and ``artemis/crisis_content/parser.py`` for the code that
produces ``ReviewCard`` instances from the doc's HTML export.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# What a status value classifies as, for slice B's benefit (routing/dedup).
# The parser never uses this to filter or drop a value -- every raw status
# string is still carried through on the card untouched.
StatusClassification = Literal["actionable", "terminal", "unknown"]


class ReviewCard(BaseModel):
    """One parsed review card from the crisis-comms content-approval doc.

    Pure data produced by ``artemis.crisis_content.parser``. A card has no
    awareness of network or database concerns.

    Identity is intentionally split into two independent parts (see
    ``docs/crisis-content-approval-pipeline.md`` "Card identity"):

    - ``identity_key`` -- ``(normalized_header, platform, ordinal)``. Cheap
      and human-legible, but breaks the moment Jen replaces an
      ``August XX`` placeholder with a real date.
    - ``copy_hash`` -- sha256 of the normalized copy body. Survives a header
      rewrite, so a later slice can recognize "same post, new date" and
      avoid re-notifying on a card that was already actioned.
    """

    model_config = ConfigDict(frozen=True)

    header: str
    date_text: str | None
    title: str
    platform: str | None
    asset_status: str | None
    copy_status: str | None
    asset_url: str | None
    copy_body: str
    identity_key: tuple[str, str | None, int]
    copy_hash: str
