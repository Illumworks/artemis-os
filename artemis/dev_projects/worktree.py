"""Git worktree manager for Forge write-mode sessions (Forge Phase 3, chunk 3.2).

Each Forge session that needs to edit code gets an isolated git worktree on a
throw-away branch.  The user's real working tree is never touched.

Public API
----------
worktree_path_for(project_path, session_id) -> Path   # sync, pure
ensure_worktree(project_path, session_id) -> str       # async, idempotent
remove_worktree(project_path, session_id, *, delete_branch) -> None
startup_sweep(get_live_session_ids) -> int
session_lock(session_id) -> asyncio.Lock

SECURITY NOTE
-------------
All git invocations use asyncio.create_subprocess_exec (no shell=True).
User-controlled strings (project_path, session_id) are never interpolated
into a shell command string.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_BASE_DIR = Path.home() / ".artemis" / "forge-worktrees"

_GIT_TIMEOUT = 30  # seconds for every subprocess git call


def _base_dir() -> Path:
    """Return the configured base directory for all forge worktrees."""
    import os

    val = os.environ.get("ARTEMIS_FORGE_WORKTREE_BASE_DIR", "").strip()
    return Path(val) if val else _DEFAULT_BASE_DIR


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class WorktreeError(Exception):
    """Raised when a git worktree operation fails in a non-recoverable way."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _project_slug(project_path: str) -> str:
    """Lowercase path.name, sanitise to [a-z0-9_-]."""
    name = Path(project_path).name.lower()
    return _SLUG_RE.sub("-", name).strip("-") or "project"


def worktree_path_for(project_path: str, session_id: int) -> Path:
    """Return the deterministic worktree path for (project_path, session_id).

    Pure function -- no IO, no git calls.
    """
    slug = _project_slug(project_path)
    return _base_dir() / slug / str(session_id)


def _branch_name(session_id: int) -> str:
    return f"forge/session-{session_id}"


# ---------------------------------------------------------------------------
# Git subprocess helpers
# ---------------------------------------------------------------------------


async def _git(
    *args: str,
    timeout: float = _GIT_TIMEOUT,
    check: bool = True,
) -> tuple[int, str, str]:
    """Run ``git <args>`` with no shell.

    Returns (returncode, stdout, stderr).
    Raises WorktreeError if ``check`` is True and returncode != 0.
    All string args must already be clean values -- never pass unsanitised user
    input directly (session_id is always cast to int first; project_path is
    validated as a git repo before use).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise WorktreeError(f"git {' '.join(args[:3])} timed out after {timeout}s") from exc

    rc = proc.returncode if proc.returncode is not None else -1
    stdout = raw_out.decode(errors="replace").strip()
    stderr = raw_err.decode(errors="replace").strip()

    if check and rc != 0:
        raise WorktreeError(f"git {' '.join(args[:4])} failed (rc={rc}): {stderr or stdout}")
    return rc, stdout, stderr


# ---------------------------------------------------------------------------
# Public async functions
# ---------------------------------------------------------------------------


async def ensure_worktree(project_path: str, session_id: int) -> str:
    """Create or reuse the worktree for this session.

    Idempotent: if the directory already exists and is a valid git worktree,
    the path is returned immediately without re-running git worktree add.

    Steps when the worktree does not yet exist:
      1. Confirm project_path is a git repo.
      2. Create the parent directory.
      3. Run ``git worktree add`` (or ``git worktree add -b`` for new branches).
         If the worktree state is stale/missing, prune first then retry.
      4. Apply per-worktree push-block as defense-in-depth (see note below).

    Push-block defense-in-depth
    ---------------------------
    After the worktree is created we:
      a. ``git -C <wt> config extensions.worktreeConfig true``
         Enables per-worktree config (stored in .git/worktrees/<id>/config.worktree).
      b. ``git -C <wt> config --worktree remote.origin.pushurl no_push``
         Sets the push URL only in this worktree's config layer.

    The ``--worktree`` flag (not ``--local``) is critical: "local" config writes
    to the main repo's .git/config and would affect every worktree and the main
    tree.  ``--worktree`` writes to .git/worktrees/<id>/config.worktree, which
    is scoped exclusively to this worktree checkout.  The main tree (and every
    other worktree) retains its normal push URL.

    This is defense-in-depth ONLY.  A motivated agent can still run
    ``git remote set-url --push origin <real-url>`` inside the worktree and undo
    it.  The real gate is human review before any Forge-branch changes are merged
    to main.  We apply the push-block anyway to raise the bar.
    """
    wt_path = worktree_path_for(project_path, session_id)
    branch = _branch_name(session_id)

    # --- Idempotency check: if the dir exists and git recognises it, reuse it.
    if wt_path.exists():
        rc, _, _ = await _git(
            "-C",
            str(wt_path),
            "rev-parse",
            "--is-inside-work-tree",
            check=False,
        )
        if rc == 0:
            logger.debug("worktree: reusing existing worktree at %s", wt_path)
            return str(wt_path)
        # Directory exists but is not a valid git worktree -- clean it up and
        # recreate so we don't leave a half-broken state.
        logger.warning(
            "worktree: %s exists but is not a valid git worktree; removing and recreating",
            wt_path,
        )
        shutil.rmtree(wt_path, ignore_errors=True)

    # --- Confirm project_path is a git repo before doing anything else.
    rc, _, stderr = await _git(
        "-C",
        project_path,
        "rev-parse",
        "--is-inside-work-tree",
        check=False,
    )
    if rc != 0:
        raise WorktreeError(
            f"project_path {project_path!r} is not inside a git repository "
            f"(git rev-parse --is-inside-work-tree: {stderr})"
        )

    # --- Create parent directory.
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Determine whether the branch already exists in the project repo.
    _rc, branch_list, _ = await _git(
        "-C",
        project_path,
        "branch",
        "--list",
        branch,
        check=False,
    )
    branch_exists = bool(branch_list.strip())

    # --- Attempt worktree add (with one prune+retry on stale state).
    await _worktree_add(
        project_path=project_path,
        wt_path=wt_path,
        branch=branch,
        branch_exists=branch_exists,
    )

    # --- Defense-in-depth push block (per-worktree, does NOT touch main tree).
    await _apply_push_block(wt_path)

    logger.info("worktree: created worktree at %s on branch %s", wt_path, branch)
    return str(wt_path)


async def _worktree_add(
    *,
    project_path: str,
    wt_path: Path,
    branch: str,
    branch_exists: bool,
    _retry: bool = False,
) -> None:
    """Run git worktree add.  On first failure, prune + retry once."""
    if branch_exists:
        # Branch already exists: attach without -b.
        rc, stdout, stderr = await _git(
            "-C",
            project_path,
            "worktree",
            "add",
            str(wt_path),
            branch,
            check=False,
        )
    else:
        # New branch: create from HEAD.
        rc, stdout, stderr = await _git(
            "-C",
            project_path,
            "worktree",
            "add",
            "-b",
            branch,
            str(wt_path),
            "HEAD",
            check=False,
        )

    if rc == 0:
        return

    combined = (stderr + " " + stdout).lower()

    # If the error suggests a stale/registered-but-missing worktree state,
    # prune and retry once.
    is_stale = (
        "already registered" in combined
        or "already exists" in combined
        or "is already checked out" in combined
    )
    if is_stale and not _retry:
        logger.warning("worktree: add failed (stale state?), pruning and retrying: %s", stderr)
        await _git("-C", project_path, "worktree", "prune", check=False)
        # Clean up any remnant directory from the failed add.
        shutil.rmtree(wt_path, ignore_errors=True)
        await _worktree_add(
            project_path=project_path,
            wt_path=wt_path,
            branch=branch,
            branch_exists=branch_exists,
            _retry=True,
        )
        return

    raise WorktreeError(f"git worktree add failed (rc={rc}): {stderr or stdout}")


async def _apply_push_block(wt_path: Path) -> None:
    """Apply per-worktree push-block config (defense-in-depth).

    --worktree scopes the change to .git/worktrees/<id>/config.worktree only,
    leaving the main repo's .git/config completely untouched.
    """
    # Step 1: enable per-worktree config layer (required for --worktree flag).
    rc, _, err = await _git(
        "-C",
        str(wt_path),
        "config",
        "extensions.worktreeConfig",
        "true",
        check=False,
    )
    if rc != 0:
        logger.warning("worktree: could not enable worktreeConfig: %s", err)
        return  # Non-fatal; continue without the push block.

    # Step 2: only set pushurl if an 'origin' remote exists.
    #         This is a safety check -- tmp repos in tests and projects that
    #         have never had a remote should not get a spurious config write.
    rc_remotes, remotes_out, _ = await _git(
        "-C",
        str(wt_path),
        "remote",
        check=False,
    )
    if rc_remotes != 0:
        logger.warning("worktree: could not list remotes; skipping push-block")
        return

    if "origin" not in remotes_out.splitlines():
        logger.debug(
            "worktree: no 'origin' remote in %s; skipping push-block (expected for test repos)",
            wt_path,
        )
        return

    # --worktree writes to .git/worktrees/<id>/config.worktree, NOT to
    # .git/config.  The main tree and all other worktrees are unaffected.
    rc, _, err = await _git(
        "-C",
        str(wt_path),
        "config",
        "--worktree",
        "remote.origin.pushurl",
        "no_push",
        check=False,
    )
    if rc != 0:
        logger.warning("worktree: could not set push-block on %s: %s", wt_path, err)
    else:
        logger.debug("worktree: push-block applied to %s", wt_path)


async def remove_worktree(
    project_path: str,
    session_id: int,
    *,
    delete_branch: bool = False,
) -> None:
    """Remove the worktree for this session.

    Failure-tolerant: errors are logged but never raised, so cleanup never
    breaks the caller.
    """
    wt_path = worktree_path_for(project_path, session_id)
    branch = _branch_name(session_id)

    # 1. Ask git to unregister and remove the worktree.
    try:
        rc, _, stderr = await _git(
            "-C",
            project_path,
            "worktree",
            "remove",
            "--force",
            str(wt_path),
            check=False,
        )
        if rc != 0:
            msg = stderr.lower()
            if "not a working tree" in msg or "does not exist" in msg:
                logger.debug(
                    "worktree: remove for session=%s already gone: %s",
                    session_id,
                    stderr,
                )
            else:
                logger.warning(
                    "worktree: remove --force for session=%s failed (rc=%s): %s",
                    session_id,
                    rc,
                    stderr,
                )
    except WorktreeError as exc:
        logger.warning("worktree: remove_worktree git call failed: %s", exc)

    # 2. Optionally delete the branch.
    if delete_branch:
        try:
            rc, _, stderr = await _git(
                "-C",
                project_path,
                "branch",
                "-D",
                branch,
                check=False,
            )
            if rc != 0:
                msg = stderr.lower()
                if "not found" in msg or "no branch" in msg:
                    logger.debug("worktree: branch %s already gone: %s", branch, stderr)
                else:
                    logger.warning(
                        "worktree: branch -D %s failed (rc=%s): %s",
                        branch,
                        rc,
                        stderr,
                    )
        except WorktreeError as exc:
            logger.warning("worktree: branch delete failed: %s", exc)

    # 3. Best-effort rmtree in case git left something behind.
    if wt_path.exists():
        try:
            shutil.rmtree(wt_path, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("worktree: rmtree(%s) failed: %s", wt_path, exc)


async def startup_sweep(
    get_live_session_ids: Callable[[], Awaitable[set[int]]] | None = None,
) -> int:
    """Scan the base dir and remove orphaned worktrees on startup.

    For each ``<slug>/<session_id>`` directory whose integer session_id is NOT
    in the live set (when a resolver is provided), the directory is removed and
    ``git worktree prune`` is attempted best-effort on the parent project repo.

    LIMITATION (v1): we cannot reliably derive the project_path from an orphan
    directory without reading the gitdir link inside the worktree (which may
    itself be broken).  We therefore:
      - rmtree the orphan directory directly (always safe -- it is a throwaway
        checkout);
      - attempt ``git worktree prune`` in the directory one level up (which
        often IS the project tree for single-project setups but is not
        guaranteed in multi-project deployments);
      - log a note suggesting the operator run ``git worktree prune`` in the
        real project root if the stale entry persists.

    This is acceptable for v1 because:
      a. The orphan directories are always safe to delete (they are throw-away
         checkouts; no unique data lives there).
      b. The stale ``.git/worktrees/<id>`` entry in the project is cosmetic
         until the next ``git worktree prune`` run; it does not block future
         worktree creation.

    Returns the count of directories pruned.
    """
    base = _base_dir()
    if not base.exists():
        return 0

    live_ids: set[int] | None = None
    if get_live_session_ids is not None:
        try:
            live_ids = await get_live_session_ids()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "worktree: startup_sweep could not fetch live session ids: %s; "
                "skipping sweep to avoid false positives",
                exc,
            )
            return 0

    pruned = 0
    for slug_dir in base.iterdir():
        if not slug_dir.is_dir():
            continue
        for session_dir in slug_dir.iterdir():
            if not session_dir.is_dir():
                continue
            # Parse the session_id from the directory name.
            try:
                dir_session_id = int(session_dir.name)
            except ValueError:
                logger.debug("worktree: startup_sweep skipping non-integer dir %s", session_dir)
                continue

            # When no resolver is provided, leave the dirs alone.
            if live_ids is None:
                continue

            if dir_session_id in live_ids:
                continue

            # This session is no longer live -- remove it.
            logger.info(
                "worktree: startup_sweep removing orphan worktree dir %s (session=%s)",
                session_dir,
                dir_session_id,
            )
            try:
                shutil.rmtree(session_dir, ignore_errors=True)
                pruned += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("worktree: startup_sweep rmtree(%s) failed: %s", session_dir, exc)
                continue

            # Best-effort git worktree prune on whatever is one level above.
            # In a typical single-project layout the slug_dir's parent is the
            # forge-worktrees base, not the project tree, so this will mostly
            # be a no-op.  The real project tree prune is up to the operator
            # or a future version that can read the gitdir link.
            # NOTE: run against slug_dir (the per-project slug folder) in case
            # the user happens to have placed worktrees adjacent to a git repo.
            _rc_unused, _so, _se = await _git(
                "-C",
                str(slug_dir),
                "worktree",
                "prune",
                check=False,
            )

    return pruned


# ---------------------------------------------------------------------------
# Per-session async lock registry
# ---------------------------------------------------------------------------

# Single-process asyncio: dict mutation is safe without a separate lock
# because the event loop is single-threaded.  A new Lock is created atomically
# from the caller's perspective -- no two coroutines can see a half-initialised
# entry because coroutine switches only happen at await points, and the dict
# read+write below has no await between them.
_session_locks: dict[int, asyncio.Lock] = {}


def session_lock(session_id: int) -> asyncio.Lock:
    """Return (creating if absent) the per-session asyncio.Lock.

    Callers should use ``async with session_lock(session_id):`` around any
    worktree mutation or Forge turn execution to serialize concurrent turns on
    the same worktree.

    Thread-safety note: this is safe in a single-process asyncio app.  If
    uvicorn is ever run with multiple workers the lock registry becomes
    per-process and will not protect cross-worker concurrency.  That scenario
    requires an external lock (e.g. a Postgres advisory lock), which is out of
    scope for v1.
    """
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]
