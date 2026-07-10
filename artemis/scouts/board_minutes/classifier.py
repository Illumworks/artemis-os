"""Mention + sentiment classifier for board-meeting text.

Detects mentions of Amira, screentime policy, and AI-in-schools in agenda-item
bodies / minutes text, classifies sentiment, and extracts the relevant
excerpt.  Two layers:

1. ``quick_relevance()`` — cheap keyword prefilter.  Only items that pass are
   sent to the LLM (bounds cost: most agenda items are facilities/HR/consent
   noise).
2. ``classify_mention()`` — LLM classification through the standard provider
   adapter layer.  Fail-safe: any LLM/JSON/validation error returns ``None``
   (callers treat that as "not classified", never crash).
3. ``keyword_classification()`` — deterministic degraded mode used when no
   LLM adapter is available: topics from keyword hits, sentiment "neutral",
   excerpt = a window around the first match.

No network or model calls happen at import time; the adapter is injected.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_logger = logging.getLogger(__name__)

# Model used for classification when the caller doesn't override it.
# Cheap + capable — same tier the memory judges use.
DEFAULT_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"

TOPICS: tuple[str, ...] = ("amira", "screentime", "ai_in_schools")

_MAX_BODY_CHARS = 6000
_EXCERPT_WINDOW = 240

# ---------------------------------------------------------------------------
# Keyword prefilter
# ---------------------------------------------------------------------------

_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "amira": re.compile(r"\bamira\b", re.IGNORECASE),
    "screentime": re.compile(
        r"screen[\s-]?time|device[\s-]?free|cell\s?phone|cellphone|phone\s(?:policy|ban|restriction)"
        r"|personal\selectronic\sdevice|bell[\s-]to[\s-]bell",
        re.IGNORECASE,
    ),
    # The bare acronym stays case-sensitive ((?-i:...)) so prose containing a
    # stray lowercase "ai" doesn't false-positive the prefilter.
    "ai_in_schools": re.compile(
        r"\bartificial intelligence\b|(?-i:\bA\.?I\.?\b)|chat\s?gpt|generative\s|(?-i:\bLLM\b)"
        r"|machine learning",
        re.IGNORECASE,
    ),
}


def detect_topics(text: str) -> list[str]:
    """Return the topics whose keyword pattern matches *text* (may be empty)."""
    return [topic for topic, pattern in _TOPIC_PATTERNS.items() if pattern.search(text)]


def quick_relevance(text: str) -> bool:
    """Cheap prefilter — True when any topic keyword appears in *text*."""
    return bool(detect_topics(text))


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


class MentionClassification(BaseModel):
    """Validated LLM (or keyword-fallback) classification of one item."""

    model_config = ConfigDict(extra="ignore")

    relevant: bool
    topics: list[Literal["amira", "screentime", "ai_in_schools"]] = Field(default_factory=list)
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    excerpt: str = ""
    rationale: str = ""
    #: "llm" or "keyword" — how this classification was produced.
    method: str = "llm"

    @field_validator("excerpt", "rationale", mode="before")
    @classmethod
    def _coerce_str(cls, value: Any) -> str:
        return "" if value is None else str(value)[:1000]


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an analyst scanning US school-district board meeting records.

Given one agenda item (title + body text), decide whether it substantively discusses any of:
- "amira": Amira Learning (the AI reading tutor product)
- "screentime": student screen-time limits, device-free policies, cell phone bans/restrictions
- "ai_in_schools": AI use in schools — adoption, pilots, guidance, pauses, or bans

Respond with ONLY a JSON object, no prose, in exactly this shape:
{
  "relevant": true|false,
  "topics": ["amira"|"screentime"|"ai_in_schools", ...],
  "sentiment": "positive"|"neutral"|"negative",
  "excerpt": "<verbatim quote of the most relevant passage, <= 60 words>",
  "rationale": "<one short sentence>"
}

Rules:
- "relevant" is true only for substantive discussion or board action, not a passing keyword hit
  (e.g. "screening" or an "AI" acronym for something else is NOT relevant).
- "sentiment" is the board's/district's disposition toward the topic action being discussed
  (e.g. adopting screen-time limits = "positive" toward screentime policy; pausing an AI tool =
  "negative" toward ai_in_schools adoption; a mention of Amira in a purchase/renewal = "positive").
- "excerpt" must be copied verbatim from the provided text."""


async def classify_mention(
    title: str,
    body: str,
    *,
    adapter: Any,
    model: str = DEFAULT_CLASSIFIER_MODEL,
) -> MentionClassification | None:
    """Classify one agenda item via the LLM adapter.

    Returns ``None`` on ANY failure (LLM error, malformed JSON, validation
    error) — callers must treat ``None`` as "no classification", never crash.
    """
    # Imported lazily so importing this module never pulls the provider stack.
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    payload = json.dumps(
        {"title": title[:500], "body": body[:_MAX_BODY_CHARS]},
        ensure_ascii=False,
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=payload)])],
        system=_SYSTEM_PROMPT,
        model=model,
        max_tokens=512,
        cache_system=True,
    )
    try:
        response = await adapter.complete(request)
        parts = [
            block.text
            for block in response.message.content
            if getattr(block, "type", "") == "text"
        ]
        raw = "".join(parts).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("classifier output is not a JSON object")
        data.setdefault("relevant", False)
        data["method"] = "llm"
        # Drop unknown topic labels instead of failing the whole result.
        data["topics"] = [t for t in (data.get("topics") or []) if t in TOPICS]
        return MentionClassification.model_validate(data)
    except Exception:
        _logger.warning(
            "Board mention classifier failed for item %r — skipping (fail-safe).",
            title[:80],
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Keyword fallback (degraded mode — no LLM available)
# ---------------------------------------------------------------------------


def keyword_classification(title: str, body: str) -> MentionClassification | None:
    """Deterministic fallback when no LLM adapter is available.

    Topics from keyword matches; sentiment is always "neutral" (we cannot
    judge disposition without a model); excerpt is a text window around the
    first match so a human reviewer can triage.
    """
    combined = f"{title}\n{body}"
    topics = detect_topics(combined)
    if not topics:
        return None

    match = _TOPIC_PATTERNS[topics[0]].search(combined)
    excerpt = ""
    if match:
        start = max(0, match.start() - _EXCERPT_WINDOW // 2)
        excerpt = combined[start : match.start() + _EXCERPT_WINDOW].strip()

    return MentionClassification(
        relevant=True,
        topics=topics,  # already restricted to TOPICS by detect_topics
        sentiment="neutral",
        excerpt=excerpt,
        rationale="keyword match (no LLM adapter available)",
        method="keyword",
    )
