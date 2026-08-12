"""Provider-unavailable vs request-failed, and why the distinction exists.

2026-08-12: every codex model began returning "The 'gpt-5.4' model is not
supported when using Codex with a ChatGPT account" — an ACCOUNT ENTITLEMENT
problem. It surfaced as a plain ``ProviderAPIError`` carrying the process exit
code (1) in the status field. 1 is not in ``_RETRYABLE_STATUS_CODES``, so
``_is_retryable`` read it as a non-transient 4xx and re-raised. The cascade
never engaged, and callers hard-failed instead of degrading to claude-code.

The fix must NOT be "make every CLI failure retryable". That would erase a
deliberate design (see ``test_no_fallback_on_codex_non_limit_failure``): a real
task failure like "Sandbox execution error" has to surface loudly rather than be
masked by a silent failover. Hence two distinct classes:

    provider cannot serve at all  -> ProviderUnavailableError -> fail over
    request reached it and failed -> ProviderAPIError         -> re-raise
"""

from __future__ import annotations

import pytest

from artemis.providers.codex.adapter import _is_unavailable
from artemis.providers.errors import (
    CodexRateLimitError,
    ProviderAPIError,
    ProviderUnavailableError,
)
from artemis.providers.fallback import _is_retryable


def test_the_exact_2026_08_12_entitlement_error_is_unavailable() -> None:
    """Verbatim from the live CLI, not paraphrased."""
    real = (
        '{"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'gpt-5.4\' model is not supported when using Codex '
        'with a ChatGPT account."}}'
    )
    assert _is_unavailable(real)


@pytest.mark.parametrize(
    "detail",
    [
        "Not authenticated. Please run codex login.",
        "401 Unauthorized",
        "The 'gpt-5' model is not supported when using Codex with a ChatGPT account.",
    ],
)
def test_auth_and_entitlement_failures_are_unavailable(detail: str) -> None:
    assert _is_unavailable(detail)


@pytest.mark.parametrize(
    "detail",
    [
        "Sandbox execution error.",
        "Reading additional input from stdin...",  # benign banner, says nothing
        "Task failed: could not apply patch",
        "",
    ],
)
def test_request_level_failures_are_not_unavailable(detail: str) -> None:
    """These must stay visible. A benign stderr banner must never be read as
    an outage — it appears on failing runs whose real cause is elsewhere."""
    assert not _is_unavailable(detail)


def test_unavailable_is_retryable_so_the_cascade_engages() -> None:
    assert _is_retryable(ProviderUnavailableError("entitlement problem"))


def test_unavailable_carries_503_and_stays_a_provider_api_error() -> None:
    """503 lands it in the retryable set; the subclass keeps existing
    ``except ProviderAPIError`` catch sites working unchanged."""
    exc = ProviderUnavailableError("nope")
    assert exc.status_code == 503
    assert isinstance(exc, ProviderAPIError)


def test_plain_cli_failure_is_still_not_retryable() -> None:
    """The guard on the original design: exit-code failures still re-raise."""
    assert not _is_retryable(ProviderAPIError(1, "codex turn.failed: Sandbox execution error."))


def test_rate_limit_is_still_retryable() -> None:
    assert _is_retryable(CodexRateLimitError(429, "You've hit your usage limit."))


def test_timeout_is_retryable() -> None:
    """408 was missing from the set: a timeout means the provider did not serve,
    which is precisely what a fallback is for."""
    assert _is_retryable(ProviderAPIError(408, "codex CLI timed out after 120 s"))


def test_genuine_bad_request_still_re_raises() -> None:
    """A real 400 must not be silently retried on another provider."""
    assert not _is_retryable(ProviderAPIError(400, "malformed request"))
