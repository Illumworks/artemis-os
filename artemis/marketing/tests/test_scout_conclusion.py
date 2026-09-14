"""A scout that stops mid-analysis must not be recorded as a success.

Every string below is taken from a real `agent_traces` row, not invented. The
fixtures a test makes up are the ones that pass while production breaks — and
these particular turns all recorded `outcome='success'` at the time.
"""

from __future__ import annotations

import pytest

from artemis.marketing.scout_conclusion import (
    fetched_anything,
    is_inconclusive,
    reason,
    stated_a_result,
    wrote_a_signal,
)

# --- real turns that did NOT conclude (recorded 'success' on 2026-09-14) ---

FEDERAL_FUNDING_STOPPED = (
    "Evaluating the results from Federal Register and Grants.gov:\n\n"
    "**Federal Register findings:**\n- Most results are priority frameworks or "
    "research programs, not active opportunities"
)
BOARD_MINUTES_STOPPED = (
    "The PDF extractor can't access the BoardDocs links directly. Let me analyze "
    "what I received from the board_minutes_fetch results. Looking at the data:"
)
LINKEDIN_STOPPED = (
    "Confirmed. I can emit only **VENDOR_DISSATISFACTION** and "
    "**LEADER_TRANSITION_INTERIM**.\n\nNow I need to fetch posts from watched "
    "profiles. However, I"
)

# --- real turns that DID conclude, several of them at zero ---

EMITTED_TWO = "**Leadership Transition Scout scan complete.**\n\n**Signals emitted: 2 new**"
EMITTED_ZERO = (
    "**Starbridge scan complete: 0 signals emitted.**\n\n**Reason:** Starbridge "
    "API is not yet implemented."
)
EMITTED_ZERO_DEDUPED = (
    "**Procurement Scout scan complete.**\n\n**Signals emitted: 0 new** "
    "(1 deduplicated — signal 3220, Dallas ISD RR-250363, already emitted)."
)

FETCH_TOOLS = ["reason_codes.get_allowlist", "board_minutes.fetch", "pdf_extractor.extract"]
FETCH_AND_WRITE = [*FETCH_TOOLS, "signal_queue.write"]
FETCH_AND_WRITE_UNDERSCORED = ["news_api_search", "signal_queue_write"]


def test_write_is_detected_in_both_tool_name_spellings() -> None:
    """MCP reports ``signal_queue.write``; the claude-code catalogue reports
    ``signal_queue_write``. Matching one spelling would miss half the runs."""
    assert wrote_a_signal(FETCH_AND_WRITE)
    assert wrote_a_signal(FETCH_AND_WRITE_UNDERSCORED)
    assert not wrote_a_signal(FETCH_TOOLS)


def test_the_allowlist_lookup_does_not_count_as_fetching() -> None:
    """It fetches configuration. Counting it would mark every run as having
    pulled data, including ones whose sources never answered."""
    assert not fetched_anything(["reason_codes.get_allowlist"])
    assert not fetched_anything(["territory_config.get_priority_states"])
    assert fetched_anything(FETCH_TOOLS)


@pytest.mark.parametrize("text", [FEDERAL_FUNDING_STOPPED, BOARD_MINUTES_STOPPED, LINKEDIN_STOPPED])
def test_unfinished_turns_are_inconclusive(text: str) -> None:
    assert not stated_a_result(text)
    assert is_inconclusive(FETCH_TOOLS, text)


@pytest.mark.parametrize("text", [EMITTED_TWO, EMITTED_ZERO, EMITTED_ZERO_DEDUPED])
def test_a_stated_count_concludes_the_scan(text: str) -> None:
    assert stated_a_result(text)
    assert not is_inconclusive(FETCH_TOOLS, text)


def test_zero_signals_is_never_a_failure() -> None:
    """The point of the gate is to tell a scan that REACHED zero from one that
    never reached anything. If this ever inverts, the gate starts pushing scouts
    to invent signals, which costs more than the silence it replaced."""
    assert not is_inconclusive(FETCH_TOOLS, EMITTED_ZERO)
    assert not is_inconclusive(FETCH_TOOLS, "No qualifying items this run.")


def test_writing_a_signal_concludes_the_scan_whatever_the_prose_says() -> None:
    """The effect outranks the narration — that is the whole design."""
    assert is_inconclusive(FETCH_TOOLS, BOARD_MINUTES_STOPPED)
    assert not is_inconclusive(FETCH_AND_WRITE, BOARD_MINUTES_STOPPED)


def test_a_run_that_never_fetched_is_not_judged_here() -> None:
    """A dark source or a missing credential is a different problem with a
    different fix; calling it an inconclusive scan would misdirect whoever reads
    the log."""
    assert not is_inconclusive(["reason_codes.get_allowlist"], "The scraper is a STUB.")
    assert not is_inconclusive([], "")


def test_reason_names_the_tools_so_an_operator_can_act() -> None:
    msg = reason(FETCH_TOOLS, FEDERAL_FUNDING_STOPPED)
    assert "board_minutes.fetch" in msg
    assert "Zero signals is a valid answer" in msg


def test_malformed_tools_used_does_not_raise() -> None:
    """``tools_used`` is JSONB and was ``[]`` for every agent for 30+ days once."""
    for junk in (None, "signal_queue.write", {"a": 1}, 7):
        assert not wrote_a_signal(junk)
        assert not is_inconclusive(junk, FEDERAL_FUNDING_STOPPED)
