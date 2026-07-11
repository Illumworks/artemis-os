"""Tool: pdf_extractor.extract

Downloads a PDF from a URL and extracts its text.
Reuses artemis.scouts._pdf.extract_text.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.egress_guard import EgressBlockedError, async_validate_url
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts._pdf import extract_text
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_MAX_CHARS = 5000

_DEF = Tool(
    name="pdf_extractor.extract",
    description=(
        "Download a PDF from a URL and extract its text content. "
        "Returns extracted text (truncated to 5000 chars) or an error string."
    ),
    input_schema={
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "URL of the PDF to download and extract."}
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        url: str = arguments.get("url", "")
        if not url:
            return "ERROR: 'url' argument is required"
        # SSRF guard: the URL is attacker-influenceable (agents pass URLs found
        # in external content). ScoutHttpClient re-checks every request/redirect
        # hop; this explicit check just produces a clear error string.
        try:
            await async_validate_url(url)
        except EgressBlockedError as exc:
            logger.warning("pdf_extractor.extract(%s): blocked — %s", url, exc)
            return f"ERROR: {exc}"
        try:
            async with ScoutHttpClient(timeout=30.0) as http:
                resp = await http.get(url)
            if resp.status_code != 200:
                return f"ERROR: HTTP {resp.status_code} fetching {url}"
            text = extract_text(resp.content, first_pages=20, last_pages=5)
            return text[:_MAX_CHARS] if text else "ERROR: no text extracted from PDF"
        except Exception as exc:
            logger.warning("pdf_extractor.extract(%s): error — %s", url, exc)
            return f"ERROR: {exc}"

    return (_DEF, _impl)


register_tool("pdf_extractor.extract", _factory)
