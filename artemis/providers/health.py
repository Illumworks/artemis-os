"""Provider health probing.

Single entry point: probe_provider_health(provider) -> health dict
                    probe_all_providers() -> list of health records

Caches results for 60s per provider to avoid hammering. Hanging probe →
"unknown" status, not "down". All probes respect a ≤ 2s timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from typing import Any

import httpx

from artemis.config import settings

logger = logging.getLogger(__name__)

# TTL in seconds for the per-provider health cache
_CACHE_TTL = 60.0

# Per-provider HTTP / process timeout (seconds)
_PROBE_TIMEOUT = 2.0

# {provider_id: {"result": dict, "expires_at": float}}
_cache: dict[str, dict[str, Any]] = {}

_ALL_PROVIDERS = (
    "claude-code",
    "codex",
    "lm-studio",
    "anthropic",
    "openai",
    "gemini",
    "openrouter",
)


def clear_health_cache() -> None:
    """Invalidate all cached health results.

    Called when connector/settings change so the dashboard immediately
    reflects the new state on next probe.
    """
    _cache.clear()


async def probe_provider_health(provider: str) -> dict[str, Any]:
    """Return health info for a single provider.

    Response shape::

        {
          "provider": "lm-studio",
          "available": true,
          "latency_ms": 42,
          "version": "qwen3-14b loaded",
          "error": null,
          "checked_at": "2026-06-06T15:30:00Z"
        }

    Always returns a dict — never raises.  On any unexpected error, the
    result has ``available=False`` and ``error`` contains the message.
    """
    now = time.monotonic()
    cached = _cache.get(provider)
    if cached and cached["expires_at"] > now:
        return dict(cached["result"])

    result = await _do_probe(provider)
    _cache[provider] = {"result": result, "expires_at": now + _CACHE_TTL}
    return dict(result)


async def probe_all_providers() -> list[dict[str, Any]]:
    """Probe every known provider in parallel; return list of health records."""
    tasks = [probe_provider_health(p) for p in _ALL_PROVIDERS]
    return list(await asyncio.gather(*tasks))


# ── per-provider probe implementations ────────────────────────────────────────


async def _do_probe(provider: str) -> dict[str, Any]:
    """Dispatch to the per-provider probe. Never raises."""
    import datetime

    checked_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base: dict[str, Any] = {
        "provider": provider,
        "available": False,
        "latency_ms": None,
        "version": None,
        "error": None,
        "checked_at": checked_at,
        "models": None,
    }
    try:
        if provider == "claude-code":
            return await _probe_cli("claude-code", "claude", base)
        elif provider == "codex":
            return await _probe_cli("codex", "codex", base)
        elif provider == "lm-studio":
            return await _probe_lm_studio(base)
        elif provider == "anthropic":
            return _probe_env_key("anthropic", "ANTHROPIC_API_KEY", base)
        elif provider == "openai":
            return _probe_env_key("openai", "OPENAI_API_KEY", base)
        elif provider == "gemini":
            # Gemini accepts either key name
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if key:
                return {**base, "available": True, "version": "key configured"}
            return {**base, "error": "GEMINI_API_KEY or GOOGLE_API_KEY not set"}
        elif provider == "openrouter":
            return _probe_env_key("openrouter", "OPENROUTER_API_KEY", base)
        else:
            return {**base, "error": f"Unknown provider: {provider!r}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health probe for %r raised unexpectedly: %s", provider, exc)
        return {**base, "available": False, "error": f"unexpected: {exc}"}


async def _probe_cli(provider: str, binary: str, base: dict[str, Any]) -> dict[str, Any]:
    """Check whether a CLI binary is on PATH, then try --version."""
    path = shutil.which(binary)
    if not path:
        return {**base, "available": False, "error": f"{binary!r} not found on PATH"}

    t0 = time.monotonic()
    version_str: str | None = None
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=_PROBE_TIMEOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT)
        version_str = stdout.decode().strip().splitlines()[0] if stdout else None
    except (TimeoutError, OSError):
        # Binary exists but --version hung or failed — still mark available
        pass
    latency_ms = round((time.monotonic() - t0) * 1000)
    return {
        **base,
        "available": True,
        "latency_ms": latency_ms,
        "version": version_str or f"{binary} on PATH",
    }


async def _probe_lm_studio(base: dict[str, Any]) -> dict[str, Any]:
    """GET /v1/models from LM Studio with 2s timeout."""
    t0 = time.monotonic()
    url = f"{settings.lm_studio_base_url}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
        latency_ms = round((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            return {
                **base,
                "error": f"LM Studio returned HTTP {resp.status_code}",
                "latency_ms": latency_ms,
            }
        data = resp.json()
        model_ids: list[str] = [m["id"] for m in data.get("data", [])]
        return {
            **base,
            "available": True,
            "latency_ms": latency_ms,
            "version": f"{len(model_ids)} model(s) loaded",
            "models": model_ids,
        }
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        latency_ms = round((time.monotonic() - t0) * 1000)
        return {
            **base,
            "latency_ms": latency_ms,
            "error": f"LM Studio unreachable: {exc}",
        }


def _probe_env_key(provider: str, env_var: str, base: dict[str, Any]) -> dict[str, Any]:
    """Check whether an API key env var is set and non-empty."""
    key = os.environ.get(env_var, "")
    if key:
        return {**base, "available": True, "version": "key configured"}
    return {**base, "error": f"{env_var} not set or empty"}
