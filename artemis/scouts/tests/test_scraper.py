"""Tests for artemis.scouts._scraper — shared Playwright wrapper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from artemis.scouts._scraper import BrowserPage, ScraperSession, scraper_context

# ---------------------------------------------------------------------------
# Fake BrowserPage for injection
# ---------------------------------------------------------------------------


class FakePage:
    """Minimal BrowserPage implementation for tests."""

    def __init__(
        self,
        html: str = "<html><body>test</body></html>",
        download_data: list[int] | None = None,
        evaluate_result: Any = None,
    ) -> None:
        self._html = html
        self._evaluate_result = download_data if evaluate_result is None else evaluate_result
        self.navigated_to: list[str] = []
        self.goto = AsyncMock(return_value=None)
        self.content = AsyncMock(return_value=html)
        self.evaluate = AsyncMock(return_value=self._evaluate_result)

    async def _goto(self, url: str, **_: Any) -> None:
        self.navigated_to.append(url)


# ---------------------------------------------------------------------------
# ScraperSession unit tests
# ---------------------------------------------------------------------------


async def test_fetch_html_returns_page_content() -> None:
    page = FakePage(html="<html>hello</html>")
    session = ScraperSession(page)
    result = await session.fetch_html("https://example.com/")
    assert result == "<html>hello</html>"


async def test_fetch_html_calls_goto_with_url() -> None:
    page = FakePage()
    session = ScraperSession(page)
    await session.fetch_html("https://example.com/page")
    page.goto.assert_called_once()
    call_args = page.goto.call_args
    assert call_args[0][0] == "https://example.com/page"


async def test_fetch_html_falls_back_on_timeout() -> None:
    """If networkidle goto raises, falls back to domcontentloaded."""
    page = FakePage(html="<html>fallback</html>")
    # First call raises (networkidle timeout), second succeeds.
    page.goto = AsyncMock(side_effect=[TimeoutError("networkidle timeout"), None])
    session = ScraperSession(page)
    result = await session.fetch_html("https://slow.example.com/")
    assert result == "<html>fallback</html>"
    assert page.goto.call_count == 2


async def test_download_bytes_evaluates_fetch_js() -> None:
    """download_bytes calls evaluate() with fetch JS and converts result to bytes."""
    expected = [80, 75, 3, 4]  # fake bytes (PK zip magic)
    page = FakePage(evaluate_result=expected)
    session = ScraperSession(page)
    result = await session.download_bytes("https://example.com/doc.pdf")
    assert result == bytes(expected)
    page.evaluate.assert_called_once()


async def test_download_bytes_url_passed_to_evaluate() -> None:
    page = FakePage(evaluate_result=[1, 2, 3])
    session = ScraperSession(page)
    await session.download_bytes("https://example.com/report.pdf")
    call_args = page.evaluate.call_args
    assert "https://example.com/report.pdf" in call_args[0]


async def test_fetch_html_simple_uses_domcontentloaded() -> None:
    page = FakePage(html="<html>simple</html>")
    session = ScraperSession(page)
    result = await session.fetch_html_simple("https://example.com/")
    assert result == "<html>simple</html>"
    call_kwargs = page.goto.call_args[1]
    assert call_kwargs.get("wait_until") == "domcontentloaded"


# ---------------------------------------------------------------------------
# scraper_context tests
# ---------------------------------------------------------------------------


async def test_scraper_context_with_injected_page_yields_session() -> None:
    page = FakePage(html="<html>injected</html>")
    async with scraper_context(_page=page) as session:
        assert isinstance(session, ScraperSession)
        result = await session.fetch_html("https://example.com/")
    assert result == "<html>injected</html>"


async def test_scraper_context_injected_page_fetch_works() -> None:
    """When _page is injected, the session can fetch HTML without a real browser."""
    page = FakePage(html="<html>no-browser</html>")
    # Deliberately do NOT install Playwright browser; the test must still pass
    # because _page short-circuits the real browser code path.
    async with scraper_context(_page=page) as session:
        html = await session.fetch_html("https://example.com/")
    assert html == "<html>no-browser</html>"


async def test_scraper_session_isinstance_browserpage_protocol() -> None:
    """FakePage satisfies the BrowserPage runtime-checkable protocol."""
    page = FakePage()
    assert isinstance(page, BrowserPage)
