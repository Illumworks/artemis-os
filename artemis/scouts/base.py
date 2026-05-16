"""BaseScout — shared base class for all Artemis scout workers.

Each scout subclass:
1. Declares ``scout_type: ClassVar[str]`` matching a scoutType in scout-packages.json.
2. Overrides ``_gather_findings()`` to return raw finding dicts.
3. Calls ``run_once()`` to execute one collection + emit cycle.

BaseScout handles HTTP submission to ``POST /api/scouts/runs``, dry-run
switching, and error logging. It never raises — all exceptions are caught
and returned in ``ScoutRunResult.errors``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

_logger = logging.getLogger(__name__)


@dataclass
class ScoutConfig:
    """Runtime config for one scout instance."""

    api_url: str = "http://localhost:8000"
    api_token: str = ""
    dry_run: bool = False
    interval_minutes: int = 60
    enabled: bool = True


@dataclass
class ScoutRunResult:
    """Result of one run_once() cycle."""

    scout_type: str
    run_id: str | None = None
    status: str = "error"
    created_count: int = 0
    skipped_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class BaseScout(ABC):
    """Abstract base for all Artemis scout workers.

    Subclasses must declare ``scout_type: ClassVar[str]`` and implement
    ``_gather_findings()``.
    """

    scout_type: ClassVar[str]

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        _client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config: ScoutConfig = config or ScoutConfig()
        self._client: httpx.AsyncClient = _client or httpx.AsyncClient(timeout=30.0)

    @abstractmethod
    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect findings for this cycle.

        Return an empty list when nothing was found. Should not raise —
        callers catch exceptions and treat them as empty results.
        """

    async def run_once(self) -> ScoutRunResult:
        """Execute one collection + emit cycle. Never raises."""
        if not self.config.enabled:
            _logger.debug("Scout %s is disabled; skipping.", self.scout_type)
            return ScoutRunResult(scout_type=self.scout_type, status="skipped")

        try:
            findings = await self._gather_findings()
        except Exception:
            _logger.exception("Scout %s _gather_findings() raised.", self.scout_type)
            findings = []

        if not findings:
            _logger.debug("Scout %s: 0 findings this cycle.", self.scout_type)
            return ScoutRunResult(scout_type=self.scout_type, status="skipped")

        return await self.emit_signals(findings)

    async def emit_signals(self, findings: list[dict[str, Any]]) -> ScoutRunResult:
        """POST findings to /api/scouts/runs. Never raises."""
        url = f"{self.config.api_url.rstrip('/')}/api/scouts/runs"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"

        payload: dict[str, Any] = {
            "scoutType": self.scout_type,
            "dryRun": self.config.dry_run,
            "findings": findings,
        }

        try:
            resp = await self._client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            result = ScoutRunResult(
                scout_type=self.scout_type,
                run_id=data.get("runId"),
                status=str(data.get("status", "unknown")),
                created_count=int(data.get("createdCount", 0)),
                skipped_count=int(data.get("skippedCount", 0)),
                errors=list(data.get("errors") or []),
            )
            _logger.info(
                "Scout %s → run %s: status=%s created=%d skipped=%d",
                self.scout_type,
                result.run_id,
                result.status,
                result.created_count,
                result.skipped_count,
            )
            return result
        except httpx.HTTPStatusError as exc:
            _logger.warning(
                "Scout %s POST /api/scouts/runs → HTTP %s: %s",
                self.scout_type,
                exc.response.status_code,
                exc.response.text[:200],
            )
            return ScoutRunResult(
                scout_type=self.scout_type,
                status="error",
                errors=[{"error": str(exc), "status_code": exc.response.status_code}],
            )
        except Exception as exc:
            _logger.warning("Scout %s emit_signals failed: %s", self.scout_type, exc)
            return ScoutRunResult(
                scout_type=self.scout_type,
                status="error",
                errors=[{"error": str(exc)}],
            )
