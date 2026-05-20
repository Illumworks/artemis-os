#!/usr/bin/env python3
"""builder_cli.py — terminal shim for Agent-Builder sessions (O1).

Wraps POST /api/builder/sessions + POST /sessions/{id}/messages so Jon can
drive a Builder conversation from a terminal. This is the "conversation logic
only" comparison path for the pre-merge kill criterion test — independent of
frontend UI quality.

Usage:
    # Start a new session and chat:
    uv run scripts/builder_cli.py

    # Resume an existing session:
    uv run scripts/builder_cli.py --session 42

    # Point at a non-localhost server:
    uv run scripts/builder_cli.py --base-url http://localhost:8001

    # Quit with Ctrl+C or type /quit, /exit, /bye.

Environment:
    ARTEMIS_TOKEN   — API bearer token (required; also readable from .env)
    ARTEMIS_URL     — base URL override (default: http://localhost:8000)

Requires: httpx (already in the project's dev deps)
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8000"
TOKEN_ENV = "ARTEMIS_TOKEN"
URL_ENV = "ARTEMIS_URL"

QUIT_COMMANDS = frozenset(["/quit", "/exit", "/bye", "/q"])


def _load_dotenv() -> None:
    """Minimal .env loader — sets os.environ for keys not already set."""
    for candidate in (".env", "../.env"):
        if not os.path.exists(candidate):
            continue
        with open(candidate) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key not in os.environ:
                    os.environ[key] = value
        break


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _get_client(base_url: str, token: str) -> Any:
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx is required. Run: uv add httpx", file=sys.stderr)
        sys.exit(1)

    return httpx.Client(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    )


def _api(client: Any, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Make an API call; raise on HTTP errors with a readable message."""
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("detail") or body.get("error") or str(body)
        except Exception:
            detail = response.text[:200]
        print(f"\nAPI error {response.status_code}: {detail}", file=sys.stderr)
        sys.exit(1)
    return response.json() if response.status_code != 204 else {}


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def create_session(client: Any) -> dict[str, Any]:
    return _api(client, "POST", "/api/builder/sessions", json={"builder_kind": "agent"})


def get_session(client: Any, session_id: int) -> dict[str, Any]:
    return _api(client, "GET", f"/api/builder/sessions/{session_id}")


def send_message(client: Any, session_id: int, content: str) -> dict[str, Any]:
    return _api(
        client,
        "POST",
        f"/api/builder/sessions/{session_id}/messages",
        json={"content": content},
    )


def list_sessions(client: Any) -> list[dict[str, Any]]:
    data = _api(client, "GET", "/api/builder/sessions")
    return data.get("sessions", [])


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"

_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if _tty else text


def _print_separator(char: str = "─", width: int = 72) -> None:
    print(_c(DIM, char * width))


def _print_assistant(text: str) -> None:
    _print_separator()
    prefix = _c(CYAN + BOLD, "Agent-Builder")
    print(f"{prefix}  {_c(DIM, '↓')}")
    # Wrap long lines for terminal readability
    for line in text.split("\n"):
        if len(line) > 100:
            wrapped = textwrap.fill(line, width=100, subsequent_indent="  ")
            print(wrapped)
        else:
            print(line)
    _print_separator()


def _print_draft(draft: dict[str, Any] | None) -> None:
    if not draft:
        return
    print(_c(YELLOW + BOLD, "\n  Draft definition updated:"))
    for key, val in draft.items():
        if key == "system_prompt":
            snippet = str(val)[:120] + ("…" if len(str(val)) > 120 else "")
            print(f"    {_c(DIM, key):20s} {snippet}")
        elif isinstance(val, list):
            print(f"    {_c(DIM, key):20s} {', '.join(str(v) for v in val)}")
        else:
            print(f"    {_c(DIM, key):20s} {val}")
    print()


def _print_welcome(session_id: int) -> None:
    _print_separator("═")
    print(_c(BOLD, "  Artemis Agent-Builder  ") + _c(DIM, f"[Session #{session_id}]"))
    print(_c(DIM, "  Describe the agent you want to build. Type /quit to exit."))
    _print_separator("═")
    print()


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------


def repl(client: Any, session: dict[str, Any]) -> None:
    session_id = session["id"]
    _print_welcome(session_id)

    # If resuming: print existing conversation
    conversation = session.get("conversation") or []
    if conversation:
        print(_c(DIM, "  Resuming session — previous conversation:"))
        _print_separator()
        for msg in conversation:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            if role == "user":
                print(_c(GREEN + BOLD, "You: ") + content)
            else:
                print(_c(CYAN + BOLD, "Agent-Builder: ") + content[:200])
        _print_separator()
        print()

    while True:
        try:
            user_input = input(_c(GREEN + BOLD, "You: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(_c(DIM, "\n  Bye."))
            break

        if not user_input:
            continue

        if user_input.lower() in QUIT_COMMANDS:
            print(_c(DIM, "  Bye."))
            break

        if user_input == "/draft":
            sess = get_session(client, session_id)
            _print_draft(sess.get("draft"))
            continue

        if user_input == "/session":
            print(_c(DIM, f"  Session ID: {session_id}  Status: {session.get('status')}"))
            continue

        if user_input == "/help":
            print(_c(DIM, "  Commands: /quit  /draft  /session  /help"))
            continue

        print(_c(DIM, "  Thinking..."))
        result = send_message(client, session_id, user_input)

        assistant_text = result.get("assistant_text", "")
        draft = result.get("draft")

        _print_assistant(assistant_text)
        _print_draft(draft)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="Terminal shim for Artemis Agent-Builder (O1 kill criterion test)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              uv run scripts/builder_cli.py                     # new session
              uv run scripts/builder_cli.py --session 42        # resume session 42
              uv run scripts/builder_cli.py --list              # list open sessions
            """
        ),
    )
    parser.add_argument("--session", type=int, default=None, help="Resume this session ID")
    parser.add_argument("--list", action="store_true", help="List open builder sessions and exit")
    parser.add_argument(
        "--base-url",
        default=os.environ.get(URL_ENV, DEFAULT_BASE_URL),
        help=f"Server base URL (default: {DEFAULT_BASE_URL} or ${URL_ENV})",
    )
    args = parser.parse_args()

    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        # Try a localhost dev convenience: no-auth if running locally
        token = "dev-token"

    client = _get_client(args.base_url, token)

    if args.list:
        sessions = list_sessions(client)
        if not sessions:
            print("No builder sessions found.")
            return
        _print_separator()
        print(_c(BOLD, f"  {'ID':>4}  {'Status':12}  Preview"))
        _print_separator()
        for s in sessions:
            preview = ""
            conv = s.get("conversation") or []
            if conv:
                preview = str(conv[0].get("content", ""))[:60]
            status = s.get("status", "?")
            color = GREEN if status == "active" else DIM
            print(f"  {s['id']:>4}  {_c(color, status):12}  {preview}")
        _print_separator()
        return

    if args.session:
        print(_c(DIM, f"  Resuming session {args.session}..."))
        session = get_session(client, args.session)
    else:
        print(_c(DIM, "  Creating new session..."))
        session = create_session(client)

    repl(client, session)


if __name__ == "__main__":
    main()
