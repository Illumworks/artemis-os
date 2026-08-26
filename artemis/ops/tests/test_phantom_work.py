"""Detecting an agent that described work it never started.

Cases are the real ones. On 2026-08-26 Callie told Josh to hold the Prince
George's sequence "until Argus clarifies who's actually in the seat" and never
called dispatch_research — nothing malfunctioned, so no plumbing check could
have caught it. The only signal is the mismatch between what she SAID and what
`agent_traces.tools_used` shows she called.

The precision cases matter as much as the detection ones: a detector that cries
wolf gets the whole health report ignored, and then the next real instance is
invisible again.
"""

from __future__ import annotations

import re

from artemis.ops.phantom_work import _CLAIMS, _excerpt

(_TOOL, _PATTERN) = _CLAIMS[0]


def _matches(body: str) -> bool:
    return _PATTERN.search(body) is not None


def test_the_tool_guarded_is_dispatch_research() -> None:
    assert _TOOL == "dispatch_research"


# ── Must be caught ───────────────────────────────────────────────────────────


def test_the_actual_2026_08_26_sentence_is_caught() -> None:
    assert _matches(
        "Hold that sequence until Argus clarifies who's actually in the seat. "
        "The equity angle holds regardless; the name doesn't."
    )


def test_common_phrasings_of_a_pending_dispatch_are_caught() -> None:
    for body in (
        "We're waiting on Argus for the decision-maker list.",
        "Waiting for Argus to come back on this one.",
        "I'll have Argus look into the procurement timing.",
        "I asked Argus to confirm the superintendent.",
        "Argus is digging into their board minutes now.",
        "Argus will verify who signed the contract.",
    ):
        assert _matches(body), body


# ── Must NOT be caught ───────────────────────────────────────────────────────


def test_merely_naming_argus_about_past_work_is_not_flagged() -> None:
    """Reporting findings is the normal case and must stay quiet.

    Her profile tells her to attribute his work by name, so this phrasing is
    frequent — flagging it would bury the real signal.
    """
    for body in (
        "Here's what Argus dug up on Hillsborough last week.",
        "Argus found three decision-makers; the CAO is the opening.",
        "Per Argus's dossier, the district renewed in March.",
        "Argus posted his findings to the channel.",
    ):
        assert not _matches(body), body


def test_unrelated_marketing_copy_is_not_flagged() -> None:
    for body in (
        "Waiting on the board vote before we send anything.",
        "Hold that sequence until we know who's in the seat.",
        "We should wait for the district's RFP to post.",
    ):
        assert not _matches(body), body


# ── The excerpt ──────────────────────────────────────────────────────────────


def test_the_excerpt_shows_the_claim_in_context() -> None:
    """A finding has to be actionable without opening the database."""
    body = (
        "Prince George's has five signals and the leadership situation is confused. "
        "Hold that sequence until Argus clarifies who's actually in the seat. "
        "The equity angle holds regardless."
    )
    match = _PATTERN.search(body)
    assert match is not None

    excerpt = _excerpt(body, match)
    assert "until Argus clarifies" in excerpt
    assert len(excerpt) < len(body)
    assert "\n" not in excerpt


def test_an_excerpt_at_the_start_is_not_prefixed_with_an_ellipsis() -> None:
    body = "Waiting on Argus before we draft."
    match = _PATTERN.search(body)
    assert match is not None
    assert not _excerpt(body, match).startswith("…")


def test_the_pattern_is_case_insensitive() -> None:
    assert _PATTERN.flags & re.IGNORECASE
    assert _matches("HOLD UNTIL ARGUS CONFIRMS THE NAME")
