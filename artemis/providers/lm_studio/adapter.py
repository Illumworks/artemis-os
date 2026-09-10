"""LM Studio adapter — OpenAI-compatible local server, no API key required.

LM Studio exposes an OpenAI-compatible API at ``http://127.0.0.1:1234/v1`` by
default.  This adapter is a thin subclass of ``OpenAIAdapter`` that:

  - overrides the base URL to point at the local server
  - bypasses the API key requirement (local server accepts any bearer token)
  - defaults to the first model reported by LM Studio's ``/v1/models`` endpoint

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Notes
-----
- ``base_url`` defaults to ``settings.lm_studio_base_url + "/v1"`` (set via
  ``ARTEMIS_LM_STUDIO_BASE_URL`` or ``LM_STUDIO_BASE_URL`` env vars).
- The adapter passes ``"not-needed"`` as the bearer token — LM Studio ignores it.
- ``default_model`` defaults to ``""`` (empty) so the server picks its loaded model.
  The caller may supply an explicit model ID from ``/v1/models``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.config import settings
from artemis.providers.openai.adapter import OpenAIAdapter

logger = logging.getLogger(__name__)

_LM_STUDIO_PLACEHOLDER_MODEL = "local-model"


def _default_base_url() -> str:
    """Return the adapter base URL (with /v1 suffix) from config."""
    return f"{settings.lm_studio_base_url}/v1"


#: Minimum completion budget for a local call.
#:
#: The models on the Studio are REASONING models: they spend tokens thinking
#: before they emit a single character of answer. Below this floor the budget is
#: consumed entirely by the reasoning block and the response comes back with
#: `finish_reason="length"` and an EMPTY string -- a 200 with a usable shape,
#: which is the worst way for this to fail.
#:
#: Measured 2026-09-09 on `qwen/qwen3.6-35b-a3b` with a three-line extraction
#: task: at max_tokens=200 it returned 0 characters after 199 completion tokens;
#: at 8192 it answered correctly, having spent 759 tokens to produce 71
#: characters. An earlier note in `feature_catalog` concluded from 300/400/1200
#: that the model was unusable. The model was fine. The budget was not, and the
#: conclusion cost us the faster of the two local models.
#:
#: 8192 matches the default the Studio's own `offload` tool uses, for the same
#: reason.
_REASONING_TOKEN_FLOOR = 8192


class LMStudioAdapter(OpenAIAdapter):
    """Conforms to ModelAdapter and SupportsStreaming via OpenAIAdapter."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        resolved_base = base_url or _default_base_url()
        resolved_model = default_model or os.environ.get(
            "LM_STUDIO_DEFAULT_MODEL", _LM_STUDIO_PLACEHOLDER_MODEL
        )
        super().__init__(
            api_key="not-needed-for-local-server",
            default_model=resolved_model,
            _base_url=resolved_base,
        )
        self._base_url = resolved_base
        self._served_models: set[str] | None = None

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run a completion via LM Studio's OpenAI-compatible API.

        Note: LM Studio's tool/function-calling support depends on the loaded
        model.  Many local models do not reliably support tool execution.
        Emit a warning so future hollowness is caught immediately.
        """
        request = self._with_reasoning_budget(request)
        request = await self._with_servable_model(request)
        if request.tools:
            logger.warning(
                "%s adapter received request.tools but does not support tool execution. "
                "Tools will be ignored. Consider routing tool-using surfaces to a "
                "tool-capable provider.",
                type(self).__name__,
            )
        response = await super().complete(request)
        # Local inference is free. The OpenAI adapter priced this call at OpenAI
        # rates -- a real summary above came back costed at $0.00038 -- which
        # would make the cost dashboard show no saving from routing work here,
        # which is the entire reason the box exists.
        with contextlib.suppress(AttributeError):
            object.__setattr__(response, "cost_usd", 0.0)
        return response

    #: The OpenAI adapter hardcodes a 120s HTTP timeout, which is right for a paid
    #: API and wrong here. A local turn on a 24,572-token prompt has to prefill all
    #: of it and then generate at roughly 100 tok/s, so two minutes is a normal
    #: turn rather than a hung one -- an agent-loop test failed at 132.1s and was
    #: read as "the model cannot drive a loop" when it was our client giving up.
    #:
    #: A local call costs no tokens, so a slow one costs only wall-clock while an
    #: abandoned one wastes the whole computation. The asymmetry runs entirely one
    #: way, which is why this is generous rather than tuned.
    REQUEST_TIMEOUT_SECONDS = 600.0

    async def _with_servable_model(self, request: CompletionRequest) -> CompletionRequest:
        """Drop a model name this server does not have, so a cascade can be data.

        A cascade hands ONE model name to every provider it tries. The scouts are
        configured `model="claude-haiku-4-5"`, so pointing one at lm-studio asked
        the Studio for a Claude model and got "Failed to load model". That made
        moving a scout to the local box a code change instead of a column update,
        which is the difference between an experiment and a project.

        An explicit local model is still honoured — asking for the coder model
        rather than the default has to keep working. Only a name the server cannot
        serve is replaced, and the list is fetched once per adapter rather than
        per call.
        """
        wanted = (request.model or "").strip()
        if not wanted:
            return request
        served = await self._servable_models()
        if served is None or wanted in served:
            return request
        logger.info(
            "lm-studio: %r is not served here (%s available) -- using the local default",
            wanted,
            len(served),
        )
        return dataclasses.replace(request, model=None)

    async def _servable_models(self) -> set[str] | None:
        """Model ids this server has, or None when it cannot be asked.

        None means "do not second-guess the caller": if the listing fails, pass
        the request through unchanged and let the real call produce the real
        error, rather than silently swapping the model on a network blip.
        """
        if self._served_models is not None:
            return self._served_models
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base_url.rstrip('/')}/models")
            resp.raise_for_status()
            self._served_models = {
                str(m.get("id")) for m in resp.json().get("data", []) if m.get("id")
            }
        except Exception:
            logger.warning("lm-studio: could not list models", exc_info=True)
            return None
        return self._served_models

    def _with_reasoning_budget(self, request: CompletionRequest) -> CompletionRequest:
        """Raise a too-small `max_tokens` to the floor rather than returning nothing.

        Logged rather than raised: a caller asking for 400 tokens wants a short
        answer and still gets one. The floor buys room for the thinking that
        precedes it, and the model stops on its own once it has answered.
        """
        if request.max_tokens >= _REASONING_TOKEN_FLOOR:
            return request
        logger.info(
            "lm-studio: raising max_tokens %s -> %s; local models spend budget on "
            "reasoning tokens and return an empty string below this",
            request.max_tokens,
            _REASONING_TOKEN_FLOOR,
        )
        return dataclasses.replace(request, max_tokens=_REASONING_TOKEN_FLOOR)
