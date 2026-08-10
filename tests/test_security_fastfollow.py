"""Unit tests for the three security fast-follows (2026-07-10).

1. Production fail-open guard: ``assert_production_auth_config`` refuses to
   boot ``env=production`` without a fully-configured CF Access identity
   layer, and is a strict no-op for development/test.
2. WebSocket auth gate: ``artemis.ws.routes._authorize_ws`` requires a valid
   CF Access JWT when CF is enabled, falls back to a constant-time shared
   token when set, and stays open only for true local dev (neither).
3. SSRF egress guard: ``artemis.egress_guard.validate_url`` blocks loopback /
   RFC-1918 / link-local / non-http(s) destinations and is wired into
   ``ScoutHttpClient`` + ``pdf_extractor``.

All tests are pure unit tests — no DB, no live server, no network.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest

from artemis.config import (
    ProductionAuthConfigError,
    Settings,
    assert_production_auth_config,
)
from artemis.egress_guard import EgressBlockedError, validate_url

# ---------------------------------------------------------------------------
# 1. Production fail-open guard
# ---------------------------------------------------------------------------


def _cfg(**overrides: Any) -> Settings:
    """Build a Settings instance without touching env vars or .env files."""
    return Settings.model_construct(**overrides)


def test_default_env_is_development() -> None:
    """The field default must be development so a normal boot is untouched."""
    assert Settings.model_fields["env"].default == "development"


def test_production_misconfig_refuses_to_boot() -> None:
    """env=production with default (fail-open) CF settings must raise."""
    cfg = _cfg(env="production")  # cf_access_enabled defaults False
    with pytest.raises(ProductionAuthConfigError) as excinfo:
        assert_production_auth_config(cfg)
    assert "CF_ACCESS_ENABLED" in str(excinfo.value)


def test_production_partial_config_refuses_to_boot() -> None:
    """Enabled flag alone is not enough — team domain and AUD are required."""
    cfg = _cfg(env="production", cf_access_enabled=True, cf_access_team_domain="", cf_access_aud="")
    with pytest.raises(ProductionAuthConfigError) as excinfo:
        assert_production_auth_config(cfg)
    msg = str(excinfo.value)
    assert "TEAM_DOMAIN" in msg and "AUD" in msg


def test_production_fully_configured_boots() -> None:
    cfg = _cfg(
        env="production",
        cf_access_enabled=True,
        cf_access_team_domain="example.cloudflareaccess.com",
        cf_access_aud="aud-tag",
    )
    assert_production_auth_config(cfg)  # must not raise


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_boots_regardless_of_cf_config(env: str) -> None:
    """dev/test boots must be unaffected even with CF fully unset."""
    cfg = _cfg(env=env)
    assert_production_auth_config(cfg)  # must not raise


# ---------------------------------------------------------------------------
# 2. WebSocket auth gate
# ---------------------------------------------------------------------------


def _fake_ws(
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return SimpleNamespace(query_params=query or {}, headers=headers or {})


class _FakeVerifier:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises

    async def verify_jwt(self, token: str) -> Any:
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(email="jon.fila@amiralearning.com", name="Jon", claims={})


@pytest.fixture()
def ws_routes(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the module and default to local-dev conditions."""
    import artemis.ws.routes as routes

    monkeypatch.delenv("ARTEMIS_TOKEN", raising=False)
    monkeypatch.setattr(routes.settings, "cf_access_enabled", False)
    return routes


async def test_ws_local_dev_allows(ws_routes: Any) -> None:
    allowed, _ = await ws_routes._authorize_ws(_fake_ws())
    assert allowed


async def test_ws_shared_token_query_param(ws_routes: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    ok, _ = await ws_routes._authorize_ws(_fake_ws(query={"token": "secret-token"}))
    assert ok


async def test_ws_shared_token_header(ws_routes: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    ok, _ = await ws_routes._authorize_ws(
        _fake_ws(headers={"Sec-WebSocket-Protocol": "secret-token"})
    )
    assert ok


async def test_ws_shared_token_wrong_or_missing_rejected(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    ok, reason = await ws_routes._authorize_ws(_fake_ws(query={"token": "nope"}))
    assert not ok and "token" in reason
    ok, _ = await ws_routes._authorize_ws(_fake_ws())
    assert not ok


def test_ws_shared_token_compare_is_constant_time(ws_routes: Any) -> None:
    """The compare must go through hmac.compare_digest (no == early-exit)."""
    import inspect

    src = inspect.getsource(ws_routes._check_auth)
    assert "compare_digest" in src
    # And behaviorally: prefix of the real token must not pass.
    assert not ws_routes._check_auth(_fake_ws(query={"token": "secret"}), "secret-token")


async def test_ws_cf_enabled_missing_jwt_rejected(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_routes.settings, "cf_access_enabled", True)
    monkeypatch.setattr(ws_routes, "get_cf_access_verifier", lambda: _FakeVerifier())
    ok, reason = await ws_routes._authorize_ws(_fake_ws())
    assert not ok and "Cf-Access-Jwt-Assertion" in reason


async def test_ws_cf_enabled_invalid_jwt_rejected(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.identity.cf_access import CfAccessVerificationError

    monkeypatch.setattr(ws_routes.settings, "cf_access_enabled", True)
    monkeypatch.setattr(
        ws_routes,
        "get_cf_access_verifier",
        lambda: _FakeVerifier(raises=CfAccessVerificationError("bad")),
    )
    ok, _ = await ws_routes._authorize_ws(
        _fake_ws(headers={"Cf-Access-Jwt-Assertion": "eyJ-bogus"})
    )
    assert not ok


async def test_ws_cf_enabled_valid_jwt_allowed(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_routes.settings, "cf_access_enabled", True)
    monkeypatch.setattr(ws_routes, "get_cf_access_verifier", lambda: _FakeVerifier())
    ok, _ = await ws_routes._authorize_ws(
        _fake_ws(headers={"Cf-Access-Jwt-Assertion": "eyJ-valid"})
    )
    assert ok


async def test_ws_cf_enabled_shared_token_is_not_a_fallback(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With CF enabled, a correct shared token must NOT bypass JWT auth."""
    from artemis.identity.cf_access import CfAccessVerificationError

    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    monkeypatch.setattr(ws_routes.settings, "cf_access_enabled", True)
    monkeypatch.setattr(
        ws_routes,
        "get_cf_access_verifier",
        lambda: _FakeVerifier(raises=CfAccessVerificationError("bad")),
    )
    ok, _ = await ws_routes._authorize_ws(
        _fake_ws(
            query={"token": "secret-token"},
            headers={"Cf-Access-Jwt-Assertion": "eyJ-bogus"},
        )
    )
    assert not ok


async def test_ws_cf_enabled_misconfigured_verifier_fails_closed(
    ws_routes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.identity.cf_access import CfAccessConfigurationError

    monkeypatch.setattr(ws_routes.settings, "cf_access_enabled", True)

    def _boom() -> Any:
        raise CfAccessConfigurationError("not configured")

    monkeypatch.setattr(ws_routes, "get_cf_access_verifier", _boom)
    ok, _ = await ws_routes._authorize_ws(_fake_ws(headers={"Cf-Access-Jwt-Assertion": "eyJ-any"}))
    assert not ok


# ---------------------------------------------------------------------------
# 3. SSRF egress guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/admin",
        "http://10.0.0.5/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.10/router",
        "http://172.16.0.1/",
        "http://[::1]:8080/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
def test_egress_blocks_private_and_loopback(url: str) -> None:
    with pytest.raises(EgressBlockedError):
        validate_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/", ""])
def test_egress_blocks_non_http_schemes(url: str) -> None:
    with pytest.raises(EgressBlockedError):
        validate_url(url)


def test_egress_allows_public_ip_literal() -> None:
    validate_url("https://93.184.216.34/page.pdf")  # must not raise


def test_egress_https_only_mode() -> None:
    with pytest.raises(EgressBlockedError):
        validate_url("http://93.184.216.34/", require_https=True)
    validate_url("https://93.184.216.34/", require_https=True)


def _resolver_returning(*ips: str) -> Any:
    def _resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)) for ip in ips
        ]

    return _resolve


def test_egress_blocks_hostname_resolving_to_private() -> None:
    """DNS name pointing at a private address (incl. rebinding) is blocked."""
    with pytest.raises(EgressBlockedError):
        validate_url("https://evil.example.com/", _resolver=_resolver_returning("10.1.2.3"))
    # Mixed public+private (rebinding-style) is also blocked.
    with pytest.raises(EgressBlockedError):
        validate_url(
            "https://evil.example.com/",
            _resolver=_resolver_returning("93.184.216.34", "127.0.0.1"),
        )


def test_egress_allows_hostname_resolving_to_public() -> None:
    validate_url("https://good.example.com/", _resolver=_resolver_returning("93.184.216.34"))


def test_egress_blocks_unresolvable_host() -> None:
    def _resolve(host: str, port: int, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    with pytest.raises(EgressBlockedError):
        validate_url("https://nope.invalid/", _resolver=_resolve)


async def test_scout_http_client_is_guarded() -> None:
    """The default ScoutHttpClient must refuse to fetch a loopback URL."""
    from artemis.scouts._http import ScoutHttpClient

    async with ScoutHttpClient(rate_limit=100.0) as http:
        with pytest.raises(EgressBlockedError):
            await http.get("http://127.0.0.1:9/anything")


async def test_pdf_extractor_blocks_private_url() -> None:
    """pdf_extractor.extract returns a clear ERROR string for a private URL."""
    from artemis.tools.pdf_extractor import _factory

    _tool, impl = _factory(None)  # type: ignore[arg-type]  # ctx unused by impl
    result = await impl({"url": "http://169.254.169.254/latest/meta-data/"})
    assert result.startswith("ERROR:")
    assert "blocked egress" in result
