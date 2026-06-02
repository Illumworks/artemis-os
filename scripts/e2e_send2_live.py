"""Lead e2e for CMP-SEND-2 on the live app.

Seeds a fresh Gate-2 approval scenario for a candidate with target_scope=TX
states, drives the approval through the HTTP endpoints, and verifies the
campaign_sends row appears + the human-gated send completes.

Run with the lead-branch app on port 8765 (started separately).

Two phases:
  HAPPY  — candidate whose target_scope district has 2 active contacts
  SKIP   — candidate whose target_scope resolves to a district with 0 contacts
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as artemis_db
from artemis.marketing.models import Approval, CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.state_machine import DeliverableState
from artemis.pipelines import repository as pipeline_repo

BASE_URL = os.environ.get("LEAD_APP_URL", "http://localhost:8765")
HAPPY_TARGET_STATE = "TX"  # Has seeded contacts
SKIP_TARGET_STATE = "VT"   # No seeded contacts in test setup


async def _seed_gate2_scenario(
    session: AsyncSession,
    *,
    state: str,
    label: str,
) -> tuple[CampaignCandidate, CampaignDeliverable, str, int]:
    signal = await create_signal(
        session,
        headline=f"CMP-SEND-2 live e2e — {label}",
        campaign_family="outreach_email",
        source_type="manual",
        summary=f"Lead e2e signal for {label}",
        discovered_by="lead-cmp-send-2",
        state=state,
        reason_codes=[{"code": "literacy_shift"}],
    )
    candidate = await create_campaign_candidate_from_signal(
        session,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )
    candidate.name = f"CMP-SEND-2 e2e {label}"
    candidate.workspace_state = "content_in_review"
    candidate.target_scope_json = {
        "mode": "states",
        "states": [state],
        "tiers": None,
        "district_ids": None,
    }

    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id=f"e2e-{label}-draft",
        campaign_id=str(candidate.id),
        status=DeliverableState.draft_ready.value,
        deliverable_metadata={
            "externalTitle": f"Outreach Email — {label}",
            "deliverableTypeSlug": "outreach_email",
            "versions": [
                {
                    "id": "v1",
                    "version_number": 1,
                    "content": (
                        f"Hello — this is a CMP-SEND-2 live e2e draft for the {label} "
                        f"scenario. Resolved district recipients are populated from "
                        f"district_contacts via the new target_scope resolver. "
                        f"No actual email leaves the system — transport is stubbed."
                    ),
                }
            ],
        },
    )
    session.add(deliverable)
    await session.flush()

    # Ensure mock agent row exists (for the human-gate executor's downstream)
    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES ('mock.post.gate', 'Mock Post Gate', '[]'::jsonb, "
            "'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        )
    )

    pipeline = await pipeline_repo.create_pipeline(
        session,
        name=f"e2e Gate-2 — {label}",
        nodes=[
            {"id": "trigger", "type": "trigger_manual", "label": "trigger",
             "config": {}, "position": {"x": 0.0, "y": 0.0}},
            {
                "id": "gate_2_approval_drawer",
                "type": "human_gate",
                "label": "gate_2_approval_drawer",
                "config": {
                    "approval_kind": "content_draft",
                    "approvers": ["jon@amiralearning.com"],
                    "timeout_hours": 72,
                },
                "position": {"x": 0.0, "y": 0.0},
            },
            {"id": "after_gate", "type": "agent_invocation", "label": "after_gate",
             "config": {"agent_id": "mock.post.gate"}, "position": {"x": 0.0, "y": 0.0}},
        ],
        edges=[
            {"id": "e1", "source_node_id": "trigger",
             "target_node_id": "gate_2_approval_drawer",
             "condition": None, "data_shape": None},
            {"id": "e2", "source_node_id": "gate_2_approval_drawer",
             "target_node_id": "after_gate",
             "condition": None, "data_shape": None},
        ],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="lead-cmp-send-2",
        target_candidate_id=candidate.id,
    )

    # Insert the Gate-2 approval row directly (skip the full pipeline executor;
    # we just need the approval pending + the run linked to the candidate).
    approval_id_row = await session.execute(
        text(
            "INSERT INTO approvals (kind, subject_id, status, created_at, pipe4_context) "
            "VALUES ('content_draft', :sid, 'pending', now(), :ctx) RETURNING id"
        ),
        {
            "sid": f"{run.id}:gate_2_approval_drawer",
            "ctx": json.dumps(
                {"pipeline_run_id": run.id, "node_id": "gate_2_approval_drawer"}
            ),
        },
    )
    approval_id = int(approval_id_row.scalar_one())

    # Mark the gate node as awaiting_approval in node_states
    await session.execute(
        text(
            "UPDATE pipeline_runs SET node_states = CAST(:ns AS jsonb) WHERE id = :id"
        ),
        {
            "id": run.id,
            "ns": json.dumps(
                {
                    "gate_2_approval_drawer": {
                        "status": "suspended",
                        "approval_id": approval_id,
                        "started_at": datetime.now(UTC).isoformat(),
                    }
                }
            ),
        },
    )

    await session.commit()
    return candidate, deliverable, run.id, approval_id


async def _snapshot(session: AsyncSession, *, deliverable_id: int) -> dict[str, Any]:
    deliverable = await session.get(CampaignDeliverable, deliverable_id)
    assert deliverable is not None
    sends = (
        await session.execute(
            text(
                "SELECT id, status, recipients, skip_reason, sent_at, sent_by, "
                "transport, transport_log FROM campaign_sends "
                "WHERE deliverable_id = :did ORDER BY id"
            ),
            {"did": deliverable_id},
        )
    ).mappings().all()
    return {
        "deliverable_status": deliverable.status,
        "campaign_sends": [dict(r) for r in sends],
    }


async def _post_decide(approval_id: int) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        resp = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
        )
        return {"status_code": resp.status_code, "body": resp.json()}


async def _get_queued() -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15.0) as client:
        resp = await client.get("/api/marketing/sends?status=queued")
        return {"status_code": resp.status_code, "body": resp.json()}


async def _post_send(send_id: int) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15.0) as client:
        resp = await client.post(
            f"/api/marketing/sends/{send_id}/send",
            json={"actor": "jon@amiralearning.com"},
        )
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"status_code": resp.status_code, "body": body}


async def _post_send_again(send_id: int) -> dict[str, Any]:
    return await _post_send(send_id)


async def run_happy() -> dict[str, Any]:
    print("\n══════════════════════════════ HAPPY PATH ══════════════════════════════")
    async with artemis_db.SessionLocal() as session:
        candidate, deliverable, run_id, approval_id = await _seed_gate2_scenario(
            session, state=HAPPY_TARGET_STATE, label="happy-tx"
        )
        before = await _snapshot(session, deliverable_id=deliverable.id)
    print(f"[seed] candidate_id={candidate.id} deliverable_id={deliverable.id} "
          f"run_id={run_id} approval_id={approval_id}")
    print(f"[before] {json.dumps(before, default=str)}")

    decide = await _post_decide(approval_id)
    print(f"[POST /approvals/{approval_id}/decision] {decide['status_code']}")
    print(f"  resume.sends={decide['body'].get('resume', {}).get('sends')}")

    async with artemis_db.SessionLocal() as session:
        after_approve = await _snapshot(session, deliverable_id=deliverable.id)
    print(f"[after approve] {json.dumps(after_approve, default=str)}")

    queued = await _get_queued()
    sends_for_us = [s for s in queued["body"] if s.get("deliverableId") == deliverable.id]
    print(f"[GET /sends?status=queued] count_total={len(queued['body'])} "
          f"count_for_us={len(sends_for_us)}")
    if not sends_for_us:
        return {"status": "FAIL_no_queued_for_us", "queued": queued}
    send = sends_for_us[0]
    print(f"  send.id={send['id']} recipients={send['recipientCount']} "
          f"districts={send['districtNames']}")

    sent = await _post_send(send["id"])
    print(f"[POST /sends/{send['id']}/send] {sent['status_code']}")
    print(f"  body.status={sent['body'].get('status')} "
          f"transport_log keys={list((sent['body'].get('transport_log') or {}).keys())}")

    async with artemis_db.SessionLocal() as session:
        after_send = await _snapshot(session, deliverable_id=deliverable.id)
    print(f"[after send] {json.dumps(after_send, default=str)}")

    # Idempotency check
    again = await _post_send_again(send["id"])
    print(f"[POST /sends/{send['id']}/send AGAIN] {again['status_code']} "
          f"body.code={(again['body'] or {}).get('detail', {}).get('code') if isinstance(again['body'].get('detail'), dict) else (again['body'] or {}).get('code')}")

    return {
        "candidate_id": candidate.id,
        "deliverable_id": deliverable.id,
        "approval_id": approval_id,
        "before": before,
        "after_approve": after_approve,
        "queued": sends_for_us,
        "sent_response": sent,
        "after_send": after_send,
        "idempotency": again,
    }


async def run_skip() -> dict[str, Any]:
    print("\n══════════════════════════════ SKIP PATH (no contacts) ══════════════════════════════")
    async with artemis_db.SessionLocal() as session:
        candidate, deliverable, run_id, approval_id = await _seed_gate2_scenario(
            session, state=SKIP_TARGET_STATE, label="skip-vt"
        )
        before = await _snapshot(session, deliverable_id=deliverable.id)
    print(f"[seed] candidate_id={candidate.id} deliverable_id={deliverable.id} "
          f"approval_id={approval_id}")

    decide = await _post_decide(approval_id)
    print(f"[POST /approvals/{approval_id}/decision] {decide['status_code']}")
    print(f"  resume.sends={decide['body'].get('resume', {}).get('sends')}")

    async with artemis_db.SessionLocal() as session:
        after_approve = await _snapshot(session, deliverable_id=deliverable.id)
    print(f"[after approve] {json.dumps(after_approve, default=str)}")

    return {
        "candidate_id": candidate.id,
        "deliverable_id": deliverable.id,
        "approval_id": approval_id,
        "before": before,
        "after_approve": after_approve,
    }


async def main() -> None:
    # Verify VT has 0 contacts before SKIP scenario
    async with artemis_db.SessionLocal() as session:
        vt_districts_with_contacts = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM districts d "
                    "JOIN district_contacts dc ON dc.district_id = d.id "
                    "WHERE d.state = 'VT' AND dc.active = true"
                )
            )
        ).scalar_one()
    print(f"[setup] VT districts with active contacts = {vt_districts_with_contacts}")
    if vt_districts_with_contacts > 0:
        print("[setup WARN] SKIP scenario will not actually skip — pick a different state")

    happy = await run_happy()
    skip = await run_skip()

    print("\n══════════════════════════════ SUMMARY ══════════════════════════════")
    print(f"HAPPY: deliverable_status before='draft_ready' "
          f"after_approve={happy.get('after_approve', {}).get('deliverable_status', 'N/A')} "
          f"after_send={happy.get('after_send', {}).get('deliverable_status', 'N/A')}")
    print(f"  send statuses: {[s['status'] for s in happy.get('after_send', {}).get('campaign_sends', [])]}")
    print(f"SKIP:  deliverable_status after_approve="
          f"{skip.get('after_approve', {}).get('deliverable_status', 'N/A')}")
    print(f"  send statuses: {[s['status'] for s in skip.get('after_approve', {}).get('campaign_sends', [])]}")


if __name__ == "__main__":
    asyncio.run(main())
