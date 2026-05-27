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
- ``complete()`` is the text-in / text-out path (no tools). ``run_with_tools()``
  (stream CC2) launches ``claude -p --mcp-config`` so claude-code runs its OWN
  agent loop against the per-run Artemis MCP server (``artemis.tools.mcp_server``),
  autonomously calling exactly the agent's scoped tools on the user's Claude
  subscription. claude-code returns a single final result; Artemis's in-process
  ``run_turn`` loop is bypassed for that path.
- Usage tokens reported by the CLI (if present) are forwarded; otherwise zeros
  are used so the Usage object is always valid.
- Raises ``MissingCliBinaryError`` at construction if the binary cannot be found.
- Raises ``ProviderAPIError`` on non-zero exit code or non-JSON output.
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

_DEFAULT_TIMEOUT_SECONDS = 300.0
#: Wall-clock subprocess timeout for the text path. The tool path reads the
#: env-configurable :func:`_timeout_seconds` so long-running agent loops get a
#: tunable bound (``--max-turns`` does NOT exist on the claude CLI; on a
#: subscription there is no per-token cost, so wall-clock is the correct guard).
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

    ``ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS`` overrides the 300s default. An
    unparseable value falls back to the default rather than crashing the run.
    """
    raw = os.environ.get("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


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
        """Run a single completion via the claude CLI."""
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

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ClaudeCodeTimeoutError(
                408,
                f"Claude CLI timed out after {int(_TIMEOUT_SECONDS)}s; trying provider cascade",
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
