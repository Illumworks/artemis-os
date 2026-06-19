"""Tests for the enablement ingest webhook + Kai's surfacing-aware retrieval.

Covers:
  - fail-closed auth (disabled secret, wrong/missing token)
  - upsert + idempotency (re-run does not duplicate)
  - multi-link asset round-trips (links JSON, requires_copy, default drive_link)
  - full_refresh soft-archive (supersede, never DELETE) of removed rows
  - search excludes archived rows and surfaces the labeled links array
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

import artemis.db as _db
from artemis.enablement.models import EnablementAsset
from artemis.routes import enablement as enablement_mod

_SOURCE = "test_training_decks"


def _asset(key: str, title: str) -> dict:
    return {
        "key": key,
        "asset_type": "training_deck",
        "title": title,
        "summary": "Getting started training for new Assess teachers",
        "audience": "Teacher",
        "tags": ["Getting Started", "Assess", "Teacher", "New"],
        "searchable_text": "covers letter naming, blending, el konin sound box",
        "links": [
            {
                "role": "deck",
                "label": "Deck (make a copy)",
                "url": "https://docs.google.com/presentation/d/DECK/edit",
                "visibility": "customer",
                "on_request": False,
                "make_copy": True,
            },
            {
                "role": "handout_customer",
                "label": "Customer handout",
                "url": "https://example.com/customer-handout",
                "visibility": "customer",
                "on_request": False,
                "make_copy": False,
            },
            {
                "role": "handout_editable",
                "label": "Editable handout (internal)",
                "url": "https://docs.google.com/document/d/EDIT/edit",
                "visibility": "internal",
                "on_request": True,
                "make_copy": True,
            },
        ],
        "requires_copy": True,
        "source_row": "3",
    }


@pytest.fixture(autouse=True)
async def _clean_and_secret(monkeypatch):
    """Set a known webhook secret and clean the test source rows around each test.

    ``engine.dispose()`` top and bottom resets the asyncpg pool so connections
    don't span event loops (matches the suite's other DB-touching tests; avoids
    the "Event loop is closed" teardown noise).
    """
    monkeypatch.setattr(enablement_mod.settings, "enablement_webhook_secret", "test-secret")
    await _db.engine.dispose()
    async with _db.SessionLocal() as session:
        await session.execute(
            delete(EnablementAsset).where(EnablementAsset.source_sheet == _SOURCE)
        )
        await session.commit()
    yield
    await _db.engine.dispose()


async def test_ingest_disabled_when_secret_unset(client, monkeypatch):
    monkeypatch.setattr(enablement_mod.settings, "enablement_webhook_secret", "")
    resp = await client.post(
        "/api/enablement/ingest",
        json={"source_sheet": _SOURCE, "assets": []},
        headers={"X-Enablement-Token": "anything"},
    )
    assert resp.status_code == 503


async def test_ingest_rejects_missing_or_wrong_token(client):
    body = {"source_sheet": _SOURCE, "assets": []}
    assert (await client.post("/api/enablement/ingest", json=body)).status_code == 401
    resp = await client.post(
        "/api/enablement/ingest", json=body, headers={"X-Enablement-Token": "nope"}
    )
    assert resp.status_code == 401


async def test_ingest_upsert_idempotent_and_multilink(client):
    body = {
        "source_sheet": _SOURCE,
        "full_refresh": True,
        "assets": [_asset(f"{_SOURCE}:3", "Getting Started (Assess, New Teacher)")],
    }
    headers = {"X-Enablement-Token": "test-secret"}

    r1 = await client.post("/api/enablement/ingest", json=body, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["upserted"] == 1

    # Re-run: same key must update, not duplicate.
    r2 = await client.post("/api/enablement/ingest", json=body, headers=headers)
    assert r2.status_code == 200

    async with _db.SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(EnablementAsset).where(EnablementAsset.source_sheet == _SOURCE)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.drive_file_id == f"{_SOURCE}:3"
    assert row.requires_copy is True
    assert isinstance(row.links, list) and len(row.links) == 3
    # default drive_link = first customer / non-on_request link (the deck)
    assert row.drive_link == "https://docs.google.com/presentation/d/DECK/edit"
    # the internal editable handout is preserved with its flags
    internal = [link for link in row.links if link["visibility"] == "internal"]
    assert internal and internal[0]["on_request"] is True


async def test_full_refresh_soft_archives_removed_rows(client):
    headers = {"X-Enablement-Token": "test-secret"}
    # First load A + B.
    await client.post(
        "/api/enablement/ingest",
        json={
            "source_sheet": _SOURCE,
            "full_refresh": True,
            "assets": [_asset(f"{_SOURCE}:A", "Deck A"), _asset(f"{_SOURCE}:B", "Deck B")],
        },
        headers=headers,
    )
    # Re-load with only A (B removed from the sheet).
    r = await client.post(
        "/api/enablement/ingest",
        json={
            "source_sheet": _SOURCE,
            "full_refresh": True,
            "assets": [_asset(f"{_SOURCE}:A", "Deck A")],
        },
        headers=headers,
    )
    assert r.json()["archived"] == 1

    async with _db.SessionLocal() as session:
        by_key = {
            row.drive_file_id: row
            for row in (
                await session.execute(
                    select(EnablementAsset).where(EnablementAsset.source_sheet == _SOURCE)
                )
            )
            .scalars()
            .all()
        }
    # B is soft-archived (supersession), NOT deleted — both rows still present.
    assert by_key[f"{_SOURCE}:A"].status == "active"
    assert by_key[f"{_SOURCE}:B"].status == "archived"


async def test_blank_strings_stored_as_null(client):
    """Empty / whitespace-only optional strings must be coerced to NULL on ingest."""
    headers = {"X-Enablement-Token": "test-secret"}
    body = {
        "source_sheet": _SOURCE,
        "assets": [
            {
                "key": f"{_SOURCE}:blank",
                "title": "Blank Field Asset",
                "audience": "",  # empty string -> NULL
                "summary": "   ",  # whitespace-only -> NULL
                "asset_type": "training_deck",
                "source_row": "",  # empty -> NULL
            }
        ],
    }
    r = await client.post("/api/enablement/ingest", json=body, headers=headers)
    assert r.status_code == 200, r.text

    async with _db.SessionLocal() as session:
        row = (
            await session.execute(
                select(EnablementAsset).where(EnablementAsset.drive_file_id == f"{_SOURCE}:blank")
            )
        ).scalar_one()

    assert row.audience is None, f"expected NULL audience, got {row.audience!r}"
    assert row.summary is None, f"expected NULL summary, got {row.summary!r}"
    assert row.source_row is None, f"expected NULL source_row, got {row.source_row!r}"
    # title and asset_type were non-empty; must be kept
    assert row.title == "Blank Field Asset"
    assert row.type == "training_deck"


async def test_non_empty_strings_preserved_and_trimmed(client):
    """Non-empty values must survive; leading/trailing whitespace must be trimmed."""
    headers = {"X-Enablement-Token": "test-secret"}
    body = {
        "source_sheet": _SOURCE,
        "assets": [
            {
                "key": f"{_SOURCE}:trim",
                "title": "  Trim Me  ",
                "audience": "  Teacher  ",
                "summary": "A real summary",
            }
        ],
    }
    r = await client.post("/api/enablement/ingest", json=body, headers=headers)
    assert r.status_code == 200, r.text

    async with _db.SessionLocal() as session:
        row = (
            await session.execute(
                select(EnablementAsset).where(EnablementAsset.drive_file_id == f"{_SOURCE}:trim")
            )
        ).scalar_one()

    assert row.title == "Trim Me"
    assert row.audience == "Teacher"
    assert row.summary == "A real summary"


async def test_search_excludes_archived_and_surfaces_links(client):
    import json

    from artemis.enablement.tools import _search_enablement_assets

    headers = {"X-Enablement-Token": "test-secret"}
    await client.post(
        "/api/enablement/ingest",
        json={
            "source_sheet": _SOURCE,
            "full_refresh": True,
            "assets": [
                _asset(f"{_SOURCE}:keep", "El Konin Blending Deck"),
                _asset(f"{_SOURCE}:gone", "El Konin Blending Deck OLD"),
            ],
        },
        headers=headers,
    )
    # Archive the OLD one via full_refresh dropping it.
    await client.post(
        "/api/enablement/ingest",
        json={
            "source_sheet": _SOURCE,
            "full_refresh": True,
            "assets": [_asset(f"{_SOURCE}:keep", "El Konin Blending Deck")],
        },
        headers=headers,
    )

    out = json.loads(await _search_enablement_assets({"query": "el konin blending", "limit": 10}))
    titles = [r["title"] for r in out["results"]]
    assert "El Konin Blending Deck" in titles
    assert "El Konin Blending Deck OLD" not in titles  # archived excluded
    # surfaced result carries the structured links array with flags
    hit = next(r for r in out["results"] if r["title"] == "El Konin Blending Deck")
    assert any(link["make_copy"] for link in hit["links"])
    assert hit["requires_copy"] is True
