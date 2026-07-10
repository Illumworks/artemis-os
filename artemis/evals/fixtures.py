"""Fixture-case loader.

v1 grades curated transcripts checked in under ``artemis/evals/fixtures/``
(one ``<agent_id>.json`` per agent, a JSON list of EvalCase objects). Real
captured outputs plug in later by constructing ``EvalCase`` objects with
``source="captured"`` — the harness takes any case list.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from artemis.evals.schemas import EvalCase

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_CASE_LIST = TypeAdapter(list[EvalCase])


class FixtureError(ValueError):
    """Fixture file missing or malformed."""


def load_fixture_cases(agent_id: str, *, fixtures_dir: Path | None = None) -> list[EvalCase]:
    """Load the checked-in eval cases for one agent.

    Raises FixtureError when the file is absent, unparseable, or contains a
    case whose ``agent_id`` doesn't match the file it lives in (a mislabeled
    case would silently be graded against the wrong rubric otherwise).
    """
    normalized = agent_id.strip().lower()
    root = fixtures_dir or FIXTURES_DIR
    path = root / f"{normalized}.json"
    if not path.exists():
        raise FixtureError(f"no fixture file for agent {agent_id!r} at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases = _CASE_LIST.validate_python(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FixtureError(f"malformed fixture file {path}: {exc}") from exc

    for case in cases:
        if case.agent_id != normalized:
            raise FixtureError(
                f"case {case.case_id!r} in {path.name} has agent_id "
                f"{case.agent_id!r} (expected {normalized!r})"
            )
    if not cases:
        raise FixtureError(f"fixture file {path} contains no cases")

    case_ids = [c.case_id for c in cases]
    if len(set(case_ids)) != len(case_ids):
        raise FixtureError(f"duplicate case_ids in {path}: {case_ids}")
    return cases
