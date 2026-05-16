"""Phase C4 tests — events pub/sub, dispatcher, shutdown.

Tests:
  - publish() returns DraftEvent on valid type
  - publish() returns None on invalid type
  - subscriber is called when event published
  - subscriber failure does not propagate
  - clear_subscribers() removes all
  - multiple subscribers all called
  - pub/sub round-trip (event data reaches subscriber)
  - subscribe returns unsubscribe fn; calling it removes subscriber
  - DRAFT_EVENT_TYPES contains expected entries
"""

from __future__ import annotations

from artemis.marketing.writing_studio.events import (
    DRAFT_EVENT_TYPES,
    DraftEvent,
    clear_subscribers,
    publish,
    subscribe,
)


class TestDraftEventTypes:
    def test_approved_in_event_types(self) -> None:
        assert "draft.approved" in DRAFT_EVENT_TYPES

    def test_rejected_in_event_types(self) -> None:
        assert "draft.rejected" in DRAFT_EVENT_TYPES

    def test_generated_in_event_types(self) -> None:
        assert "draft.generated" in DRAFT_EVENT_TYPES

    def test_revised_in_event_types(self) -> None:
        assert "draft.revised" in DRAFT_EVENT_TYPES

    def test_edited_in_event_types(self) -> None:
        assert "draft.edited" in DRAFT_EVENT_TYPES


class TestPublish:
    async def test_publish_valid_type_returns_draft_event(self) -> None:
        clear_subscribers()
        result = await publish("draft.approved", draft_id="d-1")
        assert isinstance(result, DraftEvent)
        assert result.type == "draft.approved"
        assert result.draft_id == "d-1"

    async def test_publish_invalid_type_returns_none(self) -> None:
        clear_subscribers()
        result = await publish("draft.nonexistent", draft_id="d-1")
        assert result is None

    async def test_publish_sets_event_id(self) -> None:
        clear_subscribers()
        result = await publish("draft.generated", draft_id="d-2")
        assert result is not None
        assert len(result.event_id) > 0

    async def test_publish_passes_campaign_id(self) -> None:
        clear_subscribers()
        result = await publish("draft.approved", draft_id="d-1", campaign_id="c-42")
        assert result is not None
        assert result.campaign_id == "c-42"

    async def test_publish_passes_deliverable_id(self) -> None:
        clear_subscribers()
        result = await publish("draft.approved", draft_id="d-1", deliverable_id="99")
        assert result is not None
        assert result.deliverable_id == "99"

    async def test_publish_subscriber_called(self) -> None:
        clear_subscribers()
        received: list[DraftEvent] = []

        async def cb(event: DraftEvent) -> None:
            received.append(event)

        unsubscribe = subscribe(cb)
        try:
            await publish("draft.generated", draft_id="d-10")
            assert len(received) == 1
            assert received[0].type == "draft.generated"
        finally:
            unsubscribe()
            clear_subscribers()

    async def test_publish_subscriber_failure_does_not_propagate(self) -> None:
        clear_subscribers()

        async def bad_cb(event: DraftEvent) -> None:
            raise RuntimeError("Subscriber failure")

        unsubscribe = subscribe(bad_cb)
        try:
            # Must not raise
            result = await publish("draft.approved", draft_id="d-fail")
            assert result is not None
        finally:
            unsubscribe()
            clear_subscribers()

    async def test_multiple_subscribers_all_called(self) -> None:
        clear_subscribers()
        calls: list[str] = []

        async def cb1(event: DraftEvent) -> None:
            calls.append("cb1")

        async def cb2(event: DraftEvent) -> None:
            calls.append("cb2")

        u1 = subscribe(cb1)
        u2 = subscribe(cb2)
        try:
            await publish("draft.edited", draft_id="d-multi")
            assert "cb1" in calls
            assert "cb2" in calls
        finally:
            u1()
            u2()
            clear_subscribers()

    async def test_unsubscribe_removes_subscriber(self) -> None:
        clear_subscribers()
        calls: list[int] = []

        async def cb(event: DraftEvent) -> None:
            calls.append(1)

        unsubscribe = subscribe(cb)
        await publish("draft.approved", draft_id="d-1")
        unsubscribe()
        await publish("draft.approved", draft_id="d-2")
        assert len(calls) == 1  # Only first event received

    async def test_clear_subscribers_stops_all(self) -> None:
        calls: list[int] = []

        async def cb(event: DraftEvent) -> None:
            calls.append(1)

        subscribe(cb)
        clear_subscribers()
        await publish("draft.approved", draft_id="d-1")
        assert len(calls) == 0

    async def test_pub_sub_round_trip_data(self) -> None:
        clear_subscribers()
        received: list[DraftEvent] = []

        async def cb(event: DraftEvent) -> None:
            received.append(event)

        unsubscribe = subscribe(cb)
        try:
            await publish(
                "draft.approved",
                draft_id="round-trip-1",
                campaign_id="camp-99",
                deliverable_id="del-7",
                approval_id="app-3",
                status="approved",
            )
            assert len(received) == 1
            e = received[0]
            assert e.draft_id == "round-trip-1"
            assert e.campaign_id == "camp-99"
            assert e.deliverable_id == "del-7"
            assert e.approval_id == "app-3"
            assert e.status == "approved"
        finally:
            unsubscribe()
            clear_subscribers()
