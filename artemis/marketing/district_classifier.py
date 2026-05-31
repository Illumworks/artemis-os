"""Pure district tier classification helpers.

The sizing path is intentionally deterministic and side-effect free:
given an enrollment value and a band config, return the matching tier.
No session access, no I/O, no model calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class TierBand(Protocol):
    """Structural view of a district tier band row."""

    tier: str
    min_enrollment: int | None
    max_enrollment: int | None
    display_order: int


def classify_tier(enrollment: int | None, bands: Sequence[TierBand]) -> str | None:
    """Return the tier whose inclusive bounds contain ``enrollment``.

    ``None`` enrollment stays unresolved. The supplied bands are expected to
    tile the non-negative range without gaps or overlaps.
    """

    if enrollment is None:
        return None

    for band in sorted(bands, key=lambda band: band.display_order):
        if band.min_enrollment is not None and enrollment < band.min_enrollment:
            continue
        if band.max_enrollment is not None and enrollment > band.max_enrollment:
            continue
        return band.tier
    return None


def is_supported(tier: str | None) -> bool:
    """D4 is the only unsupported tier today; unresolved districts are not supported."""

    return tier is not None and tier != "D4"
