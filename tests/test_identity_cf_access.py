"""Acceptance tests for Cloudflare Access identity verification."""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.identity import dependencies as identity_dependencies
from artemis.identity.cf_access import CfAccessVerifier
from artemis.identity.models import User
from artemis.identity.repository import get_or_create_user

TEAM_DOMAIN = "jfila.cloudflareaccess.com"
ISSUER = f"https://{TEAM_DOMAIN}"
AUDIENCE = "196c5861dc5fbe509186be11c6006510050ae562f93a52556d7ef9136042b7d6"
DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_identity",
)

if "artemis_test" not in DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={DB_URL!r} is not a safe test database."
    )

_ENGINE = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_ENGINE)
db_module.engine = _ENGINE
db_module.SessionLocal = async_sessionmaker(
    bind=_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_USERS = text("TRUNCATE users RESTART IDENTITY CASCADE")


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_jwk(private_key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


def _make_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _mint_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    email: str = "jon.fila@amiralearning.com",
    name: str = "Jon Fila",
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    not_before_delta: timedelta = timedelta(minutes=-1),
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(UTC)
    claims = {
        "email": email,
        "name": name,
        "aud": [audience],
        "iss": issuer,
        "nbf": int((now + not_before_delta).timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class FakeJwksServer:
    def __init__(self, jwks_payload: dict[str, Any]) -> None:
        self.jwks_payload = jwks_payload
        self.call_count = 0

    def set_payload(self, jwks_payload: dict[str, Any]) -> None:
        self.jwks_payload = jwks_payload

    def client_factory(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.call_count += 1
            assert str(request.url) == f"{ISSUER}/cdn-cgi/access/certs"
            return httpx.Response(200, json=self.jwks_payload)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset_identity_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cf_access_enabled", False)
    monkeypatch.setattr(settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(settings, "cf_access_aud", AUDIENCE)
    monkeypatch.setattr(settings, "token", None)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_ENGINE, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_USERS)
        yield session


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _configured_verifier(server: FakeJwksServer) -> CfAccessVerifier:
    return CfAccessVerifier(
        team_domain=TEAM_DOMAIN,
        audience=AUDIENCE,
        http_client_factory=server.client_factory,
    )


def _enable_cf_access(monkeypatch: pytest.MonkeyPatch, verifier: CfAccessVerifier) -> None:
    monkeypatch.setattr(settings, "cf_access_enabled", True)
    monkeypatch.setattr(identity_dependencies, "get_cf_access_verifier", lambda: verifier)


async def test_verifier_accepts_valid_token_and_extracts_identity() -> None:
    private_key = _make_private_key()
    kid = "kid-valid"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)

    token = _mint_token(private_key, kid=kid)
    identity = await verifier.verify_jwt(token)

    assert identity.email == "jon.fila@amiralearning.com"
    assert identity.name == "Jon Fila"
    assert server.call_count == 1


async def test_verifier_refreshes_jwks_on_kid_rotation() -> None:
    key_one = _make_private_key()
    key_two = _make_private_key()
    server = FakeJwksServer({"keys": [_make_jwk(key_one, kid="kid-1")]})
    verifier = _configured_verifier(server)

    await verifier.verify_jwt(_mint_token(key_one, kid="kid-1"))
    server.set_payload({"keys": [_make_jwk(key_two, kid="kid-2")]})
    rotated = await verifier.verify_jwt(_mint_token(key_two, kid="kid-2"))

    assert rotated.email == "jon.fila@amiralearning.com"
    assert server.call_count == 2


async def test_get_or_create_user_upserts_single_row_and_bumps_last_seen(
    db_session: AsyncSession,
) -> None:
    first = await get_or_create_user(db_session, "Jon.Fila@AmiraLearning.com", "Jon Fila")
    await db_session.commit()
    await db_session.refresh(first)
    first_seen = first.last_seen_at

    await asyncio.sleep(0.02)

    second = await get_or_create_user(db_session, "jon.fila@amiralearning.com", None)
    await db_session.commit()
    await db_session.refresh(second)

    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1
    assert second.id == first.id
    assert second.name == "Jon Fila"
    assert second.last_seen_at >= first_seen


async def test_api_me_accepts_valid_token_and_creates_user(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _make_private_key()
    kid = "kid-accept"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    token = _mint_token(private_key, kid=kid)
    response = await client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": token})

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "email": "jon.fila@amiralearning.com",
        "name": "Jon Fila",
    }

    stored = await db_session.scalar(select(User).where(User.email == "jon.fila@amiralearning.com"))
    assert stored is not None
    assert stored.name == "Jon Fila"


async def test_api_me_rejects_wrong_audience(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _make_private_key()
    kid = "kid-bad-aud"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    bad_token = _mint_token(private_key, kid=kid, audience="wrong-audience")
    response = await client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": bad_token})

    assert response.status_code == 401
    assert response.json() == {"error": "Invalid or expired token", "code": "unauthorized"}


async def test_api_me_rejects_expired_token(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _make_private_key()
    kid = "kid-expired"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    expired = _mint_token(private_key, kid=kid, expires_delta=timedelta(minutes=-1))
    response = await client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": expired})

    assert response.status_code == 401
    assert response.json() == {"error": "Invalid or expired token", "code": "unauthorized"}


async def test_api_me_rejects_bad_signature(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_key = _make_private_key()
    signing_key = _make_private_key()
    kid = "kid-bad-sig"
    server = FakeJwksServer({"keys": [_make_jwk(trusted_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    bad_signature = _mint_token(signing_key, kid=kid)
    response = await client.get("/api/me", headers={"Cf-Access-Jwt-Assertion": bad_signature})

    assert response.status_code == 401
    assert response.json() == {"error": "Invalid or expired token", "code": "unauthorized"}


async def test_api_me_rejects_missing_header_when_enabled(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _make_private_key()
    kid = "kid-missing"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    response = await client.get("/api/me")

    assert response.status_code == 401
    assert response.json() == {
        "error": "Cf-Access-Jwt-Assertion header required",
        "code": "unauthorized",
    }


async def test_api_me_returns_dev_user_when_cf_access_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.get("/api/me")

    assert response.status_code == 200
    assert response.json() == {"id": 1, "email": "dev@local", "name": "Local Dev"}

    stored = await db_session.scalar(select(User).where(User.email == "dev@local"))
    assert stored is not None


async def test_protected_route_uses_cf_access_auth_when_enabled(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _make_private_key()
    kid = "kid-route"
    server = FakeJwksServer({"keys": [_make_jwk(private_key, kid=kid)]})
    verifier = _configured_verifier(server)
    _enable_cf_access(monkeypatch, verifier)

    missing = await client.get("/api/scouts/packages")
    assert missing.status_code == 401

    token = _mint_token(private_key, kid=kid)
    accepted = await client.get("/api/scouts/packages", headers={"Cf-Access-Jwt-Assertion": token})
    assert accepted.status_code == 200
