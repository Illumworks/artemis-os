"""Tool: federal_register.search

Search the Federal Register for documents (regulations, notices, rules) — no
API key required.

Uses the public v1 ``documents.json`` endpoint (GET). Results live under the
``results`` array; each carries ``title``, ``document_number``,
``publication_date``, ``agencies`` (array of objects), ``html_url`` and
``abstract``.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
_DEFAULT_PER_PAGE = 20

_RELEVANCE_TERMS = (
    "literacy",
    "reading",
    "education",
    "tutor",
    "dyslexia",
    "student",
    "school",
    "esea",
)


def _is_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _RELEVANCE_TERMS)


def _agency_names(agencies: Any) -> list[str]:
    names: list[str] = []
    if isinstance(agencies, list):
        for ag in agencies:
            if isinstance(ag, dict):
                name = str(ag.get("name", "")).strip()
                if name:
                    names.append(name)
    return names


def _parse_results(payload: dict[str, Any], per_page: int) -> list[dict[str, Any]]:
    results = payload.get("results") or []
    out: list[dict[str, Any]] = []
    for doc in results:
        title = str(doc.get("title", "")).strip()
        abstract = str(doc.get("abstract") or "").strip()
        if (title or abstract) and not _is_relevant(f"{title} {abstract}"):
            continue
        out.append(
            {
                "title": title,
                "document_number": str(doc.get("document_number", "")).strip(),
                "publication_date": str(doc.get("publication_date", "")).strip(),
                "agencies": _agency_names(doc.get("agencies")),
                "html_url": str(doc.get("html_url", "")).strip(),
                "abstract": abstract,
            }
        )
        if len(out) >= per_page:
            break
    return out


_DEF = Tool(
    name="federal_register.search",
    description=(
        "Search the Federal Register (public v1 API, no key) for regulations "
        "and notices matching a term. Returns up to `per_page` items as JSON "
        "[{title, document_number, publication_date, agencies, html_url, "
        "abstract}], filtered to literacy/education relevance. Returns [] on "
        "any error (graceful empty)."
    ),
    input_schema={
        "type": "object",
        "required": ["term"],
        "properties": {
            "term": {
                "type": "string",
                "description": "Full-text search term, e.g. 'literacy education'.",
            },
            "per_page": {
                "type": "integer",
                "description": "Max documents to return (default 20).",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        term: str = str(arguments.get("term", "")).strip()
        if not term:
            return json.dumps([])
        per_page = int(arguments.get("per_page", _DEFAULT_PER_PAGE) or _DEFAULT_PER_PAGE)

        params = {"conditions[term]": term, "per_page": per_page}
        try:
            async with ScoutHttpClient(timeout=20.0) as http:
                resp = await http.get(_DOCUMENTS_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "federal_register.search: HTTP %d from documents.json", resp.status_code
                )
                return json.dumps([])
            payload = resp.json()
        except Exception as exc:
            logger.warning("federal_register.search(%r): error — %s", term, exc)
            return json.dumps([])

        return json.dumps(_parse_results(payload, per_page))

    return (_DEF, _impl)


register_tool("federal_register.search", _factory)
