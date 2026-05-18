"""Tests for find_cli_binary — binary discovery logic."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from artemis.providers._bin_path import find_cli_binary


def _make_executable(path: Path) -> Path:
    """Create an empty executable file at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ── env override ───────────────────────────────────────────────────────────


def test_env_override_returned_when_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path / "my-claude")
    monkeypatch.setenv("CLAUDE_BIN", str(binary))
    result = find_cli_binary("claude")
    assert result == str(binary)


def test_env_override_skipped_when_not_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    non_exec = tmp_path / "fake-llm-xyzzy"
    non_exec.write_text("#!/bin/sh\n")
    # Do NOT set executable bit
    monkeypatch.setenv("FAKE_LLM_XYZZY_BIN", str(non_exec))
    with patch("shutil.which", return_value=None):
        result = find_cli_binary("fake-llm-xyzzy")
    assert result is None


def test_env_var_name_derived_from_binary_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path / "codex")
    monkeypatch.setenv("CODEX_BIN", str(binary))
    result = find_cli_binary("codex")
    assert result == str(binary)


# ── returns None when not found ─────────────────────────────────────────────


def test_returns_none_when_binary_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOTALLY_ABSENT_BIN", raising=False)
    with patch("shutil.which", return_value=None):
        result = find_cli_binary("totally-absent")
    assert result is None


# ── extra_candidates ────────────────────────────────────────────────────────


def test_extra_candidates_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = _make_executable(tmp_path / "my-bin")
    monkeypatch.delenv("MY_BIN_BIN", raising=False)
    with patch("shutil.which", return_value=None):
        result = find_cli_binary("my-bin", extra_candidates=[str(binary)])
    assert result == str(binary)


def test_extra_candidates_skipped_when_not_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    non_exec = tmp_path / "my-bin"
    non_exec.write_text("#!/bin/sh\n")
    monkeypatch.delenv("MY_BIN_BIN", raising=False)
    with patch("shutil.which", return_value=None):
        result = find_cli_binary("my-bin", extra_candidates=[str(non_exec)])
    assert result is None


# ── shutil.which fallback ────────────────────────────────────────────────────


def test_shutil_which_fallback_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = _make_executable(tmp_path / "phantombinary")
    monkeypatch.delenv("PHANTOMBINARY_BIN", raising=False)
    with patch("shutil.which", return_value=str(binary)):
        result = find_cli_binary("phantombinary")
    assert result == str(binary)


# ── hyphen-to-underscore in env key ─────────────────────────────────────────


def test_hyphen_in_name_becomes_underscore_in_env_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path / "lm-studio")
    # lm-studio → LM_STUDIO_BIN
    monkeypatch.setenv("LM_STUDIO_BIN", str(binary))
    result = find_cli_binary("lm-studio")
    assert result == str(binary)
