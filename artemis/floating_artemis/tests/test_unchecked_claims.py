"""Catching an agent that names work instead of doing it.

Callie ended an Argus briefing with "Open flags: Confirm prior Amira relationship
in CRM, identify the curriculum or instruction lead who'd own the decision
alongside Bishop, and check whether any competitor has an active contract."

Two of those three are one check_salesforce_activity call away, and the answer
changes the pitch: 19 contacts on the account, and Roy Bishop himself listed as
Asst. Superintendent of Instruction/Curriculum, so the "new superintendent, fresh
eyes" angle describes an internal promotion of someone we already knew.

The "Look It Up Yourself" rule was in her binding block when she wrote it, which
is why this is a gate and not a fourth rule.
"""

from __future__ import annotations

from artemis.floating_artemis.unchecked_claims import find_unchecked_flags

REAL_MESSAGE = (
    "Open flags: Confirm prior Amira relationship in CRM, identify the curriculum "
    "or instruction lead who'd own the decision alongside Bishop, and check "
    "whether any competitor has an active contract. Credit to Argus."
)
ALL_TOOLS = {"check_salesforce_activity", "read_web_page", "dispatch_research"}


def test_the_message_that_prompted_this_is_caught() -> None:
    flags = find_unchecked_flags(
        REAL_MESSAGE, ["ToolSearch", "dispatch_research"], available_tools=ALL_TOOLS
    )

    assert flags is not None
    assert {i.name for i in flags.items} == {"CRM relationship", "decision makers"}


def test_calling_the_tool_satisfies_the_gate() -> None:
    """The point is the work, not the wording."""
    assert (
        find_unchecked_flags(REAL_MESSAGE, ["check_salesforce_activity"], available_tools=ALL_TOOLS)
        is None
    )


def test_a_tool_that_failed_counts_as_tried() -> None:
    """`<name>:error` means it was called and failed. Telling it to retry is noise."""
    assert (
        find_unchecked_flags(
            REAL_MESSAGE, ["check_salesforce_activity:error"], available_tools=ALL_TOOLS
        )
        is None
    )


def test_an_agent_without_the_tool_is_not_nagged() -> None:
    """Kai has no Salesforce access; deferring is the correct behaviour for him."""
    assert find_unchecked_flags(REAL_MESSAGE, [], available_tools={"read_web_page"}) is None


def test_reporting_the_answer_is_not_a_deferral() -> None:
    """The same nouns appear when the work HAS been done. It must stay quiet."""
    answered = (
        "The CRM shows 19 contacts on Grosse Pointe, last touched 2026-06-02, and "
        "Roy Bishop is listed as Asst. Superintendent of Instruction/Curriculum."
    )
    assert find_unchecked_flags(answered, [], available_tools=ALL_TOOLS) is None


def test_ordinary_prose_does_not_trip_it() -> None:
    """A gate that fires on half of normal writing gets ignored or torn out."""
    for text in (
        "We should send this Tuesday.",
        "I need to think about the sequencing.",
        "The pipeline looks healthy this quarter.",
        "Josh should see this before it goes out.",
    ):
        assert find_unchecked_flags(text, [], available_tools=ALL_TOOLS) is None, text


def test_the_correction_tells_it_to_do_the_work_not_to_apologise() -> None:
    flags = find_unchecked_flags(REAL_MESSAGE, [], available_tools=ALL_TOOLS)
    assert flags is not None

    correction = flags.correction()
    assert "check_salesforce_activity" in correction
    assert "ONE message" in correction


def test_the_correction_accepts_an_empty_result_as_a_finished_answer() -> None:
    """Otherwise it deadlocks on flags that genuinely cannot be answered.

    Competitor contracts is the live case: OpportunityCompetitor is empty.
    """
    flags = find_unchecked_flags(REAL_MESSAGE, [], available_tools=ALL_TOOLS)
    assert flags is not None

    correction = flags.correction().lower()
    assert "does not exist" in correction
    assert "complete answer" in correction


def test_each_check_is_named_once_even_when_several_cues_hit() -> None:
    text = "Next steps: confirm customer status in the CRM and check Salesforce for prior relationship."
    flags = find_unchecked_flags(text, [], available_tools=ALL_TOOLS)

    assert flags is not None
    assert len({i.name for i in flags.items}) == len(flags.items)


def test_the_gate_cannot_recurse() -> None:
    """One retry, never a loop. A second failure sends whatever it produced."""
    import inspect

    from artemis.floating_artemis.chat import handle_turn

    params = inspect.signature(handle_turn).parameters
    assert "_retry_done" in params
    assert params["_retry_done"].default is False

    src = inspect.getsource(handle_turn)
    assert "not _retry_done" in src, "the retry must be guarded by the flag"
    assert "_retry_done=True" in src, "the retry must set the flag"
