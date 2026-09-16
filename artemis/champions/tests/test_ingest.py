"""Ingest behaviour that must hold regardless of what the model says."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from artemis.champions.classify import Classification
from artemis.champions.ingest import classify_pending
from artemis.champions.models import ChampionsItem


def _item(**kw: object) -> ChampionsItem:
    base = dict(
        external_id="d-1",
        item_type="discussion",
        posted_at=datetime(2026, 9, 1, tzinfo=UTC),
        is_amira_staff=False,
        title=None,
        body="<p>hello</p>",
    )
    base.update(kw)
    return ChampionsItem(**base)


@pytest.mark.asyncio
async def test_amira_staff_posts_are_never_flagged(db_session, monkeypatch) -> None:
    """One of the original bugs: "Monthly News" tripped the `news` keyword.

    The model is deliberately fed the post anyway and its answer overridden, so
    the exclusion holds even if a future prompt starts flagging staff content.
    """
    row = _item(external_id="d-staff", is_amira_staff=True, body="<p>Amira is broken</p>")
    db_session.add(row)
    await db_session.flush()

    async def fake(**_kw: object) -> Classification:
        return Classification("staff post", "Product problem", True, True)

    monkeypatch.setattr("artemis.champions.ingest.classify_item", fake)
    classified, flagged, _, errors = await classify_pending(db_session)

    assert classified == 1 and errors == []
    assert row.product_issue is False, "staff post was flagged"
    assert row.adoption_friction is False
    assert flagged == 0


@pytest.mark.asyncio
async def test_classifier_failure_leaves_the_row_for_the_next_run(db_session, monkeypatch) -> None:
    """An unclassified row is retried. A row lost to an exception is not."""
    row = _item(external_id="d-fail")
    db_session.add(row)
    await db_session.flush()

    async def boom(**_kw: object) -> Classification:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("artemis.champions.ingest.classify_item", boom)
    classified, _, _, errors = await classify_pending(db_session)

    assert classified == 0
    assert any("d-fail" in e and "provider exploded" in e for e in errors)
    assert row.classified_at is None, "a failed item must stay in the work queue"

    fresh = (
        (
            await db_session.execute(
                select(ChampionsItem).where(ChampionsItem.classified_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert row.external_id in {r.external_id for r in fresh}


@pytest.mark.asyncio
async def test_tripwire_runs_even_when_the_model_fails(db_session, monkeypatch) -> None:
    """The escalation path is the safety net; it must not depend on the LLM."""
    row = _item(external_id="d-esc", body="<p>I have a privacy concern here</p>")
    db_session.add(row)
    await db_session.flush()

    async def boom(**_kw: object) -> Classification:
        raise RuntimeError("down")

    monkeypatch.setattr("artemis.champions.ingest.classify_item", boom)
    _, _, escalated, _ = await classify_pending(db_session)

    assert escalated == 1
    assert row.escalation is True
    assert row.escalation_terms == ["privacy concern"]


@pytest.mark.asyncio
async def test_unparseable_reply_is_not_stored_as_a_judgment(db_session, monkeypatch) -> None:
    row = _item(external_id="d-none")
    db_session.add(row)
    await db_session.flush()

    async def unparseable(**_kw: object) -> None:
        return None

    monkeypatch.setattr("artemis.champions.ingest.classify_item", unparseable)
    classified, flagged, _, _ = await classify_pending(db_session)

    assert classified == 0 and flagged == 0
    assert row.product_issue is None, "absence of a judgment must not read as False"
    assert row.summary is None


@pytest.mark.asyncio
async def test_textless_item_is_marked_seen_not_judged(db_session, monkeypatch) -> None:
    """An embedded-image post can never be classified.

    Left alone it sits in the work queue forever, retried on every run, and
    image posts are normal here so that set only grows. It is marked as seen so
    the churn stops -- but the flags stay NULL, because "we could not read it"
    must never be stored as "we looked and found no problem".
    """
    row = _item(
        external_id="c-image",
        title=None,
        body='<span class="embedExternal" data-embedjson="{}"></span>',
    )
    db_session.add(row)
    await db_session.flush()

    async def unparseable(**_kw: object) -> None:
        return None

    monkeypatch.setattr("artemis.champions.ingest.classify_item", unparseable)
    classified, flagged, _, errors = await classify_pending(db_session)

    assert classified == 0 and flagged == 0 and errors == []
    assert row.classified_at is not None, "should leave the retry queue"
    assert row.classifier_model == "skipped:no-text"
    assert row.summary is None
    assert row.product_issue is None, "unreadable must not read as 'no problem'"


@pytest.mark.asyncio
async def test_item_with_text_that_fails_to_parse_stays_in_the_queue(
    db_session, monkeypatch
) -> None:
    """The skip applies only to items with nothing to read. A real post whose
    reply was unparseable is a transient failure and must be retried."""
    row = _item(external_id="c-real", body="<p>Amira stopped loading this morning</p>")
    db_session.add(row)
    await db_session.flush()

    async def unparseable(**_kw: object) -> None:
        return None

    monkeypatch.setattr("artemis.champions.ingest.classify_item", unparseable)
    await classify_pending(db_session)

    assert row.classified_at is None
    assert row.classifier_model is None
