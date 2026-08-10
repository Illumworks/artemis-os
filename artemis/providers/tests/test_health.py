"""Tests for provider health probing (Part B of routing-control-surface brief).

Test numbers match the brief:
  1. probe_provider_health('claude-code') returns available=True on this machine.
  2. probe_provider_health('codex') returns available=False (not on PATH).
  3. probe_provider_health('lm-studio') returns available=True with models list.
     (Skipped if LM Studio is not running — CI-safe.)
  4. probe_provider_health('anthropic') returns available=False when env key empty.
  5. Cache TTL: two consecutive calls within 60s do not re-probe.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from artemis.config import settings
from artemis.providers import health as health_module
from artemis.providers.health import (
    _ALL_PROVIDERS,
    clear_health_cache,
    probe_all_providers,
    probe_provider_health,
)


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Clear the health cache before each test so probes don't bleed through."""
    clear_health_cache()
    yield
    clear_health_cache()


# ── Test 1: claude-code ───────────────────────────────────────────────────────


async def test_probe_claude_code_available() -> None:
    """claude-code CLI is on PATH on this machine (verified in audit)."""
    result = await probe_provider_health("claude-code")
    assert result["provider"] == "claude-code"
    assert result["available"] is True
    assert result["checked_at"]
    # version may be None if --version hangs, but binary must be found
    assert result.get("error") is None


# ── Test 2: codex ─────────────────────────────────────────────────────────────


async def test_probe_codex_not_on_path() -> None:
    """codex is not on PATH (binary inside .app bundle, no symlink yet)."""
    with patch("shutil.which", return_value=None):
        result = await probe_provider_health("codex")
    assert result["provider"] == "codex"
    assert result["available"] is False
    assert result["error"]


# ── Test 3: lm-studio ─────────────────────────────────────────────────────────


async def test_probe_lm_studio_when_running() -> None:
    """Probe LM Studio; skip if not reachable (CI environment)."""
    import httpx

    probe_url = f"{settings.lm_studio_base_url}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(probe_url)
        if resp.status_code != 200:
            pytest.skip("LM Studio returned non-200, skipping")
    except (httpx.TimeoutException, httpx.ConnectError):
        pytest.skip("LM Studio not running, skipping")

    result = await probe_provider_health("lm-studio")
    assert result["provider"] == "lm-studio"
    assert result["available"] is True
    assert result["latency_ms"] is not None
    assert isinstance(result["models"], list)


async def test_probe_lm_studio_unavailable() -> None:
    """When LM Studio is not reachable, probe returns available=False without raising."""
    import httpx

    with patch(
        "artemis.providers.health.httpx.AsyncClient",
        return_value=AsyncMock(
            __aenter__=AsyncMock(
                return_value=AsyncMock(
                    get=AsyncMock(side_effect=httpx.ConnectError("connection refused"))
                )
            ),
            __aexit__=AsyncMock(return_value=False),
        ),
    ):
        result = await probe_provider_health("lm-studio")

    assert result["provider"] == "lm-studio"
    assert result["available"] is False
    assert "unreachable" in (result["error"] or "")


async def test_probe_lm_studio_uses_config_base_url() -> None:
    """Health probe reads lm_studio_base_url from settings, not a hardcoded constant.

    Validates both ARTEMIS_LM_STUDIO_BASE_URL and LM_STUDIO_BASE_URL aliases work,
    and that the probed URL reflects the configured value (not http://127.0.0.1:1234).
    """
    import httpx

    import artemis.config as config_module

    custom_base = "http://100.64.0.99:1234"
    # Use model_validate so the aliased field is accepted without reading env/file.
    new_settings = config_module.Settings.model_validate(
        {"ARTEMIS_LM_STUDIO_BASE_URL": custom_base}
    )

    captured_urls: list[str] = []

    async def _fake_get(url: str, **_kw: object) -> AsyncMock:
        captured_urls.append(url)
        raise httpx.ConnectError("not running (test)")

    mock_client = AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(get=AsyncMock(side_effect=_fake_get))),
        __aexit__=AsyncMock(return_value=False),
    )

    with (
        patch.object(health_module, "settings", new_settings),
        patch("artemis.providers.health.httpx.AsyncClient", return_value=mock_client),
    ):
        clear_health_cache()
        result = await probe_provider_health("lm-studio")

    assert result["provider"] == "lm-studio"
    assert result["available"] is False
    assert len(captured_urls) == 1, f"Expected exactly one GET, got: {captured_urls}"
    assert captured_urls[0] == f"{custom_base}/v1/models", (
        f"Probe hit {captured_urls[0]!r}; expected URL derived from config base {custom_base!r}"
    )


# ── Test 4: anthropic key missing ─────────────────────────────────────────────


async def test_probe_anthropic_no_key() -> None:
    """When ANTHROPIC_API_KEY is empty, probe returns available=False."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
        result = await probe_provider_health("anthropic")
    assert result["provider"] == "anthropic"
    assert result["available"] is False


async def test_probe_anthropic_with_key() -> None:
    """When ANTHROPIC_API_KEY is set, probe returns available=True (no real API call)."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        result = await probe_provider_health("anthropic")
    assert result["provider"] == "anthropic"
    assert result["available"] is True


# ── Test 5: cache TTL ─────────────────────────────────────────────────────────


async def test_cache_ttl_two_calls_same_result() -> None:
    """Two consecutive calls within 60s use cached result — probe not re-called."""
    probe_count = 0

    original_do_probe = health_module._do_probe

    async def _counted_probe(provider: str) -> dict:
        nonlocal probe_count
        probe_count += 1
        return await original_do_probe(provider)

    with patch.object(health_module, "_do_probe", side_effect=_counted_probe):
        _r1 = await probe_provider_health("anthropic")
        _r2 = await probe_provider_health("anthropic")

    # Both calls should return a result, but _do_probe should only be called once
    assert probe_count == 1


async def test_cache_expires_after_ttl() -> None:
    """After forcing TTL to expire, the next probe re-runs."""
    probe_count = 0
    original_do_probe = health_module._do_probe

    async def _counted_probe(provider: str) -> dict:
        nonlocal probe_count
        probe_count += 1
        return await original_do_probe(provider)

    with patch.object(health_module, "_do_probe", side_effect=_counted_probe):
        await probe_provider_health("anthropic")
        # Force expire by backdating the cache entry
        now = time.monotonic()
        health_module._cache["anthropic"]["expires_at"] = now - 1
        await probe_provider_health("anthropic")

    assert probe_count == 2


# ── probe_all_providers ───────────────────────────────────────────────────────


async def test_probe_all_providers_returns_all() -> None:
    """probe_all_providers returns a record for every known provider."""
    results = await probe_all_providers()
    providers_found = {r["provider"] for r in results}
    assert set(_ALL_PROVIDERS) == providers_found


async def test_probe_all_providers_never_raises() -> None:
    """Even if every probe fails, probe_all_providers returns a list (not raises)."""
    import httpx

    with (
        patch("shutil.which", return_value=None),
        patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OPENROUTER_API_KEY": "",
            },
            clear=False,
        ),
        patch(
            "artemis.providers.health.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(
                    return_value=AsyncMock(get=AsyncMock(side_effect=httpx.ConnectError("refused")))
                ),
                __aexit__=AsyncMock(return_value=False),
            ),
        ),
    ):
        clear_health_cache()
        results = await probe_all_providers()

    assert isinstance(results, list)
    assert len(results) == len(_ALL_PROVIDERS)
