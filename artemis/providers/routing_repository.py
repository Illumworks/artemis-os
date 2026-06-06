"""Repository helpers for routing override data access.

All DB operations needed by the resolver and the routing endpoints live here,
keeping the endpoint layer thin.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.providers.routing_models import AppSettings, FeatureRoutingOverride, RoutingChangeLog

logger = logging.getLogger(__name__)

# Key used in app_settings for the persisted default cascade
DEFAULT_CASCADE_SETTINGS_KEY = "default_cascade"


async def get_routing_override_for_feature(
    session: AsyncSession, feature_tag: str
) -> FeatureRoutingOverride | None:
    """Return the active override row for ``feature_tag``, or None."""
    result = await session.execute(
        select(FeatureRoutingOverride).where(
            FeatureRoutingOverride.feature_tag == feature_tag,
            FeatureRoutingOverride.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def upsert_routing_override(
    session: AsyncSession,
    *,
    feature_tag: str,
    cascade: list[dict[str, str]],
    updated_by: str = "operator",
) -> FeatureRoutingOverride:
    """Insert or update a feature routing override.

    Uses PostgreSQL ON CONFLICT DO UPDATE so there is always at most one row
    per feature_tag.  Sets ``active=True`` even if the row was previously
    deactivated (user is restoring it).
    """
    stmt = (
        pg_insert(FeatureRoutingOverride)
        .values(
            feature_tag=feature_tag,
            cascade=cascade,
            active=True,
            updated_by=updated_by,
        )
        .on_conflict_do_update(
            index_elements=["feature_tag"],
            set_={
                "cascade": cascade,
                "active": True,
                "updated_by": updated_by,
                "updated_at": __import__("sqlalchemy").func.now(),
            },
        )
        .returning(FeatureRoutingOverride)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.flush()
    return row


async def deactivate_routing_override(
    session: AsyncSession,
    *,
    feature_tag: str,
    updated_by: str = "operator",
) -> FeatureRoutingOverride | None:
    """Set active=False for the override row. Returns the row or None if it didn't exist."""
    row = await session.execute(
        select(FeatureRoutingOverride).where(
            FeatureRoutingOverride.feature_tag == feature_tag,
        )
    )
    override = row.scalar_one_or_none()
    if override is None:
        return None
    override.active = False
    override.updated_by = updated_by
    await session.flush()
    return override


async def log_routing_change(
    session: AsyncSession,
    *,
    scope: str,
    scope_value: str | None,
    before: dict[str, object] | None,
    after: dict[str, object],
    reason: str | None = None,
    changed_by: str = "operator",
) -> RoutingChangeLog:
    """Append a row to the audit log. Always succeeds (never deletes rows)."""
    entry = RoutingChangeLog(
        changed_by=changed_by,
        scope=scope,
        scope_value=scope_value,
        before=before,
        after=after,
        reason=reason,
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_persisted_default_cascade(session: AsyncSession) -> list[str] | None:
    """Read the default cascade from app_settings. Returns None if not persisted."""
    result = await session.execute(
        select(AppSettings).where(AppSettings.key == DEFAULT_CASCADE_SETTINGS_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    val = row.value
    if isinstance(val, dict) and "cascade" in val:
        cascade = val["cascade"]
        if isinstance(cascade, list):
            return [str(p) for p in cascade]
    return None


async def persist_default_cascade(
    session: AsyncSession,
    *,
    cascade: list[str],
) -> None:
    """Upsert the default cascade into app_settings."""
    stmt = (
        pg_insert(AppSettings)
        .values(key=DEFAULT_CASCADE_SETTINGS_KEY, value={"cascade": cascade})
        .on_conflict_do_update(
            index_elements=["key"],
            set_={
                "value": {"cascade": cascade},
                "updated_at": __import__("sqlalchemy").func.now(),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


async def list_routing_changes(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return audit rows newest-first."""
    result = await session.execute(
        select(RoutingChangeLog)
        .order_by(RoutingChangeLog.changed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "changed_at": r.changed_at.isoformat(),
            "changed_by": r.changed_by,
            "scope": r.scope,
            "scope_value": r.scope_value,
            "before": r.before,
            "after": r.after,
            "reason": r.reason,
        }
        for r in rows
    ]
