"""Base class and raw item type for scout source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawItem:
    """A single raw item fetched by a scout source adapter."""

    content: str
    source_url: str | None = None
    source_title: str | None = None
    source_published_at: str | None = None  # ISO date YYYY-MM-DD
    metadata: dict[str, Any] = field(default_factory=dict)


class ScoutSourceAdapter(ABC):
    """ABC for all scout source adapters. One per scout slug."""

    @abstractmethod
    def fetch(
        self, territory_config: dict[str, Any] | None, last_run_at: datetime | None
    ) -> list[RawItem]: ...
