"""LM Studio adapter — OpenAI-compatible local server, no API key required.

LM Studio exposes an OpenAI-compatible API at ``http://localhost:1234/v1`` by
default.  This adapter is a thin subclass of ``OpenAIAdapter`` that:

  - overrides the base URL to point at the local server
  - bypasses the API key requirement (local server accepts any bearer token)
  - defaults to the first model reported by LM Studio's ``/v1/models`` endpoint

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- ``base_url`` defaults to ``LM_STUDIO_BASE_URL`` env var or ``http://localhost:1234/v1``.
- The adapter passes ``"not-needed"`` as the bearer token — LM Studio ignores it.
- ``default_model`` defaults to ``""`` (empty) so the server picks its loaded model.
  The caller may supply an explicit model ID from ``/v1/models``.
"""

from __future__ import annotations

import os

from artemis.providers.openai.adapter import OpenAIAdapter

_LM_STUDIO_DEFAULT_BASE = "http://localhost:1234/v1"
_LM_STUDIO_PLACEHOLDER_MODEL = "local-model"


class LMStudioAdapter(OpenAIAdapter):
    """Conforms to ModelAdapter and SupportsStreaming via OpenAIAdapter."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        resolved_base = (
            base_url
            or os.environ.get("LM_STUDIO_BASE_URL", _LM_STUDIO_DEFAULT_BASE)
        )
        resolved_model = default_model or os.environ.get(
            "LM_STUDIO_DEFAULT_MODEL", _LM_STUDIO_PLACEHOLDER_MODEL
        )
        super().__init__(
            api_key="not-needed-for-local-server",
            default_model=resolved_model,
            _base_url=resolved_base,
        )
