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

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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
from artemis.marketing.scout_intake import VALID_CAMPAIGN_FAMILIES

KNOWN_SCOUT_SLUGS: tuple[str, ...] = (
    "board_minutes",
    "federal_funding",
    "leadership_transition",
    "legislative",
    "linkedin_observer",
    "procurement",
    "regional_news",
    "starbridge_researcher",
    "state_doe",
)

VALID_URGENCIES: frozenset[str] = frozenset({"hot", "standard", "low", "enrichment"})
SPEC_CAMPAIGN_FAMILIES: frozenset[str] = frozenset(
    {
        "OBC",
        "Dyslexia / structured literacy",
        "Biliteracy / DLL",
        "High-impact tutoring (HIT)",
        "General growth",
    }
)
VALID_PLAYBOOK_CAMPAIGN_FAMILIES: frozenset[str] = VALID_CAMPAIGN_FAMILIES | SPEC_CAMPAIGN_FAMILIES

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


class ReasonCodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str = Field(min_length=1, max_length=120, pattern=r"^[A-Z0-9]+(?:_[A-Z0-9]+)*$")
    domain: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    what_scout_looks_for: str | None = Field(
        default=None, max_length=2000, alias="whatScoutLooksFor"
    )
    default_urgency: str | None = Field(default=None, max_length=120, alias="defaultUrgency")
    primary_scouts: list[str] = Field(default_factory=list, alias="primaryScouts")
    campaign_families: list[str] = Field(default_factory=list, alias="campaignFamilies")
    is_active: bool = Field(default=True, alias="isActive")


class ReasonCodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    description: str | None = Field(default=None, max_length=2000)
    what_scout_looks_for: str | None = Field(
        default=None, max_length=2000, alias="whatScoutLooksFor"
    )
    default_urgency: str | None = Field(default=None, max_length=120, alias="defaultUrgency")
    primary_scouts: list[str] | None = Field(default=None, alias="primaryScouts")
    campaign_families: list[str] | None = Field(default=None, alias="campaignFamilies")
    is_active: bool | None = Field(default=None, alias="isActive")


@router.get("/reason-codes")
async def list_reason_codes(
    include_inactive: bool = Query(default=False),
    include_retired: bool = Query(default=False, alias="includeRetired"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return reason codes sorted by domain ASC, code ASC.

    By default returns only active codes; pass ?include_inactive=true to see all.
    """
    stmt = select(SignalReasonCode)
    if not include_inactive and not include_retired:
        stmt = stmt.where(SignalReasonCode.is_active.is_(True))
    stmt = stmt.order_by(SignalReasonCode.domain.asc(), SignalReasonCode.code.asc())
    result = await session.execute(stmt)
    return [_serialize_reason_code(rc) for rc in result.scalars().all()]


@router.get("/reason-codes/markdown-export")
async def export_reason_codes_markdown(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    stmt = (
        select(SignalReasonCode)
        .where(SignalReasonCode.is_active.is_(True))
        .order_by(SignalReasonCode.domain.asc(), SignalReasonCode.code.asc())
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    markdown = _reason_codes_markdown(rows)
    return Response(content=markdown, media_type="text/markdown; charset=utf-8")


@router.post("/reason-codes", status_code=201)
async def create_reason_code(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new reason code. Returns 409 if code already exists."""
    payload = _parse_reason_code_create(body)
    _validate_playbook_arrays(payload.primary_scouts, payload.campaign_families)

    existing = await session.get(SignalReasonCode, payload.code)
    if existing is not None:
        raise conflict("Reason code already exists", "reason_code_conflict")  # noqa: B904

    rc = SignalReasonCode(
        code=payload.code,
        domain=payload.domain,
        description=_opt_str(payload.description),
        what_scout_looks_for=_opt_str(payload.what_scout_looks_for),
        default_urgency=_clean_urgency(payload.default_urgency),
        primary_scouts=_dedupe(payload.primary_scouts),
        campaign_families=_dedupe(payload.campaign_families),
        is_active=payload.is_active,
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

    payload = _parse_reason_code_patch(body)
    _validate_playbook_arrays(payload.primary_scouts, payload.campaign_families)

    rc = await session.get(SignalReasonCode, code)
    if rc is None:
        raise not_found("Reason code not found", "reason_code_not_found")  # noqa: B904

    updates = payload.model_dump(exclude_unset=True, by_alias=False)
    if "default_urgency" in updates:
        updates["default_urgency"] = _clean_urgency(updates["default_urgency"])
    if "description" in updates:
        updates["description"] = _opt_str(updates["description"])
    if "what_scout_looks_for" in updates:
        updates["what_scout_looks_for"] = _opt_str(updates["what_scout_looks_for"])
    if "primary_scouts" in updates:
        updates["primary_scouts"] = _dedupe(updates["primary_scouts"] or [])
    if "campaign_families" in updates:
        updates["campaign_families"] = _dedupe(updates["campaign_families"] or [])
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


def _parse_reason_code_create(body: dict[str, Any]) -> ReasonCodeCreate:
    try:
        return ReasonCodeCreate.model_validate(body)
    except ValidationError as exc:
        raise bad_request(str(exc), "reason_code_validation_failed") from exc


def _parse_reason_code_patch(body: dict[str, Any]) -> ReasonCodePatch:
    try:
        return ReasonCodePatch.model_validate(body)
    except ValidationError as exc:
        raise bad_request(str(exc), "reason_code_validation_failed") from exc


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = _opt_str(value)
        if clean and clean not in result:
            result.append(clean)
    return result


def _clean_urgency(value: str | None) -> str | None:
    clean = _opt_str(value)
    if clean is None:
        return None
    if clean not in VALID_URGENCIES:
        raise bad_request(
            f"defaultUrgency must be one of: {', '.join(sorted(VALID_URGENCIES))}",
            "reason_code_invalid_urgency",
        )
    return clean


def _validate_playbook_arrays(
    primary_scouts: list[str] | None,
    campaign_families: list[str] | None,
) -> None:
    bad_scouts = sorted(set(primary_scouts or []) - set(KNOWN_SCOUT_SLUGS))
    if bad_scouts:
        raise bad_request(
            "primaryScouts contains unknown slug(s): "
            f"{', '.join(bad_scouts)}. Valid slugs: {', '.join(KNOWN_SCOUT_SLUGS)}",
            "reason_code_invalid_primary_scouts",
        )
    bad_families = sorted(set(campaign_families or []) - VALID_PLAYBOOK_CAMPAIGN_FAMILIES)
    if bad_families:
        raise bad_request(
            "campaignFamilies contains unknown value(s): "
            f"{', '.join(bad_families)}. Valid families: "
            f"{', '.join(sorted(VALID_PLAYBOOK_CAMPAIGN_FAMILIES))}",
            "reason_code_invalid_campaign_families",
        )


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
        "primaryScouts": rc.primary_scouts or [],
        "campaignFamilies": rc.campaign_families or [],
        "isActive": rc.is_active,
        "createdAt": rc.created_at.isoformat(),
        "updatedAt": rc.updated_at.isoformat(),
    }


def _reason_codes_markdown(rows: Sequence[SignalReasonCode]) -> str:
    lines = [
        "# Signal Playbook — Generated Reason Code Snapshot",
        "",
        "_Generated from `signal_reason_codes`. The table is canonical; this markdown is export-only._",
        "",
    ]
    for rc in rows:
        lines.extend(
            [
                f"## {rc.code}",
                "",
                f"- Domain: {rc.domain}",
                f"- Trigger: {rc.description or ''}",
                f"- Scout watches: {rc.what_scout_looks_for or ''}",
                f"- Default urgency: {rc.default_urgency or ''}",
                f"- Primary scouts: {', '.join(rc.primary_scouts or []) or '—'}",
                f"- Campaign families: {', '.join(rc.campaign_families or []) or '—'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


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
