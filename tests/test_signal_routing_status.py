"""Tests for signal routing_status classification (0092 feature).

Covers:
  (a) Write-tool routing classification (deterministic DB logic):
        - resolved_district_id set AND active contact → 'routable'
        - resolved_district_id set but NO active contact → 'unrouted_no_contact'
        - inactive contact → 'unrouted_no_contact'
        - resolved_district_id is None (state-level) → 'unrouted_no_contact'
        - migration server default is 'routable'
  (b) API routing_status filter:
        - GET /api/signal-queue/?routingStatus=unrouted_no_contact
        - GET /api/signal-queue/?routingStatus=routable
        - invalid value silently ignored
        - routingStatus field present in every serialized signal
  (c) Re-seed: seed_marketing_agents() UPSERTs — re-run updates system_prompt
      and the legislative scout prompt includes the routing instruction
  (d) Static source checks (no DB): models + JS

Engine strategy (mirrors test_0089_active_runs_stale_guard):
  - NullPool engine created at MODULE level to avoid per-fixture loop issues.
  - `artemis.db.engine` and `artemis.db.SessionLocal` are overridden at module
    import time so the app's `get_session` dependency uses the test engine.
  - Per-test fixture creates sessions from the shared module engine.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_signal_routing_status: db_url={_DB_URL!r} is not a test database."
    )

# ── Module-level engine (NullPool so each connection is fresh) ────────────────
_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
# Redirect the app's get_session dependency to use the test engine.
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(  # type: ignore[assignment]
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Session fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session from the module-level test engine.

    Truncates shared tables before yielding, cleans up after.
    """
    async with AsyncSession(_test_engine, expire_on_commit=False) as session:
        await session.execute(
            text(
                "TRUNCATE signal_queue, districts, district_contacts, agents "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
        yield session
        await session.execute(
            text(
                "TRUNCATE signal_queue, districts, district_contacts, agents "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# SQL helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_district(session: AsyncSession, name: str = "Test ISD") -> int:
    row = await session.execute(
        text(
            "INSERT INTO districts (name, state, enrollment, tier, supported, "
            "on_skip_list, classification_source) "
            "VALUES (:name, 'TX', 5000, 'D2', true, false, 'manual') "
            "RETURNING id"
        ),
        {"name": name},
    )
    district_id: int = row.scalar_one()
    await session.commit()
    return district_id


async def _insert_contact(session: AsyncSession, district_id: int, *, active: bool = True) -> int:
    row = await session.execute(
        text(
            "INSERT INTO district_contacts (district_id, name, email, source, active) "
            "VALUES (:district_id, 'Test Contact', 'test@example.com', 'manual', :active) "
            "RETURNING id"
        ),
        {"district_id": district_id, "active": active},
    )
    contact_id: int = row.scalar_one()
    await session.commit()
    return contact_id


async def _insert_signal(
    session: AsyncSession,
    *,
    resolved_district_id: int | None = None,
    routing_status: str = "routable",
    district_id: str | None = None,
) -> int:
    row = await session.execute(
        text(
            "INSERT INTO signal_queue "
            "(headline, campaign_family, urgency_tier, source_type, discovered_by, "
            "signal_status, routing_status, district_id, resolved_district_id) "
            "VALUES ('Test headline', 'obc', 'standard', 'manual', 'manual', "
            "'pending_qualification', :routing_status, :district_id, :resolved_district_id) "
            "RETURNING id"
        ),
        {
            "routing_status": routing_status,
            "district_id": district_id,
            "resolved_district_id": resolved_district_id,
        },
    )
    signal_id: int = row.scalar_one()
    await session.commit()
    return signal_id


# ─────────────────────────────────────────────────────────────────────────────
# Core classification logic (inlined from signal_queue.write tool)
# ─────────────────────────────────────────────────────────────────────────────


async def _classify_routing(session: AsyncSession, resolved_district_id: int | None) -> str:
    """Inline the routing classification logic from artemis/tools/signal_queue.py.

    Testing this separately avoids needing the full tool harness (LLM, reason codes,
    etc.) while still exercising the real DB query against the test database.
    """
    from sqlalchemy import select

    from artemis.marketing.models import DistrictContact

    routing_status = "unrouted_no_contact"
    if resolved_district_id is not None:
        stmt = (
            select(DistrictContact.id)
            .where(
                DistrictContact.district_id == resolved_district_id,
                DistrictContact.active.is_(True),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            routing_status = "routable"
    return routing_status


# ─────────────────────────────────────────────────────────────────────────────
# (a) Routing classification
# ─────────────────────────────────────────────────────────────────────────────


async def test_routing_routable_when_active_contact_exists(db_session: AsyncSession) -> None:
    """resolved_district_id set + active contact → routable."""
    district_id = await _insert_district(db_session, "Fort Bend ISD")
    await _insert_contact(db_session, district_id, active=True)

    status = await _classify_routing(db_session, district_id)
    assert status == "routable"


async def test_routing_unrouted_when_no_contact_row(db_session: AsyncSession) -> None:
    """resolved_district_id set, zero contact rows → unrouted_no_contact."""
    district_id = await _insert_district(db_session, "Grosse Pointe Schools")
    # deliberately no contact inserted

    status = await _classify_routing(db_session, district_id)
    assert status == "unrouted_no_contact"


async def test_routing_unrouted_when_contact_inactive(db_session: AsyncSession) -> None:
    """resolved_district_id set, only inactive contact → unrouted_no_contact."""
    district_id = await _insert_district(db_session, "Pinellas County")
    await _insert_contact(db_session, district_id, active=False)

    status = await _classify_routing(db_session, district_id)
    assert status == "unrouted_no_contact"


async def test_routing_unrouted_when_no_resolved_district(db_session: AsyncSession) -> None:
    """resolved_district_id is None (state-level / unresolved) → unrouted_no_contact."""
    status = await _classify_routing(db_session, resolved_district_id=None)
    assert status == "unrouted_no_contact"


async def test_routing_status_column_default_is_routable(db_session: AsyncSession) -> None:
    """Migration 0092 server default is 'routable' so pre-existing rows are not mislabelled."""
    result = await db_session.execute(
        text(
            "INSERT INTO signal_queue "
            "(headline, campaign_family, urgency_tier, source_type, discovered_by, signal_status) "
            "VALUES ('Default test', 'obc', 'standard', 'manual', 'manual', "
            "'pending_qualification') "
            "RETURNING routing_status"
        )
    )
    await db_session.commit()
    default_value = result.scalar_one()
    assert default_value == "routable", f"Expected server default 'routable', got {default_value!r}"


# ─────────────────────────────────────────────────────────────────────────────
# (b) API routing_status filter
# ─────────────────────────────────────────────────────────────────────────────


async def test_api_filter_unrouted_no_contact(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET ?routingStatus=unrouted_no_contact returns only unrouted signals."""
    district_id = await _insert_district(db_session)
    await _insert_contact(db_session, district_id, active=True)
    await _insert_signal(db_session, resolved_district_id=district_id, routing_status="routable")
    await _insert_signal(db_session, routing_status="unrouted_no_contact", district_id="STATE_TX")

    resp = await client.get(
        "/api/signal-queue/",
        params={"routingStatus": "unrouted_no_contact"},
        headers={"X-API-Token": "artemis-dev"},
    )
    assert resp.status_code == 200
    signals = resp.json()["signals"]
    assert len(signals) == 1, f"Expected 1 unrouted signal, got {len(signals)}"
    assert signals[0]["routingStatus"] == "unrouted_no_contact"


async def test_api_filter_routable(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET ?routingStatus=routable returns only routable signals."""
    district_id = await _insert_district(db_session)
    await _insert_contact(db_session, district_id, active=True)
    await _insert_signal(db_session, resolved_district_id=district_id, routing_status="routable")
    await _insert_signal(db_session, routing_status="unrouted_no_contact", district_id="STATE_IL")

    resp = await client.get(
        "/api/signal-queue/",
        params={"routingStatus": "routable"},
        headers={"X-API-Token": "artemis-dev"},
    )
    assert resp.status_code == 200
    signals = resp.json()["signals"]
    assert len(signals) == 1, f"Expected 1 routable signal, got {len(signals)}"
    assert signals[0]["routingStatus"] == "routable"


async def test_api_invalid_routing_status_ignored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Invalid routingStatus query param silently ignored → returns all signals."""
    await _insert_signal(db_session, routing_status="routable")
    await _insert_signal(db_session, routing_status="unrouted_no_contact")

    resp = await client.get(
        "/api/signal-queue/",
        params={"routingStatus": "totally_bogus"},
        headers={"X-API-Token": "artemis-dev"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


async def test_api_serialization_includes_routing_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Every signal payload carries routingStatus."""
    await _insert_signal(db_session, routing_status="unrouted_no_contact")

    resp = await client.get("/api/signal-queue/", headers={"X-API-Token": "artemis-dev"})
    assert resp.status_code == 200
    signals = resp.json()["signals"]
    assert len(signals) == 1
    assert "routingStatus" in signals[0], "routingStatus field missing from API response"
    assert signals[0]["routingStatus"] == "unrouted_no_contact"


# ─────────────────────────────────────────────────────────────────────────────
# (c) Seed upsert
# ─────────────────────────────────────────────────────────────────────────────


async def test_seed_marketing_agents_upserts_not_duplicates(db_session: AsyncSession) -> None:
    """seed_marketing_agents() is idempotent: second run updates, does not insert duplicates."""
    from artemis.marketing.seeds.marketing_agents import seed_marketing_agents

    result1 = await seed_marketing_agents(db_session)
    # Either all inserted (fresh DB) or all updated (already seeded) — either is fine
    assert result1["inserted"] + result1["updated"] > 0

    # Second run: 0 inserted, all updated
    result2 = await seed_marketing_agents(db_session)
    assert result2["inserted"] == 0, (
        f"Second seed should update not insert. Got inserted={result2['inserted']}"
    )
    assert result2["updated"] > 0, (
        f"Second seed should have updated rows. Got updated={result2['updated']}"
    )


async def test_seed_legislative_scout_prompt_has_routing_instruction(
    db_session: AsyncSession,
) -> None:
    """After seed, legislative scout system_prompt contains the ALWAYS WRITE routing rule."""
    from artemis.marketing.seeds.marketing_agents import seed_marketing_agents

    await seed_marketing_agents(db_session)

    row = await db_session.execute(
        text("SELECT system_prompt FROM agents WHERE agent_id = 'marketing.scout.legislative'")
    )
    prompt = row.scalar_one()
    assert prompt is not None and len(prompt) > 0
    assert "ALWAYS WRITE" in prompt, (
        f"Expected 'ALWAYS WRITE' routing note in system_prompt. Got: {prompt[:400]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (d) Static source checks (no DB)
# ─────────────────────────────────────────────────────────────────────────────


def test_canonical_routing_statuses_exported() -> None:
    from artemis.marketing.models import CANONICAL_ROUTING_STATUSES

    assert "routable" in CANONICAL_ROUTING_STATUSES
    assert "unrouted_no_contact" in CANONICAL_ROUTING_STATUSES


def test_signal_queue_orm_model_has_routing_status() -> None:
    from artemis.marketing.models import SignalQueue

    assert hasattr(SignalQueue, "routing_status")


def test_signal_tree_js_exports_routing_statuses_constant() -> None:
    src = (ROOT / "public/js/components/signal-tree.js").read_text()
    assert "SIGNAL_ROUTING_STATUSES" in src
    assert "unrouted_no_contact" in src


def test_signal_tree_js_normalizes_routing_status() -> None:
    src = (ROOT / "public/js/components/signal-tree.js").read_text()
    assert "routingStatus" in src


def test_signal_tree_js_filter_handles_routing_statuses_key() -> None:
    src = (ROOT / "public/js/components/signal-tree.js").read_text()
    assert "routingStatuses" in src
    assert "filters.routingStatuses" in src


def test_signal_tree_js_renders_unrouted_badge() -> None:
    src = (ROOT / "public/js/components/signal-tree.js").read_text()
    assert "mkt-signal-row-unrouted" in src


def test_signal_tree_js_renders_watch_list_filter_chip() -> None:
    src = (ROOT / "public/js/components/signal-tree.js").read_text()
    assert "Unrouted / Watch-list" in src


def test_marketing_os_js_has_routing_statuses_in_filter_state() -> None:
    src = (ROOT / "public/js/features/marketing-os.js").read_text()
    assert "routingStatuses" in src


def test_migration_0092_revision_chain() -> None:
    text_content = (ROOT / "alembic/versions/0092_signal_routing_status.py").read_text()
    assert 'revision: str = "0092"' in text_content
    assert 'down_revision: str | None = "0091"' in text_content
