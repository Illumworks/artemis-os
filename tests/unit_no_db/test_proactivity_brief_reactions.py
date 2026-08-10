"""Unit tests for artemis/proactivity/brief_reactions.py.

All tests are pure-function unit tests — no DB, no Slack.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from artemis.proactivity.brief_reactions import (
    _slugify,
    make_item_key,
    make_reaction_content,
    parse_reaction_observations,
    weight_priorities,
    weight_waiting_on,
)

# ── _slugify ──────────────────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    assert _slugify("Fix Login Redirect") == "fix-login-redirect"


def test_slugify_removes_punctuation() -> None:
    assert _slugify("MT-456: Fix login!") == "mt-456-fix-login"


def test_slugify_trims_whitespace() -> None:
    assert _slugify("  hello world  ") == "hello-world"


def test_slugify_truncates_at_80() -> None:
    long_text = "a" * 100
    result = _slugify(long_text)
    assert len(result) <= 80


def test_slugify_collapses_runs() -> None:
    assert _slugify("hello   world") == "hello-world"


# ── make_item_key ─────────────────────────────────────────────────────────────


def test_make_item_key_priority() -> None:
    key = make_item_key("priority", "MT-456 Fix login redirect")
    assert key.startswith("priority:")
    assert "mt-456" in key


def test_make_item_key_waiting_on() -> None:
    key = make_item_key("waiting_on", "Alice PR review")
    assert key.startswith("waiting_on:")
    assert "alice-pr-review" in key


def test_make_item_key_deterministic() -> None:
    k1 = make_item_key("priority", "Fix login")
    k2 = make_item_key("priority", "Fix login")
    assert k1 == k2


# ── make_reaction_content ─────────────────────────────────────────────────────


def test_make_reaction_content_engage() -> None:
    content = make_reaction_content("priority", "MT-456 Fix login", "engage")
    assert content.startswith("brief_reaction:")
    assert content.endswith(":engage")


def test_make_reaction_content_mute() -> None:
    content = make_reaction_content("waiting_on", "Alice review", "mute")
    assert content.endswith(":mute")
    assert "waiting_on" in content


def test_make_reaction_content_invalid_item_type() -> None:
    with pytest.raises(ValueError, match="Invalid item_type"):
        make_reaction_content("unknown_type", "label", "engage")


def test_make_reaction_content_invalid_reaction() -> None:
    with pytest.raises(ValueError, match="Invalid reaction"):
        make_reaction_content("priority", "label", "like")


def test_make_reaction_content_all_valid_combinations() -> None:
    for item_type in ("priority", "waiting_on", "okr"):
        for reaction in ("engage", "ignore", "mute"):
            content = make_reaction_content(item_type, "some label", reaction)
            assert content.startswith("brief_reaction:")
            assert content.endswith(f":{reaction}")


# ── parse_reaction_observations ──────────────────────────────────────────────


def test_parse_empty() -> None:
    result = parse_reaction_observations([])
    assert result == {}


def test_parse_single_engage() -> None:
    obs = {"content": "brief_reaction:priority:mt-456-fix-login:engage"}
    result = parse_reaction_observations([obs])
    assert "priority:mt-456-fix-login" in result
    assert result["priority:mt-456-fix-login"] == 1.5


def test_parse_single_ignore() -> None:
    obs = {"content": "brief_reaction:waiting_on:alice-pr-review:ignore"}
    result = parse_reaction_observations([obs])
    assert result["waiting_on:alice-pr-review"] == 0.5


def test_parse_single_mute() -> None:
    obs = {"content": "brief_reaction:priority:big-ticket:mute"}
    result = parse_reaction_observations([obs])
    assert result["priority:big-ticket"] == 0.0


def test_parse_newest_wins() -> None:
    """When the same key appears multiple times, the first (newest) entry wins."""
    observations = [
        {"content": "brief_reaction:priority:some-ticket:engage"},  # newest
        {"content": "brief_reaction:priority:some-ticket:ignore"},  # older
    ]
    result = parse_reaction_observations(observations)
    # newest = "engage" → 1.5
    assert result["priority:some-ticket"] == 1.5


def test_parse_oldest_wins_when_reversed() -> None:
    """Reversed order: oldest is first → oldest wins."""
    observations = [
        {"content": "brief_reaction:priority:some-ticket:ignore"},  # "oldest" here
        {"content": "brief_reaction:priority:some-ticket:engage"},  # ignored (second)
    ]
    result = parse_reaction_observations(observations)
    assert result["priority:some-ticket"] == 0.5


def test_parse_ignores_unrelated_observations() -> None:
    observations = [
        {"content": "brief_exclusion:MT-123"},
        {"content": "brief_reaction:priority:ticket-a:engage"},
        {"content": "pre_meeting_prep_sent:evt1"},
        {"content": "some random text"},
    ]
    result = parse_reaction_observations(observations)
    assert len(result) == 1
    assert "priority:ticket-a" in result


def test_parse_pydantic_style_object() -> None:
    obs = MagicMock()
    obs.content = "brief_reaction:priority:mt-456:mute"
    result = parse_reaction_observations([obs])
    assert result["priority:mt-456"] == 0.0


def test_parse_invalid_reaction_token_skipped() -> None:
    obs = {"content": "brief_reaction:priority:mt-456:unknown_reaction"}
    result = parse_reaction_observations([obs])
    assert result == {}


def test_parse_malformed_no_colon_separator() -> None:
    obs = {"content": "brief_reaction:priority-only"}
    result = parse_reaction_observations([obs])
    assert result == {}


# ── weight_priorities ─────────────────────────────────────────────────────────


def test_weight_priorities_empty_weights() -> None:
    priorities = [
        {"item": "Fix login", "urgency": "high"},
        {"item": "Write docs", "urgency": "low"},
    ]
    result = weight_priorities(priorities, weights={})
    assert result == priorities[:3]


def test_weight_priorities_boosts_engaged_item() -> None:
    from artemis.proactivity.brief_reactions import make_item_key

    priorities = [
        {"item": "Fix login", "urgency": "medium"},
        {"item": "Write docs", "urgency": "medium"},
    ]
    key = make_item_key("priority", "Fix login")
    weights = {key: 0.5}  # de-rank "Fix login"
    result = weight_priorities(priorities, weights=weights)
    # "Write docs" (neutral 1.0) should come before "Fix login" (0.5)
    assert result[0]["item"] == "Write docs"
    assert result[1]["item"] == "Fix login"


def test_weight_priorities_drops_muted_items() -> None:
    from artemis.proactivity.brief_reactions import make_item_key

    priorities = [
        {"item": "Annoying ticket", "urgency": "medium"},
        {"item": "Real work", "urgency": "high"},
    ]
    mute_key = make_item_key("priority", "Annoying ticket")
    weights = {mute_key: 0.0}
    result = weight_priorities(priorities, weights=weights)
    items = [p["item"] for p in result]
    assert "Annoying ticket" not in items
    assert "Real work" in items


def test_weight_priorities_max_3() -> None:
    priorities = [{"item": f"Item {i}", "urgency": "medium"} for i in range(6)]
    result = weight_priorities(priorities, weights={})
    assert len(result) <= 3


# ── weight_waiting_on ─────────────────────────────────────────────────────────


def test_weight_waiting_on_empty_weights() -> None:
    waiting = [
        {"who": "Alice", "context": "PR review"},
        {"who": "Bob", "context": "Budget approval"},
    ]
    result = weight_waiting_on(waiting, weights={})
    assert result == waiting[:8]


def test_weight_waiting_on_drops_muted() -> None:
    from artemis.proactivity.brief_reactions import make_item_key

    waiting = [
        {"who": "Alice", "context": "PR review"},
        {"who": "Bob", "context": "Noisy updates"},
    ]
    mute_key = make_item_key("waiting_on", "Bob")
    weights = {mute_key: 0.0}
    result = weight_waiting_on(waiting, weights=weights)
    whos = [w["who"] for w in result]
    assert "Bob" not in whos
    assert "Alice" in whos


def test_weight_waiting_on_max_8() -> None:
    waiting = [{"who": f"Person {i}", "context": None} for i in range(12)]
    result = weight_waiting_on(waiting, weights={})
    assert len(result) <= 8


# ── Round-trip: make_reaction_content → parse ─────────────────────────────────


def test_roundtrip_engage() -> None:
    content = make_reaction_content("priority", "MT-456 Fix login redirect", "engage")
    weights = parse_reaction_observations([{"content": content}])
    # The key should match what weight_priorities looks up.
    key = make_item_key("priority", "MT-456 Fix login redirect")
    assert weights[key] == 1.5


def test_roundtrip_mute_drops_from_brief() -> None:
    content = make_reaction_content("priority", "Irrelevant ticket", "mute")
    weights = parse_reaction_observations([{"content": content}])
    priorities = [{"item": "Irrelevant ticket", "urgency": "high"}]
    result = weight_priorities(priorities, weights=weights)
    assert result == []
