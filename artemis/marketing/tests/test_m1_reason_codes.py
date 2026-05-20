"""M1 — Reason-code registry tests.

Covers:
  (a) GET /api/signal-criteria/reason-codes returns 17 seeded codes sorted by domain/code
  (b) POST /api/signal-criteria/reason-codes creates a new code that appears in GET
  (c) PATCH is_active=false hides code from default GET + intake FK rejects it
  (d) FK validation rejects unknown code with proper 400 body
  (e) seed_reason_codes() idempotent on second call
  (f) PATCH attempt to mutate code → 400
  (g) PATCH attempt to mutate domain → 400
  (h) Model round-trip: insert + read back
  (i) Trigger blocks direct DELETE
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import SignalReasonCode
from artemis.marketing.seeds.reason_codes import JOSH_SPEC_V1, seed_reason_codes

pytestmark = pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_AUTH = {"Authorization": "Bearer test-token"}

_VALID_INTAKE = {
    "sourceType": "manual",
    "headline": "Test headline for FK validation",
    "campaignFamily": "obc",
    "reasonCodes": [{"code": "POLICY_LIT_MANDATE", "evidence_quote": "test"}],
}


async def _seed(session: AsyncSession) -> dict[str, int]:
    return await seed_reason_codes(session)


# ─────────────────────────────────────────────────────────────────────────────
# (a) GET returns 17 seeded codes, sorted domain ASC / code ASC
# ─────────────────────────────────────────────────────────────────────────────


async def test_get_reason_codes_returns_17_sorted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.get("/api/signal-criteria/reason-codes", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 17

    # Verify sort: domain ASC, code ASC
    domains_codes = [(r["domain"], r["code"]) for r in data]
    assert domains_codes == sorted(domains_codes)

    # All 17 codes present
    returned_codes = {r["code"] for r in data}
    expected_codes = {row["code"] for row in JOSH_SPEC_V1}
    assert returned_codes == expected_codes


# ─────────────────────────────────────────────────────────────────────────────
# (b) POST new code roundtrips
# ─────────────────────────────────────────────────────────────────────────────


async def test_post_reason_code_roundtrip(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/api/signal-criteria/reason-codes",
        json={
            "code": "TEST_NEW_CODE",
            "domain": "POLICY",
            "description": "Test description",
            "default_urgency": "standard",
        },
        headers=_AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "TEST_NEW_CODE"
    assert body["domain"] == "POLICY"
    assert body["isActive"] is True

    # Appears in GET
    list_resp = await client.get("/api/signal-criteria/reason-codes", headers=_AUTH)
    codes = {r["code"] for r in list_resp.json()}
    assert "TEST_NEW_CODE" in codes


# ─────────────────────────────────────────────────────────────────────────────
# (c) PATCH is_active=false hides code + intake rejects it
# ─────────────────────────────────────────────────────────────────────────────


async def test_patch_is_active_false_hides_and_rejects_intake(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)

    # Deactivate POLICY_LIT_MANDATE
    patch_resp = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_LIT_MANDATE",
        json={"is_active": False},
        headers=_AUTH,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["isActive"] is False

    # Default GET should not include deactivated code
    list_resp = await client.get("/api/signal-criteria/reason-codes", headers=_AUTH)
    active_codes = {r["code"] for r in list_resp.json()}
    assert "POLICY_LIT_MANDATE" not in active_codes

    # include_inactive=true should include it
    all_resp = await client.get(
        "/api/signal-criteria/reason-codes?include_inactive=true", headers=_AUTH
    )
    all_codes = {r["code"] for r in all_resp.json()}
    assert "POLICY_LIT_MANDATE" in all_codes

    # Intake with deactivated code → 400
    intake_resp = await client.post(
        "/api/signal-queue/intake",
        json=_VALID_INTAKE,
        headers=_AUTH,
    )
    assert intake_resp.status_code == 400
    body = intake_resp.json()
    assert body["error"] == "unknown reason codes"
    assert "POLICY_LIT_MANDATE" in body["codes"]


# ─────────────────────────────────────────────────────────────────────────────
# (d) FK validation rejects unknown code with proper 400 body
# ─────────────────────────────────────────────────────────────────────────────


async def test_intake_rejects_unknown_reason_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)

    resp = await client.post(
        "/api/signal-queue/intake",
        json={
            "sourceType": "manual",
            "headline": "Test with fake code",
            "campaignFamily": "obc",
            "reasonCodes": [{"code": "FAKE_CODE", "evidence_quote": "..."}],
        },
        headers=_AUTH,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "unknown reason codes"
    assert "FAKE_CODE" in body["codes"]


# ─────────────────────────────────────────────────────────────────────────────
# (e) Seed loader idempotent on second call
# ─────────────────────────────────────────────────────────────────────────────


async def test_seed_idempotent(db_session: AsyncSession) -> None:
    first = await _seed(db_session)
    assert first["inserted"] == 17
    assert first["skipped"] == 0

    second = await _seed(db_session)
    assert second["inserted"] == 0
    assert second["skipped"] == 17

    # Count rows
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM signal_reason_codes")
    )
    assert result.scalar_one() == 17


# ─────────────────────────────────────────────────────────────────────────────
# (f) PATCH attempt to mutate code → 400
# ─────────────────────────────────────────────────────────────────────────────


async def test_patch_code_immutable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_LIT_MANDATE",
        json={"code": "NEW_CODE"},
        headers=_AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "reason_code_immutable_field"


# ─────────────────────────────────────────────────────────────────────────────
# (g) PATCH attempt to mutate domain → 400
# ─────────────────────────────────────────────────────────────────────────────


async def test_patch_domain_immutable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.patch(
        "/api/signal-criteria/reason-codes/POLICY_LIT_MANDATE",
        json={"domain": "FUNDING"},
        headers=_AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "reason_code_immutable_field"


# ─────────────────────────────────────────────────────────────────────────────
# (h) Model round-trip: insert + read back via ORM
# ─────────────────────────────────────────────────────────────────────────────


async def test_model_round_trip(db_session: AsyncSession) -> None:
    rc = SignalReasonCode(
        code="TEST_ROUND_TRIP",
        domain="POLICY",
        description="A test code",
        what_scout_looks_for="Look for X",
        default_urgency="standard",
    )
    db_session.add(rc)
    await db_session.flush()
    await db_session.refresh(rc)

    fetched = await db_session.get(SignalReasonCode, "TEST_ROUND_TRIP")
    assert fetched is not None
    assert fetched.domain == "POLICY"
    assert fetched.description == "A test code"
    assert fetched.is_active is True


# ─────────────────────────────────────────────────────────────────────────────
# (i) Trigger blocks direct DELETE
# ─────────────────────────────────────────────────────────────────────────────


async def test_trigger_blocks_delete(db_session: AsyncSession) -> None:
    await _seed(db_session)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("DELETE FROM signal_reason_codes WHERE code = 'POLICY_LIT_MANDATE'")
        )
        await db_session.flush()
