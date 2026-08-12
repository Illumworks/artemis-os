"""CCA7 -- write the decision back to Jen's doc + notify her.

Covers every item in ``briefs/cca7-writeback-and-notify-jen.md`` "Tests"
section. The Google Docs/Drive/Gmail APIs are ALWAYS mocked here -- this
suite never hits the live document (per the brief's explicit instruction and
the module's own safety framing: a bad index calculation against Jen's real
doc silently mangles someone else's document, so nothing about that risk is
exercised outside a live smoke run described in the report, not here).

Engine/fixture strategy mirrors ``tests/test_crisis_content_decisions.py``:
a per-test engine bound to ``ARTEMIS_TEST_DB_URL`` (or ``ARTEMIS_DB_URL``),
with a hard refusal to run against anything that isn't a test database, and
a TRUNCATE before each test for isolation.

Two independent layers are tested:

1. The pure Docs-API-JSON helpers (``locate_card_table``, the signature/
   header/hash matching, the insert-index computation) -- via hand-built
   fixture documents shaped like ``documents.get?includeTabsContent=true``.
2. ``deliver_decision_writeback`` end to end, with the three side-effecting
   calls (``_fetch_document``/``_insert_text``, ``_create_drive_comment``,
   ``_resolve_personal_gmail_client``) monkeypatched at the
   ``artemis.crisis_content.writeback`` module boundary, and DB reads/writes
   (the idempotency ledger, the card row) going through the real Postgres
   test database.
"""

from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.crisis_content import writeback
from artemis.crisis_content.orm import CrisisContentCard, CrisisContentDecision
from artemis.crisis_content.writeback import (
    CardNotLocatedError,
    CrisisContentWritebackDelivery,
    deliver_decision_writeback,
    locate_card_table,
    render_writeback_line,
)
from artemis.db import attach_pgvector_codec
from artemis.google_docs.models import GoogleCredential
from artemis.identity.models import User

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` here (mirrors
# test_crisis_content_poller.py) -- this file mixes async DB tests with
# plain sync tests of the pure locate_card_table/render_writeback_line
# helpers, and asyncio_mode = "auto" (pyproject.toml) already collects
# `async def test_*` correctly without the marker.

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "TRUNCATE on the live DB would destroy production data."
    )

_TRUNCATE = text(
    "TRUNCATE crisis_content_writeback_deliveries, crisis_content_decisions, "
    "crisis_content_notifications, crisis_content_copy_versions, crisis_content_cards, "
    "google_credentials, users RESTART IDENTITY CASCADE"
)

_DECIDED_AT = datetime(2026, 8, 11, 19, 14, tzinfo=UTC)


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


# ── fixture builders: Docs API JSON (includeTabsContent=true shape) ────────


def _para(text_: str, end_index: int = 1) -> dict[str, Any]:
    return {"endIndex": end_index, "paragraph": {"elements": [{"textRun": {"content": text_}}]}}


def _copy_hash(lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _card_table(*, header: str, copy_lines: list[str], status_end_index: int = 500) -> dict[str, Any]:
    """A signature-matching review-card table: header row + status/copy row."""
    return {
        "tableRows": [
            {"tableCells": [{"content": [_para(header)]}]},
            {
                "tableCells": [
                    {
                        "content": [
                            _para("Platform:"),
                            _para("Asset for review - LINK"),
                            _para(""),  # asset chip -- opaque, no text
                            _para("Copy review"),
                            _para("", end_index=status_end_index),  # copy chip -- opaque
                        ]
                    },
                    {"content": [_para(line) for line in copy_lines]},
                ]
            },
        ]
    }


def _decoy_table(header: str = "Strategy Plan notes") -> dict[str, Any]:
    """A non-review table -- missing the 'Copy review' marker on purpose."""
    return {
        "tableRows": [
            {"tableCells": [{"content": [_para(header)]}]},
            {
                "tableCells": [
                    {"content": [_para("Owner:")]},
                    {"content": [_para("Not a review card.")]},
                ]
            },
        ]
    }


def _document(tabs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "documentId": "DOC123",
        "tabs": [
            {
                "tabProperties": {"tabId": tab_id},
                "documentTab": {"body": {"content": [{"table": t} for t in tables]}},
            }
            for tab_id, tables in tabs.items()
        ],
    }


def _apply_insert_in_place(doc: dict[str, Any], *, tab_id: str, index: int, text_: str) -> None:
    """Mutate ``doc`` the way a real ``insertText`` at (tab_id, index) would.

    Appends a new paragraph to whichever status cell's last element ends
    right after ``index`` -- mirrors ``_append_index_for_status_cell``'s own
    math so the fake fetch-after-insert reflects the write.
    """
    for tab in doc.get("tabs", []):
        if str(tab["tabProperties"]["tabId"]) != tab_id:
            continue
        for item in tab["documentTab"]["body"]["content"]:
            table = item.get("table")
            if not table:
                continue
            status_cell = table["tableRows"][1]["tableCells"][0]
            last = status_cell["content"][-1]
            if last["endIndex"] - 1 == index:
                inserted = text_[1:] if text_.startswith("\n") else text_
                status_cell["content"].append(
                    {
                        "endIndex": last["endIndex"] + len(text_),
                        "paragraph": {"elements": [{"textRun": {"content": inserted}}]},
                    }
                )
                return


def _patch_docs_api(
    monkeypatch: pytest.MonkeyPatch,
    doc: dict[str, Any],
    insert_calls: list[dict[str, Any]],
    *,
    mutate_on_insert: bool = True,
    fetch_override: list[dict[str, Any]] | None = None,
) -> None:
    """Stub token resolution + fetch/insert against ``doc`` (mutated in place).

    ``fetch_override``, if given, is consumed in order for successive
    ``_fetch_document`` calls instead of snapshotting ``doc`` -- used by the
    "verification detects damage" test, which needs the SECOND fetch to
    diverge from what a real mutate-on-insert round trip would produce.
    """
    fetch_queue = list(fetch_override) if fetch_override is not None else None

    async def fake_token(session: AsyncSession) -> str:
        return "fake-access-token"

    async def fake_fetch(access_token: str, document_id: str) -> dict[str, Any]:
        if fetch_queue:
            return copy.deepcopy(fetch_queue.pop(0))
        return copy.deepcopy(doc)

    async def fake_insert(
        access_token: str, *, document_id: str, tab_id: str, index: int, text: str
    ) -> None:
        insert_calls.append({"tab_id": tab_id, "index": index, "text": text})
        if mutate_on_insert:
            _apply_insert_in_place(doc, tab_id=tab_id, index=index, text_=text)

    monkeypatch.setattr(writeback, "_resolve_docs_access_token", fake_token)
    monkeypatch.setattr(writeback, "_fetch_document", fake_fetch)
    monkeypatch.setattr(writeback, "_insert_text", fake_insert)


def _patch_comment(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], *, error: Exception | None = None
) -> None:
    async def fake_create_comment(access_token: str, *, document_id: str, content: str) -> None:
        if error is not None:
            raise error
        calls.append(content)

    monkeypatch.setattr(writeback, "_create_drive_comment", fake_create_comment)


def _patch_gmail(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]], *, fail_times: int = 0
) -> None:
    state = {"attempts": 0}

    class _FakeGmailClient:
        async def send_message(
            self,
            *,
            to: str,
            subject: str,
            body: str,
            thread_id: str | None = None,
            in_reply_to: str | None = None,
        ) -> dict[str, Any]:
            state["attempts"] += 1
            if state["attempts"] <= fail_times:
                raise RuntimeError("simulated Gmail outage")
            calls.append({"to": to, "subject": subject, "body": body})
            return {"id": "MSG1", "threadId": "THREAD1"}

    fake_client = _FakeGmailClient()

    async def fake_resolve(session: AsyncSession) -> _FakeGmailClient:
        return fake_client

    monkeypatch.setattr(writeback, "_resolve_personal_gmail_client", fake_resolve)


def _patch_alert(monkeypatch: pytest.MonkeyPatch, alerts: list[str]) -> None:
    async def fake_alert(session: AsyncSession, text_: str) -> None:
        alerts.append(text_)

    monkeypatch.setattr(writeback, "_alert_jon", fake_alert)


# ── DB seed helpers ──────────────────────────────────────────────────────────


async def _seed_card(
    db_session: AsyncSession,
    *,
    header: str = "August 11, 2026 - Welcome Back blog",
    platform: str | None = "LinkedIn",
    title: str = "Welcome Back blog",
    copy_lines: list[str] | None = None,
) -> tuple[int, str]:
    lines = copy_lines if copy_lines is not None else ["Default copy body line one."]
    copy_hash = _copy_hash(lines)
    async with db_session.begin():
        row = CrisisContentCard(
            identity_header=header,
            identity_platform=platform,
            identity_ordinal=0,
            title=title,
            asset_status="Draft",
            copy_status="Ready",
            asset_url=None,
            copy_hash=copy_hash,
        )
        db_session.add(row)
        await db_session.flush()
        card_id = row.id
    return card_id, copy_hash


async def _seed_decision(
    db_session: AsyncSession,
    *,
    card_id: int,
    route: str = "copy",
    decision: str = "approved",
    decided_by_email: str = "angela.miata@amiralearning.com",
    decided_by_slack_user_id: str = "U_ANGELA",
    note: str | None = None,
    decided_at: datetime = _DECIDED_AT,
) -> CrisisContentDecision:
    async with db_session.begin():
        row = CrisisContentDecision(
            card_id=card_id,
            route=route,
            decision=decision,
            decided_by_slack_user_id=decided_by_slack_user_id,
            decided_by_email=decided_by_email,
            note=note,
            decided_at=decided_at,
        )
        db_session.add(row)
        await db_session.flush()
        decision_id = row.id
    result = await db_session.execute(
        select(CrisisContentDecision).where(CrisisContentDecision.id == decision_id)
    )
    return result.scalar_one()


async def _ledger_actions(db_session: AsyncSession, decision_id: int) -> set[str]:
    result = await db_session.execute(
        select(CrisisContentWritebackDelivery.action).where(
            CrisisContentWritebackDelivery.decision_id == decision_id
        )
    )
    return set(result.scalars().all())


# ── locate_card_table: pure matching logic ──────────────────────────────────


def test_locate_card_table_unique_header_match() -> None:
    doc = _document(
        {
            "t1": [
                _decoy_table(),
                _card_table(header="August 11, 2026 - Welcome Back blog", copy_lines=["Copy A"]),
            ]
        }
    )
    location, count = locate_card_table(
        doc, header="August 11, 2026 - Welcome Back blog", copy_hash=_copy_hash(["Copy A"])
    )
    assert location.tab_id == "t1"
    assert count == 1  # only one signature-matching table -- the decoy doesn't count


def test_locate_card_table_ambiguous_header_disambiguated_by_copy_hash() -> None:
    shared_header = "August XX, 2026 - Welcome Back blog"
    table_a = _card_table(header=shared_header, copy_lines=["Instagram copy."])
    table_b = _card_table(header=shared_header, copy_lines=["LinkedIn copy, totally different."])
    doc = _document({"t1": [table_a, table_b]})

    location, count = locate_card_table(
        doc, header=shared_header, copy_hash=_copy_hash(["LinkedIn copy, totally different."])
    )
    assert count == 2
    assert location.table is table_b


def test_locate_card_table_zero_matches_raises() -> None:
    doc = _document({"t1": [_card_table(header="Some other post", copy_lines=["x"])]})
    with pytest.raises(CardNotLocatedError):
        locate_card_table(doc, header="Not in the doc", copy_hash=_copy_hash(["x"]))


def test_locate_card_table_ambiguous_and_hash_also_ambiguous_raises() -> None:
    shared_header = "August XX, 2026 - Welcome Back blog"
    same_copy = ["Identical copy on both -- e.g. a rename collision."]
    doc = _document(
        {
            "t1": [
                _card_table(header=shared_header, copy_lines=same_copy),
                _card_table(header=shared_header, copy_lines=same_copy),
            ]
        }
    )
    with pytest.raises(CardNotLocatedError):
        locate_card_table(doc, header=shared_header, copy_hash=_copy_hash(same_copy))


# ── render_writeback_line ────────────────────────────────────────────────────


def test_render_writeback_line_approved_includes_route_actor_and_chip_caveat() -> None:
    line = render_writeback_line(
        route="copy",
        decision="approved",
        actor_label="angela.miata@amiralearning.com",
        decided_at=_DECIDED_AT,
        note=None,
    )
    assert "Approved" in line
    assert "(copy)" in line
    assert "angela.miata@amiralearning.com" in line
    assert "Aug 11" in line
    assert "Jen's to flip" in line or "yours to flip" in line.lower()


def test_render_writeback_line_changes_requested_includes_the_note() -> None:
    line = render_writeback_line(
        route="copy",
        decision="changes_requested",
        actor_label="jon.fila@amiralearning.com",
        decided_at=_DECIDED_AT,
        note="tighten the second paragraph",
    )
    assert "Changes requested" in line
    assert "tighten the second paragraph" in line


# ── deliver_decision_writeback: end to end, three actions ──────────────────


async def test_approved_decision_correct_line_inserted_at_right_card(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["This is the approved copy."]
    card_id, _copy_hash_value = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id, decision="approved")

    doc = _document({"t1": [_decoy_table(), _card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    comment_calls: list[str] = []
    email_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, comment_calls)
    _patch_gmail(monkeypatch, email_calls)
    _patch_alert(monkeypatch, [])

    outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "delivered"
    assert len(insert_calls) == 1
    assert insert_calls[0]["tab_id"] == "t1"
    assert "\n" in insert_calls[0]["text"]  # inserted as a NEW paragraph, not glued on
    line_text = insert_calls[0]["text"].lstrip("\n")
    assert "Approved" in line_text
    assert "(copy)" in line_text
    assert "angela.miata@amiralearning.com" in line_text
    assert await _ledger_actions(db_session, decision.id) == {"doc_line", "comment", "email"}


async def test_changes_requested_line_includes_the_note(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy that needs a tweak."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(
        db_session,
        card_id=card_id,
        decision="changes_requested",
        decided_by_email="jon.fila@amiralearning.com",
        decided_by_slack_user_id="U_JON",
        note="tighten the second paragraph",
    )

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, [])
    _patch_gmail(monkeypatch, [])
    _patch_alert(monkeypatch, [])

    outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "delivered"
    assert "tighten the second paragraph" in insert_calls[0]["text"]
    assert "Changes requested" in insert_calls[0]["text"]


async def test_target_card_not_locatable_writes_nothing_logs_error_alerts_jon(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    card_id, _ = await _seed_card(db_session, header="August 11, 2026 - Welcome Back blog")
    decision = await _seed_decision(db_session, card_id=card_id)

    # The live doc no longer has any table with this header -- e.g. Jen
    # renamed it, or it moved to a tab that hasn't been polled yet.
    doc = _document({"t1": [_decoy_table(), _card_table(header="A totally different post", copy_lines=["x"])]})
    insert_calls: list[dict[str, Any]] = []
    alerts: list[str] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, [])
    _patch_gmail(monkeypatch, [])
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("ERROR"):
        outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "not_located"
    assert insert_calls == []  # nothing written
    assert any("not positively identified" in record.message for record in caplog.records)
    assert len(alerts) == 1
    assert "could not positively identify" in alerts[0].lower()
    assert await _ledger_actions(db_session, decision.id) == {"comment", "email"}  # no doc_line row


async def test_retry_of_already_delivered_decision_no_second_action(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id)

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    comment_calls: list[str] = []
    email_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, comment_calls)
    _patch_gmail(monkeypatch, email_calls)
    _patch_alert(monkeypatch, [])

    first = await deliver_decision_writeback(db_session, decision)
    second = await deliver_decision_writeback(db_session, decision)

    assert first.doc_line == "delivered"
    assert second.doc_line == "already_delivered"
    assert second.comment == "already_delivered"
    assert second.email == "already_delivered"
    assert len(insert_calls) == 1
    assert len(comment_calls) == 1
    assert len(email_calls) == 1


async def test_email_fails_doc_write_succeeded_retry_sends_only_email(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id)

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    comment_calls: list[str] = []
    email_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, comment_calls)
    _patch_gmail(monkeypatch, email_calls, fail_times=1)  # first send raises, second succeeds
    _patch_alert(monkeypatch, [])

    first = await deliver_decision_writeback(db_session, decision)
    assert first.doc_line == "delivered"
    assert first.comment == "delivered"
    assert first.email == "failed"
    assert len(insert_calls) == 1
    assert len(comment_calls) == 1
    assert email_calls == []

    second = await deliver_decision_writeback(db_session, decision)
    assert second.doc_line == "already_delivered"
    assert second.comment == "already_delivered"
    assert second.email == "delivered"
    # The doc line and comment were NOT re-attempted on retry.
    assert len(insert_calls) == 1
    assert len(comment_calls) == 1
    assert len(email_calls) == 1


async def test_comment_mentions_both_of_jens_addresses(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id)

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    comment_calls: list[str] = []
    _patch_docs_api(monkeypatch, doc, [])
    _patch_comment(monkeypatch, comment_calls)
    _patch_gmail(monkeypatch, [])
    _patch_alert(monkeypatch, [])

    await deliver_decision_writeback(db_session, decision)

    assert len(comment_calls) == 1
    assert "jen@justrightstrategy.com" in comment_calls[0]
    assert "jen@digigeeks.com" in comment_calls[0]


async def test_post_write_verification_detects_changed_card_count_and_alerts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id)

    # A SECOND, genuinely signature-matching card (not a decoy -- decoys
    # don't count toward the card total in the first place, which would
    # make removing one a no-op for this check).
    other_card = _card_table(header="A second, unrelated post", copy_lines=["Other copy."])
    doc = _document({"t1": [other_card, _card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    alerts: list[str] = []

    async def fake_token(session: AsyncSession) -> str:
        return "fake-access-token"

    fetch_count = {"n": 0}

    async def fake_fetch(access_token: str, document_id: str) -> dict[str, Any]:
        fetch_count["n"] += 1
        if fetch_count["n"] == 1:
            return copy.deepcopy(doc)
        # Second fetch (post-write verification): the OTHER card has
        # vanished on re-read -- something else changed the doc
        # concurrently. The line IS present on the target (our insert
        # succeeded), but the card count differs from the pre-write
        # baseline, which must alarm regardless.
        mutated = copy.deepcopy(doc)
        mutated["tabs"][0]["documentTab"]["body"]["content"] = [
            item
            for item in mutated["tabs"][0]["documentTab"]["body"]["content"]
            if writeback._header_text(item["table"]) == header
        ]
        _apply_insert_in_place(
            mutated,
            tab_id="t1",
            index=insert_calls[0]["index"] if insert_calls else 0,
            text_=insert_calls[0]["text"] if insert_calls else "",
        )
        return mutated

    async def fake_insert(
        access_token: str, *, document_id: str, tab_id: str, index: int, text: str
    ) -> None:
        insert_calls.append({"tab_id": tab_id, "index": index, "text": text})

    monkeypatch.setattr(writeback, "_resolve_docs_access_token", fake_token)
    monkeypatch.setattr(writeback, "_fetch_document", fake_fetch)
    monkeypatch.setattr(writeback, "_insert_text", fake_insert)
    _patch_comment(monkeypatch, [])
    _patch_gmail(monkeypatch, [])
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("CRITICAL"):
        outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "damaged"
    assert len(insert_calls) == 1  # the write itself happened exactly once
    assert any("VERIFICATION FAILED" in record.message for record in caplog.records)
    assert len(alerts) == 1
    assert "verification" in alerts[0].lower()
    assert "do not attempt a fix" in alerts[0].lower() or "do not" in alerts[0].lower()
    # Marked delivered anyway -- a retry must NOT attempt a second, possibly
    # compounding insert into a document that may already be damaged.
    assert await _ledger_actions(db_session, decision.id) >= {"doc_line"}

    retry = await deliver_decision_writeback(db_session, decision)
    assert retry.doc_line == "already_delivered"
    assert len(insert_calls) == 1  # still exactly one -- no cleanup/retry write


async def test_tabbed_doc_index_targeting_writes_to_second_tab_not_first(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body only on tab two."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    decision = await _seed_decision(db_session, card_id=card_id)

    doc = _document(
        {
            "t1": [_card_table(header="A different post entirely", copy_lines=["unrelated"])],
            "t2": [_card_table(header=header, copy_lines=copy_lines)],
        }
    )
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_comment(monkeypatch, [])
    _patch_gmail(monkeypatch, [])
    _patch_alert(monkeypatch, [])

    outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "delivered"
    assert len(insert_calls) == 1
    assert insert_calls[0]["tab_id"] == "t2"


async def test_gmail_credential_resolved_by_purpose_personal_not_fixed_user_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the historical bug: filtering by a hardcoded user_id (the
    dev@local shim, user_id=1 in production) instead of purpose='personal'
    silently grabs the wrong account's token. Two different users each hold
    a purpose='personal' credential here; the real one (more recently
    updated, standing in for Jon's actual account) must win regardless of
    which user_id happens to hold it -- there is no user_id filter in
    _resolve_personal_credential at all.
    """
    async with db_session.begin():
        dev_shim_user = User(email="dev@local", name="Dev Shim")
        real_user = User(email="jon.fila@amiralearning.com", name="Jon Fila")
        db_session.add_all([dev_shim_user, real_user])
        await db_session.flush()
        db_session.add(
            GoogleCredential(
                user_id=dev_shim_user.id,
                purpose="personal",
                access_token="DEV_SHIM_WRONG_TOKEN",
                refresh_token="dev-shim-refresh",
                expiry=datetime(2099, 1, 1, tzinfo=UTC),
                scope="https://www.googleapis.com/auth/gmail.send",
                connected_email="dev@local",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        db_session.add(
            GoogleCredential(
                user_id=real_user.id,
                purpose="personal",
                access_token="JONS_REAL_TOKEN",
                refresh_token="jon-refresh",
                expiry=datetime(2099, 1, 1, tzinfo=UTC),
                scope=(
                    "https://www.googleapis.com/auth/gmail.send "
                    "https://www.googleapis.com/auth/documents "
                    "https://www.googleapis.com/auth/drive"
                ),
                connected_email="jon.fila@amiralearning.com",
                updated_at=datetime(2026, 8, 11, tzinfo=UTC),
            )
        )

    credential = await writeback._resolve_personal_credential(db_session)
    assert credential.access_token == "JONS_REAL_TOKEN"
    assert credential.access_token != "DEV_SHIM_WRONG_TOKEN"
    assert credential.connected_email == "jon.fila@amiralearning.com"


# ── settings kill switch (defensive addition, not in the brief's list) ─────


async def test_writeback_disabled_via_settings_does_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.config import settings

    monkeypatch.setattr(settings, "crisis_content_writeback_enabled", False)

    card_id, _ = await _seed_card(db_session)
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await deliver_decision_writeback(db_session, decision)

    assert outcome.doc_line == "disabled"
    assert outcome.comment == "disabled"
    assert outcome.email == "disabled"
    assert await _ledger_actions(db_session, decision.id) == set()


def test_jen_emails_reads_both_addresses_from_settings() -> None:
    assert writeback.jen_emails() == ("jen@justrightstrategy.com", "jen@digigeeks.com")
