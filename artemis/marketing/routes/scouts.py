"""Scouts router — /api/scouts.

Endpoints:
  GET  /packages        — list declarative scout package definitions
  GET  /runs            — paginated run history
  GET  /runs/{id}       — single run detail
  POST /runs            — manual harness: dry-run or commit a batch of findings
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.repository import (
    create_scout_run,
    create_signal,
    find_signal_by_dedupe_key,
    get_scout_run,
    list_scout_runs,
    update_scout_run,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

# ── Package catalogue ─────────────────────────────────────────────────────────

_PACKAGES_PATH = (
    Path(__file__).parent.parent.parent.parent / "public" / "config" / "scout-packages.json"
)
# Fallback: look in a sibling config dir at repo root level
_ALT_PACKAGES_PATH = Path(__file__).resolve()
for _candidate in [
    Path(__file__).parent.parent.parent.parent / "config" / "scout-packages.json",
    Path(__file__).parent.parent.parent.parent.parent
    / "claudeck-artemis"
    / "config"
    / "scout-packages.json",
]:
    if _candidate.exists():
        _PACKAGES_PATH = _candidate
        break

_cached_packages: list[dict[str, Any]] | None = None


def _load_packages() -> list[dict[str, Any]]:
    global _cached_packages
    if _cached_packages is None:
        _cached_packages = json.loads(_PACKAGES_PATH.read_text()) if _PACKAGES_PATH.exists() else []
    return _cached_packages


def _get_package(scout_type: str) -> dict[str, Any] | None:
    return next((p for p in _load_packages() if p.get("scoutType") == scout_type), None)


def _make_run_id(scout_type: str) -> str:
    date = datetime.now(UTC).strftime("%Y%m%d")
    suffix = str(uuid4())[:8]
    return f"scout_run_{date}_{scout_type}_{suffix}"


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/scouts", tags=["scouts"], dependencies=[Depends(require_token)])


@router.get("/packages")
async def list_packages() -> dict[str, Any]:
    """Return all declarative scout package definitions."""
    return {"packages": _load_packages()}


@router.get("/runs")
async def list_runs(
    scout_type: str | None = Query(default=None, alias="scoutType"),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return paginated scout run history."""
    runs = await list_scout_runs(session, scout_type=scout_type, status=status, limit=limit)
    return {
        "runs": [_serialize_run(r) for r in runs],
        "total": len(runs),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:  # noqa: B008
    """Return a single scout run by ID."""
    try:
        run = await get_scout_run(session, run_id)
    except ValueError:
        raise not_found("Scout run not found", "scout_run_not_found")  # noqa: B904
    return {"run": _serialize_run(run)}


@router.post("/runs", status_code=201)
async def create_run(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Manual harness entry point.

    Body: { scoutType, dryRun?, findings: [...] }

    dryRun=true  — validate + dedupe-check only; no signals created.
    dryRun=false — validate + dedupe + createSignal for each valid finding.

    discoveredBy is always overridden to scoutType (safety invariant).
    """
    scout_type: str | None = body.get("scoutType")
    dry_run: bool = bool(body.get("dryRun", False))
    findings: Any = body.get("findings")

    if not scout_type:
        raise bad_request("scoutType is required")  # noqa: B904

    pkg = _get_package(scout_type)
    if pkg is None:
        known = ", ".join(p.get("scoutType", "") for p in _load_packages())
        raise bad_request(f"Unknown scoutType: {scout_type}. Must be one of: {known}")  # noqa: B904

    if not isinstance(findings, list) or len(findings) == 0:
        raise bad_request("findings must be a non-empty array")  # noqa: B904
    if len(findings) > 100:
        raise bad_request("findings must not exceed 100 items per run")  # noqa: B904

    run_id = _make_run_id(scout_type)

    # Create run record in pending state immediately
    await create_scout_run(session, run_id=run_id, scout_type=scout_type, status="pending")
    await session.commit()

    # ── Per-finding processing ────────────────────────────────────────────────

    allowed_source_types: list[str] = pkg.get("allowedSourceTypes", [])
    validation_errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for i, finding in enumerate(findings):
        source_type = finding.get("sourceType") or finding.get("source_type")

        # allowedSourceTypes guard
        if source_type and allowed_source_types and source_type not in allowed_source_types:
            validation_errors.append(
                {
                    "index": i,
                    "errors": [
                        f'sourceType "{source_type}" is not allowed for {scout_type}. '
                        f"Allowed: {', '.join(allowed_source_types)}"
                    ],
                }
            )
            continue

        # Override discoveredBy unconditionally
        payload = {**finding, "discovered_by": scout_type, "discoveredBy": scout_type}

        # Basic required field check (mirrors normalizeIntakePayload)
        errs = _validate_finding(payload)
        if errs:
            validation_errors.append({"index": i, "errors": errs})
            continue

        # Dedupe check
        async with session.begin_nested():
            dup = await find_signal_by_dedupe_key(
                session,
                source_url=payload.get("sourceUrl") or payload.get("source_url") or "",
                headline=payload.get("headline") or "",
            )
        if dup is not None:
            duplicates.append(
                {
                    "index": i,
                    "existing": {
                        "id": dup.id,
                        "signalStatus": dup.signal_status,
                        "headline": dup.headline,
                        "createdAt": dup.created_at.isoformat(),
                    },
                }
            )
            continue

        normalized.append({"index": i, "payload": payload})

    # ── dryRun path ───────────────────────────────────────────────────────────

    if dry_run:
        summary = {
            "valid": len(normalized),
            "invalid": len(validation_errors),
            "duplicateCount": len(duplicates),
            "validationErrors": validation_errors,
            "duplicates": duplicates,
        }
        await update_scout_run(
            session,
            run_id,
            status="dry_run_passed",
            dry_run_summary=summary,
            created_signal_ids=[],
            errors=[{"index": e["index"], "errors": e["errors"]} for e in validation_errors],
        )
        await session.commit()
        return {
            "runId": run_id,
            "status": "dry_run_passed",
            "dryRun": True,
            "inputCount": len(findings),
            "valid": len(normalized),
            "invalid": len(validation_errors),
            "duplicates": len(duplicates),
            "validationErrors": validation_errors,
        }

    # ── commit path ───────────────────────────────────────────────────────────

    created_signal_ids: list[Any] = []
    commit_errors: list[dict[str, Any]] = []

    for item in normalized:
        try:
            payload = item["payload"]
            signal = await create_signal(
                session,
                headline=payload.get("headline", ""),
                campaign_family=payload.get("campaignFamily") or payload.get("campaign_family", ""),
                source_type=payload.get("sourceType") or payload.get("source_type", "manual"),
                source_url=payload.get("sourceUrl") or payload.get("source_url"),
                source_id=payload.get("sourceId") or payload.get("source_id"),
                summary=payload.get("summary", ""),
                urgency_tier=payload.get("urgencyTier") or payload.get("urgency_tier", "standard"),
                discovered_by=scout_type,
                district_id=payload.get("districtId") or payload.get("district_id"),
                state=payload.get("state"),
                reason_codes=payload.get("reasonCodes") or payload.get("reason_codes") or [],
                provenance=payload.get("provenance"),
            )
            await session.commit()
            created_signal_ids.append(signal.id)
        except Exception as exc:
            commit_errors.append({"index": item["index"], "error": str(exc)})

    all_errors = [
        *[{"index": e["index"], "errors": e["errors"]} for e in validation_errors],
        *commit_errors,
    ]

    final_status = "failed" if commit_errors and not created_signal_ids else "committed"

    await update_scout_run(
        session,
        run_id,
        status=final_status,
        dry_run_summary=None,
        created_signal_ids=[str(s) for s in created_signal_ids],
        errors=all_errors,
    )
    await session.commit()

    return {
        "runId": run_id,
        "status": final_status,
        "dryRun": False,
        "inputCount": len(findings),
        "createdCount": len(created_signal_ids),
        "skippedCount": len(validation_errors) + len(duplicates),
        "createdSignalIds": created_signal_ids,
        "errors": all_errors,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _validate_finding(payload: dict[str, Any]) -> list[str]:
    """Minimal required-field validation matching the Node normalizeIntakePayload contract."""
    errors: list[str] = []
    headline = payload.get("headline") or payload.get("headline", "")
    campaign_family = payload.get("campaignFamily") or payload.get("campaign_family", "")
    if not str(headline).strip():
        errors.append("headline is required")
    if not str(campaign_family).strip():
        errors.append("campaignFamily is required")
    return errors


def _serialize_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "scoutType": run.scout_type,
        "status": run.status,
        "dryRunSummary": run.dry_run_summary,
        "createdSignalIds": run.created_signal_ids or [],
        "errors": run.errors or [],
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
    }
