"""CCA3 — Slack interactivity endpoint tests.

POST /api/integrations/slack/interactivity/{agent_id}

Covers: signature verification (valid / invalid / missing / stale-replay),
form + payload parsing (missing field / malformed JSON), dispatch (unknown
action_id, unknown agent_id), identity sourcing (verified payload only, never
the button value), and double-click idempotency.

DB: uses ARTEMIS_TEST_DB_URL (set by ../conftest.py) which must be at head.
Mirrors the fixture pattern in artemis/pipelines/tests/conftest.py and
tests/test_directory.py — this file lives directly under tests/, outside
any package-scoped conftest, so it wires its own db_session/engine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os as _os
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.builders.models  # noqa: F401 — register models on Base.metadata
import artemis.db
import artemis.integrations.models  # noqa: F401 — register models on Base.metadata
import artemis.marketing.models  # noqa: F401 — pipeline_runs FK campaign_candidates
import artemis.pipelines.models  # noqa: F401 — register models on Base.metadata
from artemis.db import attach_pgvector_codec
from artemis.directory.models import DirectoryPerson
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.marketing.models import Approval
from artemis.pipelines import repository as repo

pytestmark = pytest.mark.asyncio

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "TRUNCATE on the live DB would destroy production data."
    )

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    "TRUNCATE pipeline_runs, pipelines, approvals, agent_context, "
    "agent_run_trajectory_summaries, definition_proposals, agent_runs, "
    "agent_skills, agents, integrations, directory_people "
    "RESTART IDENTITY CASCADE"
)

_AGENT_ID = "kai"
_SIGNING_SECRET = "test-signing-secret-do-not-use-in-prod"
_URL = f"/api/integrations/slack/interactivity/{_AGENT_ID}"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


# ── helpers ────────────────────────────────────────────────────────────────


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
        "id": f"e_{src}_{tgt}",
        "source_node_id": src,
        "target_node_id": tgt,
        "condition": None,
        "data_shape": None,
    }


async def _setup_suspended_gate(db_session: AsyncSession) -> str:
    """Create a pipeline run with a suspended PIPE4 gate + pending Approval row."""
    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate1",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["approver@example.com"],
                "timeout_hours": 72,
            },
        ),
    ]
    edges = [_edge("trigger", "gate1")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session, name="Interactivity Test", nodes=nodes, edges=edges
        )
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="awaiting_approval",
            trigger="manual",
            triggered_by="test",
        )
        ns = {
            "trigger": {"status": "succeeded", "cost_usd": 0.0, "output_summary": "done"},
            "gate1": {"status": "suspended", "cost_usd": 0.0, "output_summary": "pending"},
        }
        await repo.update_pipeline_run(db_session, run.id, node_states=ns)
        db_session.add(
            Approval(
                kind="signal_brief",
                subject_id=f"{run.id}:gate1",
                status="pending",
                decision_payload={"run_id": run.id, "node_id": "gate1"},
                pipe4_context={
                    "pipeline_run_id": run.id,
                    "node_id": "gate1",
                    "context": {"approval_kind": "signal_brief"},
                },
            )
        )
    return run.id


async def _seed_kai_integration(db_session: AsyncSession, *, signing_secret: str = _SIGNING_SECRET) -> None:
    async with db_session.begin():
        db_session.add(
            Integration(
                provider="slack",
                workspace_id="T_TEST",
                agent_id=_AGENT_ID,
                display_name="Kai (test)",
                bot_user_id="UBOTKAI",
                encrypted_credentials=encrypt_credentials(
                    {"signing_secret": signing_secret, "access_token": "xoxb-test-token"}
                ),
                status="active",
                metadata_={},
            )
        )


def _sign(body: bytes, timestamp: str, secret: str) -> str:
    """Independent re-implementation of Slack's signing scheme, for TEST setup
    only — not the app's verifier. Mirrors the v0 HMAC-SHA256 scheme Slack
    itself uses, so tests can construct requests _verify_slack_signature will
    accept or reject on purpose.
    """
    base = f"v0:{timestamp}:{body.decode()}"
    return "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _body(payload_obj: dict[str, Any] | None) -> bytes:
    form: dict[str, str] = {}
    if payload_obj is not None:
        form["payload"] = json.dumps(payload_obj)
    return urlencode(form).encode()


def _headers(body: bytes, *, timestamp: str, secret: str, sign: bool = True) -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Slack-Request-Timestamp"] = timestamp
        headers["X-Slack-Signature"] = _sign(body, timestamp, secret)
    return headers


async def _fetch_gate_row(db_session: AsyncSession, run_id: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT p.status, p.node_states->'gate1'->>'decision', "
                "p.node_states->'gate1'->>'decided_by', a.status "
                "FROM pipeline_runs p "
                "JOIN approvals a ON a.subject_id = :subject_id "
                "WHERE p.id = :i"
            ),
            {"i": run_id, "subject_id": f"{run_id}:gate1"},
        )
    ).first()


def _approve_payload(run_id: str, *, slack_user_id: str = "USLACK123", username: str = "approver") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "actions": [
            {
                "action_id": "pipeline_approval_approve",
                "value": f"{run_id}:gate1:approved",
            }
        ],
        "user": {"id": slack_user_id, "username": username},
    }


# ── tests ──────────────────────────────────────────────────────────────────


async def test_valid_signature_records_decision(client: AsyncClient, db_session: AsyncSession) -> None:
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    body = _body(_approve_payload(run_id))
    ts = str(int(time.time()))

    with patch("artemis.pipelines.routes._dispatch_execution") as dispatch:
        resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts, secret=_SIGNING_SECRET))

    assert resp.status_code == 200
    assert resp.json().get("replace_original") is True
    dispatch.assert_called_once_with(run_id)

    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[0] == "running"
    assert row[1] == "approved"
    assert row[2] == "approver"
    assert row[3] == "approved"


async def test_invalid_signature_rejected_and_nothing_recorded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    body = _body(_approve_payload(run_id))
    ts = str(int(time.time()))
    headers = _headers(body, timestamp=ts, secret=_SIGNING_SECRET)
    headers["X-Slack-Signature"] = "v0=" + "0" * 64  # well-formed shape, wrong digest

    with patch("artemis.pipelines.routes._dispatch_execution") as dispatch:
        resp = await client.post(_URL, content=body, headers=headers)

    assert resp.status_code == 401
    dispatch.assert_not_called()

    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[1] is None  # no decision written
    assert row[3] == "pending"


async def test_missing_signature_headers_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    body = _body(_approve_payload(run_id))
    resp = await client.post(
        _URL, content=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    assert resp.status_code == 401

    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[3] == "pending"


async def test_stale_timestamp_rejected_even_with_valid_signature(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    body = _body(_approve_payload(run_id))
    stale_ts = str(int(time.time()) - 400)  # outside the 300s freshness window
    headers = _headers(body, timestamp=stale_ts, secret=_SIGNING_SECRET)  # correctly signed FOR that stale ts

    resp = await client.post(_URL, content=body, headers=headers)

    assert resp.status_code == 401

    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[3] == "pending"


async def test_missing_payload_field_is_400_not_500(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_kai_integration(db_session)

    body = _body(None)  # empty form body, no "payload" field at all
    ts = str(int(time.time()))
    resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts, secret=_SIGNING_SECRET))

    assert resp.status_code == 400


async def test_malformed_json_payload_is_400_not_500(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_kai_integration(db_session)

    body = urlencode({"payload": "{not valid json"}).encode()
    ts = str(int(time.time()))
    resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts, secret=_SIGNING_SECRET))

    assert resp.status_code == 400


async def test_unknown_action_id_acks_200_and_logs_warning(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed_kai_integration(db_session)

    payload = {
        "type": "block_actions",
        "actions": [{"action_id": "some_unrelated_action", "value": "whatever"}],
        "user": {"id": "USLACK123"},
    }
    body = _body(payload)
    ts = str(int(time.time()))

    with caplog.at_level(logging.WARNING, logger="artemis.routes.integrations_slack_interactivity"):
        resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts, secret=_SIGNING_SECRET))

    assert resp.status_code == 200
    assert any("unhandled action_id" in rec.message for rec in caplog.records)


async def test_unknown_agent_id_never_500s(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No integration row for this agent_id, and no global fallback secret —
    # the request must fail closed (401), never 500.
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)

    payload = _approve_payload("no-such-run")
    body = _body(payload)
    ts = str(int(time.time()))
    resp = await client.post(
        "/api/integrations/slack/interactivity/totally-unregistered-agent",
        content=body,
        headers=_headers(body, timestamp=ts, secret="whatever-the-attacker-guesses"),
    )

    assert resp.status_code in (401, 404)
    assert resp.status_code != 500


async def test_identity_comes_from_verified_payload_not_action_value(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The button `value` in this app is `run_id:node_id:decision` — it never
    carries a user id (see artemis/integrations/slack/messages.py). The
    property the brief is protecting against is broader than just "value":
    no identity-bearing field is trusted except the verified payload's
    top-level `user.id`. This test proves that by planting a decoy identity
    on the action itself (a shape a naive implementation might read) that
    disagrees with the real, verified top-level `user` — and asserting the
    top-level one wins.
    """
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    async with db_session.begin():
        db_session.add(
            DirectoryPerson(
                email="real.approver@amiralearning.com",
                full_name="Real Approver",
                slack_user_id="UREAL999",
            )
        )

    payload = {
        "type": "block_actions",
        "actions": [
            {
                "action_id": "pipeline_approval_approve",
                "value": f"{run_id}:gate1:approved",
                # Decoy: an attacker-shaped, action-scoped "identity" that a
                # naive implementation might read instead of the verified
                # top-level user. Must be ignored entirely.
                "user": {"id": "UATTACKER_FAKE", "username": "attacker"},
            }
        ],
        "user": {"id": "UREAL999", "username": "real_person"},  # the verified identity
    }
    body = _body(payload)
    ts = str(int(time.time()))

    with patch("artemis.pipelines.routes._dispatch_execution"):
        resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts, secret=_SIGNING_SECRET))

    assert resp.status_code == 200
    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[2] == "real.approver@amiralearning.com"  # resolved from the verified payload
    assert row[2] != "slack:UATTACKER_FAKE"


async def test_double_click_does_not_double_record(client: AsyncClient, db_session: AsyncSession) -> None:
    run_id = await _setup_suspended_gate(db_session)
    await _seed_kai_integration(db_session)

    payload = _approve_payload(run_id, slack_user_id="USLACK123", username="first_clicker")
    body = _body(payload)
    ts1 = str(int(time.time()))

    with patch("artemis.pipelines.routes._dispatch_execution") as dispatch:
        resp1 = await client.post(
            _URL, content=body, headers=_headers(body, timestamp=ts1, secret=_SIGNING_SECRET)
        )
        assert resp1.status_code == 200

        # A second, genuinely distinct Slack delivery (fresh timestamp/signature,
        # not a byte-for-byte replay) for the SAME button, as a real double-tap
        # would produce — even from a different clicker.
        payload2 = _approve_payload(run_id, slack_user_id="USLACK999", username="second_clicker")
        body2 = _body(payload2)
        ts2 = str(int(time.time()) + 1)
        resp2 = await client.post(
            _URL, content=body2, headers=_headers(body2, timestamp=ts2, secret=_SIGNING_SECRET)
        )
        assert resp2.status_code == 200

    dispatch.assert_called_once_with(run_id)  # not called again on the second click

    row = await _fetch_gate_row(db_session, run_id)
    assert row is not None
    assert row[1] == "approved"
    assert row[2] == "first_clicker"  # unchanged by the second click
    assert row[3] == "approved"
