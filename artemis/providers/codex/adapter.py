"""Codex CLI adapter — subprocess-based, no API key required.

Uses the ``codex`` binary (OpenAI Codex CLI) in ``exec --json`` mode to run
completions against the user's ChatGPT Plus subscription without per-token
API charges.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- Ported from Node reference: claudeck-artemis/server/providers/codex/index.js.
- Binary discovery delegates to ``find_cli_binary("codex")``.
- The CLI is invoked with ``exec --json --skip-git-repo-check --full-auto``.
- Stdout may contain one or more newline-delimited JSON objects (NDJSON).
  We collect all ``result``-typed objects and concatenate their text.
- Raises ``MissingCliBinaryError`` at construction if the binary cannot be found.
- Raises ``ProviderAPIError`` on non-zero exit code or unparseable output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.providers._bin_path import find_cli_binary
from artemis.providers.errors import MissingCliBinaryError, ProviderAPIError

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 120.0
_VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


class CodexAdapter:
    """Conforms to the ModelAdapter protocol. Streaming not supported."""

    def __init__(
        self,
        *,
        binary_path: str | None = None,
        default_model: str | None = None,
        default_reasoning_effort: str | None = None,
        default_speed_tier: str | None = None,
    ) -> None:
        resolved = binary_path or find_cli_binary("codex")
        if not resolved:
            raise MissingCliBinaryError("codex", "codex")
        self._binary = resolved
        self._default_model = default_model or os.environ.get("CODEX_DEFAULT_MODEL", "")
        self._default_reasoning_effort = default_reasoning_effort
        self._default_speed_tier = default_speed_tier

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run a single completion via the codex CLI.

        Note: CodexAdapter is text-only and does not support tool execution.
        Tools passed in ``request.tools`` will be silently ignored.
        Route tool-using surfaces to a tool-capable provider (anthropic, claude-code,
        gemini, openai, or openrouter).
        """
        if request.tools:
            logger.warning(
                "%s adapter received request.tools but does not support tool execution. "
                "Tools will be ignored. Consider routing tool-using surfaces to a "
                "tool-capable provider.",
                type(self).__name__,
            )
        model = request.model or self._default_model
        prompt = _flatten_to_prompt(request)

        cmd = [
            self._binary,
            "exec",
            "--json",
            "--skip-git-repo-check",
            # NOTE 2026-06-16: codex CLI (v0.129+) drift — `--quiet` was REMOVED
            # (errored) and `--full-auto` is DEPRECATED in favor of
            # `--sandbox workspace-write`. `--json` already restricts stdout to the
            # JSONL events we parse. codex exec is non-interactive, so no approval
            # flag is needed.
            "--sandbox",
            "workspace-write",
        ]
        # Codex CLI accepts `-m <model>` to pick the model. Empty string means
        # "let Codex use its subscription default" — matches the Node reference
        # (claudeck-artemis/server/providers/codex/index.js).
        if model:
            cmd.extend(["-m", model])
        effort = request.reasoning_effort or self._default_reasoning_effort
        if effort in _VALID_REASONING_EFFORTS:
            cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])
        speed = request.speed_tier or self._default_speed_tier
        if speed == "fast":
            cmd.extend(["-c", "service_tier=fast"])
        cmd.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProviderAPIError(408, "codex CLI timed out after 120 s") from None

        if proc.returncode != 0:
            raise ProviderAPIError(
                proc.returncode or 1,
                stderr.decode(errors="replace"),
            )

        raw = stdout.decode(errors="replace").strip()
        if not raw:
            raise ProviderAPIError(0, "codex CLI produced no output")

        result_text, usage = _parse_ndjson_output(raw)

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=result_text)]),
            stop_reason="end_turn",
            usage=usage,
        )


def _parse_ndjson_output(raw: str) -> tuple[str, Usage]:
    """Parse NDJSON output from the codex CLI (v0.129+ event schema).

    The codex CLI emits a stream of NDJSON events.  As of v0.129+:

    - ``{"type": "thread.started", "thread_id": "..."}``
    - ``{"type": "turn.started"}``
    - ``{"type": "item.completed", "item": {"type": "agent_message", "text": "<reply>"}}``
      — one or more of these carry the assistant's output text.
    - ``{"type": "turn.completed", "usage": {"input_tokens": N, "cached_input_tokens": N,
        "output_tokens": N, "reasoning_output_tokens": N}}``
      — carries the authoritative token counts.

    We collect text from all ``item.completed / agent_message`` events and
    usage from ``turn.completed``.  Keeping both allows multi-part replies to
    be reassembled correctly.

    Fallback: if no ``agent_message`` items are found, we scan for any event
    that looks like a legacy ``result``/``message`` object (old schema).  If
    that also yields nothing, we return an empty string so downstream callers
    receive an empty string rather than the raw NDJSON (which is never valid
    application text and was causing "Invalid JSON: trailing characters" errors
    in JSON-consuming callers).
    """
    text_parts: list[str] = []
    legacy_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        obj_type = obj.get("type", "")

        # v0.129+ primary path: item.completed carrying an agent_message item.
        if obj_type == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    text_parts.append(str(text))

        # v0.129+ usage comes on turn.completed.
        elif obj_type == "turn.completed":
            usage_data = obj.get("usage") or {}
            if usage_data:
                input_tokens = int(usage_data.get("input_tokens", input_tokens))
                output_tokens = int(usage_data.get("output_tokens", output_tokens))
                # codex names this "cached_input_tokens"; map to our field name.
                cache_read_tokens = int(
                    usage_data.get("cached_input_tokens", cache_read_tokens)
                )

        # Legacy / fallback: older schema used type=="result" or a "result" key.
        elif obj_type in ("result", "message") or "result" in obj:
            part = obj.get("result") or obj.get("output") or obj.get("message") or ""
            if part:
                legacy_parts.append(str(part))

            # Some legacy events also embed usage directly.
            usage_data = obj.get("usage") or {}
            if usage_data:
                input_tokens = int(usage_data.get("input_tokens", input_tokens))
                output_tokens = int(usage_data.get("output_tokens", output_tokens))

    # Prefer v0.129+ parts; fall back to legacy parts; never return raw NDJSON.
    if text_parts:
        result_text = "\n".join(text_parts)
    elif legacy_parts:
        result_text = "\n".join(legacy_parts)
    else:
        # Nothing parseable found — return empty string.  Callers that need text
        # will surface the empty response as an error at a higher level rather
        # than choking on raw NDJSON.
        result_text = ""

    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=cache_read_tokens,
    )
    return result_text, usage


def _flatten_to_prompt(request: CompletionRequest) -> str:
    """Flatten a CompletionRequest to a plain-text prompt string for the CLI."""
    parts: list[str] = []

    if request.system:
        parts.append(f"System: {request.system}\n\n")

    for msg in request.messages:
        role_label = "Human" if msg.role == "user" else "Assistant"
        texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        if texts:
            parts.append(f"{role_label}: {' '.join(texts)}\n\n")

    return "".join(parts).strip()
