"""CCA10 -- link a thread-attached image into Jen's doc.

Covers every item in ``briefs/cca10-slack-image-to-doc.md`` "Tests" section.
Slack and the Google Docs API are ALWAYS mocked here -- this suite never
hits the live document or a real Slack workspace, per the brief's explicit
instruction and CRITICAL CONSTRAINT 1 (no file download, no Drive upload,
no ``insertInlineImage``; ``chat.getPermalink`` only, mocked).

Three layers are tested:

1. Pure rendering (``render_image_link_line``) -- singular vs. plural
   wording, no DB, no mocks.
2. ``deliver_image_link`` end to end, with ``chat.getPermalink`` / the Docs
   API (``writeback._fetch_document``/``writeback._insert_text``, reused
   verbatim from CCA7) / the owner alert monkeypatched at the
   ``artemis.crisis_content.image_link`` module boundary, and DB reads/
   writes (the idempotency ledger, the note and card rows) going through
   the real Postgres test database.
3. The trigger wiring in ``artemis.crisis_content.thread_notes`` -- that a
   reply with an attachment schedules image-link delivery for the RIGHT
   note id, and a reply without one schedules nothing.

Engine/fixture strategy mirrors ``tests/test_crisis_content_writeback.py``:
a per-test engine bound to ``ARTEMIS_TEST_DB_URL`` (or ``ARTEMIS_DB_URL``),
with a hard refusal to run against anything that isn't a test database, and
a TRUNCATE before each test for isolation.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.crisis_content import image_link, thread_notes, writeback
from artemis.crisis_content.image_link import (
    CrisisContentImageLinkDelivery,
    deliver_image_link,
    render_image_link_line,
)
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentThreadNote,
)
from artemis.crisis_content.transitions import mark_notified
from artemis.db import attach_pgvector_codec
from artemis.integrations.slack.client import SlackAPIError

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` -- mirrors
# test_crisis_content_writeback.py; asyncio_mode = "auto" (pyproject.toml)
# already collects `async def test_*` correctly without the marker.

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "TRUNCATE on the live DB would destroy production data."
    )

_TRUNCATE = text(
    "TRUNCATE crisis_content_image_link_deliveries, crisis_content_thread_notes, "
    "crisis_content_writeback_deliveries, crisis_content_decisions, "
    "crisis_content_notifications, crisis_content_copy_versions, crisis_content_cards "
    "RESTART IDENTITY CASCADE"
)

_CHANNEL = "C0BM9TL63TL"
_THREAD_TS = "1700000000.000100"


@pytest.fixture(autouse=True)
def _image_link_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the CCA10 kill switch for this module's tests.

    ``crisis_content_image_link_enabled`` ships defaulting to **False**
    (see ``artemis/config.py``) -- turning it on means writing into an
    external vendor's live document, an explicit owner decision. These
    tests exercise the enabled behaviour, so they enable it explicitly
    rather than inheriting the production default. Mirrors
    ``test_crisis_content_writeback.py``'s ``_writeback_enabled`` fixture,
    including the "later monkeypatch wins" override used by
    ``test_disabled_via_settings_does_nothing_and_touches_nothing`` below.
    """
    from artemis.config import settings

    monkeypatch.setattr(settings, "crisis_content_image_link_enabled", True)


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


# ── fixture builders: Docs API JSON (includeTabsContent=true shape) --
# duplicated from test_crisis_content_writeback.py rather than imported,
# per that file's own precedent of not sharing fixtures across
# crisis-content test modules. ──────────────────────────────────────────────


def _para(text_: str, end_index: int = 1) -> dict[str, Any]:
    return {"endIndex": end_index, "paragraph": {"elements": [{"textRun": {"content": text_}}]}}


def _copy_hash(lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _card_table(*, header: str, copy_lines: list[str], status_end_index: int = 500) -> dict[str, Any]:
    return {
        "tableRows": [
            {"tableCells": [{"content": [_para(header)]}]},
            {
                "tableCells": [
                    {
                        "content": [
                            _para("Platform:"),
                            _para("Asset for review - LINK"),
                            _para(""),
                            _para("Copy review"),
                            _para("", end_index=status_end_index),
                        ]
                    },
                    {"content": [_para(line) for line in copy_lines]},
                ]
            },
        ]
    }


def _decoy_table(header: str = "Strategy Plan notes") -> dict[str, Any]:
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
    """Stub token resolution + fetch/insert. See writeback's own docstring.

    Patches ``writeback._fetch_document``/``writeback._insert_text``
    (``image_link.write_doc_line`` is the SAME function object as
    ``writeback.write_doc_line``, which calls those two names bound in
    ``writeback``'s own module namespace -- patching them there affects
    every caller, this module included) and ``image_link._resolve_docs_access_token``
    (image_link's own, independent credential resolution -- see that
    module's docstring for why it is not a shared import of writeback's).
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

    monkeypatch.setattr(image_link, "_resolve_docs_access_token", fake_token)
    monkeypatch.setattr(writeback, "_fetch_document", fake_fetch)
    monkeypatch.setattr(writeback, "_insert_text", fake_insert)


def _patch_alert(monkeypatch: pytest.MonkeyPatch, alerts: list[str]) -> None:
    async def fake_alert(session: AsyncSession, text_: str) -> None:
        alerts.append(text_)

    monkeypatch.setattr(image_link, "_alert_jon", fake_alert)


class _FakeSlackClient:
    """Fake ``image_link.SlackClient`` -- permalinks + confirmation replies only.

    ``get_permalink`` returns a canned permalink keyed by ``message_ts`` so
    a test can assert the permalink used was for the REPLY's own ts, not
    the parent card's -- CRITICAL CONSTRAINT 6.
    """

    instances: list[_FakeSlackClient] = []

    def __init__(
        self,
        token: str,
        *,
        permalinks: dict[str, str] | None = None,
        permalink_error: Exception | None = None,
    ) -> None:
        self.token = token
        self._permalinks = permalinks or {}
        self._permalink_error = permalink_error
        self.permalink_calls: list[tuple[str, str]] = []
        self.message_calls: list[tuple[str, str, str | None]] = []
        _FakeSlackClient.instances.append(self)

    async def get_permalink(self, channel: str, message_ts: str) -> str:
        self.permalink_calls.append((channel, message_ts))
        if self._permalink_error is not None:
            raise self._permalink_error
        return self._permalinks.get(message_ts, f"https://amira.slack.com/archives/{channel}/p{message_ts}")

    async def post_message(
        self, channel: str, text: str, thread_ts: str | None = None, blocks: list[object] | None = None
    ) -> dict[str, object]:
        self.message_calls.append((channel, text, thread_ts))
        return {"ok": True}


def _patch_slack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    permalinks: dict[str, str] | None = None,
    permalink_error: Exception | None = None,
) -> type[_FakeSlackClient]:
    """Patch ``image_link.SlackClient``/``_resolve_agent_slack_config``.

    ``deliver_image_link`` constructs its own ``SlackClient`` instance
    internally (it isn't handed one), so a test reads the call back via
    ``_FakeSlackClient.instances[-1]`` (the class this returns) rather than
    a reference captured before the call.
    """
    _FakeSlackClient.instances = []

    def _make(token: str) -> _FakeSlackClient:
        return _FakeSlackClient(token, permalinks=permalinks, permalink_error=permalink_error)

    async def fake_resolve_agent_slack_config(
        session: AsyncSession, *, agent_id: str, team_id: str | None = None
    ) -> object:
        assert agent_id == "callie"
        return SimpleNamespace(access_token="fake-callie-token")

    monkeypatch.setattr(image_link, "SlackClient", _make)
    monkeypatch.setattr(image_link, "_resolve_agent_slack_config", fake_resolve_agent_slack_config)
    return _FakeSlackClient


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


async def _seed_card_with_notification(
    db_session: AsyncSession,
    *,
    header: str = "August 11, 2026 - Welcome Back blog",
    copy_lines: list[str] | None = None,
    channel_id: str = _CHANNEL,
    message_ts: str = _THREAD_TS,
) -> int:
    """A card posted to Slack -- the state a thread reply arrives against."""
    card_id, copy_hash = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    await mark_notified(
        db_session,
        card_id,
        "copy",
        "Ready",
        copy_hash=copy_hash,
        channel_id=channel_id,
        message_ts=message_ts,
    )
    await db_session.commit()
    return card_id


async def _seed_note(
    db_session: AsyncSession,
    *,
    card_id: int,
    message_ts: str,
    channel_id: str = _CHANNEL,
    thread_ts: str = _THREAD_TS,
    has_attachment: bool = True,
    file_count: int = 1,
    slack_user_id: str = "U_ANGELA",
    author_email: str | None = "angela.miata@amiralearning.com",
    text_: str = "see the attached mockup",
) -> int:
    async with db_session.begin():
        row = CrisisContentThreadNote(
            card_id=card_id,
            route="copy",
            slack_user_id=slack_user_id,
            author_email=author_email,
            text=text_,
            has_attachment=has_attachment,
            channel_id=channel_id,
            file_count=file_count,
            message_ts=message_ts,
            thread_ts=thread_ts,
        )
        db_session.add(row)
        await db_session.flush()
        note_id = row.id
    return note_id


async def _ledger_rows(db_session: AsyncSession) -> set[int]:
    result = await db_session.execute(select(CrisisContentImageLinkDelivery.thread_note_id))
    return set(result.scalars().all())


# ── render_image_link_line: pure rendering ──────────────────────────────────


def test_render_image_link_line_singular_says_asset_not_assets() -> None:
    line = render_image_link_line(
        poster_label="angela.miata@amiralearning.com",
        posted_at=datetime(2026, 8, 11, 20, 52, tzinfo=UTC),
        permalink="https://amira.slack.com/archives/C0BM9TL63TL/p1700000000000100",
        file_count=1,
    )
    assert "🖼" in line
    assert "Asset in Slack" in line
    assert "Assets in Slack" not in line
    assert "angela.miata@amiralearning.com" in line
    assert "Aug 11" in line
    assert "https://amira.slack.com/archives/C0BM9TL63TL/p1700000000000100" in line


def test_render_image_link_line_plural_includes_the_count() -> None:
    line = render_image_link_line(
        poster_label="angela.miata@amiralearning.com",
        posted_at=datetime(2026, 8, 11, 20, 52, tzinfo=UTC),
        permalink="https://amira.slack.com/archives/C0BM9TL63TL/p1700000000000300",
        file_count=3,
    )
    assert "Assets in Slack" in line
    assert "(3 images)" in line


# ── deliver_image_link: end to end ──────────────────────────────────────────


async def test_attachment_note_delivers_permalink_line_and_one_confirmation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["This is the copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    doc = _document({"t1": [_decoy_table(), _card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    client = _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "delivered"
    assert len(insert_calls) == 1
    line_text = insert_calls[0]["text"].lstrip("\n")
    assert "🖼" in line_text
    assert "Asset in Slack" in line_text
    assert len(client.instances[-1].message_calls) == 1
    assert client.instances[-1].message_calls[0][2] == _THREAD_TS  # confirmation replied in the card's thread
    assert await _ledger_rows(db_session) == {note_id}


async def test_second_delivery_of_same_note_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    client = _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    first = await deliver_image_link(db_session, note_id)
    second = await deliver_image_link(db_session, note_id)

    assert first == "delivered"
    assert second == "already_delivered"
    assert len(insert_calls) == 1  # NOT two lines
    assert len(client.instances[-1].message_calls) == 1  # NOT two confirmations


async def test_note_without_attachment_delivers_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    card_id, _ = await _seed_card(db_session, header=header)
    note_id = await _seed_note(
        db_session,
        card_id=card_id,
        message_ts="1700000000.000210",
        has_attachment=False,
        file_count=0,
        text_="looks good, no attachment here",
    )

    doc = _document({"t1": [_card_table(header=header, copy_lines=["Default copy body line one."])]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    client = _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "skipped"
    assert insert_calls == []
    # Never even constructs a SlackClient -- the no-attachment check short-
    # circuits before Slack config resolution.
    assert client.instances == []
    assert await _ledger_rows(db_session) == set()


async def test_two_replies_with_attachments_produce_two_lines_in_order(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_a = await _seed_note(
        db_session,
        card_id=card_id,
        message_ts="1700000000.000210",
        author_email="angela.miata@amiralearning.com",
    )
    note_b = await _seed_note(
        db_session,
        card_id=card_id,
        message_ts="1700000000.000220",
        author_email="hannah.slater@amiralearning.com",
    )

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    first = await deliver_image_link(db_session, note_a)
    second = await deliver_image_link(db_session, note_b)

    assert first == "delivered"
    assert second == "delivered"
    assert len(insert_calls) == 2
    assert "angela.miata@amiralearning.com" in insert_calls[0]["text"]
    assert "hannah.slater@amiralearning.com" in insert_calls[1]["text"]
    assert await _ledger_rows(db_session) == {note_a, note_b}


async def test_one_reply_with_three_files_produces_one_line_with_count(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(
        db_session, card_id=card_id, message_ts="1700000000.000210", file_count=3
    )

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "delivered"
    assert len(insert_calls) == 1
    assert "(3 images)" in insert_calls[0]["text"]


async def test_permalink_failure_writes_nothing_no_ledger_row_retry_succeeds(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_slack(monkeypatch, permalink_error=SlackAPIError("chat.getPermalink", "ratelimited"))
    _patch_alert(monkeypatch, [])

    with caplog.at_level("ERROR"):
        outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "failed"
    assert insert_calls == []  # no doc write
    assert await _ledger_rows(db_session) == set()  # no ledger row
    assert any("chat.getPermalink failed" in record.message for record in caplog.records)

    # Retry, this time Slack cooperates -- must still work (nothing about
    # the first failure blocked a later, successful attempt).
    _patch_slack(monkeypatch)
    retry_outcome = await deliver_image_link(db_session, note_id)
    assert retry_outcome == "delivered"
    assert len(insert_calls) == 1


async def test_card_not_locatable_writes_nothing_logs_error_alerts_jon(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    card_id, _ = await _seed_card(db_session, header=header)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    # The live doc no longer has any table with this header.
    doc = _document({"t1": [_decoy_table(), _card_table(header="A totally different post", copy_lines=["x"])]})
    insert_calls: list[dict[str, Any]] = []
    alerts: list[str] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    client = _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("ERROR"):
        outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "not_located"
    assert insert_calls == []
    assert client.instances[-1].message_calls == []  # no confirmation reply either
    assert any("not positively identified" in record.message for record in caplog.records)
    assert len(alerts) == 1
    assert "could not positively identify" in alerts[0].lower()
    assert await _ledger_rows(db_session) == set()  # retry still possible


async def test_post_write_verification_detects_changed_card_count_and_alerts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

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

    monkeypatch.setattr(image_link, "_resolve_docs_access_token", fake_token)
    monkeypatch.setattr(writeback, "_fetch_document", fake_fetch)
    monkeypatch.setattr(writeback, "_insert_text", fake_insert)
    _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("CRITICAL"):
        outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "damaged"
    assert len(insert_calls) == 1  # the write itself happened exactly once
    assert any("VERIFICATION FAILED" in record.message for record in caplog.records)
    assert len(alerts) == 1
    assert "verification" in alerts[0].lower()
    assert "do not" in alerts[0].lower()
    # Marked delivered anyway -- a retry must NOT attempt a second, possibly
    # compounding insert into a document that may already be damaged.
    assert await _ledger_rows(db_session) == {note_id}

    retry = await deliver_image_link(db_session, note_id)
    assert retry == "already_delivered"
    assert len(insert_calls) == 1  # still exactly one -- no cleanup/retry write


async def test_permalink_uses_replys_own_ts_not_parent_cards(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL CONSTRAINT 6: the permalink must target the reply's ts."""
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id = await _seed_card_with_notification(
        db_session, header=header, copy_lines=copy_lines, message_ts=_THREAD_TS
    )
    reply_ts = "1700000000.000999"
    note_id = await _seed_note(db_session, card_id=card_id, message_ts=reply_ts, thread_ts=_THREAD_TS)

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    client = _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    await deliver_image_link(db_session, note_id)

    assert client.instances[-1].permalink_calls == [(_CHANNEL, reply_ts)]
    assert reply_ts != _THREAD_TS
    # And the confirmation reply still lands in the THREAD (thread_ts), not
    # as a reply to itself.
    assert client.instances[-1].message_calls[0][2] == _THREAD_TS


async def test_multi_tab_doc_writes_to_correct_tab(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body only on tab two."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    doc = _document(
        {
            "t1": [_card_table(header="A different post entirely", copy_lines=["unrelated"])],
            "t2": [_card_table(header=header, copy_lines=copy_lines)],
        }
    )
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_slack(monkeypatch)
    _patch_alert(monkeypatch, [])

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "delivered"
    assert len(insert_calls) == 1
    assert insert_calls[0]["tab_id"] == "t2"


async def test_disabled_via_settings_does_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.config import settings

    monkeypatch.setattr(settings, "crisis_content_image_link_enabled", False)

    header = "August 11, 2026 - Welcome Back blog"
    card_id, _ = await _seed_card(db_session, header=header)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000210")

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "disabled"
    assert await _ledger_rows(db_session) == set()


# ── Audit findings: the four early "failed" branches never alert Jon ───────
#
# CrisisContentThreadNote's own ORM docstring (artemis/crisis_content/orm.py)
# says channel_id/file_count are "nullable/defaulted because rows written
# before CCA10 have neither" -- a real, disclosed legacy-row shape. And the
# design doc's "Failure modes" section is explicit that every failure in this
# pipeline "must be loud" -- Jon gets a Slack DM, never just a log line.
# `deliver_image_link` alerts Jon on the four LATER failure branches
# (CardNotLocatedError, WritebackVerificationError, credential-unavailable,
# HTTP error -- all reused from writeback.py, which alerts on every one of
# its own failure branches) but NOT on the four EARLIER ones: a vanished
# note, a missing channel_id (the exact CCA10 legacy shape above), no active
# Callie Slack token, and a chat.getPermalink failure. These three tests
# document that gap with the real production shape in each case, so it is
# visible rather than silently accepted. See the audit report for the
# recommendation; nothing here is "fixed" -- these tests pin CURRENT
# behaviour so the gap doesn't get bigger without someone noticing.


async def test_legacy_note_with_null_channel_id_fails_silently_no_jon_alert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A pre-CCA10 thread note has ``has_attachment=True`` but ``channel_id
    IS NULL`` -- the column did not exist yet when the row was written (see
    ``CrisisContentThreadNote``'s own docstring). Nothing calls
    ``deliver_image_link`` for an old note today, but if a future backfill
    or manual replay ever does, this is what happens: a log line only, no
    ``_alert_jon`` call, unlike every other failure branch in this function.
    """
    header = "August 11, 2026 - Welcome Back blog"
    card_id, _ = await _seed_card(db_session, header=header)
    note_id = await _seed_note(
        db_session, card_id=card_id, message_ts="1700000000.000210", channel_id=None
    )
    alerts: list[str] = []
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("ERROR"):
        outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "failed"
    assert any("no channel_id recorded" in record.message for record in caplog.records)
    assert alerts == [], (
        "deliver_image_link does not alert Jon for a missing channel_id -- "
        "this pins that gap; if it starts alerting, update this assertion"
    )
    assert await _ledger_rows(db_session) == set()  # not marked delivered -- retriable


async def test_no_active_callie_token_fails_silently_no_jon_alert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Callie's Slack integration has no active token (revoked, uninstalled,
    or never connected). No doc write is attempted -- but also no alert,
    unlike a credential failure on the DOC side of this same function.
    """
    header = "August 11, 2026 - Welcome Back blog"
    card_id, _ = await _seed_card(db_session, header=header)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000211")

    async def fake_resolve_agent_slack_config(
        session: AsyncSession, *, agent_id: str, team_id: str | None = None
    ) -> object:
        return SimpleNamespace(access_token="")

    monkeypatch.setattr(
        image_link, "_resolve_agent_slack_config", fake_resolve_agent_slack_config
    )
    alerts: list[str] = []
    _patch_alert(monkeypatch, alerts)

    with caplog.at_level("ERROR"):
        outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "failed"
    assert any("no active Slack token" in record.message for record in caplog.records)
    assert alerts == [], (
        "deliver_image_link does not alert Jon when Callie has no active "
        "token -- this pins that gap; if it starts alerting, update this "
        "assertion"
    )


async def test_permalink_failure_also_sends_no_jon_alert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to ``test_permalink_failure_writes_nothing_no_ledger_row_
    retry_succeeds`` above, which already exercises this branch but never
    asserts on ``alerts`` either way. A PERSISTENT ``chat.getPermalink``
    failure (Callie loses channel access; the message is deleted) means
    images silently never get linked, forever, with nothing surfacing to Jon
    beyond a log line each poll -- the same "looks healthy while doing
    nothing" shape the rest of this pipeline explicitly alerts on.
    """
    header = "August 11, 2026 - Welcome Back blog"
    copy_lines = ["Copy body."]
    card_id, _ = await _seed_card(db_session, header=header, copy_lines=copy_lines)
    note_id = await _seed_note(db_session, card_id=card_id, message_ts="1700000000.000212")

    doc = _document({"t1": [_card_table(header=header, copy_lines=copy_lines)]})
    insert_calls: list[dict[str, Any]] = []
    _patch_docs_api(monkeypatch, doc, insert_calls)
    _patch_slack(monkeypatch, permalink_error=SlackAPIError("chat.getPermalink", "ratelimited"))
    alerts: list[str] = []
    _patch_alert(monkeypatch, alerts)

    outcome = await deliver_image_link(db_session, note_id)

    assert outcome == "failed"
    assert alerts == [], (
        "deliver_image_link does not alert Jon when chat.getPermalink keeps "
        "failing -- this pins that gap; if it starts alerting, update this "
        "assertion"
    )


# ── Structural regression guards -- CRITICAL CONSTRAINTS 1 & 2 in code ──────


_SIDE_EFFECTING_FUNCTIONS = (
    image_link.deliver_image_link,
    image_link.schedule_image_link_delivery,
    image_link._run_image_link_background,
)


def test_module_never_downloads_a_file_or_uploads_to_drive() -> None:
    """No ``files:read``/``url_private`` fetch, no Drive upload, no image embed.

    Static guard on the CODE of the functions that actually make network
    calls (not the module's docstring, which legitimately explains this
    constraint in prose -- mirrors the same distinction
    ``test_crisis_content_lifecycle.py`` draws for CCA9's own module).
    """
    source = "".join(inspect.getsource(fn) for fn in _SIDE_EFFECTING_FUNCTIONS)
    for forbidden in ("url_private", "files:read", "insertInlineImage", "drive/v3/files"):
        assert forbidden not in source, f"found forbidden reference to {forbidden!r}"


def test_module_never_issues_a_delete_or_replace_request() -> None:
    """CRITICAL CONSTRAINT 2: insert only, anywhere in this module."""
    source = inspect.getsource(image_link)
    for forbidden in ("deleteContentRange", "replaceAllText", "deleteText"):
        assert forbidden not in source, f"found forbidden reference to {forbidden!r}"


# ── Wiring: thread_notes.py triggers scheduling correctly (CCA10 extends CCA9) ──


async def test_reply_with_attachment_schedules_image_link_for_the_right_note(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[int] = []

    def fake_schedule(thread_note_id: int) -> None:
        scheduled.append(thread_note_id)

    posted: list[tuple[str, str, str | None]] = []

    class _FakeNudgeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def post_message(
            self, channel: str, text: str, thread_ts: str | None = None, blocks: list[object] | None = None
        ) -> dict[str, object]:
            posted.append((channel, text, thread_ts))
            return {"ok": True}

    monkeypatch.setattr(thread_notes, "schedule_image_link_delivery", fake_schedule)
    monkeypatch.setattr(thread_notes, "SlackClient", _FakeNudgeSlackClient)

    card_id = await _seed_card_with_notification(db_session, message_ts=_THREAD_TS)

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts="1700000000.000210",
        slack_user_id="U_ANGELA",
        text="see the attached mockup",
        has_files=True,
        access_token="xoxb-fake",
        file_count=1,
    )
    assert handled is True

    note = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalar_one()
    assert note.channel_id == _CHANNEL
    assert note.file_count == 1
    assert scheduled == [note.id]


async def test_reply_without_attachment_schedules_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(thread_notes, "schedule_image_link_delivery", lambda note_id: scheduled.append(note_id))

    posted: list[tuple[str, str, str | None]] = []

    class _FakeNudgeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def post_message(
            self, channel: str, text: str, thread_ts: str | None = None, blocks: list[object] | None = None
        ) -> dict[str, object]:
            posted.append((channel, text, thread_ts))
            return {"ok": True}

    monkeypatch.setattr(thread_notes, "SlackClient", _FakeNudgeSlackClient)

    await _seed_card_with_notification(db_session, message_ts=_THREAD_TS)

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts="1700000000.000211",
        slack_user_id="U_ANGELA",
        text="looks good, no attachment",
        has_files=False,
        access_token="xoxb-fake",
    )
    assert handled is True
    assert scheduled == []


async def test_retried_reply_with_attachment_schedules_the_same_existing_note(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Slack retry of the same reply must resolve to the ORIGINAL note's id,
    not fail to schedule anything -- image_link's own ledger (keyed on
    thread_note_id) is what keeps this from ever producing a second line.
    """
    scheduled: list[int] = []
    monkeypatch.setattr(thread_notes, "schedule_image_link_delivery", lambda note_id: scheduled.append(note_id))

    class _FakeNudgeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def post_message(
            self, channel: str, text: str, thread_ts: str | None = None, blocks: list[object] | None = None
        ) -> dict[str, object]:
            return {"ok": True}

    monkeypatch.setattr(thread_notes, "SlackClient", _FakeNudgeSlackClient)

    await _seed_card_with_notification(db_session, message_ts=_THREAD_TS)

    kwargs = dict(
        channel_id=_CHANNEL,
        thread_ts=_THREAD_TS,
        message_ts="1700000000.000212",
        slack_user_id="U_ANGELA",
        text="see the attached mockup",
        has_files=True,
        access_token="xoxb-fake",
        file_count=1,
    )
    await thread_notes.maybe_handle_thread_reply(db_session, **kwargs)  # type: ignore[arg-type]
    await thread_notes.maybe_handle_thread_reply(db_session, **kwargs)  # type: ignore[arg-type]

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.message_ts == "1700000000.000212")
        )
    ).scalars().all()
    assert len(notes) == 1  # ON CONFLICT DO NOTHING -- still just one row
    assert scheduled == [notes[0].id, notes[0].id]  # scheduled both times, same note id
