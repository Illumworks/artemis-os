"""An unscoped memory query must search memory, not one near-empty corner of it.

`query_memory` defaulted to scope `global:global`, which holds ONE observation of
2,170. So a query with no scope searched 0.05% of memory and truthfully reported
nothing found.

That is how Callie told Jon "nothing in memory shows Argus findings on Roy or
Grosse Pointe" ninety minutes after Argus wrote four observations naming Roy
Bishop Jr. She called the tool. The tool answered from a scope those findings
were never in, and she relayed the result in good faith -- the same shape as
dispatch_research returning "dispatched" for work it never started.

The fix widens what is SEARCHED. It must not widen what is PERMITTED, and that is
what most of this file is about.
"""

from __future__ import annotations

import pytest

from artemis.floating_artemis.memory import _enforce_agent_scope_set
from artemis.memory.schemas import Scope

ARTEMIS_ONLY = [Scope(scope_kind="agent", scope_id="artemis")]
PERSONAL = [Scope(scope_kind="personal", scope_id="1")]
MARKETING = [Scope(scope_kind="workspace", scope_id="marketing")]


def test_callie_may_read_the_workspace_the_findings_live_in() -> None:
    """The Argus dossier is written to workspace:marketing."""
    assert _enforce_agent_scope_set("callie", MARKETING) == MARKETING


def test_callie_still_cannot_read_artemis_scope() -> None:
    """The gate that matters. Widening the default must not widen the allowance."""
    assert _enforce_agent_scope_set("callie", ARTEMIS_ONLY) == []


def test_callie_still_cannot_read_personal_scope() -> None:
    assert _enforce_agent_scope_set("callie", PERSONAL) == []


def test_an_unscoped_search_is_still_filtered_per_agent() -> None:
    """Every live scope goes in; only the permitted ones come out.

    This is the exact path an unscoped query now takes, so the denial has to
    survive being handed the full set rather than a single requested scope.
    """
    every_scope = MARKETING + ARTEMIS_ONLY + PERSONAL
    allowed = _enforce_agent_scope_set("callie", every_scope)

    assert MARKETING[0] in allowed
    assert ARTEMIS_ONLY[0] not in allowed
    assert PERSONAL[0] not in allowed


def test_an_unknown_agent_gets_nothing_even_from_the_full_set() -> None:
    """Fail closed. An unrecognised caller must not be handed all of memory."""
    assert _enforce_agent_scope_set("nobody", MARKETING + ARTEMIS_ONLY) == []
    assert _enforce_agent_scope_set("", MARKETING) == []


def test_kai_cannot_reach_marketing_through_the_widened_default() -> None:
    """Kai is enablement-only; the wider default must not leak marketing to him."""
    assert _enforce_agent_scope_set("kai", MARKETING) == []


@pytest.mark.parametrize("given", ["all", "", None])
def test_all_and_blank_both_mean_everything_permitted(given: str | None) -> None:
    """ "all" previously collapsed to global:global, which meant almost nothing."""
    from artemis.floating_artemis.tools import core

    src = core.__file__
    with open(src) as fh:
        text = fh.read()

    assert 'scope = None if raw_scope in (None, "", "all") else str(raw_scope)' in text, (
        "an unscoped or 'all' query must resolve to the full permitted set"
    )
    assert '"global:global"' not in text.split("def _make_query_memory")[1][:2000], (
        "global:global must no longer be the default search scope"
    )
