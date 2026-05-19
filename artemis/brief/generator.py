"""Daily brief generator — orchestrates source gathering, LLM call, persistence.

Architecture:
  gather_sources → _build_context_string → _build_prompt
    → LLM completion (resolve_adapter chain)
      → parse JSON → save_brief_snapshot → return brief
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.brief import repository
from artemis.brief.prompt import _build_context_string, _build_prompt
from artemis.brief.sources import gather_sources

logger = logging.getLogger(__name__)

BRIEF_MODEL = "claude-haiku-4-5-20251001"

_JSON_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)


class BriefGenerationError(Exception):
    pass


def _resolve_adapter() -> tuple[Any, str]:
    """Walk the provider chain and return (adapter, provider_name).

    Chain: claude-code → codex → lm-studio → anthropic
    Raises BriefGenerationError if no provider is available.
    """
    from artemis.providers import get_adapter
    from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

    for candidate in ("claude-code", "codex", "lm-studio", "anthropic"):
        try:
            adapter = get_adapter(candidate)
            logger.info("brief: resolved adapter %r", candidate)
            return adapter, candidate
        except (MissingApiKeyError, UnknownProviderError):
            continue
        except Exception:
            continue

    raise BriefGenerationError("No LLM provider available for brief generation")


async def _call_llm(prompt: str) -> tuple[str, str, int | None, int | None]:
    """Return (output_text, model_used, tokens_input, tokens_output)."""
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    adapter, provider_name = _resolve_adapter()

    model = BRIEF_MODEL if provider_name in ("claude-code", "anthropic") else None

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        system="You are a JSON-output assistant. Return ONLY valid JSON, no markdown, no prose.",
        max_tokens=1024,
        model=model,
    )

    try:
        response = await adapter.complete(request)
    except Exception as exc:
        raise BriefGenerationError(f"LLM call failed: {exc}") from exc

    output = ""
    for block in response.message.content:
        if isinstance(block, TextBlock):
            output += block.text

    tokens_in = response.usage.input_tokens if response.usage else None
    tokens_out = response.usage.output_tokens if response.usage else None
    model_used = model or provider_name

    return output, model_used, tokens_in, tokens_out


async def generate_brief(session: AsyncSession) -> dict[str, Any]:
    """Generate a new brief, persist the snapshot, and return the parsed brief.

    Raises BriefGenerationError on failure.
    """
    sources = await gather_sources(session)
    context_string = _build_context_string(sources)
    prompt = _build_prompt(context_string)

    output, model_used, tokens_in, tokens_out = await _call_llm(prompt)

    match = _JSON_RE.search(output)
    if not match:
        raise BriefGenerationError(
            f"Brief generation returned no JSON. Raw output: {output[:200]!r}"
        )

    try:
        brief: dict[str, Any] = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BriefGenerationError(f"Brief JSON parse failed: {exc}") from exc

    from datetime import UTC, datetime

    brief["generatedAt"] = datetime.now(UTC).isoformat()
    brief["sourcesUsed"] = [
        s
        for s, v in [
            ("jira", sources.get("jira") and sources["jira"].get("connected")),
            ("calendar", sources.get("calendar")),
            ("slack", sources.get("slack") and sources["slack"].get("connected")),
            ("okr", sources.get("okr") and sources["okr"].get("objectives")),
            ("sessions", sources.get("sessions")),
            ("memory", sources.get("memory")),
            ("continuity", sources.get("previousBrief")),
        ]
        if v
    ]

    snapshot = await repository.save_brief_snapshot(
        session,
        brief_json=brief,
        sources_json={
            "sources": brief["sourcesUsed"],
            "contextTokens": len(context_string),
        },
        model=model_used,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
    )

    brief["_snapshotId"] = snapshot.id
    return brief


async def get_latest_brief(session: AsyncSession) -> dict[str, Any] | None:
    """Return the latest persisted brief (no generation). None if none exists."""
    snapshot = await repository.get_latest_brief_snapshot(session)
    if snapshot is None:
        return None
    try:
        brief = dict(snapshot.brief_json) if isinstance(snapshot.brief_json, dict) else {}
        brief["_snapshotId"] = snapshot.id
        brief["_generatedAt"] = snapshot.generated_at.isoformat()
        brief["_tokensInput"] = snapshot.tokens_input
        brief["_tokensOutput"] = snapshot.tokens_output
        return brief
    except Exception:
        return None
