"""Bounded pipeline questions, and the guards that keep them honest.

Callie will quote these figures to the person who owns the number. Josh will know
within a sentence whether one is real, and a single invented figure costs the
credibility of every true one after it.
"""

from __future__ import annotations

import pytest

from artemis.marketing.pipeline_intel import (
    JAN_2026_CAVEAT,
    SIZE_BANDS,
    UNAVAILABLE,
    big_deals_without_contacts,
    loss_reason_availability,
    win_rate_by_size,
)


class _Client:
    """Answers COUNT queries from a lookup keyed on a fragment of the SOQL."""

    def __init__(self, counts: dict[str, int], *, raise_on: str | None = None) -> None:
        self.counts = counts
        self.raise_on = raise_on
        self.queries: list[str] = []

    async def query(self, soql: str) -> list[dict]:
        self.queries.append(soql)
        if self.raise_on and self.raise_on in soql:
            raise RuntimeError("salesforce down")
        for fragment, value in self.counts.items():
            if fragment in soql:
                return [{"n": value}]
        return [{"n": 0}]


@pytest.mark.asyncio
async def test_the_january_cleanup_is_excluded_from_every_win_rate_query() -> None:
    """38 lost deals worth $46.2M, aged up to 1,182 days, closed in one month.

    Left in, it reads as a catastrophic quarter. It was inventory cleanup.
    """
    client = _Client({})

    await win_rate_by_size(client)

    assert client.queries, "the question must actually hit Salesforce"
    for soql in client.queries:
        assert "CloseDate < 2026-01-01 OR CloseDate > 2026-01-31" in soql, soql


@pytest.mark.asyncio
async def test_the_caveat_travels_with_the_loss_figures() -> None:
    """A caveat kept somewhere else is a caveat that will not be repeated."""
    answer = await win_rate_by_size(_Client({}))

    assert JAN_2026_CAVEAT in answer.caveats
    assert "1,182 days" in answer.render()


@pytest.mark.asyncio
async def test_every_answer_states_the_filter_that_produced_it() -> None:
    """ "We win 44%" is wrong. The same number with its scope is true."""
    answer = await win_rate_by_size(_Client({}), days=730)

    rendered = answer.render()
    assert "Scope:" in rendered
    assert "730 days" in rendered
    assert "excluding Jan 2026" in rendered


@pytest.mark.asyncio
async def test_a_band_with_no_closed_deals_says_so_rather_than_showing_zero_percent() -> None:
    """0% reads as "we lose them all"; the truth is "we have not closed any"."""
    answer = await win_rate_by_size(_Client({}))

    assert all(row["win_rate"] == "no closed deals" for row in answer.rows)


def test_the_bands_break_where_the_finding_is() -> None:
    """The pattern turns at $10k: 81% below, 46% above. One blended number hides it."""
    boundaries = {low for low, _, _ in SIZE_BANDS}
    assert 10_000 in boundaries
    assert 250_000 in boundaries


@pytest.mark.asyncio
async def test_missing_contacts_are_labelled_hygiene_not_risk() -> None:
    """The intuitive reading is backwards and would be repeated as a warning.

    77% of WON deals have no contact attached, against 63% of lost ones.
    """
    answer = await big_deals_without_contacts(_Client({"COUNT(Id)": 61}))

    joined = " ".join(answer.caveats)
    assert "hygiene, not deal quality" in joined
    assert "must not be described as one" in joined


@pytest.mark.asyncio
async def test_no_loss_reason_field_forbids_inferring_one() -> None:
    """A plausible story about why a deal was lost is fabrication with a citation shape."""
    # All four probe names end in "__c"; raising on only one of them left the
    # others "succeeding" and the test asserting a half-configured org.
    client = _Client({}, raise_on="__c")

    answer = await loss_reason_availability(client)

    assert answer.rows[0]["loss_reason_fields_found"] == "NONE"
    assert "do not infer one" in " ".join(answer.caveats).lower()


def test_unavailable_forbids_reporting_a_zero() -> None:
    """The Argus failure: unreachable and "nothing found" must not look alike."""
    assert "NOT a report of zero" in UNAVAILABLE
    assert "do not state or estimate" in UNAVAILABLE.lower()
