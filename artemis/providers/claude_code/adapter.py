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

MCP tool deferral fix (scout-mcp-deferral)
-------------------------------------------
claude-code ≥2.1 can enter a "deferred tool catalog" state where MCP tools are
presented lazily instead of eagerly — the scout LLM then narrates that tools are
"deferred and not yet fully connected" and emits 0 signals. Two mitigations are
applied:

1. ``MCP_CONNECTION_NONBLOCKING=false`` in the subprocess env: claude-code's
   ``isMcpLadderNonblockingEnabled()`` returns ``false`` when this variable is
   set to a falsy value, forcing the MCP server connection to be established
   synchronously before the first LLM turn. Tools are then eager/fully-connected
   from turn 1. This is the primary fix.

2. ``--no-session-persistence``: prevents per-run session writes to disk.
   Each scout run spawns a fresh subprocess; persisting sessions produces
   unnecessary I/O and can cause session-state collisions. Included here because
   it also removes one of the background startup tasks that competes with the
   MCP handshake timing.

Both mitigations are confined to the tool-run subprocess env and CLI flags; they
have no effect on the Anthropic API or the in-process adapter paths.

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

# ---------------------------------------------------------------------------
# Forge mode constants (Ares read-only project inspection).
#
# Forge mode is opted into by setting ``forge_project_path_var`` before
# calling adapter.complete().  It grants the CLI read-only native file tools
# inside the target project directory.  Bash, Write, Edit are withheld until
# a future slice adds worktree isolation.
# ---------------------------------------------------------------------------

#: Native tools allowed in Forge mode (read-only file inspection only).
_FORGE_READONLY_ALLOWED: tuple[str, ...] = ("Read", "Glob", "Grep")

#: Native tools explicitly disallowed in Forge mode.  No Bash/Write/Edit
#: until worktree isolation lands in a future slice.
_FORGE_DISALLOWED: tuple[str, ...] = (
    "Bash",
    "Write",
    "Edit",
    "WebSearch",
    "WebFetch",
    "NotebookEdit",
)


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


def _mcp_eager_env(*, claude_config_dir: str | None = None) -> dict[str, str]:
    """Build the subprocess environment that forces MCP tools to load eagerly.

    Sets ``MCP_CONNECTION_NONBLOCKING=false`` so claude-code's
    ``isMcpLadderNonblockingEnabled()`` returns ``false``, causing the MCP
    server handshake to complete synchronously before the first LLM turn.
    Without this, claude-code may enter a deferred-catalog state where tools
    are listed as "not yet fully connected" and the scout LLM narrates that
    it cannot invoke them.

    Inherits the full parent ``os.environ`` so PATH, DB credentials, and all
    other config the subprocess needs are preserved.

    When ``claude_config_dir`` is provided, sets ``CLAUDE_CONFIG_DIR`` in the
    subprocess env so the ``claude`` CLI authenticates as the account whose
    config lives at that path.  When ``None``, the key is not set and the
    subprocess inherits the ambient login (identical to previous behaviour).
    """
    env = os.environ.copy()
    env["MCP_CONNECTION_NONBLOCKING"] = "false"
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = claude_config_dir
    return env


def resolve_claude_config_dir(agent_id: str) -> str | None:
    """Return the CLAUDE_CONFIG_DIR for *agent_id*, or None if unconfigured.

    Resolution order:
    1. Exact match of *agent_id* in ``settings.claude_agent_accounts``.
    2. Longest prefix match (e.g. ``"marketing."`` covers all marketing agents).
    3. No match → ``None`` (inherit ambient login; zero behavior change).

    The matched account name is then looked up in
    ``settings.claude_account_config_dirs``.  If the account name is not in
    that map (mis-configuration), ``None`` is returned and a warning is logged.
    """
    # Late import avoids circular imports; reading the live settings object
    # ensures test monkeypatching of ARTEMIS_* env vars is respected.
    import artemis.config as _config

    _settings = _config.settings
    agent_accounts = _settings.claude_agent_accounts
    account_dirs = _settings.claude_account_config_dirs

    if not agent_accounts or not account_dirs:
        return None

    # 1. Exact match.
    account_name = agent_accounts.get(agent_id)

    # 2. Longest prefix match.
    if account_name is None:
        best_prefix: str | None = None
        for prefix, name in agent_accounts.items():
            if agent_id.startswith(prefix) and (
                best_prefix is None or len(prefix) > len(best_prefix)
            ):
                best_prefix = prefix
                account_name = name

    if account_name is None:
        return None

    config_dir = account_dirs.get(account_name)
    if config_dir is None:
        import logging

        logging.getLogger(__name__).warning(
            "resolve_claude_config_dir: agent %r mapped to account %r "
            "but that account has no entry in claude_account_config_dirs",
            agent_id,
            account_name,
        )
        return None

    return config_dir


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

        # Surface real completion status from the CLI JSON payload.
        # The claude -p --output-format json schema includes:
        #   is_error   : bool   — true when the CLI itself errored (e.g. context limit,
        #                         rate-limit, internal fault). The "result" field then
        #                         contains the error message, not the assistant reply.
        #   subtype    : str    — present on error payloads; values include
        #                         "error_during_execution" and others.
        #   result     : str    — the assistant's reply on success; error text on failure.
        #   num_turns  : int    — number of internal turns (informational).
        #   duration_ms: int    — wall-clock time in ms (informational).
        if data.get("is_error"):
            error_msg = str(data.get("result") or data.get("subtype") or "unknown error")
            raise ProviderAPIError(
                0,
                f"claude CLI reported is_error=true: {error_msg[:300]}",
            )

        result_text: str = data.get("result") or data.get("output") or data.get("message") or ""
        if not result_text.strip():
            raise ProviderAPIError(
                0,
                "claude CLI returned an empty result (no text produced)",
            )

        usage_data = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=result_text)]),
            stop_reason="end_turn",
            usage=usage,
        )

    async def _complete_with_tools(self, request: CompletionRequest) -> CompletionResponse:
        """Route a completion through the appropriate backend when tools are present (CC19).

        Reads the active tool-session scope from caller-owned contextvars:

        - **Forge mode** (``forge_project_path_var`` is set): build a read-only
          native-tools argv via ``_build_forge_command`` and run the CLI with
          ``cwd=project_path``.  No MCP server is launched.
        - **Builder** (``builder_session_id_var`` is set): scoped Artemis MCP
          server with the five Builder tools.
        - **Floating Artemis** (``floating_session_id_var`` is set): scoped
          Artemis MCP server with the session's auto-invoke tools.

        The guard requires that at least one of the three contextvars is set;
        it raises ``ProviderAPIError`` only when all three are None.

        Known limitation: /messages/stream SSE stays text-only for CC19.
        Streaming + tools is a separate brief.
        """
        # Lazy imports guard against circular-import at module load time.
        from artemis.builder.context import builder_session_id_var
        from artemis.dev_projects.context import forge_project_path_var
        from artemis.floating_artemis.context import floating_session_id_var

        builder_session_id = builder_session_id_var.get()
        floating_session_id = floating_session_id_var.get()
        forge_project_path = forge_project_path_var.get()

        agent_tools = [tool.name for tool in request.tools or []]

        if builder_session_id is None and floating_session_id is None and forge_project_path is None:
            raise ProviderAPIError(
                0,
                "_complete_with_tools called but no tool-session contextvar is set. "
                "Ensure the Builder, Floating Artemis, or Forge turn handler sets its "
                "session context before calling adapter.complete().",
            )

        model = request.model or self._default_model
        prompt = _flatten_to_prompt(request)

        # ── Forge mode: read-only native tools, no MCP server. ────────────────
        if forge_project_path is not None:
            cmd = _build_forge_command(
                binary=self._binary,
                model=model,
                project_path=forge_project_path,
            )
            return await self._run_subprocess(
                cmd, prompt, tool_run=True, project_path=forge_project_path
            )

        # ── MCP path: Builder or Floating Artemis. ────────────────────────────
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
            return await self._run_subprocess(cmd, prompt, tool_run=True)
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
        timeout_seconds: float | None = None,
        max_turns: int | None = None,
        claude_config_dir: str | None = None,
    ) -> CompletionResponse:
        """Run a tool-using agent via claude-code's own agent loop (CC2).

        Launches ``claude -p --mcp-config <tmp> --strict-mcp-config`` so
        claude-code spawns the per-run Artemis MCP server, autonomously calls the
        agent's scoped tools, and returns a single final result. The temp MCP
        config file is always cleaned up. Any failure (non-zero exit, timeout,
        launch error) raises a provider error so the caller can mark the node
        failed and let the rest of the pipeline continue.

        Args:
            timeout_seconds: Per-call wall-clock subprocess timeout. Overrides the
                global ``ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS`` env var when set.
                Useful for content nodes that should complete in seconds, not minutes.
            max_turns: When set, passes ``--max-turns <n>`` to claude-code so its
                internal agent loop is bounded. Guards against tool-use loops where
                the LLM keeps calling tools without producing a final response.
            claude_config_dir: When set, passes ``CLAUDE_CONFIG_DIR=<dir>`` in the
                subprocess env so the ``claude`` CLI authenticates as the account
                whose config lives at that path.  When ``None`` (default), the key
                is not set and the subprocess inherits the ambient login (no change
                in behaviour when the maps are empty).
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
                max_turns=max_turns,
            )
            return await self._run_subprocess(
                cmd,
                prompt,
                tool_run=True,
                timeout_seconds=timeout_seconds,
                claude_config_dir=claude_config_dir,
            )
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def _run_subprocess(
        self,
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        timeout_seconds: float | None = None,
        claude_config_dir: str | None = None,
        project_path: str | None = None,
    ) -> CompletionResponse:
        """Launch the claude CLI, enforce the wall-clock timeout, parse the result.

        Factored out so the tool path's subprocess handling is unit-testable and
        the parse logic mirrors :meth:`complete`.

        ``tool_run=True`` injects :func:`_mcp_eager_env` so that MCP tools are
        loaded synchronously (blocking) rather than deferred. On the text-only
        path (``tool_run=False``) no MCP server is involved, so the env override
        is omitted.

        ``timeout_seconds``: when provided, overrides the global
        ``ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS`` env var for this call. Callers
        can pass a tighter bound for fast-path agents (e.g., content nodes).

        ``claude_config_dir``: when provided, adds ``CLAUDE_CONFIG_DIR=<dir>``
        to the subprocess env so the ``claude`` CLI authenticates as the account
        at that path.  When ``None``, the key is absent (inherit ambient login).
        Only honoured on the tool-run path (``tool_run=True``) because the
        text-only path does not set an env at all; passing it there is a no-op.

        ``project_path``: when provided, sets the subprocess working directory
        to that path via ``cwd=``.  Used by Forge mode so the CLI runs inside
        the target project directory.  When ``None``, cwd is inherited from the
        parent process (existing behavior — no change for MCP paths).
        """
        timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
        env = _mcp_eager_env(claude_config_dir=claude_config_dir) if tool_run else None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **({"cwd": project_path} if project_path is not None else {}),
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

        # Surface real completion status — same logic as complete().
        if data.get("is_error"):
            error_msg = str(data.get("result") or data.get("subtype") or "unknown error")
            raise ProviderAPIError(
                0,
                f"claude CLI (tool run) reported is_error=true: {error_msg[:300]}",
            )

        result_text: str = data.get("result") or data.get("output") or data.get("message") or ""
        if not result_text.strip():
            raise ProviderAPIError(
                0,
                "claude CLI (tool run) returned an empty result (no text produced)",
            )

        usage_data = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_data.get("input_tokens", 0)),
            output_tokens=int(usage_data.get("output_tokens", 0)),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=result_text)]),
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


def _build_forge_command(*, binary: str, model: str, project_path: str) -> list[str]:
    """Build the Forge-mode ``claude -p`` argv.

    Forge mode gives Ares read-only native file tools (Read, Glob, Grep) inside
    a real project directory.  No Artemis MCP server is launched — this slice
    uses only the CLI's built-in tools so there is no session-scope to establish.

    Key differences from the standard MCP argv:
    - ``--add-dir <project_path>`` grants the CLI access to the project tree.
    - ``--permission-mode bypassPermissions`` runs the read-only tools without
      interactive prompts; the ``--disallowed-tools`` list is the hard boundary.
    - No ``--mcp-config`` / ``--strict-mcp-config``: no Artemis MCP tools are
      needed for this read-only inspection slice.
    - ``--allowed-tools Read Glob Grep``: explicit allowlist keeps the surface
      minimal.
    - ``--disallowed-tools Bash Write Edit WebSearch WebFetch NotebookEdit``:
      belt-and-suspenders deny list so mutating tools cannot be invoked even if
      ``bypassPermissions`` were to be loosened later.

    Returns the argv list; does NOT launch a subprocess.  Unit-testable.
    """
    return [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--add-dir",
        project_path,
        "--permission-mode",
        "bypassPermissions",
        "--allowed-tools",
        *_FORGE_READONLY_ALLOWED,
        "--disallowed-tools",
        *_FORGE_DISALLOWED,
    ]


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
    max_turns: int | None = None,
) -> list[str]:
    """Build the verified ``claude -p`` tool-run command.

    Canonical hyphenated flags; the per-agent MCP server is the only server
    (``--strict-mcp-config``); the scoped allowlist pre-approves exactly the
    agent's tools and ``--permission-mode default`` runs them without prompts.

    ``--no-session-persistence`` prevents per-run session disk writes. Each
    agent run is a fresh ephemeral subprocess — sessions are never resumed, so
    persisting them wastes I/O and can interfere with the MCP startup timing
    that triggers tool deferral.

    ``max_turns``: when provided, adds ``--max-turns <n>`` to bound the claude-code
    internal agent loop. Use for content nodes that should call one tool and return,
    preventing runaway loops where the LLM keeps calling tools without a final answer.
    """
    cmd = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--mcp-config",
        mcp_config_path,
        "--strict-mcp-config",
        "--no-session-persistence",
        "--allowed-tools",
        *allowed_tools_for(agent_tools),
        "--disallowed-tools",
        *_DISALLOWED_BUILTINS,
        "--permission-mode",
        _PERMISSION_MODE,
    ]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    return cmd


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
