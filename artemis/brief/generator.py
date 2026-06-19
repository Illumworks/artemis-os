"""Daily brief generator — orchestrates source gathering, LLM call, persistence.

Architecture:
  gather_sources → _build_context_string → _build_prompt
    → LLM completion (resolve_adapter chain)
      → Pydantic validate (DailyBrief) → save_brief_snapshot → return brief

H5: LLM output is validated against DailyBrief before persistence.
Validation failure triggers one retry with the error injected into the prompt.
Persistent failure falls back to empty DailyBrief() + warning log.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.brief import repository
from artemis.brief.prompt import _build_context_string, _build_prompt
from artemis.brief.schemas import DailyBrief
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


async def _generate_with_retry(
    prompt: str,
    *,
    max_retries: int = 1,
) -> tuple[DailyBrief, str, int | None, int | None]:
    """Call LLM, validate against DailyBrief, retry once on validation failure.

    Returns (validated_brief, model_used, tokens_in, tokens_out).
    On persistent failure returns (DailyBrief(), ...) — empty default — so
    the caller can still persist an audit row without breaking the API.
    """
    last_error: str | None = None
    model_used: str = BRIEF_MODEL
    tokens_in: int | None = None
    tokens_out: int | None = None

    for attempt in range(max_retries + 1):
        current_prompt = prompt
        if last_error is not None:
            current_prompt = (
                prompt
                + f"\n\n[CORRECTION NEEDED] Your previous response failed schema validation:\n"
                f"{last_error}\n"
                "Please return a valid JSON object matching the required schema exactly."
            )

        try:
            output, model_used, tokens_in, tokens_out = await _call_llm(current_prompt)
        except BriefGenerationError:
            # LLM call itself failed — don't retry
            logger.warning("Daily brief LLM call failed on attempt %d", attempt)
            return DailyBrief(), model_used, tokens_in, tokens_out

        # Extract JSON from output (strip markdown fences if present)
        match = _JSON_RE.search(output)
        if not match:
            last_error = f"No JSON object found in response. Output: {output[:200]!r}"
            if attempt >= max_retries:
                logger.warning("Daily brief validation persistent failure: %s", last_error)
                return DailyBrief(), model_used, tokens_in, tokens_out
            continue

        try:
            brief = DailyBrief.model_validate_json(match.group(0))
            return brief, model_used, tokens_in, tokens_out
        except ValidationError as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                logger.warning("Daily brief validation persistent failure: %s", exc)
                return DailyBrief(), model_used, tokens_in, tokens_out

    # Unreachable, but satisfies type checker
    return DailyBrief(), model_used, tokens_in, tokens_out


async def generate_brief(session: AsyncSession) -> dict[str, Any]:
    """Generate a new brief, persist the snapshot, and return the parsed brief.

    H5: LLM output is validated against DailyBrief (Pydantic).  One retry on
    failure; persistent failure persists an empty DailyBrief + logs warning.

    After LLM generation, engagement weights (from Jon's past reactions) are
    applied to re-rank top_priorities and waiting_on_you so the brief surfaces
    more of what Jon engages with and less of what he ignores/mutes.

    Raises BriefGenerationError on failure.
    """
    sources = await gather_sources(session)
    context_string = _build_context_string(sources)
    prompt = _build_prompt(context_string)

    brief_model, model_used, tokens_in, tokens_out = await _generate_with_retry(prompt)

    # ── Apply engagement-weight re-ranking (P-learning v1) ───────────────────
    engagement_weights: dict[str, Any] = sources.get("_engagement_weights") or {}
    if engagement_weights:
        try:
            from artemis.proactivity.brief_reactions import weight_priorities, weight_waiting_on

            weighted_priorities = weight_priorities(
                [p.model_dump(mode="json") for p in brief_model.top_priorities],
                engagement_weights,
            )
            weighted_waiting = weight_waiting_on(
                [w.model_dump(mode="json") for w in brief_model.waiting_on_you],
                engagement_weights,
            )
            # Rebuild the model with weighted lists.
            from artemis.brief.schemas import BriefPriority, WaitingItem

            brief_model = brief_model.model_copy(
                update={
                    "top_priorities": [BriefPriority.model_validate(p) for p in weighted_priorities],
                    "waiting_on_you": [WaitingItem.model_validate(w) for w in weighted_waiting],
                }
            )
            logger.debug(
                "Brief engagement weighting applied: %d priorities, %d waiting items (from %d weights)",
                len(brief_model.top_priorities),
                len(brief_model.waiting_on_you),
                len(engagement_weights),
            )
        except Exception:
            logger.warning("Brief engagement weighting failed — using unweighted brief", exc_info=True)

    brief: dict[str, Any] = brief_model.model_dump(mode="json")

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
