"""M3/D8: the agent builder is owner-only. require_owner must fail closed."""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import HTTPException, Request

from artemis.identity.dependencies import RequestIdentity
from artemis.identity.scope_policy import OWNER_EMAIL
from artemis.marketing.routes import _auth


class _FakeRequest:
    """Minimal stand-in; resolve_request_identity is patched so state is unused."""

    class _S:
        pass

    def __init__(self) -> None:
        self.state = _FakeRequest._S()


def _fake_request() -> Request:
    # require_owner only forwards the request to resolve_request_identity, which
    # every test here monkeypatches (or never reaches, in dev-mode). Building a
    # real Starlette Request would need a full ASGI scope for no behavioral
    # benefit, so we duck-type the minimal shape and cast for the signature.
    return cast(Request, _FakeRequest())


@pytest.mark.asyncio
async def test_require_owner_dev_mode_allows(monkeypatch):
    monkeypatch.setattr(_auth.settings, "cf_access_enabled", False, raising=False)
    # No raise == allowed.
    await _auth.require_owner(_fake_request(), None)


@pytest.mark.asyncio
async def test_require_owner_cf_owner_allows(monkeypatch):
    monkeypatch.setattr(_auth.settings, "cf_access_enabled", True, raising=False)

    async def _fake_identity(_request, _jwt):
        return RequestIdentity(email=OWNER_EMAIL, name="Owner", claims={}, source="test")

    monkeypatch.setattr(_auth, "resolve_request_identity", _fake_identity)
    await _auth.require_owner(_fake_request(), "jwt")  # no raise


@pytest.mark.asyncio
async def test_require_owner_cf_marketing_denied(monkeypatch):
    monkeypatch.setattr(_auth.settings, "cf_access_enabled", True, raising=False)

    async def _fake_identity(_request, _jwt):
        return RequestIdentity(
            email="marketer@amiralearning.com", name="Mktg", claims={}, source="test"
        )

    monkeypatch.setattr(_auth, "resolve_request_identity", _fake_identity)
    with pytest.raises(HTTPException) as exc:
        await _auth.require_owner(_fake_request(), "jwt")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_owner_cf_owner_email_case_insensitive(monkeypatch):
    monkeypatch.setattr(_auth.settings, "cf_access_enabled", True, raising=False)

    async def _fake_identity(_request, _jwt):
        return RequestIdentity(email=OWNER_EMAIL.upper(), name="Owner", claims={}, source="test")

    monkeypatch.setattr(_auth, "resolve_request_identity", _fake_identity)
    await _auth.require_owner(_fake_request(), "jwt")  # no raise
