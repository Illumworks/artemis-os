"""GONG-1: the call signal, as something Callie can be asked (not just pushed).

The tool's job is as much refusing to answer as answering. Every branch below
that could be mistaken for good news has to say what it actually is.
"""

from __future__ import annotations

import pytest

from artemis.floating_artemis.tools.gong_tools import (
    DISTRICT_CALL_SIGNAL,
    _district_call_signal,
    register_gong_tools,
)
from artemis.integrations.gong.baseline import AccountDeviation, PortfolioRates
from artemis.integrations.gong.survey import Survey


def _survey(*devs: AccountDeviation, truncated: bool = False) -> Survey:
    return Survey(
        portfolio=PortfolioRates(rates={}, call_count=733),
        deviations=list(devs),
        truncated=truncated,
    )


# ── it is actually reachable ─────────────────────────────────────────────────


def test_callies_registry_carries_it() -> None:
    """A tool registered in no registry is the failure this codebase keeps
    producing. Assert the effect, not the registration call."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    registry = build_authorized_tool_registry(set(), agent_id="callie")

    assert DISTRICT_CALL_SIGNAL in registry


def test_no_other_agent_gets_it_by_accident() -> None:
    """Callie-exclusive by design: this reads the call corpus of a company whose
    reps did not consent to us reading it, and the scope stays narrow."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    for agent in ("artemis", "kai", "ares"):
        registry = build_authorized_tool_registry(set(), agent_id=agent)
        assert DISTRICT_CALL_SIGNAL not in registry, agent


def test_it_is_read_only() -> None:
    """Layer 1 means "just tell me". Anything higher would need an approval path."""

    class _Reg:
        def __init__(self) -> None:
            self.seen: list[int] = []

        def register(self, tool: object, handler: object, layer: int = 1) -> None:
            self.seen.append(layer)

    reg = _Reg()
    register_gong_tools(reg)  # type: ignore[arg-type]

    assert reg.seen == [1]


# ── what it says when it does not know ───────────────────────────────────────


@pytest.mark.asyncio
async def test_gong_being_down_is_unknown_not_quiet(monkeypatch) -> None:
    """The failure that matters: a dead integration reported as a calm district."""

    async def _boom(**_kw: object) -> Survey:
        raise RuntimeError("gong down")

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _boom)

    out = await _district_call_signal({"district_name": "Pinellas"})

    assert "UNKNOWN" in out
    assert "not the same as a quiet district" in out


@pytest.mark.asyncio
async def test_an_ambiguous_name_asks_rather_than_picks(monkeypatch) -> None:
    """Found live on 2026-09-09: "Valley" returned Walnut Valley Unified as fact
    while five other Valley districts sat in the same window."""

    async def _survey_fn(**_kw: object) -> Survey:
        return _survey(
            AccountDeviation("Apple Valley Unified School District", 5),
            AccountDeviation("Chino Valley Unified School District", 9),
        )

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _survey_fn)

    out = await _district_call_signal({"district_name": "Valley"})

    assert "matches more than one account" in out
    assert "Apple Valley Unified School District" in out
    assert "Chino Valley Unified School District" in out
    assert "not going to pick" in out


@pytest.mark.asyncio
async def test_a_cut_candidate_list_says_it_was_cut(monkeypatch) -> None:
    """Six names and no "and 9 more" reads as the complete set."""

    async def _survey_fn(**_kw: object) -> Survey:
        return _survey(*(AccountDeviation(f"Valley District {i}", 5) for i in range(12)))

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _survey_fn)

    out = await _district_call_signal({"district_name": "Valley"})

    assert "and 6 more" in out


@pytest.mark.asyncio
async def test_nothing_unusual_is_not_a_clean_bill_of_health(monkeypatch) -> None:
    async def _survey_fn(**_kw: object) -> Survey:
        return _survey(AccountDeviation("Quiet District", 9))

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _survey_fn)

    out = await _district_call_signal({"district_name": "Quiet District"})

    assert "nothing unusual" in out.lower()
    assert "not a clean bill of health" in out


@pytest.mark.asyncio
async def test_too_few_calls_is_not_a_finding_either_way(monkeypatch) -> None:
    async def _survey_fn(**_kw: object) -> Survey:
        return _survey(AccountDeviation("Small District", 2, insufficient=True))

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _survey_fn)

    out = await _district_call_signal({"district_name": "Small District"})

    assert "Not a finding either way" in out


@pytest.mark.asyncio
async def test_a_truncated_window_is_declared(monkeypatch) -> None:
    async def _survey_fn(**_kw: object) -> Survey:
        return _survey(
            AccountDeviation("Loud District", 9, elevated_concern={"Customer concerns": 1.0}),
            truncated=True,
        )

    monkeypatch.setattr("artemis.integrations.gong.survey.run_survey", _survey_fn)

    out = await _district_call_signal({"district_name": "Loud District"})

    assert "floor rather than a total" in out


# ── what it never says ───────────────────────────────────────────────────────


def test_it_carries_no_route_to_a_rep_or_a_quote() -> None:
    """CLAUDE.md rule 4, asserted rather than intended. The credential can read
    all 22 reps' conversations, which is why this is a test and not a comment."""
    import inspect

    from artemis.floating_artemis.tools import gong_tools

    src = inspect.getsource(gong_tools)
    for forbidden in ("primaryUserId", "transcript", "speaker", "rep_name"):
        assert forbidden not in src.replace("no transcript", ""), forbidden


@pytest.mark.asyncio
async def test_an_empty_store_says_the_brief_has_not_run(monkeypatch) -> None:
    """Not "no district has a signal" -- which is what an empty list reads as."""

    async def _none(*_a: object, **_kw: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr("artemis.integrations.gong.snapshots.previous_readings", _none)

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_a: object) -> bool:
            return False

    out = await _district_call_signal({}, session_factory=lambda: _Session())

    assert "has not recorded any yet" in out
    assert "NOT that no district has a signal" in out
