"""Unit tests for Writing Studio: move new Google Doc into a configured Drive folder.

All HTTP is mocked via httpx.MockTransport — no DB, no .env required.
"""

from __future__ import annotations

import pytest
import httpx

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DOC_ID = "doc-new-abc123"
_FOLDER_ID = "19Dxp0xTwz_owGorQAc_BwSXmCJO-pPeP"
_ACCESS_TOKEN = "test-access-token"


def _make_transport(
    *,
    folder_move_status: int = 200,
    raise_on_move: bool = False,
    record: dict | None = None,
) -> httpx.MockTransport:
    """Build a mock transport for export_google_document.

    Handles:
    - POST docs.googleapis.com/v1/documents  → create doc
    - PATCH www.googleapis.com/drive/v3/files/{id}  → move (or rename)
    - GET  docs.googleapis.com/v1/documents/{id}    → fetch doc for content replace
    - POST docs.googleapis.com/v1/documents/{id}:batchUpdate → content write
    """
    if record is None:
        record = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # Create doc
        if url == "https://docs.googleapis.com/v1/documents":
            return httpx.Response(
                200, json={"documentId": _DOC_ID, "title": "Test Doc"}
            )

        # Drive files.update (move or rename) — identified by PATCH on drive URL
        if url.startswith(
            f"https://www.googleapis.com/drive/v3/files/{_DOC_ID}"
        ) and request.method == "PATCH":
            params = dict(request.url.params)
            record["drive_patch_params"] = params
            record["drive_patch_called"] = True
            record["drive_auth"] = request.headers.get("Authorization")
            if raise_on_move:
                raise httpx.ConnectError("network error")
            return httpx.Response(
                folder_move_status,
                json={"id": _DOC_ID, "parents": [_FOLDER_ID]}
                if folder_move_status < 300
                else {},
                text="" if folder_move_status < 300 else "403 Forbidden",
            )

        # Fetch doc content (for _replace_google_document_content)
        if url == f"https://docs.googleapis.com/v1/documents/{_DOC_ID}":
            return httpx.Response(
                200,
                json={
                    "documentId": _DOC_ID,
                    "title": "Test Doc",
                    "body": {"content": [{"endIndex": 1}]},
                },
            )

        # batchUpdate (content write)
        if url == f"https://docs.googleapis.com/v1/documents/{_DOC_ID}:batchUpdate":
            return httpx.Response(200, json={"replies": []})

        raise AssertionError(f"Unexpected request: {request.method} {url}")

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_move_patch_issued_when_folder_id_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When folder_id is set + create succeeds, Drive PATCH is issued with correct params."""
    from artemis.google_docs import client as gc
    from artemis.config import settings

    record: dict = {}
    monkeypatch.setattr(
        gc,
        "_make_http_client",
        lambda timeout=15.0: httpx.AsyncClient(
            transport=_make_transport(record=record), timeout=timeout
        ),
    )
    monkeypatch.setattr(settings, "writing_studio_docs_folder_id", _FOLDER_ID)

    result = await gc.export_google_document(
        access_token=_ACCESS_TOKEN,
        title="Test Doc",
        content="Hello world.",
        document_id=None,
    )

    assert result.document_id == _DOC_ID
    assert result.created is True
    assert record.get("drive_patch_called") is True
    params = record["drive_patch_params"]
    assert params["addParents"] == _FOLDER_ID
    assert params["removeParents"] == "root"
    assert params["supportsAllDrives"] == "true"
    assert record["drive_auth"] == f"Bearer {_ACCESS_TOKEN}"


async def test_doc_returned_when_move_fails_with_non_2xx(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When Drive PATCH returns non-2xx, create_document still returns the doc (no raise)."""
    import logging
    from artemis.google_docs import client as gc
    from artemis.config import settings

    record: dict = {}
    monkeypatch.setattr(
        gc,
        "_make_http_client",
        lambda timeout=15.0: httpx.AsyncClient(
            transport=_make_transport(folder_move_status=403, record=record), timeout=timeout
        ),
    )
    monkeypatch.setattr(settings, "writing_studio_docs_folder_id", _FOLDER_ID)

    with caplog.at_level(logging.WARNING, logger="artemis.google_docs.client"):
        result = await gc.export_google_document(
            access_token=_ACCESS_TOKEN,
            title="Test Doc",
            content="Hello world.",
            document_id=None,
        )

    assert result.document_id == _DOC_ID
    assert result.created is True
    assert record.get("drive_patch_called") is True
    assert "failed to move doc" in caplog.text.lower() or "403" in caplog.text


async def test_doc_returned_when_move_raises_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the Drive PATCH raises a network exception, create_document still returns the doc."""
    import logging
    from artemis.google_docs import client as gc
    from artemis.config import settings

    monkeypatch.setattr(
        gc,
        "_make_http_client",
        lambda timeout=15.0: httpx.AsyncClient(
            transport=_make_transport(raise_on_move=True), timeout=timeout
        ),
    )
    monkeypatch.setattr(settings, "writing_studio_docs_folder_id", _FOLDER_ID)

    with caplog.at_level(logging.WARNING, logger="artemis.google_docs.client"):
        result = await gc.export_google_document(
            access_token=_ACCESS_TOKEN,
            title="Test Doc",
            content="Hello world.",
            document_id=None,
        )

    assert result.document_id == _DOC_ID
    assert result.created is True
    assert "exception moving doc" in caplog.text.lower() or "network error" in caplog.text


async def test_no_move_patch_when_folder_id_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When folder_id is empty string, no Drive PATCH is issued."""
    from artemis.google_docs import client as gc
    from artemis.config import settings

    record: dict = {}
    monkeypatch.setattr(
        gc,
        "_make_http_client",
        lambda timeout=15.0: httpx.AsyncClient(
            transport=_make_transport(record=record), timeout=timeout
        ),
    )
    monkeypatch.setattr(settings, "writing_studio_docs_folder_id", "")

    result = await gc.export_google_document(
        access_token=_ACCESS_TOKEN,
        title="Test Doc",
        content="Hello world.",
        document_id=None,
    )

    assert result.document_id == _DOC_ID
    assert result.created is True
    assert not record.get("drive_patch_called"), "Drive PATCH must NOT be issued when folder_id is empty"
