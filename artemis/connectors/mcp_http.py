"""Minimal MCP client over streamable HTTP.

Some vendors expose their API only as a remote MCP server rather than a plain
REST surface — Vista Social is the first (its REST API is a paid add-on we do
not hold; the MCP endpoint is our only programmatic access). This module lets
backend code call such a server directly, with no Claude Code CLI and no agent
in the loop.

That last point is deliberate. ``docs`` and CLAUDE.md record the Argus failure:
an agent reported ``{"status": "dispatched"}`` for work that never happened, and
nobody noticed for five weeks. Ingestion jobs must therefore call the vendor
themselves and be judged on the rows they write, never on an agent's account of
what it did.

Transport notes (verified live against Vista Social MCP 1.2.0, 2026-08-25):

- One POST per JSON-RPC message to a single endpoint URL.
- The server may answer ``application/json`` *or* ``text/event-stream``; the
  spec permits either for a request that yields one response, so both are
  handled here.
- ``Mcp-Session-Id`` is optional. Vista does not issue one (it is stateless),
  but if a server does we echo it back on subsequent calls as the spec requires.
- The handshake is ``initialize`` → ``notifications/initialized`` → calls.

SECURITY — read before touching this file. Vista embeds the API key in the
endpoint *URL* (``…/mcp?api_key=…``). The URL is therefore a secret in full.
Nothing here may log it, put it in an exception message, or return it. That is
why :class:`McpError` carries only a method name and status, and why the client
stores the URL privately. ``agent_traces`` and the app log are both places this
must never reach.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from artemis.logging_setup import install_secret_redaction

logger = logging.getLogger(__name__)

#: Protocol revision we advertise in ``initialize``.
PROTOCOL_VERSION = "2025-06-18"

#: Vista Social's MCP endpoint, minus the ``?api_key=`` the credential supplies.
VISTA_MCP_BASE_URL = "https://vistasocial.com/api/integration/mcp"

_DEFAULT_TIMEOUT = 45.0


class McpError(RuntimeError):
    """An MCP call failed.

    The message deliberately names only the *method* and a status/reason — never
    the endpoint URL, which carries the API key for vendors like Vista.
    """

    def __init__(self, method: str, reason: str) -> None:
        self.method = method
        self.reason = reason
        super().__init__(f"MCP call {method!r} failed: {reason}")


def _parse_body(raw: str) -> dict[str, Any]:
    """Parse a JSON-RPC response body that may be plain JSON or a single SSE event.

    Streamable HTTP allows the server to reply with ``text/event-stream`` even
    for a one-shot request, in which case the payload arrives as ``data:`` lines.
    """
    text = raw.strip()
    if text.startswith("data:") or "\ndata:" in text:
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[len("data:") :].strip()
                break
    if not text:
        raise ValueError("empty response body")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


class McpHttpClient:
    """A single MCP session against one remote HTTP endpoint.

    Use as an async context manager so the handshake runs once and the
    connection is reused across calls::

        async with McpHttpClient(url) as mcp:
            data = await mcp.call_tool("getInboxStats", {...})
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # httpx logs "HTTP Request: POST <full url>" at INFO, which would print
        # the key for endpoints that carry it in the query string. Installing
        # the redaction filter here (it is idempotent) means the guarantee holds
        # for scripts and jobs that never run the app's lifespan.
        install_secret_redaction()
        # Private: carries the API key for Vista-style endpoints. Never expose.
        self._url = endpoint_url
        self._timeout = timeout
        self._transport = transport
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._initialized = False

    async def __aenter__(self) -> McpHttpClient:
        self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._initialized = False

    # ── transport ─────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(
        self, method: str, params: dict[str, Any] | None, *, notify: bool
    ) -> dict[str, Any] | None:
        if self._client is None:
            raise McpError(method, "client is not open")

        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = 1

        try:
            response = await self._client.post(self._url, json=body, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise McpError(method, "request timed out") from exc
        except httpx.HTTPError as exc:
            # str(exc) can embed the request URL — use the class name only.
            raise McpError(method, f"transport error ({type(exc).__name__})") from exc

        # A server that issues a session id expects it echoed on later calls.
        issued = response.headers.get("Mcp-Session-Id")
        if issued:
            self._session_id = issued

        if response.status_code >= 400:
            raise McpError(method, f"HTTP {response.status_code}")

        if notify:
            return None

        try:
            payload = _parse_body(response.text)
        except ValueError as exc:
            raise McpError(method, f"unparseable response ({exc})") from exc

        if "error" in payload:
            err = payload["error"] or {}
            raise McpError(
                method, f"{err.get('message', 'unknown error')} (code {err.get('code')})"
            )

        result: dict[str, Any] = payload.get("result") or {}
        return result

    # ── protocol ──────────────────────────────────────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        """Run the MCP handshake. Idempotent within one client."""
        if self._initialized:
            return {}
        result = await self._post(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "artemis", "version": "1"},
            },
            notify=False,
        )
        await self._post("notifications/initialized", {}, notify=True)
        self._initialized = True
        server = (result or {}).get("serverInfo", {})
        logger.info(
            "mcp handshake ok: server=%s version=%s",
            server.get("name", "?"),
            server.get("version", "?"),
        )
        return result or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool catalogue."""
        result = await self._post("tools/list", {}, notify=False)
        tools: list[dict[str, Any]] = (result or {}).get("tools", [])
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one tool and return its decoded payload.

        MCP returns content blocks. Vista puts a JSON document in a ``text``
        block, so we decode it when it parses and hand back the raw string when
        it does not — a tool that answers in prose is not an error.

        Raises :class:`McpError` when the server flags ``isError``. A tool that
        failed must never look like one that succeeded.
        """
        result = await self._post(
            "tools/call", {"name": name, "arguments": arguments}, notify=False
        )
        result = result or {}

        if result.get("isError"):
            raise McpError(f"tools/call:{name}", _first_text(result) or "tool reported an error")

        text = _first_text(result)
        if text is None:
            return result.get("structuredContent")
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text


def _first_text(result: dict[str, Any]) -> str | None:
    """Pull the first ``text`` content block out of a tools/call result."""
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            value = block.get("text")
            if isinstance(value, str):
                return value
    return None


def vista_endpoint_url(credentials: dict[str, str]) -> str:
    """Build Vista's MCP endpoint URL from stored connector credentials.

    Vista hands its users a single *link* with the key already embedded, so
    ``mcp_url`` is the field the UI asks for and the one people actually have.
    A bare ``api_key`` is accepted too, for rotation without re-pasting a URL.

    Pasting the full link into either field works: a value starting with
    ``http`` is treated as the endpoint. That leniency is deliberate — the
    alternative is a connector that silently builds a nonsense URL and fails
    with an opaque HTTP error at call time.

    The returned string contains the API key — treat it as the secret it is.
    """
    raw_url = (credentials.get("mcp_url") or "").strip()
    api_key = (credentials.get("api_key") or "").strip()

    # Either field may hold the whole link.
    for candidate in (raw_url, api_key):
        if candidate.lower().startswith("http"):
            return candidate

    if not api_key:
        raise ValueError(
            "Vista Social connector requires an mcp_url (the MCP link from Vista) or an api_key."
        )

    base = (credentials.get("api_url") or VISTA_MCP_BASE_URL).strip().rstrip("/")
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}api_key={api_key}"
