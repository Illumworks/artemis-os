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
