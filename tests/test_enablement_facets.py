"""Tests for enablement facet filters (Task 1) and list_enablement_facets (Task 2).

Covers:
  - search_enablement_assets with audience/asset_type/tags filters returns only
    matching rows and excludes non-matching ones
  - _list_enablement_facets returns correct audiences/types/tags with counts
    and excludes archived rows
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete

import artemis.db as _db
from artemis.enablement.models import EnablementAsset
from artemis.routes import enablement as enablement_mod

_SOURCE = "test_facets"


def _asset(
    key: str,
    title: str,
    *,
    audience: str = "Teacher",
    asset_type: str = "training_deck",
    tags: list[str] | None = None,
    status: str = "active",
) -> dict:
    return {
        "key": key,
        "asset_type": asset_type,
        "title": title,
        "summary": f"Summary for {title}",
        "audience": audience,
        "tags": tags or ["Getting Started", "Assess", "Teacher"],
        "searchable_text": title.lower(),
        "links": [
            {
                "role": "deck",
                "label": "Deck",
                "url": f"https://example.com/{key}",
                "visibility": "customer",
                "on_request": False,
                "make_copy": False,
            }
        ],
        "requires_copy": False,
        "source_row": "1",
        # status for the archived-exclusion tests; ingest API maps it directly
        **({"status": status} if status != "active" else {}),
    }


@pytest.fixture(autouse=True)
async def _clean_and_secret(monkeypatch):
    """Set a known webhook secret and clean test rows before/after each test.

    Matches the pattern in test_enablement_ingest.py: engine.dispose() top and
    bottom to avoid asyncpg "Event loop is closed" teardown noise.
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


# ── helpers ───────────────────────────────────────────────────────────────────


async def _ingest(client, assets: list[dict]) -> None:
    r = await client.post(
        "/api/enablement/ingest",
        json={"source_sheet": _SOURCE, "full_refresh": True, "assets": assets},
        headers={"X-Enablement-Token": "test-secret"},
    )
    assert r.status_code == 200, r.text


# ── Task 1: structured facet filters on search ───────────────────────────────


async def test_search_audience_filter_matches_and_excludes(client):
    """audience filter returns only rows with that audience (case-insensitive)."""
    from artemis.enablement.tools import _search_enablement_assets

    await _ingest(
        client,
        [
            _asset(f"{_SOURCE}:T1", "Assess Teacher Deck", audience="Teacher"),
            _asset(f"{_SOURCE}:T2", "Assess Admin Overview", audience="Admin"),
        ],
    )

    out = json.loads(
        await _search_enablement_assets({"query": "Assess", "limit": 10, "audience": "Teacher"})
    )
    titles = [r["title"] for r in out["results"]]
    assert "Assess Teacher Deck" in titles
    assert "Assess Admin Overview" not in titles

    # Case-insensitive: "teacher" should match "Teacher" stored value.
    out2 = json.loads(
        await _search_enablement_assets({"query": "Assess", "limit": 10, "audience": "teacher"})
    )
    titles2 = [r["title"] for r in out2["results"]]
    assert "Assess Teacher Deck" in titles2
    assert "Assess Admin Overview" not in titles2


async def test_search_asset_type_filter_matches_and_excludes(client):
    """asset_type filter returns only rows of that type."""
    from artemis.enablement.tools import _search_enablement_assets

    await _ingest(
        client,
        [
            _asset(f"{_SOURCE}:V1", "Getting Started Video", asset_type="student_video"),
            _asset(f"{_SOURCE}:D1", "Getting Started Deck", asset_type="training_deck"),
        ],
    )

    out = json.loads(
        await _search_enablement_assets(
            {"query": "Getting Started", "limit": 10, "asset_type": "student_video"}
        )
    )
    titles = [r["title"] for r in out["results"]]
    assert "Getting Started Video" in titles
    assert "Getting Started Deck" not in titles


async def test_search_tags_filter_all_required(client):
    """tags filter requires ALL specified tags to be present (AND semantics)."""
    from artemis.enablement.tools import _search_enablement_assets

    await _ingest(
        client,
        [
            # Has Assess + Instruct
            _asset(
                f"{_SOURCE}:AI",
                "Multi-Product Deck",
                tags=["Assess", "Instruct", "Teacher"],
            ),
            # Has only Assess
            _asset(f"{_SOURCE}:A", "Assess Only Deck", tags=["Assess", "Teacher"]),
            # Has only Instruct
            _asset(f"{_SOURCE}:I", "Instruct Only Deck", tags=["Instruct", "Teacher"]),
        ],
    )

    out = json.loads(
        await _search_enablement_assets(
            {"query": "Deck", "limit": 10, "tags": ["Assess", "Instruct"]}
        )
    )
    titles = [r["title"] for r in out["results"]]
    assert "Multi-Product Deck" in titles
    assert "Assess Only Deck" not in titles
    assert "Instruct Only Deck" not in titles


async def test_search_combined_filters(client):
    """audience + asset_type + tags can all be combined."""
    from artemis.enablement.tools import _search_enablement_assets

    await _ingest(
        client,
        [
            _asset(
                f"{_SOURCE}:exact",
                "Exact Match",
                audience="Teacher",
                asset_type="training_deck",
                tags=["Assess", "Grades K-2"],
            ),
            _asset(
                f"{_SOURCE}:wrong_aud",
                "Wrong Audience",
                audience="Admin",
                asset_type="training_deck",
                tags=["Assess", "Grades K-2"],
            ),
            _asset(
                f"{_SOURCE}:wrong_type",
                "Wrong Type",
                audience="Teacher",
                asset_type="student_video",
                tags=["Assess", "Grades K-2"],
            ),
            _asset(
                f"{_SOURCE}:wrong_tag",
                "Wrong Tag",
                audience="Teacher",
                asset_type="training_deck",
                tags=["Instruct"],
            ),
        ],
    )

    out = json.loads(
        await _search_enablement_assets(
            {
                "query": "Match",
                "limit": 10,
                "audience": "Teacher",
                "asset_type": "training_deck",
                "tags": ["Assess"],
            }
        )
    )
    titles = [r["title"] for r in out["results"]]
    assert "Exact Match" in titles
    assert "Wrong Audience" not in titles
    assert "Wrong Type" not in titles
    assert "Wrong Tag" not in titles


# ── Task 2: list_enablement_facets ───────────────────────────────────────────


async def test_list_facets_returns_audiences_types_tags(client):
    """list_enablement_facets returns correct vocabulary with accurate counts."""
    from artemis.enablement.tools import _list_enablement_facets

    await _ingest(
        client,
        [
            _asset(
                f"{_SOURCE}:F1",
                "Facet Test 1",
                audience="Teacher",
                asset_type="training_deck",
                tags=["Assess", "Grades K-2"],
            ),
            _asset(
                f"{_SOURCE}:F2",
                "Facet Test 2",
                audience="Teacher",
                asset_type="training_deck",
                tags=["Assess", "Instruct"],
            ),
            _asset(
                f"{_SOURCE}:F3",
                "Facet Test 3",
                audience="Admin",
                asset_type="doc",
                tags=["Instruct"],
            ),
        ],
    )

    out = json.loads(await _list_enablement_facets({}))

    # Check audiences — these rows contribute Teacher (×2) and Admin (×1).
    aud_map = {a["audience"]: a["count"] for a in out["audiences"]}
    assert aud_map.get("Teacher", 0) >= 2
    assert aud_map.get("Admin", 0) >= 1

    # Check types — training_deck (×2) and doc (×1) contributed by this fixture.
    type_map = {t["asset_type"]: t["count"] for t in out["types"]}
    assert type_map.get("training_deck", 0) >= 2
    assert type_map.get("doc", 0) >= 1

    # Check tags — "Assess" appeared in 2 rows, "Instruct" in 2, "Grades K-2" in 1.
    tag_map = {t["tag"]: t["count"] for t in out["tags"]}
    assert tag_map.get("Assess", 0) >= 2
    assert tag_map.get("Instruct", 0) >= 2
    assert tag_map.get("Grades K-2", 0) >= 1


async def test_list_facets_excludes_archived(client):
    """list_enablement_facets does not count archived rows."""
    from artemis.enablement.tools import _list_enablement_facets

    # Ingest one active row with a distinctive audience and tag.
    await _ingest(
        client,
        [
            _asset(
                f"{_SOURCE}:live",
                "Live Asset",
                audience="DistinctAudience",
                tags=["DistinctTag"],
            ),
            _asset(
                f"{_SOURCE}:archived",
                "Archived Asset",
                audience="DistinctAudience",
                tags=["DistinctTag"],
            ),
        ],
    )
    # Archive the second row via full_refresh dropping it.
    r = await client.post(
        "/api/enablement/ingest",
        json={
            "source_sheet": _SOURCE,
            "full_refresh": True,
            "assets": [
                _asset(
                    f"{_SOURCE}:live",
                    "Live Asset",
                    audience="DistinctAudience",
                    tags=["DistinctTag"],
                )
            ],
        },
        headers={"X-Enablement-Token": "test-secret"},
    )
    assert r.json()["archived"] == 1

    out = json.loads(await _list_enablement_facets({}))

    aud_map = {a["audience"]: a["count"] for a in out["audiences"]}
    tag_map = {t["tag"]: t["count"] for t in out["tags"]}

    # Only the live row should be counted — count must be exactly 1.
    assert aud_map.get("DistinctAudience") == 1
    assert tag_map.get("DistinctTag") == 1


async def test_list_facets_limit_param(client):
    """limit param caps the number of tag entries returned."""
    from artemis.enablement.tools import _list_enablement_facets

    # Insert two rows with distinct tags so there are at least 2 unique tag values.
    await _ingest(
        client,
        [
            _asset(f"{_SOURCE}:lim1", "Limit Test 1", tags=["TagAlpha", "TagBeta"]),
            _asset(f"{_SOURCE}:lim2", "Limit Test 2", tags=["TagGamma", "TagDelta"]),
        ],
    )

    out = json.loads(await _list_enablement_facets({"limit": 1}))
    # With limit=1, at most 1 tag entry returned.
    assert len(out["tags"]) <= 1
