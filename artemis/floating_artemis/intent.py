"""Observability-intent shortcut pre-router.

A small regex-based pre-filter that matches clearly observability-oriented
messages and routes them to a structured response instead of consuming an
LLM turn. The pattern list is intentionally small — false negatives are
cheap (just an extra LLM turn); false positives are bad (template response
when the user wanted a real conversation).

Conservative rules:
- Only match unambiguous observability queries.
- Err on the side of NOT matching when the message could mean anything else.
- Maximum 8 patterns total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    ACTIVE_RUNS = "active_runs"
    RECENT_FAILURES = "recent_failures"
    HEALTH_CHECK = "health_check"
    NONE = "none"


@dataclass(frozen=True)
class IntentMatch:
    kind: IntentKind
    confidence: float  # 0.0–1.0 (all matches here are 1.0 — it's binary)


_ACTIVE_RUNS_PATTERNS = [
    re.compile(r"what(?:'s|\s+is)\s+(?:currently\s+)?running", re.IGNORECASE),
    re.compile(r"(?:show|list)\s+(?:me\s+)?active\s+runs?", re.IGNORECASE),
    re.compile(r"any\s+(?:agents?|workflows?)\s+(?:currently\s+)?running", re.IGNORECASE),
    re.compile(r"what(?:'s|\s+is)\s+active\s+right\s+now", re.IGNORECASE),
]

_RECENT_FAILURES_PATTERNS = [
    re.compile(r"any\s+(?:recent\s+)?failures?", re.IGNORECASE),
    re.compile(
        r"(?:show|list|what)\s+(?:are\s+)?(?:the\s+)?recent\s+(?:agent\s+)?failures?", re.IGNORECASE
    ),
    re.compile(r"what\s+(?:agents?\s+)?(?:failed|broke)\s+recently", re.IGNORECASE),
]

_HEALTH_PATTERNS = [
    re.compile(r"^(?:system\s+)?health(?:\s+check)?[?.]?$", re.IGNORECASE),
    re.compile(r"are\s+(?:all\s+)?systems?\s+(?:ok|healthy|nominal)", re.IGNORECASE),
]


def classify_intent(message: str) -> IntentMatch:
    """Return the intent match for *message*, or IntentMatch(NONE) if no match."""
    text = message.strip()

    for pattern in _ACTIVE_RUNS_PATTERNS:
        if pattern.search(text):
            return IntentMatch(kind=IntentKind.ACTIVE_RUNS, confidence=1.0)

    for pattern in _RECENT_FAILURES_PATTERNS:
        if pattern.search(text):
            return IntentMatch(kind=IntentKind.RECENT_FAILURES, confidence=1.0)

    for pattern in _HEALTH_PATTERNS:
        if pattern.search(text):
            return IntentMatch(kind=IntentKind.HEALTH_CHECK, confidence=1.0)

    return IntentMatch(kind=IntentKind.NONE, confidence=0.0)


async def handle_observability_intent(
    intent: IntentMatch,
    *,
    owner_user_id: int | None = None,
) -> dict[str, Any] | None:
    """Execute the observability shortcut for the given intent.

    Returns a dict with a "response" key on match, or None if intent is NONE.
    """
    if intent.kind == IntentKind.NONE:
        return None

    if intent.kind == IntentKind.ACTIVE_RUNS:
        try:
            import artemis.db as _db
            from artemis.floating_artemis.repository import get_active_runs

            async with _db.SessionLocal() as session:
                runs = await get_active_runs(session, owner_user_id=owner_user_id)

            if not runs:
                response = "Nothing running right now."
            else:
                lines = [f"{r['run_type']} {r['subject_id']} — {r['status']}" for r in runs]
                response = f"{len(runs)} active run(s):\n" + "\n".join(lines)

            return {"intent": intent.kind, "response": response, "data": runs}
        except Exception as exc:
            return {
                "intent": intent.kind,
                "response": f"Couldn't fetch active runs: {exc}",
                "data": [],
            }

    if intent.kind == IntentKind.RECENT_FAILURES:
        from artemis.floating_artemis.tools.system import _recent_failures

        result = await _recent_failures({"limit": 5})
        return {"intent": intent.kind, "response": result, "data": None}

    if intent.kind == IntentKind.HEALTH_CHECK:
        from artemis.floating_artemis.tools.system import _health_check

        result = await _health_check({})
        return {"intent": intent.kind, "response": result, "data": None}

    return None
