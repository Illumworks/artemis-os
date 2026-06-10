"""Deterministic post-generation lint for named-agent outbound text."""

from __future__ import annotations

import re

_PROTECTED_SPAN_RE = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|https?://[^\s<>()]+",
    re.MULTILINE,
)

_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x2300, 0x23FF),
    (0x2600, 0x27BF),
    (0x1F1E6, 0x1F1FF),
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F780, 0x1F7FF),
    (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
)
_EMOJI_MODIFIERS = {0x200D, 0xFE0F}


def _is_emoji_codepoint(codepoint: int) -> bool:
    if codepoint in _EMOJI_MODIFIERS:
        return True
    if 0x1F3FB <= codepoint <= 0x1F3FF:
        return True
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)


def _strip_emoji(text: str) -> str:
    return "".join(ch for ch in text if not _is_emoji_codepoint(ord(ch)))


def _lint_plain_text(text: str) -> str:
    text = _strip_emoji(text)
    text = text.replace("\u2014", ", ").replace("\u2013", ", ")
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s+([.;:!?])", r"\1", text)
    text = re.sub(r",(?:\s*,)+", ", ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\t+", " ", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n +", "\n", text)
    text = re.sub(r",\s*(?=\n|$)", "", text)
    return text


def lint_agent_text(text: str) -> str:
    """Remove banned punctuation/emojis from agent text without touching code or URLs."""
    if not text:
        return text

    parts: list[str] = []
    cursor = 0
    for match in _PROTECTED_SPAN_RE.finditer(text):
        parts.append(_lint_plain_text(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_lint_plain_text(text[cursor:]))
    result = "".join(parts)
    return re.sub(r"[ \t]+$", "", result)
