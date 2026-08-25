"""Regression tests for the drop that started this.

On 2026-08-25 Josh posted a TSV to the demand-gen channel with no caption.
`route_inbound` required non-empty `text`, so the event was discarded as
malformed before Callie was ever invoked. She then answered his follow-up
question about a file she had never been handed, and the only trace was a single
`missing required fields` warning at a level nobody watches.

`is_routable_event` is the guard, pulled out as a pure predicate so the rule can
be pinned without standing up a Slack event, a session and a model turn.
"""

from __future__ import annotations

from artemis.routes.integrations_slack_events import is_routable_event

_SLACK_FILE = [{"id": "F123", "name": "leads.tsv", "mimetype": "text/plain"}]


def test_caption_less_upload_is_routable() -> None:
    """THE regression: a file with empty text must survive the guard."""
    assert is_routable_event(
        team_id="T123", channel_id="C0BPX9Y8WBE", text="", files=_SLACK_FILE
    )


def test_text_with_no_files_is_routable() -> None:
    assert is_routable_event(team_id="T123", channel_id="C1", text="hey callie", files=[])


def test_text_and_files_together_are_routable() -> None:
    assert is_routable_event(
        team_id="T123", channel_id="C1", text="what do you make of this?", files=_SLACK_FILE
    )


def test_genuinely_empty_event_is_still_dropped() -> None:
    """The guard must keep rejecting events with nothing in them."""
    assert not is_routable_event(team_id="T123", channel_id="C1", text="", files=[])
    assert not is_routable_event(team_id="T123", channel_id="C1", text="", files=None)


def test_missing_routing_identifiers_are_still_fatal() -> None:
    """Without a team or channel there is no session and nowhere to reply."""
    assert not is_routable_event(team_id="", channel_id="C1", text="hi", files=_SLACK_FILE)
    assert not is_routable_event(team_id="T1", channel_id="", text="hi", files=_SLACK_FILE)
