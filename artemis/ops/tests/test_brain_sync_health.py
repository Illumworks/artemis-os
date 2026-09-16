"""The health report must notice when brain sync has stopped.

From 2026-09-10 to 2026-09-16 this machine's notes repo was wedged mid-rebase.
Nothing said so: the sync guard refused correctly on every run and both hooks
discarded its output, so the repo looked normal while receiving nothing. Six
days later a session read the stale tree and reported that a document written on
another Mac did not exist.

These tests drive real git repositories rather than mocks, because the bug was
in what git actually reports -- see the unpushed-count test, which pins a
command that looks right and answers "nothing stranded" no matter what.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import artemis.ops.health as health


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """An origin and a clone, each with one commit, ready to be diverged."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "f.md").write_text("base\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "base")
    _git(origin, "branch", "-M", "main")

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], capture_output=True, check=True)
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    return origin, work


def _findings_for(monkeypatch: pytest.MonkeyPatch, repo: Path) -> list[health.Finding]:
    monkeypatch.setattr(health, "BRAIN_REPO", repo)
    report = health.Report(generated_at=None)  # type: ignore[arg-type]
    report.brain_sync = health.collect_brain_sync()
    return [f for f in health.derive_findings(report) if "brain" in f.message]


def test_healthy_repo_produces_no_finding(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    assert _findings_for(monkeypatch, work) == []


def test_wedged_rebase_is_stuck_and_names_the_fix(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact state that cost six days."""
    origin, work = repos
    (origin / "f.md").write_text("base\norigin-side\n")
    _git(origin, "commit", "-qam", "origin change")
    (work / "f.md").write_text("base\nlocal-side\n")
    _git(work, "commit", "-qam", "local change")
    _git(work, "fetch", "-q", "origin")
    _git(work, "rebase", "origin/main")

    assert (work / ".git" / "rebase-merge").exists(), "fixture failed to wedge the repo"

    findings = _findings_for(monkeypatch, work)
    assert len(findings) == 1
    assert findings[0].severity == "stuck"
    assert "WEDGED" in findings[0].message
    # The message has to carry the fix; the whole failure was nobody knowing what to do.
    assert "rebase --abort" in findings[0].message


def test_detached_head_is_reported_as_stopped(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, work = repos
    _git(work, "checkout", "-q", "--detach", "HEAD")
    findings = _findings_for(monkeypatch, work)
    assert len(findings) == 1
    assert findings[0].severity == "stuck"
    assert "detached HEAD" in findings[0].message


def test_feature_branch_is_a_warning_not_a_failure(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another agent working on a branch is normal; it still stops the backup."""
    _, work = repos
    _git(work, "checkout", "-q", "-b", "codex/scratch")
    findings = _findings_for(monkeypatch, work)
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "codex/scratch" in findings[0].message


def test_unpushed_commits_are_counted(
    repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin for a command that looks right and always says zero.

    ``git log --oneline --not --remotes`` -- which the runbook recommends as the
    check that matters -- prints nothing regardless of what is stranded, because
    git only defaults to HEAD when there are NO revision arguments and ``--not``
    is one. Only an explicit positive ref counts.
    """
    _, work = repos
    (work / "f.md").write_text("base\nstranded\n")
    _git(work, "commit", "-qam", "never pushed")

    assert _git(work, "log", "--oneline", "--not", "--remotes") == "", (
        "if this ever returns output, git changed and the collector can be simplified"
    )

    monkeypatch.setattr(health, "BRAIN_REPO", work)
    assert health.collect_brain_sync()["unpushed"] == "1"

    findings = _findings_for(monkeypatch, work)
    assert any(f.severity == "stuck" and "nowhere else" in f.message for f in findings)


def test_absent_repo_is_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "BRAIN_REPO", tmp_path / "nope")
    assert health.collect_brain_sync()["state"] == "absent"
