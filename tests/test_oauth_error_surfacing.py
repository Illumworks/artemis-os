"""Unit tests for Google token-exchange error surfacing (no DB required).

Verifies that:
- A simulated Google 4xx rejection raises GoogleTokenExchangeError with the
  correct structured fields (status, error_code, error_description).
- complete_google_oauth translates that into an HTTP 400 carrying Google's
  real error fields, NOT an opaque 502.
- A genuine network failure (httpx.ConnectError) still produces an HTTP 502.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.google_docs.client import GoogleTokenExchangeError, exchange_code_for_tokens

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(*, status_code: int, body: dict | str) -> MagicMock:
    """Build a minimal fake httpx.Response for use in mock_post."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.text = json.dumps(body)
        resp.json.return_value = body
    else:
        resp.text = body
        resp.json.side_effect = Exception("not JSON")
    return resp


# ---------------------------------------------------------------------------
# exchange_code_for_tokens — unit tests (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_code_raises_token_exchange_error_on_google_400() -> None:
    """A Google 400 invalid_grant must raise GoogleTokenExchangeError, not propagate
    httpx.HTTPStatusError and lose the reason."""
    google_error_body = {
        "error": "invalid_grant",
        "error_description": "Token has been expired or revoked.",
    }
    fake_resp = _fake_response(status_code=400, body=google_error_body)

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=fake_resp)

    with patch("artemis.google_docs.client._make_http_client", return_value=mock_http):
        with pytest.raises(GoogleTokenExchangeError) as exc_info:
            await exchange_code_for_tokens(
                code="bad-code",
                client_id="client-id",
                client_secret="client-secret",
                redirect_uri="https://example.com/callback",
            )

    exc = exc_info.value
    assert exc.status == 400
    assert exc.error_code == "invalid_grant"
    assert "expired or revoked" in (exc.error_description or "")
    assert "invalid_grant" in exc.body


@pytest.mark.asyncio
async def test_exchange_code_raises_token_exchange_error_on_google_401() -> None:
    """A Google 401 invalid_client also raises GoogleTokenExchangeError."""
    google_error_body = {
        "error": "invalid_client",
        "error_description": "The OAuth client was not found.",
    }
    fake_resp = _fake_response(status_code=401, body=google_error_body)

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=fake_resp)

    with patch("artemis.google_docs.client._make_http_client", return_value=mock_http):
        with pytest.raises(GoogleTokenExchangeError) as exc_info:
            await exchange_code_for_tokens(
                code="any-code",
                client_id="bad-client-id",
                client_secret="bad-secret",
                redirect_uri="https://example.com/callback",
            )

    exc = exc_info.value
    assert exc.status == 401
    assert exc.error_code == "invalid_client"


@pytest.mark.asyncio
async def test_exchange_code_handles_non_json_error_body() -> None:
    """If Google returns a non-JSON 400, GoogleTokenExchangeError is still raised
    with error_code=None (body is preserved as-is)."""
    fake_resp = _fake_response(status_code=400, body="Bad Request")

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=fake_resp)

    with patch("artemis.google_docs.client._make_http_client", return_value=mock_http):
        with pytest.raises(GoogleTokenExchangeError) as exc_info:
            await exchange_code_for_tokens(
                code="any-code",
                client_id="id",
                client_secret="secret",
                redirect_uri="https://example.com/callback",
            )

    exc = exc_info.value
    assert exc.status == 400
    assert exc.error_code is None
    assert exc.body == "Bad Request"


# ---------------------------------------------------------------------------
# complete_google_oauth — integration layer (no DB; mocks exchange_code_for_tokens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_google_oauth_returns_400_on_google_rejection() -> None:
    """GoogleTokenExchangeError must bubble up as HTTP 400 with Google's real reason."""
    from fastapi import HTTPException

    from artemis.google_integration import complete_google_oauth, register_google_oauth_state

    # Register a valid state token so the state-check passes.
    state = register_google_oauth_state(user_id=1, purpose="personal", source="google")

    # Stub config resolution so we don't need the DB.
    mock_config = MagicMock()
    mock_config.client_id = "client-id"
    mock_config.client_secret = "client-secret"

    with (
        patch(
            "artemis.google_integration.resolve_google_oauth_client_config",
            new=AsyncMock(return_value=mock_config),
        ),
        patch(
            "artemis.google_integration.get_google_credential",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.google_integration.exchange_code_for_tokens",
            new=AsyncMock(
                side_effect=GoogleTokenExchangeError(
                    status=400,
                    error_code="redirect_uri_mismatch",
                    error_description="Redirect URI mismatch.",
                    body='{"error":"redirect_uri_mismatch","error_description":"Redirect URI mismatch."}',
                )
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await complete_google_oauth(
                session=AsyncMock(),
                current_user_id=1,
                code="any-code",
                state=state,
                redirect_uri="https://wrong.example.com/callback",
            )

    http_exc = exc_info.value
    assert http_exc.status_code == 400
    assert http_exc.detail["error"] == "google_rejected_token_exchange"
    assert http_exc.detail["google_error"] == "redirect_uri_mismatch"
    assert http_exc.detail["google_status"] == 400


@pytest.mark.asyncio
async def test_complete_google_oauth_returns_502_on_network_error() -> None:
    """A genuine network failure (httpx.ConnectError) must still produce HTTP 502."""
    from fastapi import HTTPException

    from artemis.google_integration import complete_google_oauth, register_google_oauth_state

    state = register_google_oauth_state(user_id=2, purpose="personal", source="google")

    mock_config = MagicMock()
    mock_config.client_id = "client-id"
    mock_config.client_secret = "client-secret"

    with (
        patch(
            "artemis.google_integration.resolve_google_oauth_client_config",
            new=AsyncMock(return_value=mock_config),
        ),
        patch(
            "artemis.google_integration.get_google_credential",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.google_integration.exchange_code_for_tokens",
            new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await complete_google_oauth(
            session=AsyncMock(),
            current_user_id=2,
            code="any-code",
            state=state,
            redirect_uri="https://example.com/callback",
        )

    http_exc = exc_info.value
    assert http_exc.status_code == 502
    assert "google_oauth_failed" in str(http_exc.detail)
