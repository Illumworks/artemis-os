"""Provider error types.

Design language: one clear exception per failure mode so callers can branch
without inspecting exception messages.
"""

from __future__ import annotations


class MissingApiKeyError(Exception):
    """Raised at adapter construction time when the required API key is absent."""


class UnknownProviderError(Exception):
    """Raised by get_adapter() when the provider_id string is not registered."""


class ProviderAPIError(Exception):
    """Raised when the provider returns a non-2xx HTTP response.

    Attributes
    ----------
    status_code : int
        The HTTP status code returned by the provider (e.g. 400, 429, 500).
    body : str
        The raw response body text for debugging. May be empty.
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Provider API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class ClaudeCodeTimeoutError(ProviderAPIError):
    """Raised when the Claude Code CLI exceeds its completion timeout."""


class GeminiRateLimitError(ProviderAPIError):
    """Raised when Gemini returns HTTP 429 (rate limited) or 503 (overloaded/UNAVAILABLE).

    Subclasses ProviderAPIError so existing ``except ProviderAPIError`` catch
    sites are unaffected.  Callers that want to special-case the Gemini runtime
    fallback can catch this narrower type instead.
    """


class CodexRateLimitError(ProviderAPIError):
    """Raised when the Codex CLI reports a usage-limit or rate-limit failure.

    Detected from NDJSON events whose message contains "usage limit",
    "rate limit", or "try again" (case-insensitive).  Subclasses
    ``ProviderAPIError`` with status_code=429 so the fallback machinery treats
    it identically to a Gemini 429.  Other ``turn.failed`` reasons (auth errors,
    task failures, etc.) are NOT mapped to this type — they surface as plain
    ``ProviderAPIError`` so genuine failures remain visible.
    """


class ProviderUnavailableError(ProviderAPIError):
    """Raised when a provider cannot serve AT ALL — as distinct from a request
    that reached it and failed.

    The distinction matters for the fallback cascade, and conflating the two
    caused a real outage. "Sandbox execution error" is a request-level bug and
    must surface loudly rather than being masked by a silent failover — that is
    the deliberate design behind ``test_no_fallback_on_codex_non_limit_failure``.
    But "The 'gpt-5.4' model is not supported when using Codex with a ChatGPT
    account" is not a bug in the request: the provider is simply unusable, and
    falling through to another one is exactly right.

    On 2026-08-12 every codex model returned that entitlement error. Because it
    surfaced as a plain ``ProviderAPIError`` with the process exit code (1) in
    the status field, ``_is_retryable`` read it as a non-transient 4xx and
    re-raised — so the cascade never engaged and callers hard-failed instead of
    degrading to claude-code. Subclasses ``ProviderAPIError`` (status 503) so
    existing catch sites are unaffected while the fallback treats it as
    transient.
    """

    def __init__(self, body: str) -> None:
        super().__init__(503, body)


class MissingCliBinaryError(Exception):
    """Raised at adapter construction time when the required CLI binary is absent.

    Attributes
    ----------
    provider : str
        The provider ID (e.g. ``"claude-code"``).
    binary_name : str
        The binary that was searched for (e.g. ``"claude"``).
    """

    def __init__(self, provider: str, binary_name: str) -> None:
        self.provider = provider
        self.binary_name = binary_name
        env_hint = f"{binary_name.upper().replace('-', '_')}_BIN"
        super().__init__(
            f"Provider {provider!r} requires the {binary_name!r} binary, "
            "which was not found on PATH or in common install locations. "
            f"Install it and ensure it is executable, or override via {env_hint}."
        )
