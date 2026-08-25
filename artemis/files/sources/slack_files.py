"""Download a file a Slack event told us about.

Slack's `files[]` array carries a `url_private`, which is NOT a public link: it
needs the bot token as a bearer credential and requires `files:read`. Nothing in
this repo could fetch one before 2026-08-25 -- several modules were deliberately
built to route around the missing scope.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from artemis.files.extract import extract
from artemis.files.extract.base import (
    MAX_DOWNLOAD_BYTES,
    AccessDeniedError,
    ExtractedFile,
    FileTooLargeError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _looks_like_slack_login_page(payload: bytes, content_type: str) -> bool:
    """Detect Slack answering a bad token with an HTML sign-in page.

    Slack does NOT return 401 for an unauthorised `url_private` fetch -- it
    returns 200 with the workspace login page. Taken at face value that becomes a
    "successfully extracted" HTML document full of Slack's own chrome, which an
    agent would then summarise as if it were the file. This check is the only
    thing standing between a missing scope and a confidently wrong answer.
    """
    if "text/html" not in content_type.lower():
        return False
    head = payload[:2048].lower()
    return b"<!doctype html" in head or b"<html" in head


async def fetch_slack_file(
    file_obj: dict[str, Any],
    *,
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> ExtractedFile:
    """Download one Slack file and return its extraction.

    `file_obj` is an entry from the event's `files[]` array.

    Raises `AccessDeniedError` when the token cannot read the file, and
    `FileTooLargeError` when Slack reports a size over the ceiling -- checked
    from the metadata BEFORE downloading, so an enormous upload costs nothing.
    """
    filename = str(file_obj.get("name") or file_obj.get("title") or "untitled")
    mimetype = str(file_obj.get("mimetype") or "")
    url = str(file_obj.get("url_private_download") or file_obj.get("url_private") or "")
    permalink = str(file_obj.get("permalink") or "")
    declared_size = int(file_obj.get("size") or 0)

    # A Google Drive file shared into Slack arrives as an external stub with no
    # downloadable bytes. Say so precisely rather than failing as a bad download:
    # the fix is to paste the Drive link, which the Google path can read.
    if str(file_obj.get("mode") or "") in {"external", "hosted"} and not url:
        raise AccessDeniedError(
            f"{filename} is linked from an external service rather than uploaded to "
            "Slack, so there are no file bytes to read. Share the original link instead."
        )

    if not url:
        raise AccessDeniedError(
            f"{filename} arrived without a download URL, so it could not be read."
        )

    if declared_size > MAX_DOWNLOAD_BYTES:
        raise FileTooLargeError(
            f"{filename} is {declared_size:,} bytes, over the "
            f"{MAX_DOWNLOAD_BYTES:,}-byte limit, so it was not downloaded."
        )

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        response = await http.get(url, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        raise AccessDeniedError(
            f"{filename} could not be downloaded from Slack ({type(exc).__name__})."
        ) from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code in (401, 403):
        raise AccessDeniedError(
            f"Slack refused access to {filename} (HTTP {response.status_code}). This is a "
            "permissions problem, not a problem with the file: the bot needs files:read "
            "and must be a member of the conversation."
        )
    if response.status_code >= 400:
        raise AccessDeniedError(
            f"Slack returned HTTP {response.status_code} for {filename}, so it was not read."
        )

    payload = response.content
    if _looks_like_slack_login_page(payload, response.headers.get("content-type", "")):
        raise AccessDeniedError(
            f"Slack returned its sign-in page instead of {filename}, which means the token "
            "is not authorised to read it. The bot needs files:read and membership of the "
            "conversation. (Slack answers 200 here, not 401.)"
        )

    return extract(
        payload,
        filename=filename,
        mimetype=mimetype,
        source="slack",
        source_url=permalink,
    )
