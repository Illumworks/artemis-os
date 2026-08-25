"""Tests for the MCP-over-HTTP connector client.

Two things here are load-bearing beyond ordinary coverage:

1. **No error may carry the endpoint URL.** Vista embeds the API key in the URL,
   so a leaked URL is a leaked credential. ``test_*_never_leaks_url`` pins that.
2. **A failed tool call must not look like a success.** The Argus incident cost
   five weeks because a failure path returned a success-shaped result.

Transport shapes are modelled on the live Vista Social MCP 1.2.0 responses
observed on 2026-08-25: stateless (no ``Mcp-Session-Id``), ``application/json``,
JSON document inside a single ``text`` content block.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from artemis.connectors.mcp_http import (
    PROTOCOL_VERSION,
    VISTA_MCP_BASE_URL,
    McpError,
    McpHttpClient,
    _first_text,
    _parse_body,
    vista_endpoint_url,
)

SECRET_URL = "https://vistasocial.com/api/integration/mcp?api_key=SUPERSECRETKEY123"


# ── helpers ───────────────────────────────────────────────────────────────────


def _rpc_result(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": payload})


def _tool_text(doc: Any) -> httpx.Response:
    text = doc if isinstance(doc, str) else json.dumps(doc)
    return _rpc_result({"content": [{"type": "text", "text": text}]})


def _scripted(responses: dict[str, httpx.Response], *, record: list[dict[str, Any]] | None = None):
    """MockTransport that dispatches on the JSON-RPC method name."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if record is not None:
            record.append({"body": body, "headers": dict(request.headers)})
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        return responses.get(method, _rpc_result({}))

    return httpx.MockTransport(handler)


def _handshake_only(record: list[dict[str, Any]] | None = None):
    return _scripted(
        {
            "initialize": _rpc_result(
                {"serverInfo": {"name": "Vista Social MCP", "version": "1.2.0"}}
            )
        },
        record=record,
    )


# ── _parse_body ───────────────────────────────────────────────────────────────


def test_parse_body_plain_json() -> None:
    assert _parse_body('{"a": 1}') == {"a": 1}


def test_parse_body_sse_event() -> None:
    """Streamable HTTP may answer a one-shot request as a single SSE event."""
    raw = 'event: message\ndata: {"a": 2}\n\n'
    assert _parse_body(raw) == {"a": 2}


def test_parse_body_sse_leading_data_line() -> None:
    assert _parse_body('data: {"a": 3}') == {"a": 3}


def test_parse_body_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty response body"):
        _parse_body("   ")


# ── _first_text ───────────────────────────────────────────────────────────────


def test_first_text_finds_text_block() -> None:
    result = {"content": [{"type": "image"}, {"type": "text", "text": "hi"}]}
    assert _first_text(result) == "hi"


def test_first_text_returns_none_when_absent() -> None:
    assert _first_text({"content": [{"type": "image"}]}) is None
    assert _first_text({}) is None


def test_first_text_ignores_non_string_text() -> None:
    assert _first_text({"content": [{"type": "text", "text": 5}]}) is None


# ── vista_endpoint_url ────────────────────────────────────────────────────────


def test_vista_url_from_mcp_url_link() -> None:
    """The field the UI asks for: the whole link, key already embedded."""
    assert vista_endpoint_url({"mcp_url": SECRET_URL}) == SECRET_URL


def test_vista_url_from_bare_api_key() -> None:
    assert vista_endpoint_url({"api_key": "abc"}) == f"{VISTA_MCP_BASE_URL}?api_key=abc"


def test_vista_url_accepts_full_link_pasted_into_api_key() -> None:
    """People paste the link into whichever box they see first."""
    assert vista_endpoint_url({"api_key": SECRET_URL}) == SECRET_URL


def test_vista_url_respects_custom_base() -> None:
    got = vista_endpoint_url({"api_key": "k", "api_url": "https://example.test/mcp/"})
    assert got == "https://example.test/mcp?api_key=k"


def test_vista_url_appends_to_existing_query() -> None:
    got = vista_endpoint_url({"api_key": "k", "api_url": "https://example.test/mcp?v=2"})
    assert got == "https://example.test/mcp?v=2&api_key=k"


def test_vista_url_strips_whitespace() -> None:
    assert vista_endpoint_url({"mcp_url": f"  {SECRET_URL}  "}) == SECRET_URL


@pytest.mark.parametrize("creds", [{}, {"api_key": ""}, {"mcp_url": "   "}])
def test_vista_url_missing_credential_raises(creds: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="requires an mcp_url"):
        vista_endpoint_url(creds)


# ── handshake ─────────────────────────────────────────────────────────────────


async def test_initialize_performs_handshake_in_order() -> None:
    record: list[dict[str, Any]] = []
    async with McpHttpClient(SECRET_URL, transport=_handshake_only(record)) as mcp:
        assert mcp is not None
    methods = [r["body"]["method"] for r in record]
    assert methods == ["initialize", "notifications/initialized"]
    assert record[0]["body"]["params"]["protocolVersion"] == PROTOCOL_VERSION
    # A notification carries no id — that is what makes it a notification.
    assert "id" not in record[1]["body"]


async def test_initialize_is_idempotent() -> None:
    record: list[dict[str, Any]] = []
    async with McpHttpClient(SECRET_URL, transport=_handshake_only(record)) as mcp:
        await mcp.initialize()
        await mcp.initialize()
    assert [r["body"]["method"] for r in record].count("initialize") == 1


async def test_protocol_header_is_sent() -> None:
    record: list[dict[str, Any]] = []
    async with McpHttpClient(SECRET_URL, transport=_handshake_only(record)):
        pass
    assert record[0]["headers"]["mcp-protocol-version"] == PROTOCOL_VERSION
    assert "text/event-stream" in record[0]["headers"]["accept"]


async def test_session_id_is_echoed_when_server_issues_one() -> None:
    """Vista is stateless, but a spec-compliant server may demand the header."""
    record: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        record.append(dict(request.headers))
        if body["method"] == "initialize":
            r = _rpc_result({"serverInfo": {}})
            r.headers["Mcp-Session-Id"] = "sess-42"
            return r
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return _rpc_result({"tools": []})

    async with McpHttpClient(SECRET_URL, transport=httpx.MockTransport(handler)) as mcp:
        await mcp.list_tools()

    assert "mcp-session-id" not in record[0]  # none known yet on the first call
    assert record[-1]["mcp-session-id"] == "sess-42"


async def test_client_not_open_raises() -> None:
    mcp = McpHttpClient(SECRET_URL)
    with pytest.raises(McpError, match="client is not open"):
        await mcp.initialize()


async def test_aclose_is_safe_to_call_twice() -> None:
    mcp = McpHttpClient(SECRET_URL, transport=_handshake_only())
    await mcp.aclose()
    await mcp.aclose()


# ── list_tools / call_tool ────────────────────────────────────────────────────


async def test_list_tools_returns_catalogue() -> None:
    transport = _scripted(
        {
            "initialize": _rpc_result({"serverInfo": {}}),
            "tools/list": _rpc_result({"tools": [{"name": "getInboxStats"}, {"name": "whoami"}]}),
        }
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        tools = await mcp.list_tools()
    assert [t["name"] for t in tools] == ["getInboxStats", "whoami"]


async def test_list_tools_empty_when_absent() -> None:
    async with McpHttpClient(SECRET_URL, transport=_handshake_only()) as mcp:
        assert await mcp.list_tools() == []


async def test_call_tool_decodes_json_text_block() -> None:
    """Vista returns a JSON document inside a text block."""
    doc = {"total": 19, "breakdown": [{"key": "linkedin", "count": 9}]}
    transport = _scripted(
        {"initialize": _rpc_result({"serverInfo": {}}), "tools/call": _tool_text(doc)}
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        assert await mcp.call_tool("getInboxStats", {"profile_id": [1]}) == doc


async def test_call_tool_passes_through_prose() -> None:
    """A tool answering in prose is not an error."""
    transport = _scripted(
        {"initialize": _rpc_result({"serverInfo": {}}), "tools/call": _tool_text("not json")}
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        assert await mcp.call_tool("getVistaHelp", {}) == "not json"


async def test_call_tool_sends_name_and_arguments() -> None:
    record: list[dict[str, Any]] = []
    transport = _scripted(
        {"initialize": _rpc_result({"serverInfo": {}}), "tools/call": _tool_text({})},
        record=record,
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        await mcp.call_tool("whoami", {"a": 1})
    params = record[-1]["body"]["params"]
    assert params == {"name": "whoami", "arguments": {"a": 1}}


async def test_call_tool_falls_back_to_structured_content() -> None:
    transport = _scripted(
        {
            "initialize": _rpc_result({"serverInfo": {}}),
            "tools/call": _rpc_result({"structuredContent": {"x": 1}}),
        }
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        assert await mcp.call_tool("whoami", {}) == {"x": 1}


# ── failures must look like failures ──────────────────────────────────────────


async def test_tool_error_flag_raises_rather_than_returning_a_result() -> None:
    """The Argus rule: a failed call must never be mistakable for a success."""
    transport = _scripted(
        {
            "initialize": _rpc_result({"serverInfo": {}}),
            "tools/call": _rpc_result(
                {"isError": True, "content": [{"type": "text", "text": "quota exceeded"}]}
            ),
        }
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError, match="quota exceeded"):
            await mcp.call_tool("getInboxStats", {})


async def test_tool_error_flag_without_text_still_raises() -> None:
    transport = _scripted(
        {
            "initialize": _rpc_result({"serverInfo": {}}),
            "tools/call": _rpc_result({"isError": True}),
        }
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError, match="tool reported an error"):
            await mcp.call_tool("getInboxStats", {})


async def test_jsonrpc_error_raises() -> None:
    transport = _scripted(
        {
            "initialize": _rpc_result({"serverInfo": {}}),
            "tools/list": httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method"}},
            ),
        }
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError, match="no method"):
            await mcp.list_tools()


async def test_http_error_status_raises() -> None:
    transport = _scripted(
        {"initialize": _rpc_result({"serverInfo": {}}), "tools/list": httpx.Response(503)}
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError, match="HTTP 503"):
            await mcp.list_tools()


async def test_unparseable_body_raises() -> None:
    transport = _scripted(
        {"initialize": _rpc_result({"serverInfo": {}}), "tools/list": httpx.Response(200, text="")}
    )
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError, match="unparseable response"):
            await mcp.list_tools()


async def test_timeout_raises_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    mcp = McpHttpClient(SECRET_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(McpError, match="request timed out"):
        async with mcp:
            pass


# ── the credential must never escape ──────────────────────────────────────────


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(200, text=""),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}}),
    ],
)
async def test_errors_never_leak_url(failure: httpx.Response) -> None:
    transport = _scripted({"initialize": _rpc_result({"serverInfo": {}}), "tools/list": failure})
    async with McpHttpClient(SECRET_URL, transport=transport) as mcp:
        with pytest.raises(McpError) as excinfo:
            await mcp.list_tools()
    rendered = f"{excinfo.value} {excinfo.value.reason!r} {excinfo.value.args!r}"
    assert "SUPERSECRETKEY123" not in rendered
    assert "vistasocial.com" not in rendered


async def test_transport_exception_never_leaks_url() -> None:
    """httpx embeds the request URL in its own messages — we must not pass it on."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed connecting to {request.url}", request=request)

    mcp = McpHttpClient(SECRET_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(McpError) as excinfo:
        async with mcp:
            pass
    assert "SUPERSECRETKEY123" not in str(excinfo.value)
    assert excinfo.value.reason == "transport error (ConnectError)"


async def test_handshake_log_does_not_include_url(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        async with McpHttpClient(SECRET_URL, transport=_handshake_only()):
            pass
    assert "SUPERSECRETKEY123" not in caplog.text
    assert "Vista Social MCP" in caplog.text
