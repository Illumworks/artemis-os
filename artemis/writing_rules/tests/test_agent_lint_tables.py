"""Unit tests for markdown-table flattening in lint_agent_text.

Pure-function tests — no DB required.  Run with:
    uv run pytest artemis/writing_rules/tests/test_agent_lint_tables.py -q
"""

from __future__ import annotations

from artemis.writing_rules.agent_lint import lint_agent_text

# ── 2-column table → bold-label bullets ──────────────────────────────────────


def test_two_col_table_flattened_to_bold_bullets() -> None:
    table = "| Field | Value |\n| --- | --- |\n| Status | Active |\n| Region | West |"
    result = lint_agent_text(table)
    assert "| --- |" not in result
    assert "- **Status:** Active" in result
    assert "- **Region:** West" in result
    # Header row should be dropped for 2-col case
    assert "Field" not in result or "**Field" not in result


def test_two_col_table_no_trailing_pipes() -> None:
    """Table rows without trailing pipes should still be parsed."""
    table = "| Field | Value\n| --- | ---\n| Name | Jon"
    result = lint_agent_text(table)
    assert "- **Name:** Jon" in result
    assert "| --- |" not in result


# ── 3-column table → comma-joined bullets ────────────────────────────────────


def test_three_col_table_flattened_to_comma_joined() -> None:
    table = (
        "| Signal | Score | Date |\n"
        "| --- | --- | --- |\n"
        "| HB 123 | 85 | 2026-01-15 |\n"
        "| SB 456 | 72 | 2026-02-01 |"
    )
    result = lint_agent_text(table)
    assert "| --- |" not in result
    # Data rows become bullet lines
    assert "- HB 123, 85, 2026-01-15" in result
    assert "- SB 456, 72, 2026-02-01" in result


# ── Table inside fenced code block is untouched ───────────────────────────────


def test_table_inside_code_fence_untouched() -> None:
    text = "Here is an example:\n```\n| A | B |\n| --- | --- |\n| x | y |\n```\nDone."
    result = lint_agent_text(text)
    # The pipe table inside the fence must survive intact
    assert "| A | B |" in result
    assert "| --- | --- |" in result
    assert "| x | y |" in result


# ── Non-table prose with a stray pipe is untouched ───────────────────────────


def test_stray_pipe_in_prose_untouched() -> None:
    prose = "The result is foo | bar (a bitwise OR)."
    result = lint_agent_text(prose)
    assert "foo | bar" in result


def test_single_pipe_row_no_separator_untouched() -> None:
    """A single pipe row without a separator row below it is NOT a table."""
    text = "| Just one row |"
    result = lint_agent_text(text)
    # No separator row means it should pass through as-is (minus emoji/em-dash)
    assert "|" in result


# ── Empty / normal text unchanged ─────────────────────────────────────────────


def test_empty_string_returns_empty() -> None:
    assert lint_agent_text("") == ""


def test_normal_text_unchanged() -> None:
    text = "Everything looks good. The campaign launches Monday."
    result = lint_agent_text(text)
    assert result == text


# ── Table embedded in surrounding text ───────────────────────────────────────


def test_table_embedded_in_surrounding_text() -> None:
    text = (
        "Here is a summary:\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        "| Owner | Jon |\n"
        "| Due | Friday |\n"
        "Let me know if you need anything else."
    )
    result = lint_agent_text(text)
    assert "- **Owner:** Jon" in result
    assert "- **Due:** Friday" in result
    assert "Here is a summary:" in result
    assert "Let me know if you need anything else." in result
    assert "| --- |" not in result


# ── Existing lint rules still apply after table flatten ──────────────────────


def test_emoji_stripped_from_table_output() -> None:
    """Emojis in table cells must be stripped by the existing emoji pass."""
    table = "| Item | Status |\n| --- | --- |\n| Deal | Done \U0001f44d |"
    result = lint_agent_text(table)
    # Thumbs-up emoji should be gone
    assert "\U0001f44d" not in result
    assert "- **Deal:** Done" in result


def test_em_dash_replaced_in_table_cell() -> None:
    """Em-dashes in table cells should be replaced by the lint pass."""
    table = "| Name | Note |\n| --- | --- |\n| Acme | Top—tier |"
    result = lint_agent_text(table)
    assert "—" not in result
    assert "- **Acme:**" in result
