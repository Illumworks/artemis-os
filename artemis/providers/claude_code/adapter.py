"""Claude Code CLI adapter — subprocess-based, no API key required.

Uses the ``claude`` binary (Claude Code CLI) in non-interactive ``--print`` mode
with ``--output-format json`` to run completions against the user's Claude Max
subscription without per-token API charges.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- Binary discovery delegates to ``find_cli_binary("claude")``.
- The full conversation is flattened to a single prompt string (system header +
  role-prefixed turns) and piped to the CLI via stdin.
- ``complete()`` is the text-in / text-out path.  When ``request.tools`` is
  non-empty, it routes to ``_complete_with_tools()`` which launches
  ``claude -p --mcp-config`` against a scoped Artemis MCP server. Builder
  turns use the Builder-scoped server; Floating Artemis turns use their own
  session-scoped server for auto-invoke tools.
- ``run_with_tools()`` (stream CC2) — unchanged; still used by pipeline agents.
- Usage tokens reported by the CLI (if present) are forwarded; otherwise zeros
  are used so the Usage object is always valid.
- Raises ``MissingCliBinaryError`` at construction if the binary cannot be found.
- Raises ``ProviderAPIError`` on non-zero exit code or non-JSON output.

CC19 architecture note
----------------------
claude-code's CLI does not expose a turn-by-turn tool_use API; it runs its own
internal agent loop. ``_complete_with_tools`` therefore launches a scoped MCP
server, passes session identity through a caller-owned contextvar, and lets the
subprocess handle all tool-use iterations internally. Builder turns use
``artemis.tools.mcp_server --builder-session-id``. Floating Artemis turns use
``artemis.tools.mcp_server --floating-session-id`` and only expose auto-invoke
tools on that path.

Streaming note
--------------
``/messages/stream`` SSE stays text-only for CC19.  Streaming + tools is a
separate brief.  Known limitation documented here.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.providers._bin_path import find_cli_binary
from artemis.providers.errors import ClaudeCodeTimeoutError, MissingCliBinaryError, ProviderAPIError
from artemis.tools.mcp_server import mcp_tool_name

_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "900"))
#: Wall-clock subprocess timeout. Both the text path and the tool path call
#: :func:`_timeout_seconds` so ``ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS`` tunes
#: both without a code change. Default 900s (3× the observed 300s hit point on
#: the qualifier; scouts finish in 30-60s so they are unaffected).
_TIMEOUT_SECONDS = _DEFAULT_TIMEOUT_SECONDS
_DEFAULT_MODEL = "claude-sonnet-4-6"

#: claude-code built-in tools we explicitly deny in the tool path. The agent's
#: scoped MCP tools (the ``--allowed-tools`` allowlist) are the security
#: boundary; denying the built-ins keeps the surface to exactly Artemis tools.
_DISALLOWED_BUILTINS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
)

#: Headless ``-p`` permission mode. ``default`` honours the ``--allowed-tools``
#: pre-approval list and runs those tools WITHOUT interactive prompts, while
#: still refusing anything not on the list. We deliberately avoid
#: ``bypassPermissions`` ("skip all permissions") — the scoped allowlist, not a
#: blanket bypass, is the security boundary.
_PERMISSION_MODE = "default"


def _timeout_seconds() -> float:
    """Read the wall-clock subprocess timeout (env-configurable).

    Reads ``os.environ`` on every call so ``monkeypatch.setenv`` in tests
    takes effect without reloading the module. ``ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS``
    overrides the 900s default. An unparseable value falls back silently.
    """
    raw = os.environ.get("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS")
    if not raw:
        return 900.0
    try:
        return float(raw)
    except ValueError:
        return 900.0


class ClaudeCodeAdapter:
    """Conforms to the ModelAdapter protocol. Streaming not supported."""

    def __init__(self, *, binary_path: str | None = None, default_model: str | None = None) -> None:
        resolved = binary_path or find_cli_binary("claude")
        if not resolved:
            raise MissingCliBinaryError("claude-code", "claude")
        self._binary = resolved
        self._default_model = default_model or os.environ.get(
            "CLAUDE_CODE_DEFAULT_MODEL", _DEFAULT_MODEL
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run a single completion via the claude CLI.

        When ``request.tools`` is non-empty, routes to ``_complete_with_tools``
        (CC19 MCP path) so the Builder LLM can call builder_* tools inside the
        claude-code subprocess.  The subscription-only invariant is preserved —
        no Anthropic API key is used on this path.
        """
        if request.tools:
            return await self._complete_with_tools(request)

        model = request.model or self._default_model
        prompt = _flatten_to_prompt(request)

        cmd = [
            self._binary,
            "--print",
            "--output-format",
            "json",
            "--model",
            model,
        ]

        timeout = _timeout_seconds()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ClaudeCodeTimeoutError(
                408,
                f"Claude CLI timed out after {int(timeout)}s; trying provider cascade",
            ) from None

        if proc.returncode != 0:
            raise ProviderAPIError(
                proc.returncode or 1,
                stderr.decode(errors="replace"),
            )

        raw = stdout.decode(errors="replace").strip()
        if not raw:
            raise ProviderAPIError(0, "claude CLI produced no output")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderAPIError(0, f"Non-JSON from claude CLI: {raw[:300]}") from exc

        result_text = data.get("result") or data.get("output") or data.get("message") or ""
        usage_data = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=str(result_text))]),
            stop_reason="end_turn",
            usage=usage,
        )

    async def _complete_with_tools(self, request: CompletionRequest) -> CompletionResponse:
        """Route a completion through the Artemis MCP server when tools are present (CC19).

        Reads the active tool-session scope from caller-owned contextvars:
        Builder uses ``builder_session_id_var``; Floating Artemis uses
        ``floating_session_id_var``. Launches ``claude -p --mcp-config`` against
        the matching scoped MCP server.

        Design:
        - claude-code's internal agent loop handles all tool-use iterations.
        - Builder proposal rows land in the DB via the MCP server during the subprocess run.
        - We return the final text answer as a CompletionResponse with stop_reason="end_turn".
        - Builder and Floating Artemis callers both short-circuit their local
          tool loops because there are no tool_use blocks in the returned response.

        Known limitation: /messages/stream SSE stays text-only for CC19.
        Streaming + tools is a separate brief.
        """
        from artemis.builder.context import builder_session_id_var
        from artemis.floating_artemis.context import floating_session_id_var

        builder_session_id = builder_session_id_var.get()
        floating_session_id = floating_session_id_var.get()
        agent_tools = [tool.name for tool in request.tools or []]
        if builder_session_id is None and floating_session_id is None:
            raise ProviderAPIError(
                0,
                "_complete_with_tools called but no tool-session contextvar is set. "
                "Ensure the Builder or Floating Artemis turn handler sets its "
                "session context before calling adapter.complete().",
            )

        model = request.model or self._default_model
        prompt = _flatten_to_prompt(request)

        if builder_session_id is not None:
            config = _build_builder_mcp_config(builder_session_id=builder_session_id)
        else:
            config = _build_floating_artemis_mcp_config(
                session_id=str(floating_session_id),
                tool_names=agent_tools,
            )
        tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w", suffix=".mcp.json", prefix="artemis-builder-mcp-", delete=False
        )
        try:
            json.dump(config, tmp)
            tmp.flush()
            tmp.close()

            if builder_session_id is not None:
                # Filter allowed tools to only the builder_* tools.
                from artemis.tools.mcp_server import BUILDER_MCP_TOOL_NAMES

                allowed = [f"mcp__artemis__{n}" for n in BUILDER_MCP_TOOL_NAMES]
            else:
                allowed = allowed_tools_for(agent_tools)

            cmd = [
                self._binary,
                "-p",
                "--output-format",
                "json",
                "--model",
                model,
                "--mcp-config",
                tmp.name,
                "--strict-mcp-config",
                "--allowed-tools",
                *allowed,
                "--disallowed-tools",
                *_DISALLOWED_BUILTINS,
                "--permission-mode",
                _PERMISSION_MODE,
            ]
            return await self._run_subprocess(cmd, prompt)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def run_with_tools(
        self,
        request: CompletionRequest,
        *,
        agent_id: str,
        run_id: str,
        pipeline_run_id: str | None,
        agent_tools: list[str],
    ) -> CompletionResponse:
        """Run a tool-using agent via claude-code's own agent loop (CC2).

        Launches ``claude -p --mcp-config <tmp> --strict-mcp-config`` so
        claude-code spawns the per-run Artemis MCP server, autonomously calls the
        agent's scoped tools, and returns a single final result. The temp MCP
        config file is always cleaned up. Any failure (non-zero exit, timeout,
        launch error) raises a provider error so the caller can mark the node
        failed and let the rest of the pipeline continue.
        """
        model = request.model or self._default_model
        prompt = _flatten_to_prompt(request)

        config = _build_mcp_config(
            agent_id=agent_id, run_id=run_id, pipeline_run_id=pipeline_run_id
        )
        # NamedTemporaryFile(delete=False) so the path survives for the child
        # process; we unlink it in finally.
        tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w", suffix=".mcp.json", prefix="artemis-mcp-", delete=False
        )
        try:
            json.dump(config, tmp)
            tmp.flush()
            tmp.close()

            cmd = _build_launch_command(
                binary=self._binary,
                model=model,
                mcp_config_path=tmp.name,
                agent_tools=agent_tools,
            )
            return await self._run_subprocess(cmd, prompt)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def _run_subprocess(self, cmd: list[str], prompt: str) -> CompletionResponse:
        """Launch the claude CLI, enforce the wall-clock timeout, parse the result.

        Factored out so the tool path's subprocess handling is unit-testable and
        the parse logic mirrors :meth:`complete`.
        """
        timeout = _timeout_seconds()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ProviderAPIError(0, f"failed to launch claude CLI: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ClaudeCodeTimeoutError(
                408,
                f"Claude CLI (tool run) timed out after {int(timeout)}s",
            ) from None

        if proc.returncode != 0:
            raise ProviderAPIError(
                proc.returncode or 1,
                stderr.decode(errors="replace"),
            )

        raw = stdout.decode(errors="replace").strip()
        if not raw:
            raise ProviderAPIError(0, "claude CLI (tool run) produced no output")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderAPIError(0, f"Non-JSON from claude CLI: {raw[:300]}") from exc

        result_text = data.get("result") or data.get("output") or data.get("message") or ""
        usage_data = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=str(result_text))]),
            stop_reason="end_turn",
            usage=usage,
        )


def _build_mcp_config(
    *,
    agent_id: str,
    run_id: str,
    pipeline_run_id: str | None,
) -> dict[str, Any]:
    """Build the per-run stdio MCP config (``--mcp-config`` schema).

    Uses ``sys.executable`` so the spawned MCP server runs in the same venv.
    Omits ``--pipeline-run-id`` from the args when it is None.
    """
    args = [
        "-m",
        "artemis.tools.mcp_server",
        "--agent-id",
        agent_id,
        "--run-id",
        run_id,
    ]
    if pipeline_run_id is not None:
        args += ["--pipeline-run-id", pipeline_run_id]
    return {
        "mcpServers": {
            "artemis": {
                "command": sys.executable,
                "args": args,
            }
        }
    }


def _build_builder_mcp_config(*, builder_session_id: int) -> dict[str, Any]:
    """Build the Builder-scoped stdio MCP config (CC19).

    Passes ``--builder-session-id`` to the MCP server so it serves the five
    Builder tools scoped to the correct session.  Uses ``sys.executable`` to
    ensure the spawned server runs in the same venv.
    """
    return {
        "mcpServers": {
            "artemis": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "artemis.tools.mcp_server",
                    "--builder-session-id",
                    str(builder_session_id),
                ],
            }
        }
    }


def _build_floating_artemis_mcp_config(*, session_id: str, tool_names: list[str]) -> dict[str, Any]:
    """Build the Floating Artemis stdio MCP config.

    Passes the session id plus the exact tool-name allowlist chosen by the
    parent turn handler so the subprocess mirrors the in-process tool scope.
    """
    args = [
        "-m",
        "artemis.tools.mcp_server",
        "--floating-session-id",
        session_id,
    ]
    for tool_name in tool_names:
        args += ["--tool-name", tool_name]
    return {
        "mcpServers": {
            "artemis": {
                "command": sys.executable,
                "args": args,
            }
        }
    }


def allowed_tools_for(agent_tools: list[str]) -> list[str]:
    """Map each agent tool to its claude-code allowlist entry.

    ``signal_queue.write`` → ``mcp__artemis__signal_queue_write``. Uses CC1's
    :func:`mcp_tool_name` — the transform is never re-derived here.
    """
    return [f"mcp__artemis__{mcp_tool_name(t)}" for t in agent_tools]


def _build_launch_command(
    *,
    binary: str,
    model: str,
    mcp_config_path: str,
    agent_tools: list[str],
) -> list[str]:
    """Build the verified ``claude -p`` tool-run command (no ``--max-turns``).

    Canonical hyphenated flags; the per-agent MCP server is the only server
    (``--strict-mcp-config``); the scoped allowlist pre-approves exactly the
    agent's tools and ``--permission-mode default`` runs them without prompts.
    """
    return [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--mcp-config",
        mcp_config_path,
        "--strict-mcp-config",
        "--allowed-tools",
        *allowed_tools_for(agent_tools),
        "--disallowed-tools",
        *_DISALLOWED_BUILTINS,
        "--permission-mode",
        _PERMISSION_MODE,
    ]


def _flatten_to_prompt(request: CompletionRequest) -> str:
    """Flatten a CompletionRequest to a plain-text prompt for the CLI.

    Format: optional System header, then Human/Assistant turns.
    """
    parts: list[str] = []

    if request.system:
        parts.append(f"System: {request.system}\n\n")

    for msg in request.messages:
        role_label = "Human" if msg.role == "user" else "Assistant"
        texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        if texts:
            parts.append(f"{role_label}: {' '.join(texts)}\n\n")

    return "".join(parts).strip()
