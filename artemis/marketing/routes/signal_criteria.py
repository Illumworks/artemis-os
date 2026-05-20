"""Signal Criteria router — /api/signal-criteria.

Endpoints:
  GET  /rulesets              — list all rulesets (one per family)
  GET  /rulesets/{family}     — get single ruleset with active version details
  POST /rulesets              — create ruleset
  POST /rulesets/{id}/activate — activate a ruleset version
  GET  /reason-codes          — list reason codes (active only by default)
  POST /reason-codes          — create a new reason code
  PATCH /reason-codes/{code}  — update description/urgency/is_active (code+domain immutable)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import Ruleset, SignalReasonCode, TerritoryConfig
from artemis.marketing.repository import (
    activate_ruleset_version,
    get_territory_config,
    list_ruleset_versions,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/signal-criteria",
    tags=["signal-criteria"],
    dependencies=[Depends(require_token)],
)


# ── Rulesets ──────────────────────────────────────────────────────────────────


@router.get("/rulesets")
async def list_rulesets(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return all ruleset versions."""
    rulesets = await list_ruleset_versions(session)
    return [_serialize_ruleset(r) for r in rulesets]


@router.get("/rulesets/{family}")
async def get_ruleset(
    family: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return all versions for a family, with the active version flagged."""
    versions = await list_ruleset_versions(session, family=family)
    if not versions:
        raise not_found("Ruleset not found", "signal_criteria_ruleset_not_found")  # noqa: B904

    active = next((r for r in versions if r.state == "active"), None)
    result: dict[str, Any] = {
        "family": family,
        "versions": [_serialize_ruleset(r) for r in versions],
        "activeVersionDetails": _serialize_ruleset(active) if active else None,
    }
    return result


@router.post("/rulesets", status_code=201)
async def create_ruleset(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new ruleset version (in draft state)."""
    family = _opt_str(body.get("family"))
    version_tag = _opt_str(body.get("versionTag") or body.get("version_tag"))

    if not family:
        raise bad_request("family is required", "signal_criteria_ruleset_missing_family")  # noqa: B904
    if not version_tag:
        raise bad_request("versionTag is required", "signal_criteria_ruleset_missing_version_tag")  # noqa: B904

    # Check uniqueness
    existing = await session.execute(
        select(Ruleset).where(Ruleset.family == family, Ruleset.version_tag == version_tag).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise conflict(
            "Ruleset version already exists for this family",
            code="signal_criteria_ruleset_conflict",
        )

    ruleset = Ruleset(
        family=family,
        version_tag=version_tag,
        hard_filters=body.get("hardFilters") or body.get("hard_filters") or [],
        weighted_signals=body.get("weightedSignals") or body.get("weighted_signals") or [],
        qualitative_rubrics=body.get("qualitativeRubrics") or body.get("qualitative_rubrics") or [],
        state=body.get("state", "draft"),
    )
    session.add(ruleset)
    await session.flush()
    await session.refresh(ruleset)
    await session.commit()
    return _serialize_ruleset(ruleset)


@router.post("/rulesets/{ruleset_id}/activate")
async def activate_ruleset(
    ruleset_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Activate a ruleset version; archives any currently active version for the same family."""
    try:
        ruleset = await activate_ruleset_version(session, ruleset_id)
    except ValueError:
        raise not_found("Ruleset not found", "signal_criteria_ruleset_not_found")  # noqa: B904
    await session.commit()
    return _serialize_ruleset(ruleset)


# ── Territory Config ──────────────────────────────────────────────────────────


@router.get("/territory/{family}")
async def get_territory(
    family: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any] | None:
    """Return territory config for a campaign family (Python shape: arrays)."""
    config = await get_territory_config(session, family)
    if config is None:
        return {"family": family, "hotStates": [], "standardStates": [], "unlistedMultiplier": 0.85}
    return _serialize_territory(config)


@router.put("/territory/{family}")
async def upsert_territory(
    family: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Upsert territory config for a family (Python normalized shape)."""
    config = await get_territory_config(session, family)
    if config is None:
        config = TerritoryConfig(
            family=family,
            hot_states=body.get("hotStates") or body.get("hot_states") or [],
            standard_states=body.get("standardStates") or body.get("standard_states") or [],
            unlisted_multiplier=body.get("unlistedMultiplier", 0.85),
        )
        session.add(config)
    else:
        if "hotStates" in body or "hot_states" in body:
            config.hot_states = body.get("hotStates") or body.get("hot_states") or []
        if "standardStates" in body or "standard_states" in body:
            config.standard_states = body.get("standardStates") or body.get("standard_states") or []
        if "unlistedMultiplier" in body:
            config.unlisted_multiplier = body["unlistedMultiplier"]

    await session.flush()
    await session.refresh(config)
    await session.commit()
    return _serialize_territory(config)


# ── Reason Codes ─────────────────────────────────────────────────────────────


@router.get("/reason-codes")
async def list_reason_codes(
    include_inactive: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return reason codes sorted by domain ASC, code ASC.

    By default returns only active codes; pass ?include_inactive=true to see all.
    """
    stmt = select(SignalReasonCode)
    if not include_inactive:
        stmt = stmt.where(SignalReasonCode.is_active.is_(True))
    stmt = stmt.order_by(SignalReasonCode.domain.asc(), SignalReasonCode.code.asc())
    result = await session.execute(stmt)
    return [_serialize_reason_code(rc) for rc in result.scalars().all()]


@router.post("/reason-codes", status_code=201)
async def create_reason_code(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new reason code. Returns 409 if code already exists."""
    code = _opt_str(body.get("code"))
    domain = _opt_str(body.get("domain"))
    if not code:
        raise bad_request("code is required", "reason_code_missing_code")  # noqa: B904
    if not domain:
        raise bad_request("domain is required", "reason_code_missing_domain")  # noqa: B904

    existing = await session.get(SignalReasonCode, code)
    if existing is not None:
        raise conflict("Reason code already exists", "reason_code_conflict")  # noqa: B904

    rc = SignalReasonCode(
        code=code,
        domain=domain,
        description=_opt_str(body.get("description")),
        what_scout_looks_for=_opt_str(body.get("what_scout_looks_for")),
        default_urgency=_opt_str(body.get("default_urgency")),
        is_active=bool(body.get("is_active", True)),
    )
    session.add(rc)
    await session.flush()
    await session.refresh(rc)
    await session.commit()
    return _serialize_reason_code(rc)


@router.patch("/reason-codes/{code}")
async def patch_reason_code(
    code: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update mutable fields. code and domain are immutable — returns 400 if attempted."""
    if "code" in body:
        raise bad_request("code is immutable", "reason_code_immutable_field")  # noqa: B904
    if "domain" in body:
        raise bad_request("domain is immutable in v1", "reason_code_immutable_field")  # noqa: B904

    rc = await session.get(SignalReasonCode, code)
    if rc is None:
        raise not_found("Reason code not found", "reason_code_not_found")  # noqa: B904

    mutable = {"description", "what_scout_looks_for", "default_urgency", "is_active"}
    updates: dict[str, Any] = {k: v for k, v in body.items() if k in mutable}
    if updates:
        await session.execute(
            update(SignalReasonCode).where(SignalReasonCode.code == code).values(**updates)
        )
        await session.refresh(rc)
    await session.commit()
    await session.refresh(rc)
    return _serialize_reason_code(rc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _opt_str(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _serialize_ruleset(r: Ruleset) -> dict[str, Any]:
    return {
        "id": r.id,
        "family": r.family,
        "versionTag": r.version_tag,
        "hardFilters": r.hard_filters or [],
        "weightedSignals": r.weighted_signals or [],
        "qualitativeRubrics": r.qualitative_rubrics or [],
        "state": r.state,
        "createdAt": r.created_at.isoformat(),
    }


def _serialize_reason_code(rc: SignalReasonCode) -> dict[str, Any]:
    return {
        "code": rc.code,
        "domain": rc.domain,
        "description": rc.description,
        "whatScoutLooksFor": rc.what_scout_looks_for,
        "defaultUrgency": rc.default_urgency,
        "isActive": rc.is_active,
        "createdAt": rc.created_at.isoformat(),
        "updatedAt": rc.updated_at.isoformat(),
    }


def _serialize_territory(t: TerritoryConfig) -> dict[str, Any]:
    return {
        "id": t.id,
        "family": t.family,
        "hotStates": t.hot_states or [],
        "standardStates": t.standard_states or [],
        "unlistedMultiplier": t.unlisted_multiplier,
        "createdAt": t.created_at.isoformat(),
        "updatedAt": t.updated_at.isoformat(),
    }
