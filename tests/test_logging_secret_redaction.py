"""Tests for credential redaction in log records.

Vista Social's MCP endpoint authenticates with the key in the URL query string,
and httpx logs every request line at INFO. Pinning httpx to WARNING hides that
today but does not guarantee it: one debug session, or one cron that never runs
`configure_logging()`, and a live credential lands in `app.err.log`.

The regression that motivated the type handling below: the first version of the
filter guarded on `isinstance(value, str)` and missed the key entirely, because
httpx passes `request.url` as an `httpx.URL` object rather than a string.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from artemis.logging_setup import (
    SecretRedactingFilter,
    configure_logging,
    install_secret_redaction,
    redact_secrets,
)

SECRET = "SUPERSECRETKEY123"
SECRET_URL = f"https://vistasocial.com/api/integration/mcp?api_key={SECRET}"


# ── redact_secrets ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "param",
    ["api_key", "apikey", "access_token", "token", "key", "API_KEY", "Api_Key"],
)
def test_redacts_each_secret_param(param: str) -> None:
    got = redact_secrets(f"https://x.test/a?{param}={SECRET}")
    assert SECRET not in got
    assert "<redacted>" in got


def test_redacts_gemini_style_key_param() -> None:
    """Gemini authenticates with `?key=` — a real in-repo case, not hypothetical."""
    got = redact_secrets("https://generativelanguage.googleapis.com/v1/models?key=abc123")
    assert "abc123" not in got


def test_preserves_surrounding_query_params() -> None:
    got = redact_secrets(f"https://x.test/a?page=2&api_key={SECRET}&sort=asc")
    assert got == "https://x.test/a?page=2&api_key=<redacted>&sort=asc"


def test_leaves_innocuous_text_alone() -> None:
    for text in ["no secrets here", "count=5", "monkey=banana", "keyboard=1"]:
        assert redact_secrets(text) == text


def test_redacts_multiple_occurrences() -> None:
    got = redact_secrets(f"a?api_key={SECRET} and b?token={SECRET}")
    assert SECRET not in got
    assert got.count("<redacted>") == 2


# ── the filter ────────────────────────────────────────────────────────────────


def _record(msg: str, args: object = None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_filter_redacts_string_arg() -> None:
    record = _record("HTTP Request: %s %s", ("POST", SECRET_URL))
    assert SecretRedactingFilter().filter(record) is True
    assert SECRET not in record.getMessage()


def test_filter_redacts_non_string_url_object() -> None:
    """The regression: httpx passes an httpx.URL, not a str."""
    record = _record("HTTP Request: %s %s", ("POST", httpx.URL(SECRET_URL)))
    SecretRedactingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_filter_redacts_message_without_args() -> None:
    record = _record(f"connecting to {SECRET_URL}")
    SecretRedactingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_filter_redacts_dict_args() -> None:
    # Mapping-style args reach LogRecord tuple-wrapped, as `logger.info(msg, d)`
    # produces; LogRecord then unwraps the single mapping itself.
    record = _record("%(url)s", ({"url": SECRET_URL},))
    assert isinstance(record.args, dict)
    SecretRedactingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_filter_preserves_numeric_arg_types() -> None:
    """A %d must still receive an int after filtering."""
    record = _record("n=%d url=%s", (42, SECRET_URL))
    SecretRedactingFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert record.args[0] == 42
    assert record.getMessage().startswith("n=42 ")


def test_filter_never_drops_records() -> None:
    assert SecretRedactingFilter().filter(_record("anything")) is True


# ── installation ──────────────────────────────────────────────────────────────


def test_install_is_idempotent() -> None:
    logger = logging.getLogger("httpx")
    before = len(logger.filters)
    install_secret_redaction()
    install_secret_redaction()
    install_secret_redaction()
    after = len(logger.filters)
    assert after <= before + 1


def test_installed_filter_redacts_through_caplog(caplog: pytest.LogCaptureFixture) -> None:
    install_secret_redaction()
    logger = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        logger.info('HTTP Request: %s %s "%s"', "POST", httpx.URL(SECRET_URL), "200 OK")
    assert SECRET not in caplog.text
    assert "<redacted>" in caplog.text


def test_configure_logging_installs_redaction_and_keeps_root_handlers() -> None:
    """CLAUDE.md rule: logging config stays additive, or caplog goes blind."""
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging()
    assert all(h in root.handlers for h in before)
    assert any(isinstance(f, SecretRedactingFilter) for f in logging.getLogger("httpx").filters)
