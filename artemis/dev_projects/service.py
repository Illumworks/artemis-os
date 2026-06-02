"""Service helpers for Dev Projects."""

from __future__ import annotations

from pathlib import Path

from artemis.dev_projects.schemas import FileSearchResult

_IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}


def list_project_files(base: str, query: str = "", *, limit: int = 40) -> list[FileSearchResult]:
    root = Path(base).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return []
    needle = query.lower().strip()
    results: list[FileSearchResult] = []
    for path in root.rglob("*"):
        if len(results) >= limit:
            break
        rel_parts = path.relative_to(root).parts
        if any(part in _IGNORED_DIRS for part in rel_parts):
            continue
        rel = "/".join(rel_parts)
        if needle and needle not in rel.lower():
            continue
        results.append(
            FileSearchResult(
                path=rel,
                name=path.name,
                type="dir" if path.is_dir() else "file",
            )
        )
    return results
