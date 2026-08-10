"""Deterministic post-generation lint for named-agent outbound text."""

from __future__ import annotations

import re

# ── Markdown → Slack mrkdwn converter ────────────────────────────────────────


def md_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn syntax.

    Conversions applied (outside code spans/blocks and Slack-link spans):
      - ``**bold**`` / ``__bold__``          → ``*bold*``  (Slack bold)
      - ``## Header`` / ``### Header`` …     → ``*Header*``  (any ATX heading level)
      - ``[label](url)``                      → ``<url|label>``  (Slack link)

    Deliberately left alone:
      - Fenced code blocks (``` … ```) and inline code (` … `) — untouched
      - Bare URLs (https?://…) — untouched (Slack auto-links them)
      - ``_italic_`` — already valid Slack italic; not double-converted
      - Bullet list markers (``- `` / ``* `` at line start) — Slack renders these fine
      - Existing Slack mrkdwn (``<url|label>``, ``*bold*``) — idempotent-safe

    Safety / idempotency:
      - Bold conversion runs on ``**...**`` / ``__...__`` only; single ``*`` is left
        alone so Slack's existing italic/bold markers are not double-processed.
      - Heading conversion strips the leading ``#``-sequence + whitespace so the
        text itself is never double-bolded on re-run (``*Header*`` has no ``#``).
      - Slack-link conversion is guarded against mangling existing ``<url|text>``
        spans because those contain ``<`` which is not a ``[`` character.
      - All transformations are performed on non-protected substrings only —
        content inside `` ` `` and ``` ``` spans is passed through verbatim.
    """
    if not text:
        return text

    # ── Protected-span splitter ────────────────────────────────────────────────
    # We split the text into alternating unprotected / protected segments.
    # Protected = fenced code blocks, inline code, existing Slack link spans (<…>).
    # Transformations only apply to unprotected segments.
    _PROTECTED_RE = re.compile(
        r"```[\s\S]*?```"  # fenced code block
        r"|`[^`\n]+`"  # inline code
        r"|<[^>]+\|[^>]+>"  # existing Slack link  <url|label>
        r"|<https?://[^>]+>",  # bare Slack URL  <https://…>
        re.MULTILINE,
    )

    segments: list[str] = []
    cursor = 0
    for m in _PROTECTED_RE.finditer(text):
        segments.append(("free", text[cursor : m.start()]))
        segments.append(("prot", m.group(0)))
        cursor = m.end()
    segments.append(("free", text[cursor:]))

    def _convert_free(s: str) -> str:
        """Apply all mrkdwn conversions to a free (unprotected) text segment."""
        if not s:
            return s

        # 1. Markdown links [label](url)  →  <url|label>
        #    Guard: only fire when url doesn't already contain < to avoid
        #    re-processing partially-converted text.
        s = re.sub(r"\[([^\]]+)\]\(([^)<>]+)\)", r"<\2|\1>", s)

        # 2. ATX headings (any level: # … ######) at the start of a line.
        #    Strips the leading hashes + optional space, then wraps in *…*.
        #    Already-converted lines start with * so no re-match.
        s = re.sub(r"(?m)^#{1,6}\s+(.*?)\s*$", r"*\1*", s)

        # 3. Bold: **text** and __text__  →  *text*
        #    Uses a non-greedy match; requires at least 1 non-whitespace char
        #    between the delimiters to avoid converting standalone **.
        #    The negative look-around prevents matching a leading/trailing space
        #    inside the markers (common in ``** bold **`` typos that would look
        #    odd anyway).
        s = re.sub(r"\*\*(\S[\s\S]*?\S|\S)\*\*", r"*\1*", s)
        s = re.sub(r"__(\S[\s\S]*?\S|\S)__", r"*\1*", s)

        return s

    return "".join(_convert_free(seg) if kind == "free" else seg for kind, seg in segments)


_PROTECTED_SPAN_RE = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|https?://[^\s<>()]+",
    re.MULTILINE,
)

# Matches a fenced code block so we can skip table detection inside them.
_FENCED_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)

# A pipe-table row: at least two pipe-separated cells (leading/trailing pipe optional).
_TABLE_ROW_RE = re.compile(r"^\s*\|?([^|\n]+\|)+[^|\n]*\|?\s*$")

# A separator row: cells containing only dashes, colons, and spaces.
_SEP_ROW_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _parse_pipe_row(line: str) -> list[str]:
    """Split a pipe-table row into trimmed cell strings."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _flatten_table(table_lines: list[str]) -> str:
    """Convert a detected markdown table to bullet lines.

    - 2-column tables: `- **Header0:** cell1` per data row (header row dropped).
    - 3+ column tables: `- cell0, cell1, cell2, ...` per data row (header kept as bold label line).
    - Separator row always dropped.
    """
    if len(table_lines) < 2:  # need at least a header + separator
        return "\n".join(table_lines)

    # Find separator row index (must be the 2nd row for a canonical table).
    sep_idx = 1
    if not _SEP_ROW_RE.match(table_lines[sep_idx]):
        return "\n".join(table_lines)  # not a real table

    header_cells = _parse_pipe_row(table_lines[0])
    data_rows = table_lines[sep_idx + 1 :]

    output_lines: list[str] = []

    if len(header_cells) == 2:
        # 2-column → bold-label bullets; drop header row.
        for row_line in data_rows:
            cells = _parse_pipe_row(row_line)
            if len(cells) >= 2:
                output_lines.append(f"- **{cells[0]}:** {cells[1]}")
            elif len(cells) == 1 and cells[0]:
                output_lines.append(f"- {cells[0]}")
    else:
        # 3+ columns → comma-joined bullets; keep header as a bold label line.
        header_label = ", ".join(f"**{c}**" for c in header_cells if c)
        if header_label:
            output_lines.append(header_label)
        for row_line in data_rows:
            cells = _parse_pipe_row(row_line)
            non_empty = [c for c in cells if c]
            if non_empty:
                output_lines.append("- " + ", ".join(non_empty))

    return "\n".join(output_lines)


def _flatten_tables(text: str) -> str:
    """Replace all markdown tables in *text* with flattened bullet lists.

    Tables inside fenced code blocks (``` ... ```) are left untouched.
    A sequence of lines is recognised as a table only when it contains a
    valid separator row (cells of only `-`, `:`, and spaces) immediately
    after the first (header) row.
    """
    # Collect fenced-block spans so we can skip them.
    protected: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _FENCED_BLOCK_RE.finditer(text)
    ]

    def _in_protected(pos: int) -> bool:
        return any(start <= pos < end for start, end in protected)

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    # Track character offset of line start so we can check protection.
    char_offset = 0

    while i < len(lines):
        line = lines[i]
        line_start = char_offset
        char_offset += len(line) + 1  # +1 for the \n

        # If this line is inside a fenced block, pass through.
        if _in_protected(line_start):
            result.append(line)
            i += 1
            continue

        # Check if this line looks like a table header row.
        if _TABLE_ROW_RE.match(line) and "|" in line:
            # Collect a run of pipe-containing lines.
            table_block: list[str] = [line]
            j = i + 1
            j_offset = char_offset
            while j < len(lines) and "|" in lines[j] and not _in_protected(j_offset):
                table_block.append(lines[j])
                j_offset += len(lines[j]) + 1
                j += 1

            # Only treat as a table if we have at least header + separator rows
            # and the second line is a real separator row.
            if len(table_block) >= 2 and _SEP_ROW_RE.match(table_block[1]):
                flattened = _flatten_table(table_block)
                result.append(flattened)
                i = j
                char_offset = j_offset
                continue

        result.append(line)
        i += 1

    return "\n".join(result)


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
    """Remove banned punctuation/emojis from agent text without touching code or URLs.

    Pre-pass: markdown tables are flattened to bullet lists before the
    emoji/em-dash stripping runs.  Tables inside fenced code blocks are
    left untouched, consistent with the existing protected-span logic.
    """
    if not text:
        return text

    # Pre-pass: flatten markdown tables (skips content inside ``` blocks).
    text = _flatten_tables(text)

    parts: list[str] = []
    cursor = 0
    for match in _PROTECTED_SPAN_RE.finditer(text):
        parts.append(_lint_plain_text(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_lint_plain_text(text[cursor:]))
    result = "".join(parts)
    return re.sub(r"[ \t]+$", "", result)
