"""Internal event bus for Writing Studio draft lifecycle events.

Port of writing-studio-events.js.

Single-process, in-memory asyncio.Queue-based pub/sub.
Events: draft.approved, draft.rejected, draft.revised, draft.generated, draft.edited.

Subscriber failures and persistence failures are swallowed so they can never
interrupt a draft operation.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ── Valid event types ──────────────────────────────────────────────────────────

DRAFT_EVENT_TYPES: frozenset[str] = frozenset(
    [
        "draft.generated",
        "draft.edited",
        "draft.approved",
        "draft.rejected",
        "draft.revised",  # Python adds: maps to Node's draft.regenerated
        "draft.regenerated",
    ]
)

# ── Event payload ──────────────────────────────────────────────────────────────


@dataclass
class DraftEvent:
    """A draft lifecycle event."""

    event_id: str
    type: str
    draft_id: str
    campaign_id: str | None = None
    deliverable_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    approval_id: str | None = None
    status: str | None = None
    source: str = "writing_studio"
    timestamp: float = field(default_factory=lambda: datetime.now(UTC).timestamp())
    extra: dict[str, Any] | None = None


# ── Subscriber registry ────────────────────────────────────────────────────────

SubscriberCallback = Callable[[DraftEvent], Coroutine[Any, Any, None]]

_subscribers: list[SubscriberCallback] = []


def subscribe(callback: SubscriberCallback) -> Callable[[], None]:
    """Subscribe to all draft lifecycle events.

    Returns an unsubscribe function.
    """
    _subscribers.append(callback)

    def _unsubscribe() -> None:
        with contextlib.suppress(ValueError):
            _subscribers.remove(callback)

    return _unsubscribe


def clear_subscribers() -> None:
    """Remove all subscribers — test cleanup only."""
    _subscribers.clear()


# ── Emission ───────────────────────────────────────────────────────────────────


async def publish(
    event_type: str,
    draft_id: str,
    *,
    campaign_id: str | None = None,
    deliverable_id: str | None = None,
    version_id: str | None = None,
    version_number: int | None = None,
    approval_id: str | None = None,
    status: str | None = None,
    extra: dict[str, Any] | None = None,
) -> DraftEvent | None:
    """Emit a draft lifecycle event.

    Unknown event types are logged and dropped (not raised).
    Subscriber failures are isolated — one failing subscriber never
    prevents others from running.

    Returns the built DraftEvent, or None if the type is invalid.
    """
    if event_type not in DRAFT_EVENT_TYPES:
        return None

    event = DraftEvent(
        event_id=str(uuid.uuid4()),
        type=event_type,
        draft_id=draft_id,
        campaign_id=campaign_id,
        deliverable_id=deliverable_id,
        version_id=version_id,
        version_number=version_number,
        approval_id=approval_id,
        status=status,
        extra=extra,
    )

    for sub in list(_subscribers):
        with contextlib.suppress(Exception):
            await sub(event)

    return event
