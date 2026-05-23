"""Claude Code CLI adapter — subprocess-based, no API key required.

Uses the ``claude`` binary (Claude Code CLI) in non-interactive ``--print`` mode
with ``--output-format json`` to run completions against the user's Claude Max
subscription without per-token API charges.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- Binary discovery delegates to ``find_cli_binary("claude")``.
- The full conversation is flattened to a single prompt string (system header +
  role-prefixed turns) and piped to the CLI via stdin.  Tool use is not
  supported — the Claude Code CLI is a text-in / text-out interface.
- Usage tokens reported by the CLI (if present) are forwarded; otherwise zeros
  are used so the Usage object is always valid.
- Raises ``MissingCliBinaryError`` at construction if the binary cannot be found.
- Raises ``ProviderAPIError`` on non-zero exit code or non-JSON output.
"""

from __future__ import annotations

import asyncio
import json
import os

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.providers._bin_path import find_cli_binary
from artemis.providers.errors import ClaudeCodeTimeoutError, MissingCliBinaryError, ProviderAPIError

_TIMEOUT_SECONDS = 300.0
_DEFAULT_MODEL = "claude-sonnet-4-6"


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
