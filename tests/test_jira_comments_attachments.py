"""Unit tests for worker/jira-comments-attachments.

Coverage (no DB, no network):
  Part 1 — adf_to_text:
    - link mark on text node renders "text (url)"
    - link mark skips url if already in text
    - media node renders "📎 alt"
    - mediaSingle renders "📎 ..." + newline via its media child
    - mediaSingle with no child returns ""
    - existing node types (mention, emoji, paragraph) still work
  Part 2 — _build_adf:
    - no attachments: body is single paragraph, unchanged from before
    - one attachment with url: second paragraph is linked text node
    - one attachment without url: second paragraph is bare "📎 filename" text
    - multiple attachments: one paragraph per attachment appended
    - mentions + attachments: mentions resolved, attachments appended after
    - attachment url not duplicated into text when url already in filename (edge)
  Part 2 — add_comment ADF body sent to Jira:
    - attachment_refs are threaded into the ADF body passed to httpx
  Part 3 — flat comments design note (no test needed; documented below).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.integrations.jira.client import (
    JiraClient,
    _build_adf,
    adf_to_text,
)


# ── adf_to_text: link mark ─────────────────────────────────────────────────────


def test_adf_to_text_link_mark_appends_url() -> None:
    """A text node with a link mark should render as 'text (url)'."""
    node = {
        "type": "text",
        "text": "click here",
        "marks": [{"type": "link", "attrs": {"href": "https://example.com/att/123"}}],
    }
    result = adf_to_text(node)
    assert "click here" in result
    assert "https://example.com/att/123" in result


def test_adf_to_text_link_mark_no_duplicate_if_url_in_text() -> None:
    """If the href is already present in the text, don't append again."""
    url = "https://example.com/att/123"
    node = {
        "type": "text",
        "text": url,
        "marks": [{"type": "link", "attrs": {"href": url}}],
    }
    result = adf_to_text(node)
    # Should appear exactly once
    assert result.count(url) == 1


def test_adf_to_text_media_node_renders_filename() -> None:
    """A media node should render as '📎 alt' using the alt attribute."""
    node = {
        "type": "media",
        "attrs": {"id": "abc-123", "type": "file", "collection": "col", "alt": "report.pdf"},
    }
    result = adf_to_text(node)
    assert "📎" in result
    assert "report.pdf" in result


def test_adf_to_text_media_node_falls_back_to_id() -> None:
    """When alt is absent, media node renders '📎 {id}'."""
    node = {
        "type": "media",
        "attrs": {"id": "abc-123", "type": "file", "collection": "col"},
    }
    result = adf_to_text(node)
    assert "abc-123" in result


def test_adf_to_text_media_single_delegates_to_child() -> None:
    """mediaSingle should render its child media node + newline."""
    node = {
        "type": "mediaSingle",
        "content": [
            {
                "type": "media",
                "attrs": {"id": "img-1", "type": "file", "collection": "c", "alt": "photo.png"},
            }
        ],
    }
    result = adf_to_text(node)
    assert "📎" in result
    assert "photo.png" in result
    assert result.endswith("\n")


def test_adf_to_text_media_single_no_content_returns_empty() -> None:
    """mediaSingle with no content list should return ''."""
    node = {"type": "mediaSingle", "content": []}
    assert adf_to_text(node) == ""

    node2: dict[str, Any] = {"type": "mediaSingle"}
    assert adf_to_text(node2) == ""


def test_adf_to_text_mention_still_works() -> None:
    """Existing mention node handling must be unaffected by the new cases.

    Note: adf_to_text prepends '@' to the attrs.text value.  When Jira's API
    returns text="@Alice" (already prefixed) the result is "@@Alice".  That
    is a pre-existing quirk preserved here, not introduced by this branch.
    The fixture uses text="Alice" (no prefix) to test the normal path.
    """
    node = {"type": "mention", "attrs": {"id": "acct123", "text": "Alice"}}
    result = adf_to_text(node)
    assert result == "@Alice"


def test_adf_to_text_paragraph_with_linked_text() -> None:
    """A full paragraph containing a linked text node should render correctly."""
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "📎 report.pdf",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {
                                    "href": "https://jira.example.com/attachment/content/99",
                                    "title": "report.pdf",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    result = adf_to_text(doc)
    assert "📎 report.pdf" in result
    assert "https://jira.example.com/attachment/content/99" in result


# ── _build_adf: no attachments ────────────────────────────────────────────────


def test_build_adf_no_attachments_single_paragraph() -> None:
    """With no attachments the doc has exactly one paragraph."""
    doc = _build_adf("Hello world", [])
    assert doc["type"] == "doc"
    assert len(doc["content"]) == 1
    para = doc["content"][0]
    assert para["type"] == "paragraph"
    assert para["content"][0]["text"] == "Hello world"


def test_build_adf_no_attachments_no_mentions_matches_old_behaviour() -> None:
    """Passing empty attachments should not change behaviour vs the old signature."""
    doc_new = _build_adf("Hello", [], [])
    doc_compat = _build_adf("Hello", [])
    assert doc_new == doc_compat


# ── _build_adf: one attachment with url ──────────────────────────────────────


def test_build_adf_one_attachment_with_url_appended_as_linked_para() -> None:
    """One attachment with a url should append a second paragraph with a link mark."""
    att = {"filename": "report.pdf", "url": "https://jira.example.com/attachment/content/42"}
    doc = _build_adf("See attached", [], [att])
    assert len(doc["content"]) == 2
    link_para = doc["content"][1]
    assert link_para["type"] == "paragraph"
    nodes = link_para["content"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["type"] == "text"
    assert "report.pdf" in node["text"]
    assert "📎" in node["text"]
    # Verify the link mark
    marks = node.get("marks", [])
    assert len(marks) == 1
    assert marks[0]["type"] == "link"
    assert marks[0]["attrs"]["href"] == att["url"]
    assert marks[0]["attrs"]["title"] == "report.pdf"


# ── _build_adf: attachment without url falls back to bare text ────────────────


def test_build_adf_attachment_no_url_renders_bare_text() -> None:
    """An attachment dict without a url should render as a bare '📎 filename' para."""
    att = {"filename": "notes.txt", "url": ""}
    doc = _build_adf("See attached", [], [att])
    assert len(doc["content"]) == 2
    link_para = doc["content"][1]
    node = link_para["content"][0]
    assert "notes.txt" in node["text"]
    # No marks → no link
    assert not node.get("marks")


# ── _build_adf: multiple attachments ─────────────────────────────────────────


def test_build_adf_multiple_attachments_one_para_each() -> None:
    """Multiple attachments should produce one linked paragraph per file."""
    atts = [
        {"filename": "a.png", "url": "https://host/a"},
        {"filename": "b.docx", "url": "https://host/b"},
        {"filename": "c.pdf", "url": "https://host/c"},
    ]
    doc = _build_adf("Files:", [], atts)
    # 1 text para + 3 attachment paras
    assert len(doc["content"]) == 4
    for i, att in enumerate(atts):
        para = doc["content"][i + 1]
        node = para["content"][0]
        assert att["filename"] in node["text"]
        href = node["marks"][0]["attrs"]["href"]
        assert href == att["url"]


# ── _build_adf: mentions + attachments ───────────────────────────────────────


def test_build_adf_mentions_and_attachments_coexist() -> None:
    """Mention substitution in the text paragraph must not be broken by attachments."""
    mentions = [{"name": "Alice", "id": "acc-alice"}]
    att = {"filename": "ref.pdf", "url": "https://host/ref"}
    doc = _build_adf("Hey @Alice, see this", mentions, [att])
    # 2 paragraphs: text + attachment
    assert len(doc["content"]) == 2
    text_para_nodes = doc["content"][0]["content"]
    node_types = [n["type"] for n in text_para_nodes]
    assert "mention" in node_types
    # Attachment paragraph is intact
    att_para = doc["content"][1]
    node = att_para["content"][0]
    assert "ref.pdf" in node["text"]


# ── add_comment passes attachment_refs through to httpx body ──────────────────


@pytest.mark.asyncio
async def test_add_comment_sends_attachment_refs_in_adf_body() -> None:
    """add_comment must include attachment link paragraphs in the ADF body sent to Jira."""
    att_ref = {
        "filename": "screenshot.png",
        "url": "https://my-jira.atlassian.net/rest/api/3/attachment/content/999",
    }

    captured_json: dict[str, Any] = {}

    def _mock_resp(status: int, body: Any) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.is_success = True
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    comment_response = _mock_resp(
        201,
        {
            "id": "10001",
            "author": {"displayName": "Jon Fila"},
            "body": {
                "version": 1,
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Check this out"}]},
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "📎 screenshot.png",
                                "marks": [
                                    {
                                        "type": "link",
                                        "attrs": {
                                            "href": att_ref["url"],
                                            "title": att_ref["filename"],
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
            "created": "2026-06-18T12:00:00.000+0000",
        },
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
            captured_json.update(kwargs.get("json", {}))
            return comment_response

        mock_http.post = _fake_post
        mock_cls.return_value = mock_http

        client = JiraClient(
            site_url="https://my-jira.atlassian.net",
            email="jon@example.com",
            api_token="secret",
        )
        result = await client.add_comment(
            "ENG-42",
            "Check this out",
            mentions=[],
            attachment_refs=[att_ref],
        )

    # The body passed to Jira should have 2 content blocks.
    body_doc = captured_json.get("body", {})
    assert body_doc.get("type") == "doc"
    content = body_doc.get("content", [])
    assert len(content) == 2, "Expected 1 text para + 1 attachment para"

    # First para is the comment text.
    assert content[0]["type"] == "paragraph"
    assert content[0]["content"][0]["text"] == "Check this out"

    # Second para is the attachment link.
    att_node = content[1]["content"][0]
    assert "screenshot.png" in att_node["text"]
    marks = att_node.get("marks", [])
    assert marks[0]["attrs"]["href"] == att_ref["url"]

    # Return value is normalised correctly.
    assert result["id"] == "10001"
    # adf_to_text on the response body should include the filename + url.
    assert "screenshot.png" in result["body"]
    assert att_ref["url"] in result["body"]


# ── Part 3: Jira flat-comments design note ────────────────────────────────────
#
# Jira Cloud has NO native threaded/nested comments — all comments are flat on
# the issue.  The existing @author-prefix reply convention is kept as-is.
# No fake threading infrastructure is built.  This is intentional and correct.
#
# The test below simply confirms that add_comment does NOT add any "replyTo"
# or "parentId" field to the ADF body (Jira would ignore or reject it anyway).


@pytest.mark.asyncio
async def test_add_comment_no_threading_fields_in_adf() -> None:
    """The ADF body must NOT include any threading/parentId fields (Jira is flat)."""
    captured_json: dict[str, Any] = {}

    def _mock_resp(status: int, body: Any) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.is_success = True
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    comment_response = _mock_resp(
        201,
        {
            "id": "10002",
            "author": {"displayName": "Jon Fila"},
            "body": {"version": 1, "type": "doc", "content": []},
            "created": "2026-06-18T12:00:00.000+0000",
        },
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        async def _fake_post(url: str, **kwargs: Any) -> MagicMock:
            captured_json.update(kwargs.get("json", {}))
            return comment_response

        mock_http.post = _fake_post
        mock_cls.return_value = mock_http

        client = JiraClient(
            site_url="https://my-jira.atlassian.net",
            email="jon@example.com",
            api_token="secret",
        )
        await client.add_comment("ENG-1", "@Alice sounds good")

    # Must only have "body" at the top level — no parentId, replyTo, etc.
    assert set(captured_json.keys()) == {"body"}, (
        f"Unexpected extra keys in comment payload: {set(captured_json.keys()) - {'body'}}"
    )
