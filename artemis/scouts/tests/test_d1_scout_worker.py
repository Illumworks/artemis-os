"""Tests for D1 scout worker scaffold.

Covers: ScoutConfig defaults, BaseScout ABC enforcement, stub scout_type class
vars, run_once paths (disabled/empty/exception/findings), emit_signals (URL,
payload, auth, response parsing, HTTP errors, network errors), load_config
(defaults, YAML, env overrides), scout_config_for, create_scheduler.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts.base import BaseScout, ScoutConfig, ScoutRunResult
from artemis.scouts.config import WorkerConfig, load_config, scout_config_for
from artemis.scouts.linkedin_observer import LinkedInObserverScout
from artemis.scouts.regional_news_scout import RegionalNewsScout
from artemis.scouts.scheduler import create_scheduler
from artemis.scouts.starbridge_researcher import StarbridgeResearcherScout

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------



# Minimal raw finding that survives canonical Finding normalization
# (emit_signals now drops findings that can't be normalized).
_RAW_FINDING: dict[str, Any] = {
    "headline": "Bill introduced",
    "sourceUrl": "https://example.com/bill/1",
    "urgency": "standard",
    "reasonCodes": ["BILL_INTRODUCED"],
}


class _StubScout(BaseScout):
    scout_type = "stub_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        _client: httpx.AsyncClient | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(config, _client=_client)
        self._findings = findings or []

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return self._findings


class _RaisingScout(BaseScout):
    scout_type = "raising_scout"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        raise RuntimeError("simulated gather failure")


class _Scout2(BaseScout):
    scout_type = "scout_two"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return []


def _mock_client(
    response_json: dict[str, Any],
    status_code: int = 200,
) -> httpx.AsyncClient:
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    if status_code >= 400:
        mock_resp.text = str(response_json)
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP error",
            request=MagicMock(),
            response=mock_resp,
        )
    else:
        mock_resp.raise_for_status.return_value = None
    client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = mock_resp
    return typing.cast(httpx.AsyncClient, client)


# ---------------------------------------------------------------------------
# ScoutConfig defaults
# ---------------------------------------------------------------------------


def test_scout_config_defaults() -> None:
    cfg = ScoutConfig()
    assert cfg.api_url == "http://localhost:8000"
    assert cfg.api_token == ""
    assert cfg.dry_run is False
    assert cfg.interval_minutes == 60
    assert cfg.enabled is True


def test_scout_config_custom_values() -> None:
    cfg = ScoutConfig(
        api_url="http://x:9000",
        api_token="tok",
        dry_run=True,
        interval_minutes=30,
        enabled=False,
    )
    assert cfg.api_url == "http://x:9000"
    assert cfg.api_token == "tok"
    assert cfg.dry_run is True
    assert cfg.interval_minutes == 30
    assert cfg.enabled is False


# ---------------------------------------------------------------------------
# BaseScout ABC enforcement
# ---------------------------------------------------------------------------


def test_base_scout_cannot_instantiate_directly() -> None:
    with pytest.raises(TypeError):
        BaseScout()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Stub scout class vars
# ---------------------------------------------------------------------------


def test_starbridge_researcher_scout_type() -> None:
    assert StarbridgeResearcherScout.scout_type == "starbridge_researcher"


def test_regional_news_scout_type() -> None:
    assert RegionalNewsScout.scout_type == "regional_news_scout"


def test_linkedin_observer_scout_type() -> None:
    assert LinkedInObserverScout.scout_type == "linkedin_observer"


def test_stub_scouts_instantiate_with_config() -> None:
    cfg = ScoutConfig(enabled=True, interval_minutes=45)
    scout = StarbridgeResearcherScout(cfg)
    assert scout.config.interval_minutes == 45


# ---------------------------------------------------------------------------
# run_once paths
# ---------------------------------------------------------------------------


async def test_run_once_skips_when_disabled() -> None:
    scout = _StubScout(ScoutConfig(enabled=False), findings=[{"x": 1}])
    result = await scout.run_once()
    assert result.status == "skipped"
    assert result.scout_type == "stub_scout"


async def test_run_once_skips_when_no_findings() -> None:
    scout = _StubScout(ScoutConfig(enabled=True), findings=[])
    result = await scout.run_once()
    assert result.status == "skipped"


async def test_run_once_does_not_raise_on_gather_exception() -> None:
    scout = _RaisingScout(ScoutConfig(enabled=True))
    result = await scout.run_once()
    assert result.status == "skipped"


async def test_run_once_calls_emit_when_findings_present() -> None:
    client = _mock_client(
        {"runId": "r1", "status": "ok", "createdCount": 2, "skippedCount": 0, "errors": []}
    )
    scout = _StubScout(ScoutConfig(enabled=True), _client=client, findings=[_RAW_FINDING])
    result = await scout.run_once()
    assert result.status == "ok"
    assert result.run_id == "r1"
    assert result.created_count == 2


# ---------------------------------------------------------------------------
# emit_signals — URL construction
# ---------------------------------------------------------------------------


async def test_emit_signals_url_no_trailing_slash() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(api_url="http://localhost:8000"), _client=client)
    await scout.emit_signals([_RAW_FINDING])
    url = typing.cast(AsyncMock, client).post.call_args[0][0]
    assert url == "http://localhost:8000/api/scouts/runs"


async def test_emit_signals_url_strips_trailing_slash() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(api_url="http://localhost:8000/"), _client=client)
    await scout.emit_signals([_RAW_FINDING])
    url = typing.cast(AsyncMock, client).post.call_args[0][0]
    assert url == "http://localhost:8000/api/scouts/runs"


# ---------------------------------------------------------------------------
# emit_signals — payload
# ---------------------------------------------------------------------------


async def test_emit_signals_payload_structure() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(dry_run=True), _client=client)
    await scout.emit_signals([_RAW_FINDING])
    payload = typing.cast(AsyncMock, client).post.call_args[1]["json"]
    assert payload["scoutType"] == "stub_scout"
    assert payload["dryRun"] is True
    # Findings are normalized to the canonical wire contract before POSTing.
    assert len(payload["findings"]) == 1
    wire = payload["findings"][0]
    assert wire["headline"] == "Bill introduced"
    assert wire["sourceUrl"] == "https://example.com/bill/1"
    assert wire["campaignFamily"]
    assert wire["urgencyTier"] == "standard"
    assert wire["discoveredBy"] == "stub_scout"


async def test_emit_signals_includes_auth_header_when_token_set() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(api_token="secret"), _client=client)
    await scout.emit_signals([_RAW_FINDING])
    headers = typing.cast(AsyncMock, client).post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer secret"


async def test_emit_signals_no_auth_header_when_token_empty() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(api_token=""), _client=client)
    await scout.emit_signals([_RAW_FINDING])
    headers = typing.cast(AsyncMock, client).post.call_args[1]["headers"]
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# emit_signals — response parsing
# ---------------------------------------------------------------------------


async def test_emit_signals_parses_run_id_and_counts() -> None:
    client = _mock_client(
        {
            "runId": "abc-123",
            "status": "complete",
            "createdCount": 5,
            "skippedCount": 2,
            "errors": [],
        }
    )
    scout = _StubScout(ScoutConfig(), _client=client)
    result = await scout.emit_signals([_RAW_FINDING])
    assert result.run_id == "abc-123"
    assert result.status == "complete"
    assert result.created_count == 5
    assert result.skipped_count == 2
    assert result.errors == []


async def test_emit_signals_handles_missing_optional_fields() -> None:
    client = _mock_client({"status": "ok"})
    scout = _StubScout(ScoutConfig(), _client=client)
    result = await scout.emit_signals([_RAW_FINDING])
    assert result.run_id is None
    assert result.created_count == 0
    assert result.skipped_count == 0


# ---------------------------------------------------------------------------
# emit_signals — error handling
# ---------------------------------------------------------------------------


async def test_emit_signals_http_error_returns_error_result() -> None:
    client = _mock_client({"message": "unauthorized"}, status_code=401)
    scout = _StubScout(ScoutConfig(), _client=client)
    result = await scout.emit_signals([_RAW_FINDING])
    assert result.status == "error"
    assert result.errors[0]["status_code"] == 401


async def test_emit_signals_network_error_returns_error_result() -> None:
    client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = httpx.ConnectError("connection refused")
    scout = _StubScout(ScoutConfig(), _client=typing.cast(httpx.AsyncClient, client))
    result = await scout.emit_signals([_RAW_FINDING])
    assert result.status == "error"
    assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# ScoutRunResult defaults
# ---------------------------------------------------------------------------


def test_scout_run_result_defaults() -> None:
    r = ScoutRunResult(scout_type="my_scout")
    assert r.run_id is None
    assert r.status == "error"
    assert r.created_count == 0
    assert r.skipped_count == 0
    assert r.errors == []


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_defaults_when_no_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.api_url == "http://localhost:8000"
    assert cfg.api_token == ""
    assert cfg.dry_run is False
    assert cfg.scouts == {}


def test_load_config_reads_yaml(tmp_path: Path) -> None:
    f = tmp_path / "scouts.yaml"
    f.write_text(
        "api_url: http://test:9000\ndry_run: false\n"
        "scouts:\n  my_scout:\n    enabled: true\n    interval_minutes: 30\n"
    )
    cfg = load_config(f)
    assert cfg.api_url == "http://test:9000"
    assert "my_scout" in cfg.scouts
    assert cfg.scouts["my_scout"].interval_minutes == 30
    assert cfg.scouts["my_scout"].enabled is True


def test_load_config_env_overrides_api_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_API_URL", "http://env:7000")
    f = tmp_path / "scouts.yaml"
    f.write_text("api_url: http://yaml:8000\n")
    cfg = load_config(f)
    assert cfg.api_url == "http://env:7000"


def test_load_config_env_overrides_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "envtoken")
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.api_token == "envtoken"


def test_load_config_dry_run_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_SCOUT_DRY_RUN", "1")
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.dry_run is True


def test_load_config_dry_run_not_active_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARTEMIS_SCOUT_DRY_RUN", raising=False)
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.dry_run is False


def test_load_config_board_peer_validation_scout_enabled_general_mode() -> None:
    """The real config/scouts.yaml: board_peer_validation_scout is enabled
    (2026-07-11 board-meeting scout go-live, seeded ahead of the delayed
    Salesforce customer list — general board-intel mode; see the scout's
    module docstring / peer_scout.py for the exclusion-provider deferral)."""
    cfg = load_config()  # no path override -> reads the real config/scouts.yaml
    assert "board_peer_validation_scout" in cfg.scouts
    assert cfg.scouts["board_peer_validation_scout"].enabled is True


def test_load_config_propagates_api_url_to_scout_configs(tmp_path: Path) -> None:
    f = tmp_path / "scouts.yaml"
    f.write_text(
        "api_url: http://shared:8080\n"
        "scouts:\n  scout_a:\n    enabled: true\n    interval_minutes: 60\n"
    )
    cfg = load_config(f)
    assert cfg.scouts["scout_a"].api_url == "http://shared:8080"


# ---------------------------------------------------------------------------
# scout_config_for
# ---------------------------------------------------------------------------


def test_scout_config_for_known_scout() -> None:
    known = ScoutConfig(interval_minutes=120, enabled=False)
    worker_cfg = WorkerConfig(api_url="http://x:8000", scouts={"my_scout": known})
    assert scout_config_for(worker_cfg, "my_scout") is known


def test_scout_config_for_unknown_scout_falls_back_to_globals() -> None:
    worker_cfg = WorkerConfig(api_url="http://fallback:8000", api_token="tok", dry_run=True)
    result = scout_config_for(worker_cfg, "unknown_scout")
    assert result.api_url == "http://fallback:8000"
    assert result.api_token == "tok"
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# create_scheduler
# ---------------------------------------------------------------------------


def test_create_scheduler_schedules_enabled_scout() -> None:
    scouts: list[BaseScout] = [_StubScout(ScoutConfig(enabled=True, interval_minutes=60))]
    scheduler = create_scheduler(scouts)
    assert len(scheduler.get_jobs()) == 1
    assert scheduler.get_job("stub_scout") is not None


def test_create_scheduler_skips_disabled_scout() -> None:
    scouts: list[BaseScout] = [_StubScout(ScoutConfig(enabled=False, interval_minutes=60))]
    scheduler = create_scheduler(scouts)
    assert len(scheduler.get_jobs()) == 0


def test_create_scheduler_mixed_enabled_disabled() -> None:
    scouts: list[BaseScout] = [
        _StubScout(ScoutConfig(enabled=True, interval_minutes=30)),
        _Scout2(ScoutConfig(enabled=False, interval_minutes=60)),
    ]
    scheduler = create_scheduler(scouts)
    assert len(scheduler.get_jobs()) == 1
    assert scheduler.get_job("stub_scout") is not None
    assert scheduler.get_job("scout_two") is None


def test_create_scheduler_empty_list_returns_no_jobs() -> None:
    scheduler = create_scheduler([])
    assert len(scheduler.get_jobs()) == 0
