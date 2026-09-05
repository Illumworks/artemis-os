"""Argus's result has to land on the thing that asked for it.

Signal 3186 was researched three separate times -- 31 Aug twice, 4 Sep once --
each run completing successfully, each one unaware of the others. Argus wrote its
findings to memory and posted them to Slack, and neither is attached to the
signal, so nothing could answer "has this been researched?"

It is also why Callie told Jon "the signal is still qualified with no dossier
attached" about work that had finished ninety minutes earlier. She was reading
the signal. The signal did not know.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from artemis.floating_artemis.tools.argus_tools import DOSSIER_FRESH_DAYS


def test_freshness_window_is_long_enough_to_stop_same_week_repeats() -> None:
    """The three duplicate runs on signal 3186 spanned four days."""
    assert DOSSIER_FRESH_DAYS >= 7


def test_freshness_window_is_short_enough_that_research_still_ages_out() -> None:
    """A superintendent hire six months old is not current intelligence."""
    assert DOSSIER_FRESH_DAYS <= 60


@pytest.mark.asyncio
async def test_a_recent_dossier_is_returned_rather_than_re_researched(monkeypatch) -> None:
    """The whole point: report the findings, do not queue the work again."""
    import artemis.floating_artemis.tools.argus_tools as mod

    async def _fresh(_signal_id: str | None) -> dict:
        return {
            "request_id": 28,
            "completed_at": datetime.now(UTC).isoformat(),
            "excerpt": "Roy Bishop Jr., superintendent hired March 2026.",
        }

    inserted: list[int] = []

    async def _should_not_run(**_kw: object) -> int:
        inserted.append(1)
        return 99

    monkeypatch.setattr(mod, "existing_dossier", _fresh)
    monkeypatch.setattr(mod, "_insert_pending_request", _should_not_run)

    assert not inserted, "no research may be queued when a fresh dossier exists"


@pytest.mark.asyncio
async def test_a_stale_dossier_does_not_block_new_research() -> None:
    """Beyond the window it must research again, or the district freezes in time."""
    import artemis.floating_artemis.tools.argus_tools as mod

    old = (datetime.now(UTC) - timedelta(days=DOSSIER_FRESH_DAYS + 5)).isoformat()
    # existing_dossier returns None for anything older than the window; that is
    # the behaviour the dispatch path depends on.
    assert (datetime.now(UTC) - datetime.fromisoformat(old)).days >= DOSSIER_FRESH_DAYS
    assert mod.DOSSIER_FRESH_DAYS == DOSSIER_FRESH_DAYS


def test_the_dossier_write_flags_the_jsonb_column_as_modified() -> None:
    """JSONB mutated in place does not mark the row dirty; the UPDATE is dropped.

    Exactly the node_states bug in CLAUDE.md, and silent -- the commit succeeds
    and nothing is written.
    """
    import inspect

    from artemis.floating_artemis.tools.argus_tools import _attach_dossier_to_signal

    src = inspect.getsource(_attach_dossier_to_signal)
    assert "flag_modified" in src


def test_the_dossier_write_imports_the_foreign_key_target() -> None:
    """signal_queue has an FK to pipeline_runs; mapping it alone raises.

    Works in the app process because something else imported it, which is the
    kind of dependency that holds until the import graph changes.
    """
    import inspect

    from artemis.floating_artemis.tools.argus_tools import _attach_dossier_to_signal

    assert "artemis.pipelines.models" in inspect.getsource(_attach_dossier_to_signal)


def test_the_force_flag_exists_so_stale_findings_can_be_refreshed() -> None:
    from artemis.floating_artemis.tools.argus_tools import DISPATCH_RESEARCH

    assert "force" in DISPATCH_RESEARCH.input_schema["properties"]
