"""Did a scout run actually finish, or did it just stop talking?

**The failure this exists for.** On 2026-09-14 the `federal_funding` scout called
its fetch tools, began "Evaluating the results from Federal Register and
Grants.gov:", listed what it had found — and ended there. It never called
`signal_queue.write`. The run was recorded `outcome='success'`.

That is not rare. Across **753 scout turns in the 30 days to 2026-09-14, 161
(21%) ended the same way**: fetched something, wrote nothing, and never said what
the answer was. Every single one recorded `success`. Concentrated in
`board_minutes` (45), `federal_funding` (33), `linkedin_observer` (29) and
`state_doe` (26) — which is most of why those scouts look near-dead.

**It is not a truncation and not a provider.** Output tokens on those turns run to
11k against a 22k ceiling seen elsewhere, so nothing was cut off, and the rate is
21% on claude-code against 24% on the local model. The turn simply ends
mid-thought and the loop accepts it as the final answer.

**Why this is a code gate and not another line of prompt.** The scout instruction
in `builders/executor.py` already says, in capitals, to call `signal_queue.write`
for each qualifying signal and to state so explicitly when nothing qualifies. The
behaviour recurs anyway, and CLAUDE.md is explicit about what follows: when a rule
in the binding block is not holding, the next fix is a gate in code, not a louder
rule. So this module does not ask the agent for anything. It reads what the run
DID and refuses to call an unfinished run a success.

**Zero signals is not a failure** — it is the common, correct answer, and this
module must never push a scout toward inventing one. What it distinguishes is a
scan that reached zero from a scan that never reached anything.
"""

from __future__ import annotations

import re
from typing import Any

#: A signal actually reaching the queue. Tool names arrive dotted from the MCP
#: server (``signal_queue.write``) and underscored from the claude-code
#: catalogue (``signal_queue_write``), so match on the parts.
_WRITE = re.compile(r"signal_queue[._]write", re.I)

#: A tool that pulls data in. The allowlist lookup is excluded deliberately: it
#: fetches configuration, so counting it would mark every run as having fetched.
_FETCH = re.compile(r"(search|fetch|get_bill|extract)", re.I)
_NOT_FETCH = re.compile(r"allowlist|priority_states|watch_keywords", re.I)

#: The run stating its own answer. Either a count ("Signals emitted: 0 new") or
#: an unambiguous nothing-qualified sentence. Both forms appear in the live
#: turns that DID conclude; the patterns were validated against all 753.
_STATED_COUNT = re.compile(
    r"(?:signals?\s+(?:emitted|written|created|added)\D{0,12}(\d+))"
    r"|(?:emitted\D{0,12}(\d+)\s+signals?)"
    r"|(?:\b(\d+)\s+(?:new\s+)?signals?\s+(?:emitted|written|created|added))",
    re.I,
)
_STATED_NONE = re.compile(
    r"(no|none|zero|nothing)\s+(?:\w+\s+){0,3}"
    r"(qualif\w+|signals?|items?|results?|matches)\b[^.]{0,60}"
    r"|(?:nothing|none)\s+(?:qualified|to\s+emit|met\s+the)",
    re.I,
)


def _names(tools_used: Any) -> list[str]:
    if not isinstance(tools_used, list):
        return []
    return [str(t) for t in tools_used]


def wrote_a_signal(tools_used: Any) -> bool:
    """Whether `signal_queue.write` was actually called (success or error)."""
    return any(_WRITE.search(n) for n in _names(tools_used))


def fetched_anything(tools_used: Any) -> bool:
    """Whether the run pulled in any source data at all."""
    return any(_FETCH.search(n) and not _NOT_FETCH.search(n) for n in _names(tools_used))


def stated_a_result(output_summary: str | None) -> bool:
    """Whether the run's own final message says what the answer was."""
    text = (output_summary or "").strip()
    if not text:
        return False
    return bool(_STATED_COUNT.search(text) or _STATED_NONE.search(text))


def is_inconclusive(tools_used: Any, output_summary: str | None) -> bool:
    """True when the run pulled data in and then neither wrote nor concluded.

    Deliberately narrow. A run that wrote a signal concluded by definition. A run
    that stated its count — including zero — concluded. A run that never fetched
    anything has a different problem (a dark source, a missing credential) and is
    not this module's to judge.
    """
    if not fetched_anything(tools_used):
        return False
    return not wrote_a_signal(tools_used) and not stated_a_result(output_summary)


def reason(tools_used: Any, output_summary: str | None) -> str:
    """A line an operator can act on, for the scout_runs row and the log."""
    called = ", ".join(_names(tools_used)) or "(none recorded)"
    return (
        "scan did not conclude: fetch tools returned data but the run neither "
        "called signal_queue.write nor stated a result. Zero signals is a valid "
        "answer — this run did not reach one. "
        f"tools_used=[{called}]"
    )
