"""Stream 2b — the two asks Sara made directly in #enablement-library.

F6.1 (2026-06-19) "Why did you choose option 1 and 2 over option 3?" Kai had not
     ranked at all. It listed in arbitrary order and the numbering implied a
     preference that did not exist. Search now returns `rank`, `relevance`, and
     an `ordering` verdict, so Kai either gives a real reason or says the list
     is unordered.

F6.3 (2026-07-20) Sara pasted a Drive link and asked whether it was
     customer-facing. Kai could only say "that URL isn't in the catalog" and
     then over-explained. get_enablement_asset now accepts a URL, matches it
     against drive_link and the links JSONB, and answers from the matched link's
     own visibility flag rather than from anything inferred about the URL.

Implemented WITHOUT adding a tool: Kai's registry stays at four. A URL lookup is
the same read-only authority over the same table he already reads.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.enablement.tools import (
    _get_enablement_asset,
    _match_link_in_asset,
    _normalize_url,
    _relevance_scores,
    _search_enablement_assets,
)

CUSTOMER_URL = "https://explore.amiralearning.com/hubfs/Handout_Getting_Started.pdf"
INTERNAL_URL = "https://docs.google.com/presentation/d/17yz-STz/copy"


def _make_asset(**kwargs: Any) -> MagicMock:
    defaults: dict[str, Any] = {
        "drive_file_id": "abc123",
        "asset_name": "Getting Started",
        "title": "Getting Started Handout",
        "summary": None,
        "drive_link": "https://drive.google.com/file/d/abc123",
        "links": [
            {
                "url": CUSTOMER_URL,
                "role": "handout_customer",
                "label": "Customer handout (share this)",
                "visibility": "customer",
                "on_request": False,
                "make_copy": False,
            },
            {
                "url": INTERNAL_URL,
                "role": "deck",
                "label": "Training deck (CSM)",
                "visibility": "internal",
                "on_request": False,
                "make_copy": True,
            },
        ],
        "requires_copy": False,
        "type": "handout",
        "confidence_label": None,
        "audience": "Teacher",
        "tags": ["Assess"],
        "transcript_link": None,
        "status": "active",
        "source_scope": "enablement",
        "source_sheet": "teacher_resources_internal",
        "source_row": "42",
    }
    defaults.update(kwargs)
    asset = MagicMock()
    for key, value in defaults.items():
        setattr(asset, key, value)
    return asset


def _session_returning(assets: list[MagicMock]) -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value.all.return_value = assets
    result.scalar_one_or_none.return_value = assets[0] if assets else None
    session.execute = AsyncMock(return_value=result)
    return session


async def _search(assets: list[MagicMock], query: str) -> dict[str, Any]:
    with (
        patch("artemis.db.SessionLocal", return_value=_session_returning(assets)),
        patch(
            "artemis.memory.embeddings.MiniLMProvider.embed",
            new=AsyncMock(return_value=[0.1] * 384),
        ),
    ):
        return json.loads(await _search_enablement_assets({"query": query}))


async def _lookup(assets: list[MagicMock], identifier: str) -> dict[str, Any]:
    with patch("artemis.db.SessionLocal", return_value=_session_returning(assets)):
        return json.loads(await _get_enablement_asset({"drive_file_id_or_name": identifier}))


# ── F6.1: ordering must be earned ─────────────────────────────────────────────


async def test_distinct_scores_are_reported_as_ranked() -> None:
    assets = [
        _make_asset(title="Assignment Completion Walkthrough", asset_name="a"),
        _make_asset(title="Something Unrelated Entirely", asset_name="b"),
    ]
    data = await _search(assets, "assignment completion walkthrough")
    assert data["ordering"] == "ranked_by_relevance"
    assert data["results"][0]["rank"] == 1
    assert data["results"][0]["relevance"] > data["results"][1]["relevance"]
    assert "say why" in data["ordering_note"]


async def test_tied_scores_are_reported_as_unordered() -> None:
    """The Sara case: three equally-close results must not look like a ranking."""
    assets = [
        _make_asset(title="Alpha Guide", asset_name="a", tags=[]),
        _make_asset(title="Beta Guide", asset_name="b", tags=[]),
        _make_asset(title="Gamma Guide", asset_name="c", tags=[]),
    ]
    data = await _search(assets, "guide")
    assert data["ordering"] == "unordered_tied"
    note = data["ordering_note"].lower()
    assert "arbitrary" in note
    assert "do not present one as the best match" in note


async def test_every_result_carries_rank_and_relevance() -> None:
    assets = [_make_asset(title=f"Guide {i}", asset_name=str(i)) for i in range(3)]
    data = await _search(assets, "guide")
    assert [r["rank"] for r in data["results"]] == [1, 2, 3]
    assert all("relevance" in r for r in data["results"])


def test_scores_are_all_zero_when_query_has_no_usable_terms() -> None:
    """A two-letter query genuinely cannot rank anything. Say unordered, not ranked."""
    assets = [_make_asset(), _make_asset()]
    assert _relevance_scores(assets, "hi") == [0, 0]


def test_reranking_still_puts_the_title_match_first() -> None:
    """The 2026-06-20 behaviour must survive the scoring refactor."""
    from artemis.enablement.tools import _rerank_enablement_

    wrong = _make_asset(title="Assess Overview", asset_name="wrong")
    right = _make_asset(title="Instruct-Core Coherent", asset_name="right")
    ordered = _rerank_enablement_([wrong, right], "Instruct-Core Coherent")
    assert ordered[0] is right


# ── F6.3: pasted-link verdicts ────────────────────────────────────────────────


async def test_customer_link_is_reported_safe_to_send() -> None:
    data = await _lookup([_make_asset()], CUSTOMER_URL)
    assert data["found"] is True
    assert data["verdict"] == "customer_facing"
    assert data["customer_facing"] is True
    assert data["matched_link"]["role"] == "handout_customer"


async def test_internal_link_is_reported_not_customer_facing() -> None:
    """Sara's actual question. Getting this backwards sends internal material out."""
    data = await _lookup([_make_asset()], INTERNAL_URL)
    assert data["verdict"] == "internal_only"
    assert data["customer_facing"] is False


async def test_archived_asset_is_never_customer_facing() -> None:
    data = await _lookup([_make_asset(status="archived")], CUSTOMER_URL)
    assert data["verdict"] == "archived_do_not_send"
    assert data["customer_facing"] is False


async def test_missing_visibility_flag_needs_verification() -> None:
    asset = _make_asset(
        links=[{"url": CUSTOMER_URL, "role": "x", "label": "x"}],
    )
    data = await _lookup([asset], CUSTOMER_URL)
    assert data["verdict"] == "unknown_visibility"
    assert data["customer_facing"] is False


async def test_unmatched_url_says_so_and_forbids_speculation() -> None:
    """F2 lives here too: no match must not become a theory about why."""
    data = await _lookup([], "https://explore.amiralearning.com/hubfs/Nope.pdf")
    assert data["found"] is False
    assert data["verdict"] == "not_a_catalog_asset"
    guidance = data["guidance"]
    assert "stop" in guidance.lower()
    assert "speculate" in guidance.lower()


async def test_url_that_hits_a_candidate_row_but_no_link_is_not_a_match() -> None:
    """A SQL LIKE prefilter hit is not a match. Only the normalizer decides."""
    other = _make_asset(
        drive_link="https://drive.google.com/file/d/other",
        links=[{"url": "https://explore.amiralearning.com/hubfs/Different.pdf"}],
    )
    data = await _lookup([other], "https://explore.amiralearning.com/hubfs/Nope.pdf")
    assert data["found"] is False


async def test_non_url_lookup_keeps_the_original_shape() -> None:
    """Existing callers by id/name must be unaffected."""
    data = await _lookup([_make_asset()], "abc123")
    assert data["found"] is True
    assert "verdict" not in data
    assert data["asset"]["drive_file_id"] == "abc123"


async def test_drive_link_itself_matches() -> None:
    data = await _lookup([_make_asset()], "https://drive.google.com/file/d/abc123")
    assert data["found"] is True
    assert data["matched_link"]["role"] == "drive_link"


# ── URL normalization ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Drive.Google.com/file/d/abc", "https://drive.google.com/file/d/abc"),
        ("https://x.com/a/", "https://x.com/a"),
        ("https://x.com/a?usp=sharing", "https://x.com/a"),
        ("https://x.com/a#page=2", "https://x.com/a"),
        ("  https://x.com/a  ", "https://x.com/a"),
    ],
)
def test_normalize_url_ignores_incidental_differences(raw: str, expected: str) -> None:
    assert _normalize_url(raw) == expected


def test_normalize_url_preserves_path_case_so_documents_do_not_collide() -> None:
    """Drive ids are case-sensitive. Lowercasing the path would merge two files."""
    assert _normalize_url("https://x.com/AbC") != _normalize_url("https://x.com/abc")


def test_match_link_returns_none_for_a_different_url() -> None:
    assert _match_link_in_asset(_make_asset(), "https://x.com/other") is None


def test_match_link_tolerates_malformed_link_entries() -> None:
    asset = _make_asset(links=["not-a-dict", None, {"no_url": 1}, {"url": CUSTOMER_URL}])
    matched = _match_link_in_asset(asset, CUSTOMER_URL)
    assert matched is not None and matched["url"] == CUSTOMER_URL


# ── Registry unchanged ────────────────────────────────────────────────────────


def test_stream_2b_added_no_tools() -> None:
    """The verified-link capability rides on an existing tool by design."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    reg = build_authorized_tool_registry(set(), agent_id="kai", speaker_id="U09F3EPJXSQ")
    assert len(reg) == 4
