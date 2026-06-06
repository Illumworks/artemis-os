"""Routing control surface endpoints.

All endpoints are under ``/api/routing`` and require a bearer token when
``ARTEMIS_TOKEN`` is set (dev mode: no auth).

Endpoints:
  GET    /api/routing/health
  GET    /api/routing/features
  POST   /api/routing/features/{feature_tag}/override
  DELETE /api/routing/features/{feature_tag}/override
  GET    /api/routing/default-cascade
  POST   /api/routing/default-cascade
  GET    /api/routing/changes-log
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.providers.feature_catalog import (
    FEATURE_TAGS,
    FEATURES,
    KNOWN_PROVIDERS,
    get_default_cascade,
)
from artemis.providers.health import probe_all_providers
from artemis.providers.resolver import DEFAULT_CASCADE
from artemis.providers.routing_repository import (
    deactivate_routing_override,
    get_persisted_default_cascade,
    list_routing_changes,
    log_routing_change,
    persist_default_cascade,
    upsert_routing_override,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/routing",
    tags=["routing"],
    dependencies=[Depends(require_token)],  # noqa: B008
)


# ── Pydantic request/response models ──────────────────────────────────────────


class CascadeStep(BaseModel):
    provider: str
    model: str | None = None

    @field_validator("provider")
    @classmethod
    def provider_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_PROVIDERS:
            raise ValueError(f"Unknown provider {v!r}. Known: {sorted(KNOWN_PROVIDERS)}")
        return v


class OverrideRequest(BaseModel):
    cascade: list[CascadeStep]
    reason: str | None = None

    @field_validator("cascade")
    @classmethod
    def cascade_not_empty(cls, v: list[CascadeStep]) -> list[CascadeStep]:
        if not v:
            raise ValueError("cascade must not be empty")
        return v


class DefaultCascadeRequest(BaseModel):
    cascade: list[str]
    reason: str | None = None

    @field_validator("cascade")
    @classmethod
    def cascade_providers_known(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cascade must not be empty")
        for p in v:
            if p not in KNOWN_PROVIDERS:
                raise ValueError(f"Unknown provider {p!r}. Known: {sorted(KNOWN_PROVIDERS)}")
        return v


# ── Helper ────────────────────────────────────────────────────────────────────


async def _check_cascade_warnings(cascade: list[CascadeStep]) -> list[str]:
    """Return warning strings for any cascade steps whose provider is unavailable."""
    all_health = await probe_all_providers()
    health_map = {h["provider"]: h for h in all_health}
    warnings: list[str] = []
    for step in cascade:
        info = health_map.get(step.provider)
        if info and not info.get("available"):
            err = info.get("error") or "unavailable"
            warnings.append(
                f"{step.provider!r} is currently unavailable ({err}). "
                "The cascade will fall through to the next step."
            )
    return warnings


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/health")
async def get_provider_health() -> dict[str, Any]:
    """Return health status for all known providers."""
    records = await probe_all_providers()
    return {"providers": records}


@router.get("/features")
async def get_features(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return all known feature_tags with their effective cascade.

    For each feature:
    - ``is_override``: True if an active DB override exists
    - ``current_cascade``: the override cascade if one exists, else the catalog default
    - ``default_cascade``: always the catalog default
    - ``updated_at`` / ``updated_by``: populated only when an override exists
    """
    from sqlalchemy import select

    from artemis.providers.routing_models import FeatureRoutingOverride

    result = await session.execute(
        select(FeatureRoutingOverride).where(FeatureRoutingOverride.active.is_(True))
    )
    active_overrides = {row.feature_tag: row for row in result.scalars().all()}

    features: list[dict[str, Any]] = []
    for tag, meta in FEATURES.items():
        default_cascade = get_default_cascade(tag)
        override = active_overrides.get(tag)
        if override:
            current = list(override.cascade)
            is_override = True
            updated_at = override.updated_at.isoformat()
            updated_by = override.updated_by
        else:
            current = default_cascade
            is_override = False
            updated_at = None
            updated_by = None

        features.append(
            {
                "feature_tag": tag,
                "label": meta["label"],
                "description": meta["description"],
                "recommended_tier": meta["recommended_tier"],
                "current_cascade": current,
                "is_override": is_override,
                "default_cascade": default_cascade,
                "updated_at": updated_at,
                "updated_by": updated_by,
            }
        )
    return {"features": features}


@router.post("/features/{feature_tag}/override")
async def set_feature_override(
    feature_tag: str,
    body: OverrideRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Upsert a per-feature routing override.

    Validation:
    - ``feature_tag`` must be in the canonical list (422 otherwise)
    - ``cascade`` must not be empty (422)
    - All cascade providers must be known (422)
    - Unavailable providers generate warnings but do NOT block the save
    """
    if feature_tag not in FEATURE_TAGS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown feature_tag {feature_tag!r}",
                "code": "unknown_feature_tag",
                "known": sorted(FEATURE_TAGS),
            },
        )

    cascade_dicts = [step.model_dump(exclude_none=True) for step in body.cascade]
    warnings = await _check_cascade_warnings(body.cascade)

    # Read previous override for the audit log
    from sqlalchemy import select

    from artemis.providers.routing_models import FeatureRoutingOverride

    prev_result = await session.execute(
        select(FeatureRoutingOverride).where(FeatureRoutingOverride.feature_tag == feature_tag)
    )
    prev = prev_result.scalar_one_or_none()
    before = {"cascade": list(prev.cascade), "active": prev.active} if prev else None

    row = await upsert_routing_override(
        session,
        feature_tag=feature_tag,
        cascade=cascade_dicts,
    )
    await log_routing_change(
        session,
        scope="feature",
        scope_value=feature_tag,
        before=before,
        after={"cascade": cascade_dicts, "active": True},
        reason=body.reason,
    )
    await session.commit()

    return {
        "feature_tag": row.feature_tag,
        "cascade": row.cascade,
        "active": row.active,
        "updated_at": row.updated_at.isoformat(),
        "updated_by": row.updated_by,
        "warnings": warnings,
    }


@router.delete("/features/{feature_tag}/override")
async def delete_feature_override(
    feature_tag: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Deactivate a per-feature override (sets active=False, never hard-deletes).

    After this, ``resolve_adapter_async(feature_tag=...)`` falls back to the
    catalog default for this feature.
    """
    if feature_tag not in FEATURE_TAGS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown feature_tag {feature_tag!r}",
                "code": "unknown_feature_tag",
                "known": sorted(FEATURE_TAGS),
            },
        )

    from sqlalchemy import select

    from artemis.providers.routing_models import FeatureRoutingOverride

    prev_result = await session.execute(
        select(FeatureRoutingOverride).where(FeatureRoutingOverride.feature_tag == feature_tag)
    )
    prev = prev_result.scalar_one_or_none()

    if prev is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "No override found for this feature_tag", "code": "not_found"},
        )

    before = {"cascade": list(prev.cascade), "active": prev.active}

    await deactivate_routing_override(session, feature_tag=feature_tag)
    await log_routing_change(
        session,
        scope="feature",
        scope_value=feature_tag,
        before=before,
        after={"cascade": list(prev.cascade), "active": False},
        reason="reset to default",
    )
    await session.commit()

    return {
        "feature_tag": feature_tag,
        "active": False,
        "message": "Override deactivated. Feature will use catalog default cascade.",
    }


@router.get("/default-cascade")
async def get_default_cascade_route(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the current default cascade (persisted or code default)."""
    persisted = await get_persisted_default_cascade(session)
    cascade = persisted if persisted is not None else list(DEFAULT_CASCADE)
    return {
        "cascade": cascade,
        "source": "persisted" if persisted is not None else "code_default",
    }


@router.post("/default-cascade")
async def set_default_cascade(
    body: DefaultCascadeRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Persist a new default cascade order to app_settings.

    The new cascade takes effect on the NEXT call to ``resolve_adapter``
    (no in-flight changes, no restart required).
    """
    prev = await get_persisted_default_cascade(session)
    before: dict[str, object] | None = {"cascade": prev} if prev is not None else None

    await persist_default_cascade(session, cascade=body.cascade)
    await log_routing_change(
        session,
        scope="default_cascade",
        scope_value=None,
        before=before,
        after={"cascade": body.cascade},
        reason=body.reason,
    )
    await session.commit()

    return {
        "cascade": body.cascade,
        "message": "Default cascade updated. Takes effect on the next resolve_adapter call.",
    }


@router.get("/changes-log")
async def get_changes_log(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return paginated routing audit log, newest first."""
    rows = await list_routing_changes(session, limit=limit, offset=offset)
    return {"changes": rows, "limit": limit, "offset": offset}
