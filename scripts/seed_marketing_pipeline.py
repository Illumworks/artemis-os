"""One-shot CLI for seeding the canonical Marketing Pipeline."""

from __future__ import annotations

import asyncio
import sys
from importlib import import_module
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if __name__ == "__main__":
    run_seed = import_module("artemis.pipelines.seeds.marketing_pipeline").run_seed
    asyncio.run(run_seed())
