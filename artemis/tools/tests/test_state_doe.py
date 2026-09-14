"""Tests for state_doe.fetch tool — uses MockTransport, no live HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from artemis.tools.context import ToolContext
from artemis.tools.state_doe import _factory

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.state_doe",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _load_fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _re_date(xml: str, days_ago: int = 2) -> str:
    """Replace every pubDate in a fixture with a recent one, shape intact."""
    import re
    from datetime import UTC, datetime, timedelta

    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return re.sub(r"<pubDate>[^<]*</pubDate>", f"<pubDate>{stamp}</pubDate>", xml)


@pytest.mark.asyncio
async def test_fetch_with_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fixture's pubDates are fixed at May 2026, which aged past the tool's
    # 42-day window and made this test fail for a reason it is not about. Dates
    # are rewritten to "recently" at load time so it cannot rot again.
    xml = _re_date(_load_fixture("state_doe_sample.xml"))
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=xml.encode("utf-8"))
            )
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"state": "FL"}))
    assert len(items) >= 1 and items[0]["title"] and items[0]["link"]


@pytest.mark.asyncio
async def test_fetch_unknown_state() -> None:
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": "ZZ"})) == []


@pytest.mark.asyncio
async def test_fetch_empty_state() -> None:
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": ""})) == []


@pytest.mark.asyncio
async def test_fetch_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, content=b"err"))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": "FL"})) == []


# ── Payload trimming (2026-09-14) ─────────────────────────────────────────────
# Six states returned 441,270 characters in one turn; past a threshold in that
# band the harness spills the result to a file and the scout stalls holding a
# filename. Recency does the cutting — the feed is not sorted, so a count cap
# would have thrown away September to keep 2024.

from datetime import UTC, datetime, timedelta  # noqa: E402

from artemis.tools.state_doe import _MAX_ITEMS, _recent_first  # noqa: E402


def _item(days_ago: float, title: str = "t", link: str = "https://x.test/a") -> dict[str, Any]:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return {
        "title": title,
        "link": link,
        "published": when.strftime("%a, %d %b %Y %H:%M:%S %z"),
        "summary": '<a href="https://x.test/a">' + title + "</a>&nbsp;&nbsp;Publisher",
        "_source_type": "doe_rss",
    }


def test_stale_items_are_dropped_and_recent_ones_kept() -> None:
    items = [_item(400), _item(2), _item(900), _item(10)]
    out = _recent_first(items, 42)
    assert len(out) == 2


def test_results_come_back_newest_first() -> None:
    """The live feed is a merge of several sources and is NOT sorted — Maryland's
    third item was from July while its first was September."""
    out = _recent_first([_item(30, "old"), _item(1, "new"), _item(15, "mid")], 42)
    assert [i["title"] for i in out] == ["new", "mid", "old"]


def test_only_the_three_needed_fields_survive() -> None:
    out = _recent_first([_item(1)], 42)
    assert set(out[0]) == {"title", "link", "published"}


def test_the_link_is_passed_through_character_for_character() -> None:
    """149 fabricated state_doe signals were caught because a real Google News id
    is long and opaque. Shortening or resolving this field would destroy the one
    thing that check depends on."""
    url = "https://news.google.com/rss/articles/CBMitgFBVV95cUxOdXpjM1dvTHBadWlzWGxkRFR6SXQ0"
    out = _recent_first([_item(1, link=url)], 42)
    assert out[0]["link"] == url


def test_an_unreadable_date_keeps_the_item_rather_than_dropping_it() -> None:
    """We cannot show it is stale, and losing real intelligence to a formatting
    quirk is the more expensive mistake. It sorts last, not first."""
    undated = {"title": "no date", "link": "https://x.test/b", "published": "not a date"}
    out = _recent_first([undated, _item(1, "dated")], 42)
    assert [i["title"] for i in out] == ["dated", "no date"]
    assert _recent_first([{"title": "x", "link": "y"}], 42)


def test_a_flood_of_recent_items_is_capped_after_sorting() -> None:
    """The backstop can only ever drop the oldest of the fresh ones."""
    out = _recent_first([_item(i * 0.01, f"t{i}") for i in range(200)], 42)
    assert len(out) == _MAX_ITEMS
    assert out[0]["title"] == "t0"


def test_nothing_recent_returns_empty_rather_than_stale_items() -> None:
    """An empty result is a real answer. Backfilling with 2024 items would invite
    a scout to emit a two-year-old story as news."""
    assert _recent_first([_item(400), _item(900)], 42) == []
