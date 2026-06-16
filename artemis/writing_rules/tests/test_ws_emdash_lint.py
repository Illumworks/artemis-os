"""Deterministic em/en-dash enforcement for Writing Studio drafted content.

These are pure-function tests — no DB, no network.  They verify that:

1. lint_agent_text strips em/en-dashes and preserves markdown structure
   (the underlying guarantee is already covered by test_agent_lint_tables.py;
   this file tests it specifically in the WS compose-path context).

2. The compose path (parse_draft_fence → lint_agent_text) removes dashes from
   both the chat message AND the fenced draft deliverable.

3. The rewrite-span path receives the same treatment.

4. Markdown structure (headings, bullets, bold, code fences) survives lint.

Run with:
    uv run pytest artemis/writing_rules/tests/test_ws_emdash_lint.py -q
"""

from __future__ import annotations

import pytest

from artemis.marketing.writing_studio.compose_engine import parse_draft_fence
from artemis.writing_rules.agent_lint import lint_agent_text

# ── 1. Basic lint correctness (em/en-dash removal) ────────────────────────────


def test_em_dash_replaced_with_comma() -> None:
    assert "—" not in lint_agent_text("This is top—tier copy.")


def test_en_dash_replaced_with_comma() -> None:
    assert "–" not in lint_agent_text("Pages 1–10 of the report.")


def test_both_dashes_in_one_string() -> None:
    result = lint_agent_text("a—b–c")
    assert "—" not in result
    assert "–" not in result


def test_no_dashes_text_unchanged() -> None:
    text = "Everything looks good. The campaign launches Monday."
    assert lint_agent_text(text) == text


# ── 2. Markdown structure survives lint ──────────────────────────────────────


def test_headings_survive() -> None:
    text = "## Campaign Summary\n\nThis is top-quality copy."
    result = lint_agent_text(text)
    assert "## Campaign Summary" in result


def test_bold_text_survives() -> None:
    text = "**Important:** Review before sending."
    result = lint_agent_text(text)
    assert "**Important:**" in result


def test_bullet_list_survives() -> None:
    text = "- First bullet\n- Second bullet\n- Third bullet"
    result = lint_agent_text(text)
    assert "- First bullet" in result
    assert "- Second bullet" in result
    assert "- Third bullet" in result


def test_code_fence_content_untouched() -> None:
    """Em-dashes inside code fences must NOT be replaced (protected span)."""
    text = "Here is code:\n```python\nx = a—b  # em-dash in code\n```\nDone."
    result = lint_agent_text(text)
    # The em-dash inside the fence is preserved (protected span).
    assert "a—b" in result


def test_url_in_text_untouched() -> None:
    """URLs are protected spans; no mangling."""
    text = "See https://example.com/page—info for details."
    result = lint_agent_text(text)
    # URL is preserved even if it contains unusual chars
    assert "https://example.com/page—info" in result


def test_headings_and_bullets_with_dashes() -> None:
    """Dashes in headings/bullets are stripped; structure survives."""
    text = (
        "## Key Points\n\n"
        "- Headline copy—designed to convert\n"
        "- Results: 85% open rate–top quartile\n"
    )
    result = lint_agent_text(text)
    assert "—" not in result
    assert "–" not in result
    assert "## Key Points" in result
    assert result.count("- ") >= 2  # both bullets present


# ── 3. Compose path: parse_draft_fence + lint_agent_text ──────────────────────
#
# Simulate what compose_draft does after the LLM returns:
#   cleaned_response_text → parse_draft_fence → lint both outputs


def test_compose_path_strips_dash_from_chat_message() -> None:
    """The conversational part of a compose reply loses em-dashes."""
    llm_response = (
        "Here's a draft — have a look.\n\n"
        "```artemis-draft\n"
        "The body copy goes here.\n"
        "```"
    )
    chat_message, draft_copy = parse_draft_fence(llm_response)
    chat_message = lint_agent_text(chat_message)
    if draft_copy is not None:
        draft_copy = lint_agent_text(draft_copy)

    assert "—" not in chat_message
    assert draft_copy is not None
    assert "—" not in draft_copy


def test_compose_path_strips_dash_from_deliverable_fence() -> None:
    """The fenced draft copy (deliverable) loses em-dashes."""
    llm_response = (
        "Revised draft below.\n\n"
        "```artemis-draft\n"
        "This is top—tier copy designed to convert.\n"
        "Pages 1–5 outline the key points.\n"
        "```"
    )
    chat_message, draft_copy = parse_draft_fence(llm_response)
    chat_message = lint_agent_text(chat_message)
    if draft_copy is not None:
        draft_copy = lint_agent_text(draft_copy)

    assert draft_copy is not None
    assert "—" not in draft_copy
    assert "–" not in draft_copy
    # Prose is still there (dashes replaced with comma + space)
    assert "top" in draft_copy
    assert "tier" in draft_copy


def test_compose_path_no_fence_strips_chat_message() -> None:
    """When no artemis-draft fence is present, the full response is linted."""
    llm_response = "Sure — here are some ideas for the campaign copy."
    chat_message, draft_copy = parse_draft_fence(llm_response)
    chat_message = lint_agent_text(chat_message)

    assert draft_copy is None
    assert "—" not in chat_message
    assert "Sure" in chat_message  # prose survives


def test_compose_path_markdown_in_fence_survives() -> None:
    """Markdown structure inside the artemis-draft fence survives lint."""
    llm_response = (
        "Here is the draft.\n\n"
        "```artemis-draft\n"
        "## Campaign Brief\n\n"
        "**Objective:** Increase awareness—driving enrollment.\n\n"
        "- Bullet one\n"
        "- Bullet two\n"
        "```"
    )
    chat_message, draft_copy = parse_draft_fence(llm_response)
    chat_message = lint_agent_text(chat_message)
    if draft_copy is not None:
        draft_copy = lint_agent_text(draft_copy)

    assert draft_copy is not None
    assert "—" not in draft_copy
    assert "## Campaign Brief" in draft_copy
    assert "**Objective:**" in draft_copy
    assert "- Bullet one" in draft_copy
    assert "- Bullet two" in draft_copy


# ── 4. Rewrite-span path: lint_agent_text applied to span result ──────────────


def test_rewrite_span_path_strips_em_dash() -> None:
    """Simulates the rewrite-span endpoint's lint step."""
    # Model returns a span with em-dashes (unwrapped already by parse_draft_fence logic)
    rewritten_text = "Top—tier engagement copy designed for superintendents."
    rewritten_text = lint_agent_text(rewritten_text)

    assert "—" not in rewritten_text
    assert "tier" in rewritten_text


def test_rewrite_span_path_strips_en_dash() -> None:
    rewritten_text = "Results improved 10–15% across all cohorts."
    rewritten_text = lint_agent_text(rewritten_text)

    assert "–" not in rewritten_text
    assert "Results improved" in rewritten_text


def test_rewrite_span_path_preserves_structure() -> None:
    """Light markdown in a rewritten span survives lint."""
    rewritten_text = "**Achievement:** Students improved—85% met benchmarks."
    rewritten_text = lint_agent_text(rewritten_text)

    assert "—" not in rewritten_text
    assert "**Achievement:**" in rewritten_text
    assert "85%" in rewritten_text
