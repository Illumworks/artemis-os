"""Fetch the crisis-content Google Doc via the HTML export endpoint.

Background: ``docs/crisis-content-approval-pipeline.md``. Chip values
(the dropdown "statuses" Jen sets, e.g. Platform / Asset for review /
Copy review) are opaque through ``documents.get`` -- the Docs API returns a
chip as ``{"startIndex": N, "endIndex": N+1}`` with no content, no type, no
value at all. This is verified, not assumed; do not go back to
``documents.get`` for this doc.

The HTML export endpoint renders chip values as plain text while preserving
``<table>``/``<tr>``/``<td>`` structure and ``href``s, which is what makes
this parseable at all. It is undocumented (not part of the Drive/Docs API)
and honored today by observation only -- everything in this module is
isolated behind ``fetch_crisis_content_export_html`` so that if the endpoint
ever changes shape or stops honoring a bearer token, the fallback (asking
Jen to type a plain-text status instead of using a chip) touches only this
one function. ``parser.py`` never imports this module or ``httpx``.

This module performs no writes of any kind. It does not call any Docs or
Drive mutation endpoint, and it must not: the target doc belongs to an
external vendor (Jen, DigiGeeks) who does not know this pipeline reads it.

Token handling is deliberately NOT reinvented here -- mirror
``artemis/routes/google_docs.py`` (``_valid_access_token``): whoever calls
this function resolves/refreshes a ``purpose="personal"`` credential first
and passes in the resulting bearer token. This module only ever sees a
already-valid access token and returns HTML; it has no DB session and no
credential-refresh logic of its own.
"""

from __future__ import annotations

import httpx

from artemis.crisis_content.parser import SignInPageError, looks_like_sign_in_page

__all__ = ["TARGET_DOCUMENT_ID", "fetch_crisis_content_export_html"]

# The live target doc ("Draft Amira Social Content Plan"), for callers'
# convenience/documentation. Not enforced -- the fetch function below takes
# any document_id, since the signature-based card extraction is what makes
# this tab-agnostic and doc-agnostic to begin with.
TARGET_DOCUMENT_ID = "1IcXikVORzIfzKxsU57zoKTf2jr5rqmIkNHmHP0EAPUw"

_EXPORT_URL_TEMPLATE = "https://docs.google.com/document/d/{document_id}/export?format=html"


async def fetch_crisis_content_export_html(
    *,
    document_id: str,
    access_token: str,
    timeout: float = 20.0,
) -> str:
    """GET the doc's HTML export and return the raw HTML body.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response (loud by
    default, same as the rest of the Google Docs client -- see
    ``artemis/google_docs/client.py``), and ``SignInPageError`` if the 200
    body is actually a Google sign-in page rather than document content.
    ``follow_redirects=True`` is required; the export endpoint redirects.
    """
    url = _EXPORT_URL_TEMPLATE.format(document_id=document_id)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()

    html = response.text
    if looks_like_sign_in_page(html):
        raise SignInPageError(
            "GET .../export?format=html returned 200 with a Google sign-in "
            "page body instead of document HTML -- the access token is "
            "likely invalid or expired."
        )
    return html
