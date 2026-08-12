"""CCA5 — crisis-content decision loop tests.

Covers every item in ``briefs/cca5-approval-loop.md`` "Tests" section:
authenticated + authorized Approve clicks (both routes, both directions),
the unknown-user path, identity-from-verified-payload-not-value, the
Request-changes modal + its view_submission, the double-click guard, and
the append-only "changes_requested then later approved" survival guarantee.

POST /api/integrations/slack/interactivity/{agent_id} — same endpoint
``tests/test_slack_interactivity.py`` covers for the pre-existing
pipeline_approval_* actions; this file exercises the NEW crisis_content_*
dispatch branch added alongside it. That file is left completely unmodified
-- see the brief's constraint that verification stays untouched.

DB: uses ARTEMIS_TEST_DB_URL (set by ../conftest.py), which must be at head.
Mirrors the fixture pattern in tests/test_slack_interactivity.py — this file
lives directly under tests/, outside any package-scoped conftest, so it
wires its own db_session/engine bound to the same URL the app's dependency
injection will use.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os as _os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.config import settings
from artemis.crisis_content import slack_actions
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
    CrisisContentDecision,
)
from artemis.crisis_content.transitions import mark_notified
from artemis.db import attach_pgvector_codec
from artemis.directory.models import DirectoryPerson
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration

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
    "TRUNCATE crisis_content_decisions, crisis_content_notifications, "
    "crisis_content_copy_versions, crisis_content_cards, integrations, "
    "directory_people RESTART IDENTITY CASCADE"
)

_AGENT_ID = "callie"
_SIGNING_SECRET = "test-signing-secret-do-not-use-in-prod"
_URL = f"/api/integrations/slack/interactivity/{_AGENT_ID}"

_JON = ("jon.fila@amiralearning.com", "U_JON")
_ANGELA = ("angela.miata@amiralearning.com", "U_ANGELA")
_HANNAH = ("hannah.slater@amiralearning.com", "U_HANNAH")
_JACLYN = ("jaclyn.wright@amiralearning.com", "U_JACLYN")
_STRANGER_SLACK_ID = "U_TOTALLY_UNKNOWN"


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


def _sign(body: bytes, timestamp: str, secret: str) -> str:
    base = f"v0:{timestamp}:{body.decode()}"
    return "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _body(payload_obj: dict[str, Any] | None) -> bytes:
    form: dict[str, str] = {}
    if payload_obj is not None:
        form["payload"] = json.dumps(payload_obj)
    return urlencode(form).encode()


def _headers(body: bytes, *, timestamp: str, secret: str = _SIGNING_SECRET) -> dict[str, str]:
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": _sign(body, timestamp, secret),
    }


async def _post(client: AsyncClient, payload: dict[str, Any]) -> Any:
    body = _body(payload)
    ts = str(int(time.time()))
    resp = await client.post(_URL, content=body, headers=_headers(body, timestamp=ts))
    return resp


async def _seed_callie_integration(db_session: AsyncSession) -> None:
    async with db_session.begin():
        db_session.add(
            Integration(
                provider="slack",
                workspace_id="T_TEST",
                agent_id=_AGENT_ID,
                display_name="Callie (test)",
                bot_user_id="UBOTCALLIE",
                encrypted_credentials=encrypt_credentials(
                    {"signing_secret": _SIGNING_SECRET, "access_token": "xoxb-callie-test-token"}
                ),
                status="active",
                metadata_={},
            )
        )


async def _seed_directory(db_session: AsyncSession) -> None:
    async with db_session.begin():
        for email, slack_user_id in (_JON, _ANGELA, _HANNAH, _JACLYN):
            db_session.add(
                DirectoryPerson(email=email, full_name=email, slack_user_id=slack_user_id)
            )


async def _seed_card(
    db_session: AsyncSession,
    *,
    header: str = "August XX, 2026 - Welcome Back blog",
    platform: str | None = "LinkedIn",
    ordinal: int = 0,
    copy_body: str = "Default copy body.",
) -> int:
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()
    async with db_session.begin():
        row = CrisisContentCard(
            identity_header=header,
            identity_platform=platform,
            identity_ordinal=ordinal,
            title="Welcome Back blog",
            asset_status="Draft",
            copy_status="Ready",
            asset_url=None,
            copy_hash=copy_hash,
        )
        db_session.add(row)
        await db_session.flush()
        card_id = row.id
    return card_id


def _click_payload(
    *,
    action_id: str,
    card_id: int,
    route: str,
    slack_user_id: str,
    username: str = "clicker",
    decoy_action_user_id: str | None = None,
    response_url: str = "https://hooks.slack.test/actions/FAKE1",
    trigger_id: str = "TRIGGER123",
    message_ts: str = "1700000000.000100",
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "action_id": action_id,
        "value": f"{card_id}:{route}",
    }
    if decoy_action_user_id is not None:
        # Decoy: an attacker-shaped, action-scoped "identity" a naive
        # implementation might read instead of the verified top-level user.
        # Must be ignored entirely -- see
        # test_identity_comes_from_verified_payload_not_action_value.
        action["user"] = {"id": decoy_action_user_id, "username": "attacker"}
    return {
        "type": "block_actions",
        "actions": [action],
        "user": {"id": slack_user_id, "username": username},
        "response_url": response_url,
        "trigger_id": trigger_id,
        "container": {"type": "message", "message_ts": message_ts},
    }


def _view_submission_payload(
    *, private_metadata: dict[str, Any], note: str | None, slack_user_id: str
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if note is not None:
        values = {
            slack_actions._NOTE_BLOCK_ID: {
                slack_actions._NOTE_ACTION_ID: {"type": "plain_text_input", "value": note}
            }
        }
    return {
        "type": "view_submission",
        "user": {"id": slack_user_id, "username": "clicker"},
        "view": {
            "callback_id": slack_actions.CRISIS_CONTENT_VIEW_CALLBACK_ID,
            "private_metadata": json.dumps(private_metadata),
            "state": {"values": values},
        },
    }


async def _decisions_for(db_session: AsyncSession, card_id: int, route: str) -> list[Any]:
    result = await db_session.execute(
        select(CrisisContentDecision)
        .where(CrisisContentDecision.card_id == card_id, CrisisContentDecision.route == route)
        .order_by(CrisisContentDecision.id.asc())
    )
    return list(result.scalars().all())


# ── Approve: authorization matrix ───────────────────────────────────────────


async def test_approve_by_allowed_copy_approver_records_and_updates_card(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_ANGELA[1],
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("replace_original") is True
    assert "Approved" in body.get("text", "")

    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decision == "approved"
    assert rows[0].decided_by_email == _ANGELA[0]
    assert rows[0].decided_by_slack_user_id == _ANGELA[1]


@pytest.mark.parametrize(
    "approver", [_ANGELA, _HANNAH, _JACLYN], ids=["angela", "hannah", "jaclyn"]
)
async def test_copy_route_allows_each_of_angela_hannah_jaclyn(
    client: AsyncClient, db_session: AsyncSession, approver: tuple[str, str]
) -> None:
    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)
    email, slack_user_id = approver

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=slack_user_id,
        ),
    )

    assert resp.status_code == 200
    assert resp.json().get("replace_original") is True
    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decided_by_email == email


async def test_copy_route_allows_jon_as_redundancy(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Jon is on the copy allowlist as a deliberate backstop.

    Added 2026-08-11 at his request: during a crisis push, copy must not sit
    unapproved because all three primary approvers happen to be unavailable.
    He is redundancy, not a routine approver -- cards are still addressed to
    Angela/Hannah/Jaclyn (see docs/crisis-content-approval-pipeline.md
    "Routing"), and this is the ONLY overlap between the two routes.

    This test previously asserted the opposite (Jon rejected on copy). It was
    re-pointed rather than deleted, so the reversal stays visible in history
    instead of looking like coverage that was quietly dropped.
    """
    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_JON[1],
        ),
    )

    assert resp.status_code == 200
    assert resp.json().get("replace_original") is True
    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decided_by_email == _JON[0]


async def test_copy_route_still_rejects_an_unlisted_colleague(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding Jon must not have turned the copy allowlist into "anyone".

    The guard that matters after widening: a real @amiralearning.com address
    that is not on the list is still refused. An earlier draft of the CCA5
    brief described the copy approvers as "all @amiralearning.com", which
    could have been implemented as company-wide authorization -- this pins
    that it was not.
    """
    ephemeral_calls: list[str] = []

    async def fake_ephemeral(response_url: str | None, text: str) -> None:
        ephemeral_calls.append(text)

    monkeypatch.setattr(slack_actions, "_post_ephemeral", fake_ephemeral)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    # A real, resolvable colleague at the company domain who is NOT on the
    # allowlist. Distinct from the unresolvable-user case, which denies for a
    # different reason -- this one proves the allowlist itself is closed.
    async with db_session.begin():
        db_session.add(
            DirectoryPerson(
                email="someone.else@amiralearning.com",
                full_name="Someone Else",
                slack_user_id="U_SOMEONE_ELSE",
            )
        )
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id="U_SOMEONE_ELSE",
        ),
    )

    assert resp.status_code == 200
    assert resp.json() == {}  # no replace_original -- card untouched
    rows = await _decisions_for(db_session, card_id, "copy")
    assert rows == []
    assert len(ephemeral_calls) == 1
    assert "not an approver" in ephemeral_calls[0].lower()


@pytest.mark.parametrize(
    ("approver", "authorized"),
    [(_JON, True), (_ANGELA, False)],
    ids=["jon_allowed", "angela_rejected"],
)
async def test_asset_route_jon_only(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    approver: tuple[str, str],
    authorized: bool,
) -> None:
    ephemeral_calls: list[str] = []

    async def fake_ephemeral(response_url: str | None, text: str) -> None:
        ephemeral_calls.append(text)

    monkeypatch.setattr(slack_actions, "_post_ephemeral", fake_ephemeral)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)
    email, slack_user_id = approver

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="asset",
            slack_user_id=slack_user_id,
        ),
    )

    assert resp.status_code == 200
    rows = await _decisions_for(db_session, card_id, "asset")
    if authorized:
        assert resp.json().get("replace_original") is True
        assert len(rows) == 1
        assert rows[0].decided_by_email == email
        assert ephemeral_calls == []
    else:
        assert resp.json() == {}
        assert rows == []
        assert len(ephemeral_calls) == 1


# ── Unknown user / identity sourcing ────────────────────────────────────────


async def test_unknown_slack_user_no_row_ephemeral_no_500(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ephemeral_calls: list[str] = []

    async def fake_ephemeral(response_url: str | None, text: str) -> None:
        ephemeral_calls.append(text)

    monkeypatch.setattr(slack_actions, "_post_ephemeral", fake_ephemeral)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)  # stranger is deliberately NOT in here
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_STRANGER_SLACK_ID,
        ),
    )

    assert resp.status_code == 200
    assert resp.status_code != 500
    rows = await _decisions_for(db_session, card_id, "copy")
    assert rows == []
    assert len(ephemeral_calls) == 1


async def test_identity_comes_from_verified_payload_not_action_value(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The button `value` here is `card_id:route` -- it never carries a user
    id. This proves the broader property: no identity-bearing field is
    trusted except the verified payload's top-level `user.id`, by planting a
    decoy identity ON THE ACTION ITSELF (a shape a naive implementation
    might read) that disagrees with the real, verified top-level `user` --
    and asserting the top-level one wins.
    """
    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_ANGELA[1],  # the verified, top-level identity
            decoy_action_user_id="UATTACKER_FAKE",  # must be ignored
        ),
    )

    assert resp.status_code == 200
    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decided_by_slack_user_id == _ANGELA[1]
    assert rows[0].decided_by_email == _ANGELA[0]
    assert rows[0].decided_by_slack_user_id != "UATTACKER_FAKE"


# ── Request changes: modal + view_submission ────────────────────────────────


async def test_request_changes_opens_modal_with_private_metadata(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, dict[str, Any]]] = []

    class _FakeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def views_open(self, trigger_id: str, view: dict[str, Any]) -> dict[str, Any]:
            opened.append((trigger_id, view))
            return {"ok": True}

    monkeypatch.setattr(slack_actions, "SlackClient", _FakeSlackClient)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_request_changes",
            card_id=card_id,
            route="copy",
            slack_user_id=_HANNAH[1],
            trigger_id="TRIGGER_XYZ",
        ),
    )

    assert resp.status_code == 200
    assert resp.json() == {}
    assert len(opened) == 1
    trigger_id, view = opened[0]
    assert trigger_id == "TRIGGER_XYZ"
    assert view["callback_id"] == slack_actions.CRISIS_CONTENT_VIEW_CALLBACK_ID
    metadata = json.loads(view["private_metadata"])
    assert metadata["card_id"] == card_id
    assert metadata["route"] == "copy"

    # No decision yet -- opening the modal must not itself decide anything.
    rows = await _decisions_for(db_session, card_id, "copy")
    assert rows == []


async def test_view_submission_persists_note(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    updates: list[tuple[str, list[Any]]] = []

    async def fake_update(response_url: str | None, *, text: str, blocks: list[Any]) -> None:
        updates.append((text, blocks))

    monkeypatch.setattr(slack_actions, "_update_card_via_response_url", fake_update)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    resp = await _post(
        client,
        _view_submission_payload(
            private_metadata={
                "card_id": card_id,
                "route": "copy",
                "response_url": "https://hooks.slack.test/actions/FAKE2",
                "message_ts": "1700000000.000100",
            },
            note="Cut the second sentence, it reads as a promise we can't keep.",
            slack_user_id=_JACLYN[1],
        ),
    )

    assert resp.status_code == 200
    assert resp.json() == {}

    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decision == "changes_requested"
    assert rows[0].note == "Cut the second sentence, it reads as a promise we can't keep."
    assert rows[0].decided_by_email == _JACLYN[0]
    assert len(updates) == 1  # the original card was updated via response_url


# ── Double-click guard / append-only survival ───────────────────────────────


async def test_second_click_on_decided_card_no_duplicate_row(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ephemeral_calls: list[str] = []

    async def fake_ephemeral(response_url: str | None, text: str) -> None:
        ephemeral_calls.append(text)

    monkeypatch.setattr(slack_actions, "_post_ephemeral", fake_ephemeral)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    first = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_ANGELA[1],
        ),
    )
    assert first.status_code == 200
    assert first.json().get("replace_original") is True

    # A second, genuinely distinct delivery for the SAME button -- even from
    # a different (also-eligible) approver, as "any one is sufficient" plus
    # a stale client tapping a button that should already be gone would
    # produce.
    second = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_HANNAH[1],
        ),
    )
    assert second.status_code == 200
    assert second.json() == {}  # no second replace_original

    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decided_by_email == _ANGELA[0]  # unchanged by the second click
    assert len(ephemeral_calls) == 1
    assert "already decided" in ephemeral_calls[0].lower()


async def test_changes_requested_then_later_approved_both_rows_survive(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, dict[str, Any]]] = []

    class _FakeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def views_open(self, trigger_id: str, view: dict[str, Any]) -> dict[str, Any]:
            opened.append((trigger_id, view))
            return {"ok": True}

    async def fake_update(response_url: str | None, *, text: str, blocks: list[Any]) -> None:
        pass

    monkeypatch.setattr(slack_actions, "SlackClient", _FakeSlackClient)
    monkeypatch.setattr(slack_actions, "_update_card_via_response_url", fake_update)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    # 1. Request changes, by Angela.
    click_resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_request_changes",
            card_id=card_id,
            route="copy",
            slack_user_id=_ANGELA[1],
        ),
    )
    assert click_resp.status_code == 200
    assert len(opened) == 1
    _, view = opened[0]
    metadata = json.loads(view["private_metadata"])

    submit_resp = await _post(
        client,
        _view_submission_payload(
            private_metadata=metadata,
            note="Please cut the last line.",
            slack_user_id=_ANGELA[1],
        ),
    )
    assert submit_resp.status_code == 200

    rows_after_first = await _decisions_for(db_session, card_id, "copy")
    assert len(rows_after_first) == 1
    assert rows_after_first[0].decision == "changes_requested"

    # 2. Later, Hannah approves the same card+route.
    approve_resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_HANNAH[1],
        ),
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json().get("replace_original") is True

    rows_after_second = await _decisions_for(db_session, card_id, "copy")
    assert len(rows_after_second) == 2  # BOTH rows survive -- nothing updated or deleted
    assert rows_after_second[0].decision == "changes_requested"
    assert rows_after_second[0].note == "Please cut the last line."
    assert rows_after_second[0].decided_by_email == _ANGELA[0]
    assert rows_after_second[1].decision == "approved"
    assert rows_after_second[1].decided_by_email == _HANNAH[0]


# ── CCA9: @-mention Jen in-thread on changes_requested only ─────────────────


def _fake_slack_post_message_client(
    posted: list[tuple[str, str, str | None]],
) -> type:
    class _FakeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def post_message(
            self,
            channel: str,
            text: str,
            thread_ts: str | None = None,
            blocks: Any = None,
        ) -> dict[str, Any]:
            posted.append((channel, text, thread_ts))
            return {"ok": True}

    return _FakeSlackClient


async def test_changes_requested_mentions_jen_in_thread_with_note_text(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changes_requested decision threads a real ``<@…>`` mention for Jen,
    in the card's own thread, carrying the approver's note text.
    """
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "U016P00LP08")

    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(slack_actions, "SlackClient", _fake_slack_post_message_client(posted))

    async def fake_update(response_url: str | None, *, text: str, blocks: list[Any]) -> None:
        pass

    monkeypatch.setattr(slack_actions, "_update_card_via_response_url", fake_update)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    # CCA9: where the card was posted -- what `find_posted_location` reads
    # to know which thread to post Jen's mention into.
    async with db_session.begin():
        await mark_notified(
            db_session,
            card_id,
            "copy",
            "Ready",
            copy_hash="irrelevant-for-this-test",
            channel_id="C0BM9TL63TL",
            message_ts="1700000000.000100",
        )

    resp = await _post(
        client,
        _view_submission_payload(
            private_metadata={
                "card_id": card_id,
                "route": "copy",
                "response_url": "https://hooks.slack.test/actions/FAKE3",
                "message_ts": "1700000000.000100",
            },
            note="tighten the second paragraph",
            slack_user_id=_ANGELA[1],
        ),
    )

    assert resp.status_code == 200
    assert resp.json() == {}
    assert len(posted) == 1
    channel, jen_text, thread_ts = posted[0]
    assert channel == "C0BM9TL63TL"
    assert thread_ts == "1700000000.000100"
    assert "<@U016P00LP08>" in jen_text
    assert "tighten the second paragraph" in jen_text

    # Ready-for-review cards say the plain word Jen elsewhere (CCA8) -- this
    # message is the ONE exception where a real mention is correct; that
    # doesn't relax anywhere else, which is covered by
    # tests/test_crisis_content_voice.py's own tests, left untouched here.


async def test_changes_requested_with_empty_jen_setting_posts_without_broken_mention(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty ``crisis_content_jen_slack_user_id`` -> the message still posts,
    naming Jen in plain text, never a broken ``<@>``.
    """
    monkeypatch.setattr(settings, "crisis_content_jen_slack_user_id", "")

    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(slack_actions, "SlackClient", _fake_slack_post_message_client(posted))

    async def fake_update(response_url: str | None, *, text: str, blocks: list[Any]) -> None:
        pass

    monkeypatch.setattr(slack_actions, "_update_card_via_response_url", fake_update)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    async with db_session.begin():
        await mark_notified(
            db_session,
            card_id,
            "copy",
            "Ready",
            copy_hash="irrelevant-for-this-test",
            channel_id="C0BM9TL63TL",
            message_ts="1700000000.000200",
        )

    resp = await _post(
        client,
        _view_submission_payload(
            private_metadata={
                "card_id": card_id,
                "route": "copy",
                "response_url": "https://hooks.slack.test/actions/FAKE4",
                "message_ts": "1700000000.000200",
            },
            note="cut the CTA, it's too pushy",
            slack_user_id=_HANNAH[1],
        ),
    )

    assert resp.status_code == 200
    assert len(posted) == 1
    _channel, jen_text, _thread_ts = posted[0]
    assert "<@" not in jen_text
    assert "Jen" in jen_text
    assert "cut the CTA, it's too pushy" in jen_text


async def test_changes_requested_with_no_posted_location_skips_jen_mention_without_crashing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``crisis_content_notifications`` row for this card+route (e.g. a
    card actioned before CCA9, or a poller path that never recorded one) ->
    the request still succeeds and records the decision; there is simply
    nothing to thread Jen's mention onto. Regression guard for every OTHER
    test in this file, all of which seed a bare card with no notification
    row and must keep passing unchanged.
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(slack_actions, "SlackClient", _fake_slack_post_message_client(posted))

    async def fake_update(response_url: str | None, *, text: str, blocks: list[Any]) -> None:
        pass

    monkeypatch.setattr(slack_actions, "_update_card_via_response_url", fake_update)

    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)  # no mark_notified call -- no posted location

    resp = await _post(
        client,
        _view_submission_payload(
            private_metadata={
                "card_id": card_id,
                "route": "copy",
                "response_url": "https://hooks.slack.test/actions/FAKE5",
                "message_ts": "1700000000.000300",
            },
            note="no notification row exists for this card",
            slack_user_id=_JACLYN[1],
        ),
    )

    assert resp.status_code == 200
    assert posted == []  # nothing to thread onto -- skipped, not crashed

    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 1
    assert rows[0].decision == "changes_requested"


async def test_reopened_card_buttons_work_again(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A re-fired card's buttons must actually work.

    CCA11 re-posts a card whose approved copy was later edited. The worker
    flagged that the card shipped with live buttons while
    is_blocked_by_existing_decision still treated `approved` as terminal --
    so every click on the re-fire answered "Already decided". That is worse
    than not re-posting at all: it tells the approver something needs
    re-reviewing and then refuses to let them do it.

    A prior decision only blocks while it is still ABOUT the current copy.
    Once the copy has been revised past it, the decision is stale and the
    buttons must be live again.
    """
    await _seed_callie_integration(db_session)
    await _seed_directory(db_session)
    card_id = await _seed_card(db_session)

    # First approval lands normally.
    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_ANGELA[1],
        ),
    )
    assert resp.status_code == 200
    assert len(await _decisions_for(db_session, card_id, "copy")) == 1

    # A second click with the copy UNCHANGED is still correctly blocked.
    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_HANNAH[1],
        ),
    )
    assert resp.status_code == 200
    assert len(await _decisions_for(db_session, card_id, "copy")) == 1, (
        "an unchanged card must stay decided"
    )

    # Now the copy is revised after the approval -- the card reopens, and the
    # stale approval must no longer block a fresh decision.
    row = (
        await db_session.execute(select(CrisisContentCard).where(CrisisContentCard.id == card_id))
    ).scalar_one()
    row.copy_hash = hashlib.sha256(b"revised after approval").hexdigest()
    db_session.add(
        CrisisContentCopyVersion(
            card_id=card_id,
            copy_hash=row.copy_hash,
            copy_body="revised after approval",
            first_seen_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    resp = await _post(
        client,
        _click_payload(
            action_id="crisis_content_approve",
            card_id=card_id,
            route="copy",
            slack_user_id=_HANNAH[1],
        ),
    )
    assert resp.status_code == 200
    rows = await _decisions_for(db_session, card_id, "copy")
    assert len(rows) == 2, (
        "a card reopened by a post-approval edit must accept a fresh decision — "
        "got a blocked click instead"
    )
    assert rows[-1].decided_by_email == _HANNAH[0]
