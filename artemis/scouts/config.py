"""Scout worker configuration — loads from config/scouts.yaml + env overrides.

Env vars take precedence over YAML:
  ARTEMIS_API_URL        → api_url (default: http://localhost:8000)
  ARTEMIS_TOKEN          → api_token
  ARTEMIS_SCOUT_DRY_RUN  → set to "1" to force dry_run=True on all scouts
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from artemis.scouts.base import ScoutConfig

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "scouts.yaml"


@dataclass
class WorkerConfig:
    """Top-level worker process configuration."""

    api_url: str = "http://localhost:8000"
    api_token: str = ""
    dry_run: bool = False
    scouts: dict[str, ScoutConfig] = field(default_factory=dict)


def load_config(path: Path | None = None) -> WorkerConfig:
    """Load worker config from YAML + env overrides."""
    cfg_path = path or _CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open() as f:
            raw = yaml.safe_load(f) or {}

    api_url = os.environ.get("ARTEMIS_API_URL") or str(raw.get("api_url", "http://localhost:8000"))
    api_token = os.environ.get("ARTEMIS_TOKEN") or str(raw.get("api_token", ""))
    dry_run = os.environ.get("ARTEMIS_SCOUT_DRY_RUN") == "1" or bool(raw.get("dry_run", False))

    scout_cfgs: dict[str, ScoutConfig] = {}
    for scout_type, scout_raw in (raw.get("scouts") or {}).items():
        scout_cfgs[str(scout_type)] = ScoutConfig(
            api_url=api_url,
            api_token=api_token,
            dry_run=dry_run,
            interval_minutes=int(scout_raw.get("interval_minutes", 60)),
            enabled=bool(scout_raw.get("enabled", True)),
        )

    return WorkerConfig(
        api_url=api_url,
        api_token=api_token,
        dry_run=dry_run,
        scouts=scout_cfgs,
    )


def scout_config_for(worker_cfg: WorkerConfig, scout_type: str) -> ScoutConfig:
    """Return a ScoutConfig for a scout_type, using global defaults if not in YAML."""
    return worker_cfg.scouts.get(
        scout_type,
        ScoutConfig(
            api_url=worker_cfg.api_url,
            api_token=worker_cfg.api_token,
            dry_run=worker_cfg.dry_run,
        ),
    )
