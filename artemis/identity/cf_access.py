"""Cloudflare Access JWT verification.

This module verifies the `Cf-Access-Jwt-Assertion` request header against the
team JWKS published at:

  https://<team_domain>/cdn-cgi/access/certs

Security invariants for Artemis trust decisions:
  - RS256 signature must validate against the current JWKS
  - `aud` must include the configured Cloudflare Access AUD
  - `iss` must exactly equal `https://<team_domain>`
  - `exp` and `nbf` must both be present and valid
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt import InvalidTokenError

from artemis.config import settings

_JWKS_CACHE_TTL_SECONDS = 300.0


class CfAccessConfigurationError(RuntimeError):
    """Raised when CF Access verification is enabled but required config is absent."""


class CfAccessVerificationError(ValueError):
    """Raised when a CF Access JWT cannot be trusted."""


@dataclass(frozen=True)
class VerifiedCfAccessIdentity:
    """Trusted identity extracted from a verified Cloudflare Access JWT."""

    email: str
    name: str | None
    claims: dict[str, Any]


class CfAccessVerifier:
    """Verify Cloudflare Access JWTs using a cached JWKS."""

    def __init__(
        self,
        *,
        team_domain: str,
        audience: str,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        jwks_cache_ttl_seconds: float = _JWKS_CACHE_TTL_SECONDS,
    ) -> None:
        self._team_domain = team_domain.strip().lower()
        self._audience = audience.strip()
        self._http_client_factory = http_client_factory or self._default_http_client
        self._jwks_cache_ttl_seconds = jwks_cache_ttl_seconds
        self._cached_jwks: dict[str, Any] | None = None
        self._cached_at_monotonic: float = 0.0
        self._jwks_lock: asyncio.Lock | None = None

    @property
    def issuer(self) -> str:
        return f"https://{self._team_domain}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/cdn-cgi/access/certs"

    def _default_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=5.0)

    async def _ensure_lock(self) -> Any:
        if self._jwks_lock is None:
            self._jwks_lock = asyncio.Lock()
        return self._jwks_lock

    def _jwks_is_fresh(self) -> bool:
        if self._cached_jwks is None:
            return False
        return (time.monotonic() - self._cached_at_monotonic) < self._jwks_cache_ttl_seconds

    async def _fetch_jwks(self) -> dict[str, Any]:
        async with self._http_client_factory() as client:
            response = await client.get(self.jwks_url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise CfAccessVerificationError("Cloudflare Access JWKS payload is invalid")
        return payload

    async def _get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh and self._jwks_is_fresh():
            return self._cached_jwks or {"keys": []}

        lock = await self._ensure_lock()
        async with lock:
            if not force_refresh and self._jwks_is_fresh():
                return self._cached_jwks or {"keys": []}
            try:
                jwks = await self._fetch_jwks()
            except httpx.HTTPError as exc:
                raise CfAccessVerificationError("Failed to fetch Cloudflare Access JWKS") from exc
            self._cached_jwks = jwks
            self._cached_at_monotonic = time.monotonic()
            return jwks

    @staticmethod
    def _find_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        for key in jwks.get("keys", []):
            if isinstance(key, dict) and key.get("kid") == kid:
                return key
        return None

    async def verify_jwt(self, token: str) -> VerifiedCfAccessIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise CfAccessVerificationError("JWT header is invalid") from exc

        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm != "RS256" or not isinstance(kid, str) or not kid:
            raise CfAccessVerificationError("JWT must declare RS256 and a key id")

        jwks = await self._get_jwks()
        jwk = self._find_jwk(jwks, kid)
        if jwk is None:
            jwks = await self._get_jwks(force_refresh=True)
            jwk = self._find_jwk(jwks, kid)
        if jwk is None:
            raise CfAccessVerificationError("JWT key id not found in Cloudflare Access JWKS")

        try:
            public_key = cast(RSAPublicKey, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk)))
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self.issuer,
                options={"require": ["exp", "nbf", "iss", "aud"]},
            )
        except InvalidTokenError as exc:
            raise CfAccessVerificationError("JWT signature or claims are invalid") from exc

        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise CfAccessVerificationError("JWT is missing a valid email claim")

        name = claims.get("name")
        return VerifiedCfAccessIdentity(
            email=email.strip().lower(),
            name=name.strip() if isinstance(name, str) and name.strip() else None,
            claims=claims,
        )


@lru_cache(maxsize=1)
def get_cf_access_verifier() -> CfAccessVerifier:
    """Build the singleton verifier from runtime config."""
    team_domain = settings.cf_access_team_domain.strip()
    audience = settings.cf_access_aud.strip()
    if not team_domain or not audience:
        raise CfAccessConfigurationError(
            "CF Access is enabled but CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD are not configured"
        )
    return CfAccessVerifier(team_domain=team_domain, audience=audience)
