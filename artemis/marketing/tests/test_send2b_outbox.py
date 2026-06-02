"""SEND2-B — outbox + state machine + e2e approve→queued + no-contacts→skipped.

Coverage:
A. State machine: transition() allows approved→queued_for_send→sent;
   rejects sent→anything; rejects draft_ready→queued_for_send.
B. Recipient resolution (real DB): all 5 scope modes + fallback.
C. enqueue_send_for_deliverable: happy path, zero-contacts, wrong-state.
D. POST /api/marketing/sends/{id}/send: queued→sent, already-sent→409, skipped→409.
E. GET /api/marketing/sends?status=queued: shape + camelCase keys.
F. E2E approve hook: approve with contacts → deliverable=queued_for_send,
   send=queued, sends[] in response.
G. No-contacts approve hook: approve with no contacts → deliverable=approved,
   send=skipped, sends[0].status=skipped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.contacts import create_contact
from artemis.marketing.models import (
    Approval,
    CampaignCandidate,
    CampaignDeliverable,
    CampaignSend,
    District,
)
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.sends import (
    enqueue_send_for_deliverable,
    mark_send_sent,
    resolve_district_ids_for_candidate,
    resolve_recipients_for_candidate,
)
from artemis.marketing.state_machine import (
    DeliverableState,
    IllegalTransition,
    transition,
)
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.executor import PipelineExecutor

pytestmark = pytest.mark.asyncio

_PIPELINE_TRUNCATE = text("TRUNCATE pipeline_runs, pipelines RESTART IDENTITY CASCADE")


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _make_district(
    session: AsyncSession,
    *,
    name: str = "Test District",
    state: str = "TX",
    tier: str | None = "D1",
    supported: bool = True,
) -> District:
    d = District(name=name, state=state, tier=tier, supported=supported)
    session.add(d)
    await session.flush()
    return d


async def _make_candidate(
    session: AsyncSession,
    *,
    target_scope_json: dict[str, Any] | None = None,
    resolved_district_id: int | None = None,
) -> CampaignCandidate:
    signal = await create_signal(
        session,
        headline="Test signal",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Test",
        discovered_by="test",
        state="TX",
        reason_codes=[],
    )
    if resolved_district_id is not None:
        signal.resolved_district_id = resolved_district_id
        await session.flush()

    candidate = await create_campaign_candidate_from_signal(
        session, signal_id=signal.id, ruleset_version_tag="v1"
    )
    if target_scope_json is not None:
        candidate.target_scope_json = target_scope_json
        await session.flush()
    return candidate


async def _make_approved_deliverable(
    session: AsyncSession, candidate_id: int
) -> CampaignDeliverable:
    d = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id="stub-draft-1",
        campaign_id=str(candidate_id),
        status=DeliverableState.approved.value,
        deliverable_metadata={
            "externalTitle": "Test Deliverable",
            "deliverableTypeSlug": "outreach_email",
            "versions": [{"id": "v1", "version_number": 1, "content": "Draft content here."}],
        },
    )
    session.add(d)
    await session.flush()
    return d


def _node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": node_id,
        "config": config or {},
        "position": {"x": 0.0, "y": 0.0},
    }


def _edge(src: str, tgt: str) -> dict[str, Any]:
    return {
        "id": f"edge_{src}_{tgt}",
        "source_node_id": src,
        "target_node_id": tgt,
        "condition": None,
        "data_shape": None,
    }


async def _seed_gate2_run_with_scope(
    session: AsyncSession,
    target_scope_json: dict[str, Any] | None,
) -> tuple[CampaignCandidate, CampaignDeliverable, str, int]:
    """Seed a Gate-2 pipeline run with a candidate whose target_scope is provided."""
    await session.execute(_PIPELINE_TRUNCATE)
    await session.commit()

    signal = await create_signal(
        session,
        headline="Scoped district signal",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Test",
        discovered_by="test",
        state="TX",
        reason_codes=[],
    )
    candidate = await create_campaign_candidate_from_signal(
        session, signal_id=signal.id, ruleset_version_tag="v1"
    )
    candidate.name = "E2E Scoped Campaign"
    candidate.workspace_state = "content_in_review"
    if target_scope_json is not None:
        candidate.target_scope_json = target_scope_json
    await session.flush()

    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="stub-draft-e2e",
        campaign_id=str(candidate.id),
        status=DeliverableState.draft_ready.value,
        deliverable_metadata={
            "externalTitle": "E2E Test Draft",
            "deliverableTypeSlug": "outreach_email",
            "versions": [{"id": "v1", "version_number": 1, "content": "E2E content preview."}],
        },
    )
    session.add(deliverable)
    await session.flush()

    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES ('mock.post.gate.send2b', 'Mock Post Gate Send2B', '[]'::jsonb, "
            "'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        )
    )

    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Send2B Gate 2 Test",
        nodes=[
            _node("trigger", "trigger_manual"),
            _node(
                "gate_2_approval",
                "human_gate",
                {
                    "approval_kind": "content_draft",
                    "approvers": ["reviewer@example.com"],
                    "timeout_hours": 72,
                },
            ),
            _node("post_gate", "agent_invocation", {"agent_id": "mock.post.gate.send2b"}),
        ],
        edges=[
            _edge("trigger", "gate_2_approval"),
            _edge("gate_2_approval", "post_gate"),
        ],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=candidate.id,
    )
    await session.commit()

    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(session)

    approval_id = (
        await session.execute(
            text(
                "SELECT id FROM approvals WHERE kind = 'content_draft' AND subject_id = :sid"
            ),
            {"sid": f"{run.id}:gate_2_approval"},
        )
    ).scalar_one()
    await session.commit()
    return candidate, deliverable, run.id, int(approval_id)


# ── A. State machine transitions ──────────────────────────────────────────────


async def test_state_machine_allows_approved_to_queued_for_send(
    db_session: AsyncSession,
) -> None:
    candidate = await _make_candidate(db_session)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    result = await transition(db_session, "deliverable", deliverable.id, "queued_for_send")
    assert result.status == DeliverableState.queued_for_send.value


async def test_state_machine_allows_queued_for_send_to_sent(
    db_session: AsyncSession,
) -> None:
    candidate = await _make_candidate(db_session)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    await transition(db_session, "deliverable", deliverable.id, "queued_for_send")
    result = await transition(db_session, "deliverable", deliverable.id, "sent")
    assert result.status == DeliverableState.sent.value


async def test_state_machine_rejects_sent_to_anything(
    db_session: AsyncSession,
) -> None:
    candidate = await _make_candidate(db_session)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    await transition(db_session, "deliverable", deliverable.id, "queued_for_send")
    await transition(db_session, "deliverable", deliverable.id, "sent")

    with pytest.raises(IllegalTransition):
        await transition(db_session, "deliverable", deliverable.id, "approved")


async def test_state_machine_rejects_draft_ready_to_queued_for_send(
    db_session: AsyncSession,
) -> None:
    candidate = await _make_candidate(db_session)
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="d1",
        status=DeliverableState.draft_ready.value,
        deliverable_metadata={},
    )
    db_session.add(deliverable)
    await db_session.commit()

    with pytest.raises(IllegalTransition):
        await transition(db_session, "deliverable", deliverable.id, "queued_for_send")


# ── B. Recipient resolution ────────────────────────────────────────────────────


async def test_resolve_all_districts_mode(db_session: AsyncSession) -> None:
    d1 = await _make_district(db_session, name="Dist1", supported=True)
    d2 = await _make_district(db_session, name="Dist2", supported=True)
    d3 = await _make_district(db_session, name="Dist3", supported=True)
    d4 = await _make_district(db_session, name="Dist4_unsupported", supported=False)
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "all_districts"}
    )
    await db_session.commit()

    ids = await resolve_district_ids_for_candidate(db_session, candidate)
    assert d1.id in ids
    assert d2.id in ids
    assert d3.id in ids
    assert d4.id not in ids
    assert ids == sorted(ids)


async def test_resolve_states_mode(db_session: AsyncSession) -> None:
    tx = await _make_district(db_session, name="TX Dist", state="TX", supported=True)
    ca = await _make_district(db_session, name="CA Dist", state="CA", supported=True)
    _fl = await _make_district(db_session, name="FL Dist", state="FL", supported=True)
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["TX", "CA"]}
    )
    await db_session.commit()

    ids = await resolve_district_ids_for_candidate(db_session, candidate)
    assert tx.id in ids
    assert ca.id in ids
    assert _fl.id not in ids


async def test_resolve_district_tier_mode(db_session: AsyncSession) -> None:
    d1_tier = await _make_district(db_session, name="D1 Dist", tier="D1", supported=True)
    d2_tier = await _make_district(db_session, name="D2 Dist", tier="D2", supported=True)
    d3_tier = await _make_district(db_session, name="D3 Dist", tier="D3", supported=True)
    candidate = await _make_candidate(
        db_session,
        target_scope_json={"mode": "district_tier", "tiers": ["D1", "D2"]},
    )
    await db_session.commit()

    ids = await resolve_district_ids_for_candidate(db_session, candidate)
    assert d1_tier.id in ids
    assert d2_tier.id in ids
    assert d3_tier.id not in ids


async def test_resolve_named_districts_mode_no_supported_filter(
    db_session: AsyncSession,
) -> None:
    d_unsupported = await _make_district(db_session, name="Unsupported", supported=False)
    d_supported = await _make_district(db_session, name="Supported", supported=True)
    candidate = await _make_candidate(
        db_session,
        target_scope_json={
            "mode": "named_districts",
            "district_ids": [d_unsupported.id],
        },
    )
    await db_session.commit()

    ids = await resolve_district_ids_for_candidate(db_session, candidate)
    # named_districts mode: no supported filter — unsupported district is included
    assert d_unsupported.id in ids
    assert d_supported.id not in ids


async def test_resolve_fallback_to_resolved_district_id(db_session: AsyncSession) -> None:
    d = await _make_district(db_session, name="Fallback Dist")
    await db_session.commit()

    candidate = await _make_candidate(db_session, resolved_district_id=d.id)
    # No target_scope_json set
    candidate.target_scope_json = None
    await db_session.flush()
    await db_session.commit()

    ids = await resolve_district_ids_for_candidate(db_session, candidate)
    assert ids == [d.id]


async def test_resolve_recipients_returns_contact_snapshot(db_session: AsyncSession) -> None:
    d = await _make_district(db_session, name="Contact Dist", state="TX")
    await db_session.flush()
    await create_contact(db_session, district_id=d.id, name="Alice", email="alice@example.com", title="Super")
    await create_contact(db_session, district_id=d.id, name="Bob", email="bob@example.com")
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["TX"]}
    )
    await db_session.commit()

    district_ids, snapshot = await resolve_recipients_for_candidate(db_session, candidate)
    assert d.id in district_ids
    assert len(snapshot) == 2
    alice = next(r for r in snapshot if r["name"] == "Alice")
    assert alice["email"] == "alice@example.com"
    assert alice["title"] == "Super"
    assert alice["district_id"] == d.id
    assert "contact_id" in alice


# ── C. enqueue_send_for_deliverable ───────────────────────────────────────────


async def test_enqueue_happy_path(db_session: AsyncSession) -> None:
    d = await _make_district(db_session, name="Happy Dist", state="TX")
    await db_session.flush()
    await create_contact(db_session, district_id=d.id, name="Alice", email="alice@ex.com")
    await create_contact(db_session, district_id=d.id, name="Bob", email="bob@ex.com")

    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["TX"]}
    )
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable, actor="test_actor"
    )
    await db_session.flush()

    assert send.status == "queued"
    assert len(send.recipients) == 2
    assert send.skip_reason is None

    # Deliverable transitioned to queued_for_send
    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.queued_for_send.value


async def test_enqueue_zero_contacts_skipped(db_session: AsyncSession) -> None:
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["TX"]}
    )
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    # No contacts in TX at all
    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.flush()

    assert send.status == "skipped"
    assert send.skip_reason == "no_contacts_on_file"
    assert send.recipients == []

    # Deliverable stays approved
    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.approved.value


async def test_enqueue_wrong_state_raises(db_session: AsyncSession) -> None:
    candidate = await _make_candidate(db_session)
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="d1",
        status=DeliverableState.draft_ready.value,
        deliverable_metadata={},
    )
    db_session.add(deliverable)
    await db_session.commit()

    with pytest.raises(ValueError, match="must be in state 'approved'"):
        await enqueue_send_for_deliverable(
            db_session, candidate=candidate, deliverable=deliverable
        )


# ── D. POST /api/marketing/sends/{id}/send ────────────────────────────────────


async def test_post_send_transitions_queued_to_sent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    d = await _make_district(db_session, name="SendDist", state="TX")
    await db_session.flush()
    await create_contact(db_session, district_id=d.id, name="Carol", email="carol@ex.com")
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["TX"]}
    )
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.commit()

    response = await client.post(
        f"/api/marketing/sends/{send.id}/send", json={"actor": "jon@amiralearning.com"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["sentBy"] == "jon@amiralearning.com"
    assert body["sentAt"] is not None
    assert body["transport"] == "stub"

    # Deliverable should now be in 'sent' state
    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.sent.value


async def test_post_send_already_sent_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    d = await _make_district(db_session, name="409 Dist", state="CA")
    await db_session.flush()
    await create_contact(db_session, district_id=d.id, name="Dave", email="dave@ex.com")
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["CA"]}
    )
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.commit()

    # First send
    first = await client.post(f"/api/marketing/sends/{send.id}/send", json={"actor": "op"})
    assert first.status_code == 200

    # Second send
    second = await client.post(f"/api/marketing/sends/{send.id}/send", json={"actor": "op"})
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "send_not_queued"
    assert "sent" in body["error"]


async def test_post_send_skipped_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # No contacts so the send is skipped
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["WY"]}
    )
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.commit()

    assert send.status == "skipped"

    response = await client.post(f"/api/marketing/sends/{send.id}/send", json={"actor": "op"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "send_not_queued"


# ── E. GET /api/marketing/sends?status=queued ─────────────────────────────────


async def test_list_sends_returns_queued_rows(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    d = await _make_district(db_session, name="List Dist", state="NM")
    await db_session.flush()
    await create_contact(db_session, district_id=d.id, name="Eve", email="eve@ex.com")
    candidate = await _make_candidate(
        db_session, target_scope_json={"mode": "states", "states": ["NM"]}
    )
    candidate.name = "NM Campaign"
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.commit()

    response = await client.get("/api/marketing/sends?status=queued")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) >= 1

    row = next(r for r in body if r["id"] == send.id)
    # Verify camelCase shape
    assert "candidateId" in row
    assert "deliverableId" in row
    assert "recipientCount" in row
    assert "districtIds" in row
    assert "districtNames" in row
    assert "queuedAt" in row
    assert "sentAt" in row
    assert "sentBy" in row
    assert "skipReason" in row
    assert "draftPreview" in row

    assert row["status"] == "queued"
    assert row["candidateName"] == "NM Campaign"
    assert row["recipientCount"] == 1
    assert d.id in row["districtIds"]
    assert "List Dist" in row["districtNames"]


# ── F. E2E approve hook: with contacts → queued ───────────────────────────────


async def test_e2e_approve_with_contacts_creates_queued_send(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # Seed a TX district with 1 contact
    tx_district = await _make_district(db_session, name="TX E2E Dist", state="TX")
    await db_session.flush()
    await create_contact(db_session, district_id=tx_district.id, name="Frank", email="frank@e2e.com")
    await db_session.commit()

    # Seed gate-2 run with target_scope = {mode: "states", states: ["TX"]}
    candidate, deliverable, run_id, approval_id = await _seed_gate2_run_with_scope(
        db_session, target_scope_json={"mode": "states", "states": ["TX"]}
    )

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        response = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "approved"

    # sends[] key present in resume
    resume = payload["resume"]
    assert "sends" in resume
    assert len(resume["sends"]) >= 1
    send_info = resume["sends"][0]
    assert send_info["status"] == "queued"
    assert send_info["recipient_count"] == 1

    # Check deliverable state
    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.queued_for_send.value

    # campaign_sends row exists
    from sqlalchemy import select as _select

    result = await db_session.execute(
        _select(CampaignSend).where(CampaignSend.id == send_info["send_id"])
    )
    send_row = result.scalar_one()
    assert send_row.status == "queued"
    assert len(send_row.recipients) == 1


# ── G. E2E approve hook: no contacts → skipped ────────────────────────────────


async def test_e2e_approve_no_contacts_creates_skipped_send(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # No contacts in AK
    candidate, deliverable, run_id, approval_id = await _seed_gate2_run_with_scope(
        db_session, target_scope_json={"mode": "states", "states": ["AK"]}
    )

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        response = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "approved"

    resume = payload["resume"]
    assert "sends" in resume
    assert len(resume["sends"]) >= 1
    send_info = resume["sends"][0]
    assert send_info["status"] == "skipped"
    assert send_info["skip_reason"] == "no_contacts_on_file"

    # Deliverable stays approved (not queued_for_send)
    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.approved.value

    # campaign_sends row exists with status=skipped
    from sqlalchemy import select as _select

    result = await db_session.execute(
        _select(CampaignSend).where(CampaignSend.id == send_info["send_id"])
    )
    send_row = result.scalar_one()
    assert send_row.status == "skipped"
    assert send_row.skip_reason == "no_contacts_on_file"
