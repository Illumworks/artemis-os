"""Pending-ask detection helpers.

Determines whether an outbound agent message constitutes an ask directed at
Jon (the owner).  Purely functional — no I/O.

Rules (any one sufficient):
  1. Text contains a @Jon mention (``<@USER_ID>`` where USER_ID is Jon's Slack
     user id, or the literal strings ``@Jon`` / ``@jon``).
  2. Text ends with a question mark (direct question).
  3. Text contains a question-word phrase that clearly requests a decision
     ("can you", "could you", "would you", "what do you think", "should we",
     "let me know", "please advise", "your call", etc.).

These heuristics are conservative — false-negatives are fine (we miss a few
asks); false-positives are costly (noise in Jon's DM).  The caller can always
pass an explicit ``force=True`` to override.
"""

from __future__ import annotations

import re

# Slack user-ID placeholders that are substituted at test time.
_JON_MENTION_RE = re.compile(
    r"<@[A-Z0-9]+>",  # any user mention — we check against the allowed list
    re.IGNORECASE,
)

# Lowercase phrases that signal a direct request for Jon's attention/input.
_ASK_PHRASES: tuple[str, ...] = (
    "can you",
    "could you",
    "would you",
    "should we",
    "should i",
    "what do you think",
    "let me know",
    "please advise",
    "your call",
    "up to you",
    "thoughts?",
    "input?",
    "feedback?",
    "okay with",
    "ok with",
    "waiting on you",
    "need your",
    "needs your",
)

# Regex for a trailing question — catches "..." followed by "?" at end-of-string.
_TRAILING_QUESTION_RE = re.compile(r"\?\s*$", re.MULTILINE)


def is_jon_mention(text: str, *, jon_slack_id: str = "") -> bool:
    """Return True if *text* contains a Slack @-mention matching Jon's id.

    ``jon_slack_id`` is the Slack user ID (e.g. ``U09F3EPJXSQ``).  When empty
    the check falls back to the literal strings ``@Jon`` / ``@jon``.
    """
    if jon_slack_id:
        if f"<@{jon_slack_id}>" in text:
            return True
    # Fallback literal check (useful in tests / when ID is unknown)
    return bool(re.search(r"@[Jj]on\b", text))


def _has_ask_phrase(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _ASK_PHRASES)


def _has_trailing_question(text: str) -> bool:
    return bool(_TRAILING_QUESTION_RE.search(text))


def is_pending_ask(text: str, *, jon_slack_id: str = "") -> bool:
    """Return True if this outbound agent message is an ask directed at Jon.

    Used to decide whether to record a pending-ask row.  Intentionally
    conservative — we'd rather miss a few than flood Jon's DM.

    Detection rules (any one sufficient):
      1. @-mention of Jon (Slack <@UID> or literal @Jon).
      2. Trailing question mark — text clearly ends with "?".
      3. Ask phrase present (e.g. "let me know", "can you", "your call") —
         these are directive phrases that solicit Jon's input even without
         a literal "?".
    """
    if not text or not text.strip():
        return False
    if is_jon_mention(text, jon_slack_id=jon_slack_id):
        return True
    if _has_trailing_question(text):
        return True
    if _has_ask_phrase(text):
        return True
    return False


def extract_summary(text: str, *, max_len: int = 200) -> str:
    """Return a short summary of the ask for display in escalation messages.

    Takes the first sentence / line that contains a question or mention,
    falling back to the first ``max_len`` characters.
    """
    stripped = text.strip()
    # Try first line that ends with "?"
    for line in stripped.splitlines():
        line = line.strip()
        if line.endswith("?") and len(line) > 5:
            return line[:max_len]
    # Fall back to first ``max_len`` chars
    if len(stripped) <= max_len:
        return stripped
    return stripped[:max_len].rsplit(" ", 1)[0] + "..."
