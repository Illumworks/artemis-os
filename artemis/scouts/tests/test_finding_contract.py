"""Tests for the canonical scout Finding contract (Part 1 of the scout-fleet fix).

Proves the audit failure mode is closed:
1. Finding.from_raw normalizes legacy mapper dicts (no headline, ``urgency``
   key, source_url buried in metadata) into the wire contract.
2. BaseScout.emit_signals POSTs normalized payloads — asserted against a
   mocked HTTP ingest.
3. Every emitted finding passes the server's ``_validate_finding`` and its
   scout type + sourceType resolve against the in-repo package catalogue
   (previously 6 of 9 types were unregistered → 400 for every run).

No real network, no DB.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.marketing.routes.scouts import _get_package, _load_packages, _validate_finding
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.board_minutes.mapping import meeting_item_to_finding
from artemis.scouts.finding import (
    DEFAULT_CAMPAIGN_FAMILY,
    Finding,
    campaign_family_for_reason_codes,
)
from artemis.scouts.linkedin.mapping import post_to_finding
from artemis.scouts.procurement.mapping import posting_to_finding
from artemis.scouts.runner import _REGISTRY
from artemis.scouts.starbridge.client import StarbridgeItem
from artemis.scouts.starbridge.mapping import item_to_finding as starbridge_item_to_finding
from artemis.scouts.state_doe.mapping import item_to_finding as doe_item_to_finding

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DISTRICT: dict[str, Any] = {
    "district_id": "FL_pinellas",
    "state": "FL",
    "boarddocs_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
    "granicus_url": None,
    "district_site_url": None,
}


def _board_item(text: str = "Board approved literacy curriculum adoption.") -> dict[str, Any]:
    return {
        "title": "Regular Board Meeting — Literacy Update",
        "text": text,
        "date": "2026-06-01",
        "source_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/goto?open&id=ABC123",
        "speaker_attribution": None,
    }


class _StubScout(BaseScout):
    scout_type = "board_minutes_scout"

    async def _gather_findings(self) -> list[dict[str, Any]]:
        return []


def _mock_ingest(response_json: dict[str, Any] | None = None) -> AsyncMock:
    """AsyncMock httpx client whose post() returns a canned ingest response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 201
    resp.raise_for_status.return_value = None
    resp.json.return_value = response_json or {
        "runId": "scout_run_test",
        "status": "committed",
        "createdCount": 1,
        "skippedCount": 0,
        "errors": [],
    }
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = resp
    return client


# ===========================================================================
# Finding.from_raw normalization
# ===========================================================================


def test_from_raw_legacy_board_minutes_shape() -> None:
    """A real board-minutes mapper dict (no headline/family/top-level URL) normalizes."""
    raw = meeting_item_to_finding(_board_item(), _DISTRICT)
    assert raw is not None
    # The legacy mapper output itself would be REJECTED by the server:
    assert _validate_finding(raw), "precondition: raw mapper dict must fail validation"

    finding = Finding.from_raw(raw, scout_type="board_minutes_scout")
    wire = finding.to_wire()

    assert wire["headline"]
    assert wire["campaignFamily"]
    assert wire["sourceUrl"] == raw["metadata"]["source_url"]
    assert wire["urgencyTier"] in ("hot", "standard", "enrichment")
    assert wire["districtId"] == "FL_pinellas"
    assert wire["state"] == "FL"
    assert wire["discoveredBy"] == "board_minutes_scout"
    # And the normalized wire payload passes the server validator:
    assert _validate_finding(wire) == []


def test_from_raw_urgency_alias_low_maps_to_enrichment() -> None:
    finding = Finding.from_raw(
        {
            "headline": "Some headline",
            "sourceUrl": "https://example.com/x",
            "urgency": "low",
            "reasonCodes": [],
        },
        scout_type="state_doe_scout",
    )
    assert finding.urgency_tier == "enrichment"


def test_from_raw_unknown_urgency_defaults_standard() -> None:
    finding = Finding.from_raw(
        {"headline": "H", "sourceUrl": "https://example.com/y", "urgency": "urgent!!"},
        scout_type="state_doe_scout",
    )
    assert finding.urgency_tier == "standard"


def test_from_raw_reason_code_dicts_coerced() -> None:
    finding = Finding.from_raw(
        {
            "headline": "H",
            "sourceUrl": "https://example.com/z",
            "reasonCodes": [{"code": "TX_HB1416_WAIVER", "confidence": 0.9}, "BILL_ENACTED"],
        },
        scout_type="legislative_scout",
    )
    assert finding.reason_codes == ["TX_HB1416_WAIVER", "BILL_ENACTED"]
    # Family derived from the first mapped code.
    assert finding.campaign_family == "hit"


def test_from_raw_headline_derived_from_evidence() -> None:
    finding = Finding.from_raw(
        {
            "evidence": "Board approves RFP for literacy tutoring. Second sentence here.",
            "sourceUrl": "https://example.com/e",
        },
        scout_type="procurement_scout",
    )
    assert finding.headline == "Board approves RFP for literacy tutoring."


def test_from_raw_source_url_from_metadata_html_url() -> None:
    """federal_funding puts its URL in metadata.html_url — must be found."""
    finding = Finding.from_raw(
        {
            "headline": "Federal Register notice",
            "metadata": {"html_url": "https://federalregister.gov/d/2026-1234"},
        },
        scout_type="federal_funding_scout",
    )
    assert finding.source_url == "https://federalregister.gov/d/2026-1234"


def test_from_raw_urn_fallback_from_source_id() -> None:
    """No URL anywhere but a stable id → deterministic URN (dedupe still works)."""
    finding = Finding.from_raw(
        {"headline": "Starbridge item", "metadata": {"item_id": "sb-42"}},
        scout_type="starbridge_researcher",
    )
    assert finding.source_url == "urn:artemis-scout:starbridge_researcher:sb-42"


def test_from_raw_unsalvageable_raises() -> None:
    """No headline, no evidence, no URL, no id → ValueError (caller drops it)."""
    with pytest.raises(ValueError):
        Finding.from_raw({"metadata": {}}, scout_type="board_minutes_scout")


def test_from_raw_explicit_family_alias_normalized() -> None:
    finding = Finding.from_raw(
        {
            "headline": "H",
            "sourceUrl": "https://example.com/f",
            "campaignFamily": "Dyslexia / Structured Literacy",
        },
        scout_type="state_doe_scout",
    )
    assert finding.campaign_family == "dyslexia"


def test_campaign_family_for_reason_codes_default() -> None:
    assert campaign_family_for_reason_codes(["BILL_ENACTED"]) == DEFAULT_CAMPAIGN_FAMILY
    assert campaign_family_for_reason_codes([]) == DEFAULT_CAMPAIGN_FAMILY
    assert campaign_family_for_reason_codes(["DISTRICT_DLL_EXPANSION"]) == "biliteracy"


# ===========================================================================
# Every in-repo mapper's output normalizes and passes the server validator
# ===========================================================================


def _mapper_outputs() -> list[tuple[str, dict[str, Any]]]:
    """(scout_type, raw finding) pairs produced by the REAL mappers."""
    pairs: list[tuple[str, dict[str, Any]]] = []

    board = meeting_item_to_finding(_board_item(), _DISTRICT)
    assert board is not None
    pairs.append(("board_minutes_scout", board))

    pairs.append(
        (
            "procurement_scout",
            posting_to_finding(
                {
                    "portal_id": "tx_esbd",
                    "state": "TX",
                    "rfp_id": "RFP-2026-001",
                    "title": "ELA Curriculum and Tutoring Services RFP",
                    "agency": "Dallas ISD",
                    "posted_date": "2026-06-01",
                    "due_date": "2026-07-01",
                    "source_url": "https://esbd.example.com/rfp/1",
                    "description": "Literacy tutoring services for K-3 students.",
                    "scope_text": "reading intervention",
                }
            ),
        )
    )

    pairs.append(
        (
            "state_doe_scout",
            doe_item_to_finding(
                {
                    "title": "State issues dyslexia screening mandate",
                    "summary": "New mandate requires universal dyslexia screening.",
                    "link": "https://doe.example.gov/news/1",
                    "_source_type": "doe_rss",
                },
                "tx",
            ),
        )
    )

    pairs.append(
        (
            "linkedin_observer",
            post_to_finding(
                {
                    "url": "https://linkedin.com/posts/abc",
                    "post_id": "p1",
                    "profile_id": "https://linkedin.com/in/supe",
                    "posted_at": "2026-06-20T12:00:00Z",
                    "text": "Proud of our literacy tutoring gains this year!",
                },
                {
                    "district_id": "TX_dallas",
                    "state": "TX",
                    "name": "Jane Doe",
                    "role": "Superintendent",
                    "profile_id": "https://linkedin.com/in/supe",
                },
            ),
        )
    )

    pairs.append(
        (
            "starbridge_researcher",
            starbridge_item_to_finding(
                StarbridgeItem(
                    item_id="sb-1",
                    title="District literacy RFP intent",
                    summary="Board signals upcoming reading curriculum purchase.",
                    state="TX",
                )
            ),
        )
    )

    return [(st, raw) for st, raw in pairs if raw is not None]


def test_all_mapper_outputs_normalize_and_validate() -> None:
    for scout_type, raw in _mapper_outputs():
        wire = Finding.from_raw(raw, scout_type=scout_type).to_wire()
        errs = _validate_finding(wire)
        assert errs == [], f"{scout_type}: {errs} (wire={wire})"


# ===========================================================================
# Package catalogue — all scout types registered in-repo
# ===========================================================================


def test_catalogue_registers_every_scout_type() -> None:
    """Every scout class in the runner registry must resolve a package."""
    packages = _load_packages()
    assert packages, "config/scout-packages.json must load a non-empty catalogue"
    registered = {p["scoutType"] for p in packages}
    for scout_type in _REGISTRY:
        assert scout_type in registered, f"{scout_type} missing from scout-packages.json"


def test_catalogue_allows_each_mappers_source_type() -> None:
    """Each mapper's emitted sourceType must be allowed for its scout type."""
    for scout_type, raw in _mapper_outputs():
        pkg = _get_package(scout_type)
        assert pkg is not None, f"no package for {scout_type}"
        wire = Finding.from_raw(raw, scout_type=scout_type).to_wire()
        allowed = pkg.get("allowedSourceTypes", [])
        assert wire["sourceType"] in allowed, (
            f"{scout_type}: sourceType {wire['sourceType']!r} not in {allowed}"
        )


def test_peer_validation_scout_registered() -> None:
    assert _get_package("board_peer_validation_scout") is not None


# ===========================================================================
# emit_signals — normalized payload hits the (mocked) ingest
# ===========================================================================


async def test_emit_signals_posts_normalized_payload() -> None:
    """Legacy mapper dicts go in; canonical wire findings come out on the POST."""
    client = _mock_ingest()
    scout = _StubScout(ScoutConfig(api_url="http://testserver", api_token="tok"), _client=client)

    raw = meeting_item_to_finding(_board_item(), _DISTRICT)
    assert raw is not None
    result = await scout.emit_signals([raw])

    assert client.post.called
    call = client.post.call_args
    assert call.args[0] == "http://testserver/api/scouts/runs"
    payload = call.kwargs["json"]
    assert payload["scoutType"] == "board_minutes_scout"
    assert len(payload["findings"]) == 1

    wire = payload["findings"][0]
    # The exact fields the ingest validator requires, all populated:
    assert wire["headline"].strip()
    assert wire["campaignFamily"].strip()
    assert wire["sourceUrl"].strip()
    assert _validate_finding(wire) == []
    # Dedupe key fields are populated (url + headline).
    assert wire["sourceUrl"].startswith("https://")

    # JSON-serializable end to end.
    json.dumps(payload)

    assert result.status == "committed"
    assert result.created_count == 1


async def test_emit_signals_drops_unsalvageable_finding_and_reports() -> None:
    client = _mock_ingest()
    scout = _StubScout(ScoutConfig(api_url="http://testserver"), _client=client)

    good = meeting_item_to_finding(_board_item(), _DISTRICT)
    assert good is not None
    bad = {"metadata": {}}  # nothing derivable

    result = await scout.emit_signals([bad, good])

    payload = client.post.call_args.kwargs["json"]
    assert len(payload["findings"]) == 1  # bad one dropped client-side
    assert any("normalization failed" in str(e.get("error", "")) for e in result.errors)


async def test_emit_signals_all_invalid_no_post() -> None:
    client = _mock_ingest()
    scout = _StubScout(ScoutConfig(api_url="http://testserver"), _client=client)

    result = await scout.emit_signals([{"metadata": {}}])

    assert not client.post.called
    assert result.status == "error"
    assert result.errors
