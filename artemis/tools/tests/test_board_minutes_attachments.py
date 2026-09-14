"""BoardDocs: the documents, not the page that links to them.

`board_minutes` was the largest single source of inconclusive scout runs — 45 in
the 30 days to 2026-09-14 — and its traces all said the same thing: "PDF
extraction from BoardDocs returned 403 errors on all attempts, so content
verification is limited to agenda titles."

Two separate faults, both ours, both reproduced live against Dallas ISD before
these tests were written.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from artemis.tools.board_minutes import _MAX_ITEMS, _sort_key
from artemis.tools.context import ToolContext
from artemis.tools.pdf_extractor import _BROWSER_UA
from artemis.tools.pdf_extractor import _factory as _pdf_factory


def _ctx() -> ToolContext:
    return ToolContext(
        session=None,  # type: ignore[arg-type]
        agent_id="marketing.scout.board_minutes",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


# ── Fault 1: the bare httpx User-Agent is refused by CloudFront ───────────────
# Live, on one Dallas ISD board document:
#   python-httpx UA -> 403 text/html  919 bytes    server: CloudFront
#   browser UA      -> 200 application/pdf 645,868 bytes  b'%PDF-1.7'


@pytest.mark.asyncio
async def test_the_pdf_extractor_sends_a_browser_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        seen["headers"] = kwargs.get("headers") or {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["request_ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, content=b"%PDF-1.7\nnot really a pdf")

        kwargs["_inner"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _pdf_factory(_ctx())
    await impl({"url": "https://go.boarddocs.com/tx/disd/Board.nsf/pfiles/X/$file/a.pdf"})

    assert seen["headers"].get("User-Agent") == _BROWSER_UA
    assert "httpx" not in seen["headers"].get("User-Agent", "").lower()


@pytest.mark.asyncio
async def test_a_redirect_is_followed_rather_than_reported_as_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document links routinely redirect — a CDN hop, or a permalink resolving to
    the file. Not following them produced a run of "HTTP 302 fetching ..."
    failures that read like dead links."""
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__
    hops: list[str] = []

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            hops.append(str(request.url))
            if len(hops) == 1:
                return httpx.Response(302, headers={"Location": "https://cdn.test/real.pdf"})
            return httpx.Response(200, content=b"%PDF-1.7\nbody")

        kwargs["_inner"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _pdf_factory(_ctx())
    result = await impl({"url": "https://go.boarddocs.com/a.pdf"})

    assert len(hops) == 2, "the redirect was not followed"
    assert not result.startswith("ERROR: HTTP 302")


# ── Fault 2: the scout had no PDF URL to pass ─────────────────────────────────
# Every BoardDocs pdf_extractor call on record targeted a `goto?open&id=` or
# `/Board.nsf/Public` permalink — an HTML page. Fixing the UA alone only changes
# the error from 403 to "PdfiumError: Data format error".


def test_agenda_items_sort_newest_first() -> None:
    """Capping an unsorted list would drop this month's meetings to keep last
    year's."""
    items = [{"date": "2025-01-02"}, {"date": "2026-09-01"}, {"date": "2026-02-26"}]
    assert [i["date"] for i in sorted(items, key=_sort_key, reverse=True)] == [
        "2026-09-01",
        "2026-02-26",
        "2025-01-02",
    ]


def test_an_item_with_no_date_sorts_last_rather_than_raising() -> None:
    items: list[dict[str, Any]] = [{"date": None}, {"date": "2026-09-01"}, {}]
    ordered = sorted(items, key=_sort_key, reverse=True)
    assert ordered[0]["date"] == "2026-09-01"


def test_the_item_cap_is_small_enough_to_stay_inline() -> None:
    """One run pulled 273 agenda items across three districts; at 2,000 chars
    apiece that is half a million characters in a single tool result, which the
    harness spills to a file — and the scout then stalls holding a filename."""
    assert _MAX_ITEMS * 2000 < 100_000


@pytest.mark.asyncio
async def test_attachment_urls_are_parsed_from_the_public_files_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint is undocumented and appears nowhere else in the codebase, so
    its shape is pinned here: verified live 2026-09-14 against Dallas ISD."""
    from artemis.scouts._http import ScoutHttpClient
    from artemis.scouts.board_minutes.client import fetch_agenda_item_files

    body = (
        '<a class="public-file" href="/tx/disd/Board.nsf/pfiles/DQWSDN723353/'
        '$file/02-26-26-%20Board%20Doc-WAIVER.pdf">Board Doc</a>'
        '<a class="public-file" href="/tx/disd/Board.nsf/pfiles/DQWQF8690E02/'
        '$file/Detail.pdf">Detail</a>'
    )
    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    async with ScoutHttpClient(timeout=5.0) as http:
        urls = await fetch_agenda_item_files(
            "https://go.boarddocs.com/tx/disd/Board.nsf", "DQWQ9M683D42", http
        )

    assert len(urls) == 2
    assert urls[0].startswith("https://go.boarddocs.com/tx/disd/Board.nsf/pfiles/")
    assert urls[0].endswith(".pdf")


@pytest.mark.asyncio
async def test_an_attachment_failure_never_costs_us_the_agenda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An item with no readable attachment is still a useful item."""
    from artemis.scouts._http import ScoutHttpClient
    from artemis.scouts.board_minutes.client import fetch_agenda_item_files

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        kwargs["_inner"] = httpx.AsyncClient(transport=httpx.MockTransport(boom))
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    async with ScoutHttpClient(timeout=5.0) as http:
        assert (
            await fetch_agenda_item_files("https://go.boarddocs.com/x/Board.nsf", "A", http) == []
        )


def test_the_tool_tells_the_scout_which_url_to_extract() -> None:
    """Passing `source_url` to pdf_extractor is what produced "Data format
    error" on every call; the description now says so explicitly."""
    from artemis.tools.board_minutes import _DEF

    assert "attachment_urls" in _DEF.description
    assert "do NOT pass 'source_url'" in _DEF.description
    assert json.dumps(_DEF.input_schema)
