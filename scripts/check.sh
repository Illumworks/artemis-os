#!/usr/bin/env bash
# Local CI-equivalent: lint, type-check, test. Run before opening a review.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "→ ruff check"
uv run ruff check artemis tests

echo "→ ruff format --check"
uv run ruff format --check artemis tests

echo "→ mypy"
uv run mypy artemis

echo "→ pytest"
uv run pytest

echo "✓ all checks passed"
