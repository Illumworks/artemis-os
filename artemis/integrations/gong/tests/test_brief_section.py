"""The districts talking about Amira, in the daily pulse.

Both halves belong: a customer with a problem and a district saying something
good are each the market talking about us. The positive half is the one nothing
currently surfaces -- `Amira Solving Problems` fires on 100% of Madera Unified's
calls and outside those calls nobody has heard it.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.integrations.gong import brief_section as mod
from artemis.integrations.gong.baseline import AccountDeviation
from artemis.integrations.gong.client import GongMetadataClient


def test_it_follows_the_section_contract() -> None:
    """`build_<feed>_section(session) -> str | None`, and never raises."""
    import inspect

    assert inspect.iscoroutinefunction(mod.build_gong_section)
    src = inspect.getsource(mod.build_gong_section)
    assert "except Exception" in src, "one dead feed must not take down the brief"
    assert "return None" in src


@pytest.mark.asyncio
async def test_no_credentials_means_no_section_not_an_error(monkeypatch) -> None:
    """A missing key must leave the brief intact and say nothing."""
    from artemis.config import settings

    monkeypatch.setattr(settings, "gong_access_key", "", raising=False)

    assert await mod.build_gong_section() is None


@pytest.mark.asyncio
async def test_a_gong_outage_drops_the_section_silently(monkeypatch) -> None:
    """The brief is a pulse, not a status page. A dead feed contributes nothing."""

    async def _boom(self, path, body):  # noqa: ANN001, ARG001
        raise RuntimeError("gong down")

    monkeypatch.setattr(GongMetadataClient, "_post", _boom)

    assert await mod.build_gong_section() is None


def test_the_section_never_claims_to_know_what_was_said() -> None:
    """Tracker counts are not quotes, and the footer has to say so."""
    import inspect

    src = inspect.getsource(mod)
    assert "never what anyone said" in src


def test_it_reports_both_directions() -> None:
    """Concern alone would make this a churn report rather than a pulse."""
    import inspect

    src = inspect.getsource(mod.build_gong_section)
    assert "raising more than usual" in src
    assert "sounding positive" in src


def test_it_is_ranked_and_capped_rather_than_exhaustive() -> None:
    """27 of 72 judged accounts carry some elevated tracker.

    All of them in a daily brief is a wall; the top few is a pulse.
    """
    assert mod.MAX_PER_KIND <= 5


def test_the_window_lets_an_account_reach_the_minimum_sample() -> None:
    """Only 42 of 313 accounts have four or more calls in 120 days.

    A shorter window would judge almost nobody.
    """
    from artemis.integrations.gong.baseline import MIN_CALLS_FOR_SIGNAL

    assert mod.LOOKBACK_DAYS >= 90
    assert MIN_CALLS_FOR_SIGNAL >= 3


def test_a_flagged_account_carries_its_sample_size() -> None:
    """ "100% of 3 calls" and "100% of 30" deserve different reactions."""
    dev = AccountDeviation(
        account_name="A District",
        calls_considered=9,
        elevated_concern={"Objections (tracker)": 1.0},
    )

    assert "9 calls" in dev.describe()


def _call(account: str, tracker: str, hit: bool) -> dict[str, Any]:
    """One call in the shape `/v2/calls/extensive` actually returns."""
    return {
        "metaData": {"id": f"{account}-{tracker}-{hit}", "started": "2026-08-01T10:00:00Z"},
        "context": [
            {"objects": [{"objectType": "Account", "fields": [{"name": "Name", "value": account}]}]}
        ],
        "parties": [{"affiliation": "Internal"}, {"affiliation": "External"}],
        "content": {"trackers": [{"name": tracker, "count": 1 if hit else 0}]},
    }


@pytest.mark.asyncio
async def test_a_district_getting_worse_says_so_on_the_line(monkeypatch) -> None:
    """The clause cannot fire in production until a reading is three weeks old,
    so without this the rendering path ships unexercised and nobody finds out
    for 21 days. Same trap as `write_snapshots`, which nothing called for a day.
    """
    from datetime import date

    from artemis.config import settings
    from artemis.integrations.gong.snapshots import Reading

    # Set explicitly rather than inherited from .env: a worktree without one
    # would otherwise return None at the credential check and pass vacuously.
    monkeypatch.setattr(settings, "gong_access_key", "test", raising=False)
    monkeypatch.setattr(settings, "gong_access_key_secret", "test", raising=False)

    async def _calls(self, path, body):  # noqa: ANN001, ARG001
        # Nine of ten calls fire the tracker at Rising, none at Calm: enough for
        # Rising to sit above a portfolio norm it is itself half of.
        return {
            "calls": [_call("Rising District", "Customer concerns", i < 9) for i in range(10)]
            + [_call("Calm District", "Customer concerns", False) for _ in range(10)],
            "records": {},
        }

    async def _previous(_flagged):  # noqa: ANN001
        from artemis.integrations.gong.snapshots import trend_for

        was = Reading(
            on=date(2026, 8, 1),
            account_name="Rising District",
            calls=9,
            concern={"Customer concerns": 0.30},
            advocacy={},
        )
        return {d.account_name: trend_for(d, was) for d in _flagged}

    monkeypatch.setattr(GongMetadataClient, "_post", _calls)
    monkeypatch.setattr(mod, "_trends", _previous)
    monkeypatch.setattr(mod, "_record", lambda flagged: _noop())

    body = await mod.build_gong_section()

    assert body is not None
    assert "Rising District" in body
    assert "(up from 30% on 01 Aug)" in body, body


async def _noop() -> None:
    return None
