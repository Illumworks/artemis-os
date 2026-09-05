"""Catch an agent naming work it could have done, instead of doing it.

Callie ended an Argus briefing with:

    Open flags: Confirm prior Amira relationship in CRM, identify the curriculum
    or instruction lead who'd own the decision alongside Bishop, and check
    whether any competitor has an active contract.

Two of those three are one `check_salesforce_activity` call away. Running it takes
a second and returns 19 contacts on the account, most recently touched three
months earlier, and Roy Bishop himself listed as Asst. Superintendent of
Instruction/Curriculum -- which means the "new superintendent, fresh eyes" angle
the whole briefing was built on describes an internal promotion of someone we
already knew. She had the tool. She wrote the to-do instead.

The "Look It Up Yourself" rule in her profile already forbids this, and it was in
her binding block when she did it. Per the lesson in CLAUDE.md, the answer to a
rule that has already been broken from the binding block is not a louder rule --
it is a gate.

**This is a nudge, not a wall.** It returns a correction the caller hands back for
ONE retry. Blocking the send outright would trade a wrong answer for silence, and
silence is the failure that lost Sara's question in August. If the retry still
cannot answer, the message goes out with an honest reason.

**"I checked and the data does not exist" passes.** Some flags genuinely cannot be
answered -- competitor contracts is the live example, since OpportunityCompetitor
is empty. A gate that cannot accept that answer deadlocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Phrases that introduce a list of things somebody ought to go and do. These are
#: how the deferral actually gets written; a generic "should" or "need to" catches
#: half of ordinary prose and would make the gate useless.
_DEFERRAL_MARKERS = re.compile(
    r"(open flags?|next steps?|to confirm|still to check|outstanding questions?"
    r"|before outreach|someone should|we should (confirm|check|verify|identify)"
    r"|worth (confirming|checking|verifying)|needs? (confirming|checking|verification))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Checkable:
    """A thing an agent might defer, and the tool that would settle it."""

    name: str
    #: Words that indicate this is what is being deferred.
    cues: tuple[str, ...]
    tool: str
    #: Said to the agent when it deferred this instead of calling the tool.
    instruction: str


CHECKABLE: tuple[Checkable, ...] = (
    Checkable(
        name="CRM relationship",
        cues=("crm", "customer status", "prior relationship", "existing customer", "salesforce"),
        tool="check_salesforce_activity",
        instruction=(
            "whether we already know this district -- call check_salesforce_activity, "
            "which returns customer status, every contact on the account and when "
            "each was last touched"
        ),
    ),
    Checkable(
        name="decision makers",
        cues=(
            "decision maker",
            "decision-maker",
            "curriculum lead",
            "instruction lead",
            "who owns the decision",
            "identify the",
            "point of contact",
        ),
        tool="check_salesforce_activity",
        instruction=(
            "who the decision makers are -- check_salesforce_activity lists the "
            "contacts on the account with their titles, so the curriculum and "
            "instruction leads are named rather than guessed at"
        ),
    ),
    Checkable(
        name="pipeline history",
        cues=("opportunity history", "deal history", "pipeline", "past deals", "won or lost"),
        tool="check_salesforce_activity",
        instruction=(
            "the opportunity history -- check_salesforce_activity reports open and "
            "prior opportunities for the account"
        ),
    ),
)


@dataclass(frozen=True)
class UncheckedFlags:
    """Things the message defers that a tool in this turn could have settled."""

    items: tuple[Checkable, ...]

    def correction(self) -> str:
        """The text handed back for one retry. Speaks to the agent, not the reader."""
        lines = [
            "HOLD. Your reply defers work you can do right now, in this turn, with "
            "tools you already have. You listed it as something for a person to go "
            "and check. Do it instead, then rewrite.",
            "",
            "What you deferred, and what settles it:",
        ]
        for item in self.items:
            lines.append(f"  - {item.instruction} [{item.tool}]")
        lines += [
            "",
            "Then send ONE message with the answers folded in, not a list of things to look into.",
            "",
            "If a tool comes back empty or the data genuinely does not exist, that is "
            "a complete answer and you should say so plainly -- 'there is no "
            "competitor contract recorded, the field is empty' is finished work. "
            "What is not acceptable is telling someone to go and check something you "
            "could have checked.",
        ]
        return "\n".join(lines)


def find_unchecked_flags(
    text: str,
    tools_used: list[str] | None,
    *,
    available_tools: set[str] | None = None,
) -> UncheckedFlags | None:
    """Return what the message defers that this turn could have answered.

    None means nothing to correct: no deferral language, or the agent already
    called the tool, or it does not have the tool at all.
    """
    if not text or not _DEFERRAL_MARKERS.search(text):
        return None

    # A tool recorded as "<name>:error" was called and failed. That is not a
    # deferral -- the agent tried, and telling it to try again is noise.
    called = {t.split(":", 1)[0] for t in (tools_used or [])}
    lowered = text.lower()

    found: list[Checkable] = []
    for item in CHECKABLE:
        if item.tool in called:
            continue
        if available_tools is not None and item.tool not in available_tools:
            # Not the agent's fault it cannot answer; it has no such tool.
            continue
        if any(cue in lowered for cue in item.cues):
            found.append(item)

    if not found:
        return None
    # Two cues can point at one tool; report each distinct check once.
    seen: set[str] = set()
    unique: list[Checkable] = []
    for item in found:
        if item.name not in seen:
            seen.add(item.name)
            unique.append(item)
    return UncheckedFlags(items=tuple(unique))
