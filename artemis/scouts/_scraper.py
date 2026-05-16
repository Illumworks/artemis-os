"""Shared async scraper for all Artemis scout workers.

Wraps Playwright's async API behind a thin context-manager interface.
All production scraping goes through ``scraper_context()``.  Tests inject
a fake page via the ``_page`` parameter — no real browser is needed.

Usage::

    async with scraper_context() as scraper:
        html = await scraper.fetch_html("https://example.com/page")
        pdf_bytes = await scraper.download_bytes("https://example.com/doc.pdf")
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocols — injectable for tests
# ---------------------------------------------------------------------------


@runtime_checkable
class BrowserPage(Protocol):
    """Minimal interface the scraper needs from a Playwright page."""

    async def goto(self, url: str, *, wait_until: str = "load", timeout: float = 30_000) -> Any: ...

    async def content(self) -> str: ...

    async def evaluate(self, expression: str, *args: Any) -> Any: ...


# ---------------------------------------------------------------------------
# ScraperSession — wraps a single page
# ---------------------------------------------------------------------------


class ScraperSession:
    """A single-page scraping session.

    In production this wraps a Playwright ``Page``.
    In tests inject a ``BrowserPage`` mock via ``_page``.
    """

    def __init__(self, page: BrowserPage) -> None:
        self._page = page

    async def fetch_html(self, url: str, *, timeout: float = 30_000) -> str:
        """Navigate to *url* and return the rendered HTML."""
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=timeout)
        except Exception:
            # Some pages raise on networkidle — fall back to domcontentloaded.
            await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return await self._page.content()

    async def download_bytes(self, url: str, *, timeout: float = 60_000) -> bytes:
        """Download *url* as raw bytes (e.g., a PDF) via browser fetch.

        Uses the browser's fetch API so cookies / session are preserved.
        """
        js = """
        async (url) => {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const buf = await resp.arrayBuffer();
            return Array.from(new Uint8Array(buf));
        }
        """
        result: list[int] = await self._page.evaluate(js, url)
        return bytes(result)

    async def fetch_html_simple(self, url: str) -> str:
        """Navigate and return HTML without networkidle wait (for slow pages)."""
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        return await self._page.content()


# ---------------------------------------------------------------------------
# scraper_context — async context manager
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def scraper_context(
    *,
    headless: bool = True,
    _page: BrowserPage | None = None,
) -> AsyncGenerator[ScraperSession, None]:
    """Async context manager that yields a :class:`ScraperSession`.

    Parameters
    ----------
    headless:
        Run the browser headlessly (default ``True``).
    _page:
        Inject a pre-built ``BrowserPage`` — **tests only**.  When provided,
        no real Playwright browser is launched.
    """
    if _page is not None:
        yield ScraperSession(_page)
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (compatible; ArtemisScout/1.0; +https://amiralearning.com/bot)"
            ),
            java_script_enabled=True,
        )
        page = await context.new_page()
        try:
            yield ScraperSession(page)  # type: ignore[arg-type]
        except Exception:
            _logger.exception("ScraperSession raised during context body")
            raise
        finally:
            await context.close()
            await browser.close()
