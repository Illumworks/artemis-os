"""CI test: no direct .status = writes in artemis/marketing/ production code.

After M3a all status mutations must flow through transition().
This test greps the source tree and fails if any direct assignment is found
outside the state_machine.py internals and the approved exclusion list.
"""

from __future__ import annotations

import re
from pathlib import Path

# Files explicitly excluded from the grep check.
# Each entry must have an inline comment explaining why.
EXCLUDED_FILES = {
    "artemis/marketing/writing_studio/external.py",
    # ExternalDraft is an in-memory stub of an external service's
    # vocabulary, not a DB-backed state machine. The .status assignment
    # there mirrors the upstream service's payload shape, so it must
    # remain a bare string rather than our SignalState/etc. enum.
    "artemis/marketing/repository.py",
    # repository.py contains approval.status = decision which updates the
    # Approval model's status column — a separate lifecycle from the 5
    # campaign state machine columns. Approval status is not covered by
    # SignalState/BriefState/etc. enums.
    "artemis/marketing/routes/content_assets.py",
    # content_assets.py contains asset.status = body["status"] which updates
    # ContentAsset.status — also not covered by the campaign state machine.
    # ContentAsset status is a freeform content lifecycle column.
    "artemis/marketing/cross_reference.py",
    # M4 qualifier rule layer integration. Direct writes are intentional
    # temporary stubs (TODO(M3) comments inline) until M3 transition() is
    # guaranteed merged. Brief §6 explicitly permits this pattern. Removal
    # tracked in m3b-attribution-cleanup.
    "artemis/marketing/sends.py",
    # SEND2-B mark_send_sent writes send.status = "sent" directly.
    # campaign_sends.status is a queue lifecycle (queued|sent|failed|skipped)
    # separate from the 5 campaign state-machine columns — same category as
    # Approval.status and ContentAsset.status above. The deliverable side of
    # the send (queued_for_send → sent) still flows through transition().
}

# The state_machine.py internals legitimately use setattr() for the actual
# DB write — that's intentional, not a violation.
EXCLUDED_FILES.add("artemis/marketing/state_machine.py")

# Pattern for direct ``.status =`` writes (production code).
# We specifically look for assignment (=) not equality checks (==).
# Columns like ``signal_status`` are not part of the campaign state machine
# this guard is enforcing and would otherwise false-positive on SQL strings.
_ASSIGN_PATTERN = re.compile(r"\.status\s*=(?!=)")


def _repo_root() -> Path:
    """Return the repository root by walking up from this file."""
    here = Path(__file__).resolve().parent
    # tests/ is one level below the repo root
    return here.parent


def test_no_direct_status_writes() -> None:
    """Grep artemis/marketing/ for direct .status= assignments.

    Fails if any match is found outside the exclusion list.
    """
    repo_root = _repo_root()
    marketing_dir = repo_root / "artemis" / "marketing"
    assert marketing_dir.exists(), f"Marketing module not found at {marketing_dir}"

    violations: list[str] = []

    for py_file in sorted(marketing_dir.rglob("*.py")):
        # Build a repo-relative path for the exclusion check
        try:
            rel = py_file.relative_to(repo_root)
        except ValueError:
            continue

        rel_str = str(rel)
        if rel_str in EXCLUDED_FILES:
            continue

        # Skip __pycache__
        if "__pycache__" in rel_str:
            continue

        lines = py_file.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            # Skip comment lines
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _ASSIGN_PATTERN.search(line):
                violations.append(f"{rel_str}:{lineno}: {line.rstrip()}")

    assert not violations, (
        "Direct .status= writes found outside exclusion list.\n"
        "Replace them with transition() calls from state_machine.py.\n\n" + "\n".join(violations)
    )
