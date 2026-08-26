"""Tests for the Slack side of attachment intake.

Seeded to match the shape production ACTUALLY has, per CLAUDE.md: Slack labels
uploads with unhelpful mimetypes, answers an unauthorised file fetch with a 200
and an HTML login page rather than a 401, and sends `file_share` events whose
`text` is empty.
"""

from __future__ import annotations

import httpx
import pytest

from artemis.files.extract.base import AccessDeniedError, FileTooLargeError
from artemis.files.service import AttachmentResult, render_for_prompt
from artemis.files.sources.slack_files import fetch_slack_file

TSV = b"District\tState\nAustin ISD\tTX\nDallas ISD\tTX\n"


def _file_obj(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "F123",
        "name": "leads.tsv",
        # Slack really does send text/plain for a .tsv — routing must not trust it.
        "mimetype": "text/plain",
        "size": len(TSV),
        "url_private_download": "https://files.slack.com/files-pri/T1-F123/leads.tsv",
        "permalink": "https://amira.slack.com/files/U1/F123/leads.tsv",
    }
    base.update(overrides)
    return base


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_downloads_and_extracts_a_tsv() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        return httpx.Response(200, content=TSV, headers={"content-type": "text/plain"})

    async with _client(handler) as client:
        result = await fetch_slack_file(_file_obj(), access_token="xoxb-test", client=client)

    assert result.kind == "tabular"
    assert result.tables[0].columns == ["District", "State"]
    assert result.source_url.endswith("leads.tsv")


@pytest.mark.asyncio
async def test_slack_login_page_is_reported_as_permissions_not_as_content() -> None:
    """Slack answers an unauthorised file fetch with 200 + its sign-in HTML.

    Taken at face value that extracts cleanly as an HTML document, and the agent
    would summarise Slack's own login chrome as though it were the file.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<!DOCTYPE html><html><body>Sign in to Slack</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    async with _client(handler) as client:
        with pytest.raises(AccessDeniedError) as excinfo:
            await fetch_slack_file(_file_obj(), access_token="bad", client=client)

    assert "files:read" in excinfo.value.reason
    assert "not authorised" in excinfo.value.reason


@pytest.mark.asyncio
async def test_403_names_permissions_rather_than_blaming_the_file() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "not_allowed"})

    async with _client(handler) as client:
        with pytest.raises(AccessDeniedError) as excinfo:
            await fetch_slack_file(_file_obj(), access_token="x", client=client)

    assert "not a problem with the file" in excinfo.value.reason


@pytest.mark.asyncio
async def test_oversize_file_is_refused_from_metadata_without_downloading() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"x")

    async with _client(handler) as client:
        with pytest.raises(FileTooLargeError):
            await fetch_slack_file(
                _file_obj(size=200 * 1024 * 1024), access_token="x", client=client
            )

    assert called is False, "an oversize file must not be downloaded at all"


@pytest.mark.asyncio
async def test_external_drive_stub_says_to_share_the_link() -> None:
    """A Drive file shared into Slack has no bytes; the remedy is the link."""
    async with _client(lambda r: httpx.Response(200)) as client:
        with pytest.raises(AccessDeniedError) as excinfo:
            await fetch_slack_file(
                _file_obj(mode="external", url_private_download="", url_private=""),
                access_token="x",
                client=client,
            )

    assert "Share the original link" in excinfo.value.reason


def test_failures_are_rendered_into_the_prompt_not_omitted() -> None:
    """An unreadable file must reach the agent as a stated failure."""
    rendered = render_for_prompt(
        [
            AttachmentResult(
                label="q3.xlsx",
                failure_kind="AccessDeniedError",
                failure_reason="q3.xlsx is not shared with the agent's Google account.",
            )
        ]
    )
    assert "FILES YOU COULD NOT READ" in rendered
    assert "not shared with" in rendered
    assert "do not guess" in rendered.lower()


def test_empty_result_renders_nothing() -> None:
    assert render_for_prompt([]) == ""
