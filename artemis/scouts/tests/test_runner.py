"""Tests for artemis.scouts.runner — operator CLI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.scouts.base import ScoutRunResult
from artemis.scouts.runner import _REGISTRY, _build_scout, _parse_args, _run_once, main

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_known_scouts() -> None:
    assert "legislative_scout" in _REGISTRY
    assert "federal_funding_scout" in _REGISTRY
    assert "starbridge_researcher" in _REGISTRY
    assert "regional_news_scout" in _REGISTRY
    assert "linkedin_observer" in _REGISTRY


# ---------------------------------------------------------------------------
# _build_scout
# ---------------------------------------------------------------------------


def test_build_scout_unknown_type_raises() -> None:
    from artemis.scouts.base import ScoutConfig

    cfg = ScoutConfig()
    with pytest.raises(ValueError, match="Unknown scout_type"):
        _build_scout("does_not_exist", cfg)


def test_build_scout_legislative() -> None:
    from artemis.scouts.base import ScoutConfig

    scout = _build_scout("legislative_scout", ScoutConfig())
    assert scout.scout_type == "legislative_scout"


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


def test_parse_args_once() -> None:
    args = _parse_args(["--once", "legislative_scout"])
    assert args.once == "legislative_scout"
    assert args.watch is False
    assert args.dry_run is False


def test_parse_args_once_dry_run() -> None:
    args = _parse_args(["--once", "legislative_scout", "--dry-run"])
    assert args.dry_run is True


def test_parse_args_watch() -> None:
    args = _parse_args(["--watch"])
    assert args.watch is True
    assert args.once is None


def test_parse_args_once_and_watch_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--once", "foo", "--watch"])


# ---------------------------------------------------------------------------
# _run_once
# ---------------------------------------------------------------------------


async def test_run_once_unknown_scout_returns_1() -> None:
    code = await _run_once("no_such_scout", dry_run=False)
    assert code == 1


async def test_run_once_valid_scout_returns_0() -> None:
    fake_result = ScoutRunResult(scout_type="legislative_scout", status="skipped")
    with patch("artemis.scouts.runner._build_scout") as mock_build:
        mock_scout = MagicMock()
        mock_scout.run_once = AsyncMock(return_value=fake_result)
        mock_build.return_value = mock_scout
        code = await _run_once("legislative_scout", dry_run=False)
    assert code == 0


async def test_run_once_dry_run_forces_dry_run_config() -> None:
    """--dry-run overrides the YAML config."""
    fake_result = ScoutRunResult(scout_type="legislative_scout", status="skipped")
    built_configs: list[object] = []

    def capture_build(scout_type: str, cfg: object) -> MagicMock:
        built_configs.append(cfg)
        m = MagicMock()
        m.run_once = AsyncMock(return_value=fake_result)
        return m

    with patch("artemis.scouts.runner._build_scout", side_effect=capture_build):
        await _run_once("legislative_scout", dry_run=True)

    assert len(built_configs) == 1
    from artemis.scouts.base import ScoutConfig

    assert isinstance(built_configs[0], ScoutConfig)
    assert built_configs[0].dry_run is True


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


def test_main_once_unknown_scout_exits_1() -> None:
    code = main(["--once", "nonexistent_scout"])
    assert code == 1


def test_main_once_known_scout_exits_0() -> None:
    fake_result = ScoutRunResult(scout_type="legislative_scout", status="skipped")
    with patch("artemis.scouts.runner._build_scout") as mock_build:
        mock_scout = MagicMock()
        mock_scout.run_once = AsyncMock(return_value=fake_result)
        mock_build.return_value = mock_scout
        code = main(["--once", "legislative_scout"])
    assert code == 0
