"""Unit tests for md_to_mrkdwn — the Markdown → Slack mrkdwn converter.

Pure-function tests: no DB, no network, no env vars required.

Run with:
    uv run pytest artemis/writing_rules/tests/test_md_to_mrkdwn.py -q
"""

from __future__ import annotations

from artemis.writing_rules.agent_lint import md_to_mrkdwn

# ── Bold ─────────────────────────────────────────────────────────────────────


def test_double_asterisk_bold() -> None:
    assert md_to_mrkdwn("**bold**") == "*bold*"


def test_double_underscore_bold() -> None:
    assert md_to_mrkdwn("__bold__") == "*bold*"


def test_bold_inline() -> None:
    result = md_to_mrkdwn("Hello **world**, how are you?")
    assert result == "Hello *world*, how are you?"


def test_bold_multi_word() -> None:
    assert md_to_mrkdwn("**this is bold text**") == "*this is bold text*"


def test_single_asterisk_unchanged() -> None:
    # Single * is already valid Slack italic — must not be touched
    assert md_to_mrkdwn("*italic*") == "*italic*"


# ── Headers ──────────────────────────────────────────────────────────────────


def test_h2_header() -> None:
    assert md_to_mrkdwn("## My Header") == "*My Header*"


def test_h3_header() -> None:
    assert md_to_mrkdwn("### Section Three") == "*Section Three*"


def test_h1_header() -> None:
    assert md_to_mrkdwn("# Top Level") == "*Top Level*"


def test_h6_header() -> None:
    assert md_to_mrkdwn("###### Deep Level") == "*Deep Level*"


def test_header_in_multiline() -> None:
    text = "Intro\n## Summary\nBody text"
    result = md_to_mrkdwn(text)
    assert result == "Intro\n*Summary*\nBody text"


def test_hash_mid_line_not_converted() -> None:
    # A # that is NOT at the start of a line must be left alone
    assert md_to_mrkdwn("Use #hashtag today") == "Use #hashtag today"


# ── Links ─────────────────────────────────────────────────────────────────────


def test_markdown_link() -> None:
    assert md_to_mrkdwn("[Click here](https://example.com)") == "<https://example.com|Click here>"


def test_markdown_link_inline() -> None:
    result = md_to_mrkdwn("See [docs](https://docs.example.com) for details.")
    assert result == "See <https://docs.example.com|docs> for details."


# ── Bullets — must be preserved ──────────────────────────────────────────────


def test_dash_bullet_unchanged() -> None:
    text = "- item one\n- item two"
    assert md_to_mrkdwn(text) == text


def test_asterisk_bullet_unchanged() -> None:
    # "* item" at line start is a bullet, not bold; md_to_mrkdwn must not mangle it
    text = "* item one\n* item two"
    result = md_to_mrkdwn(text)
    assert result == text


# ── Code — must be untouched ─────────────────────────────────────────────────


def test_inline_code_unchanged() -> None:
    # Bold markers inside backtick code must not be converted
    assert md_to_mrkdwn("`**not bold**`") == "`**not bold**`"


def test_fenced_code_unchanged() -> None:
    code = "```\n**not bold**\n## not a header\n[not](a_link)\n```"
    assert md_to_mrkdwn(code) == code


def test_fenced_code_with_language_tag() -> None:
    code = "```python\nx = **2**\n```"
    assert md_to_mrkdwn(code) == code


def test_inline_code_in_context() -> None:
    text = "Run `**uv run**` to execute"
    assert md_to_mrkdwn(text) == "Run `**uv run**` to execute"


# ── Bare URLs — must be left alone ───────────────────────────────────────────


def test_bare_url_unchanged() -> None:
    url = "https://example.com/path?q=1&r=2"
    assert md_to_mrkdwn(url) == url


def test_bare_url_in_context() -> None:
    text = "Visit https://example.com for more info."
    assert md_to_mrkdwn(text) == text


# ── Existing Slack mrkdwn — idempotency ──────────────────────────────────────


def test_slack_link_not_double_converted() -> None:
    # Already-converted Slack link must survive a second pass unchanged
    slack_link = "<https://example.com|Click here>"
    assert md_to_mrkdwn(slack_link) == slack_link


def test_already_slack_bold_unchanged() -> None:
    # *bold* is already Slack bold — must not be touched
    assert md_to_mrkdwn("*bold*") == "*bold*"


def test_double_run_idempotent_bold() -> None:
    original = "**bold text**"
    once = md_to_mrkdwn(original)
    twice = md_to_mrkdwn(once)
    assert once == "*bold text*"
    assert twice == once  # second run must not change anything


def test_double_run_idempotent_header() -> None:
    original = "## My Section"
    once = md_to_mrkdwn(original)
    twice = md_to_mrkdwn(once)
    assert once == "*My Section*"
    assert twice == once


def test_double_run_idempotent_link() -> None:
    original = "[label](https://example.com)"
    once = md_to_mrkdwn(original)
    twice = md_to_mrkdwn(once)
    assert once == "<https://example.com|label>"
    assert twice == once


# ── Plain text — pass-through ─────────────────────────────────────────────────


def test_plain_string_unchanged() -> None:
    text = "Hello, this is a plain sentence with no markdown."
    assert md_to_mrkdwn(text) == text


def test_empty_string() -> None:
    assert md_to_mrkdwn("") == ""


# ── Combined / realistic agent output ────────────────────────────────────────


def test_realistic_agent_reply() -> None:
    text = (
        "## Summary\n"
        "Here are **three** key points:\n"
        "- First item\n"
        "- Second item\n"
        "See [our docs](https://docs.example.com) for details.\n"
        "Run `pip install **x**` to install.\n"
    )
    result = md_to_mrkdwn(text)
    assert result.startswith("*Summary*\n")
    assert "*three*" in result
    assert "- First item" in result
    assert "- Second item" in result
    assert "<https://docs.example.com|our docs>" in result
    # inline code must be untouched
    assert "`pip install **x**`" in result
