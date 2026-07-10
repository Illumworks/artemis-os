"""Tests for the board-minutes v2 peer-validation foundation.

Covers:
- BoardDocs agenda-item BODY retrieval (BD-GetAgendaItem + goto fallback)
- LLM mention+sentiment classifier (mocked adapter; fail-safe on bad JSON)
- keyword prefilter + degraded keyword-only classification
- pluggable customer-exclusion filter
- BoardPeerValidationScout end-to-end with mocked HTTP + LLM
- emitted findings normalize and pass the server-side ingest validator

No real network, no real model calls.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from artemis.marketing.routes.scouts import _validate_finding
from artemis.scouts.base import ScoutConfig
from artemis.scouts.board_minutes.classifier import (
    MentionClassification,
    classify_mention,
    detect_topics,
    keyword_classification,
    quick_relevance,
)
from artemis.scouts.board_minutes.client import (
    _strip_html,
    fetch_agenda_item_body,
    fetch_boarddocs,
)
from artemis.scouts.board_minutes.customers import (
    SalesforceCustomerExclusions,
    StaticCustomerExclusions,
)
from artemis.scouts.board_minutes.mapping import peer_item_to_finding
from artemis.scouts.board_minutes.peer_scout import (
    _DEFAULT_PEER_WATCH_LIST,
    BoardPeerValidationScout,
    load_watch_list,
)
from artemis.scouts.finding import Finding

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DISTRICT: dict[str, Any] = {
    "district_id": "FL_pinellas",
    "state": "FL",
    "boarddocs_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
}

_SHELL_HTML = '<a committeeid="C1" class="committee" aria-label="Governing Board">Board</a>'

_MEETINGS_JSON = json.dumps(
    [{"unique": "M1", "name": "Regular Board Meeting", "numberdate": "20260601", "unid": "u1"}]
)

_AGENDA_HTML = (
    '<ul><li class="agenda item" unique="ITEM1">'
    '<span class="title">Student Device and Screen Time Policy</span></li>'
    '<li class="agenda item" unique="ITEM2">'
    '<span class="title">Parking Lot Resurfacing</span></li></ul>'
)

_ITEM1_BODY_HTML = (
    "<div><h2>Student Device and Screen Time Policy</h2>"
    "<p>The board discussed adopting new screen time limits for elementary "
    "students, restricting personal device use bell-to-bell.</p>"
    "<p>Motion passed 5-0.</p></div>"
)

_ITEM2_BODY_HTML = "<div><p>Approve contract for parking lot resurfacing at Central High.</p></div>"


class _FakeHttp:
    """Duck-typed ScoutHttpClient replacement routing by URL substring."""

    def __init__(self, routes: list[tuple[str, str, int, str]] | None = None) -> None:
        # (method, url_substring, status_code, body)
        self.routes = routes if routes is not None else self._default_routes()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    #: Per-item detail bodies served by the BD-GetAgendaItem route,
    #: keyed by the ``id`` field of the POSTed form data.
    item_bodies: dict[str, str] = {"ITEM1": _ITEM1_BODY_HTML, "ITEM2": _ITEM2_BODY_HTML}

    @staticmethod
    def _default_routes() -> list[tuple[str, str, int, str]]:
        return [
            ("POST", "BD-GetMeetingsList", 200, _MEETINGS_JSON),
            ("POST", "BD-GetAgenda?", 200, _AGENDA_HTML),
            ("POST", "BD-GetAgendaItem", 200, ""),  # body chosen per item id below
            ("GET", "/Public", 200, _SHELL_HTML),
        ]

    def _respond(self, method: str, url: str, kwargs: dict[str, Any]) -> MagicMock:
        self.calls.append((method, url, kwargs))
        for m, fragment, status, body in self.routes:
            if m == method and fragment in url:
                if fragment == "BD-GetAgendaItem":
                    item_id = (kwargs.get("data") or {}).get("id", "")
                    body = self.item_bodies.get(item_id, "")
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = status
                resp.text = body
                resp.content = body.encode()
                return resp
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 404
        resp.text = ""
        resp.content = b""
        return resp

    async def get(self, url: str, **kwargs: Any) -> MagicMock:
        return self._respond("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> MagicMock:
        return self._respond("POST", url, kwargs)


def _adapter_returning(text: str) -> AsyncMock:
    """Mock LLM adapter whose complete() yields a single text block."""
    adapter = AsyncMock()
    adapter.complete.return_value = SimpleNamespace(
        message=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
    )
    return adapter


_SCREENTIME_LLM_JSON = json.dumps(
    {
        "relevant": True,
        "topics": ["screentime"],
        "sentiment": "positive",
        "excerpt": "adopting new screen time limits for elementary students",
        "rationale": "Board adopted a screen-time limit policy.",
    }
)


# ===========================================================================
# HTML stripping + body retrieval
# ===========================================================================


def test_strip_html_removes_tags_and_scripts() -> None:
    html = "<script>var x=1;</script><p>Screen&nbsp;time &amp; devices</p><div>Line two</div>"
    text = _strip_html(html)
    assert "var x" not in text
    assert "Screen" in text and "& devices" in text.replace("\n", " ")
    assert "<p>" not in text


async def test_fetch_agenda_item_body_primary_path() -> None:
    http = _FakeHttp()
    body = await fetch_agenda_item_body(
        "https://go.boarddocs.com/fl/pcsfl/Board.nsf", "ITEM1", "C1", http  # type: ignore[arg-type]
    )
    assert "screen time limits" in body
    assert "<p>" not in body


async def test_fetch_agenda_item_body_falls_back_to_goto() -> None:
    """BD-GetAgendaItem 404s → the goto permalink is fetched instead."""
    routes = [
        ("GET", "goto?open&id=ITEM1", 200, _ITEM1_BODY_HTML),
    ]
    http = _FakeHttp(routes)
    body = await fetch_agenda_item_body(
        "https://go.boarddocs.com/fl/pcsfl/Board.nsf", "ITEM1", "C1", http  # type: ignore[arg-type]
    )
    assert "screen time limits" in body
    # Both endpoints were attempted, in order.
    methods = [(m, "BD-GetAgendaItem" in u or "goto" in u) for m, u, _ in http.calls]
    assert methods[0][0] == "POST"
    assert methods[-1][0] == "GET"


async def test_fetch_agenda_item_body_returns_empty_on_total_failure() -> None:
    http = _FakeHttp(routes=[])  # everything 404s
    body = await fetch_agenda_item_body(
        "https://go.boarddocs.com/fl/pcsfl/Board.nsf", "ITEM1", "C1", http  # type: ignore[arg-type]
    )
    assert body == ""


async def test_fetch_boarddocs_with_bodies_populates_item_text() -> None:
    http = _FakeHttp()
    items = await fetch_boarddocs(_DISTRICT, http, fetch_bodies=True)  # type: ignore[arg-type]
    assert len(items) == 2
    screentime_item = next(i for i in items if "Screen Time" in i["title"])
    # text now carries the item BODY, not just the title.
    assert "screen time limits" in screentime_item["text"]
    assert screentime_item["date"] == "2026-06-01"
    assert screentime_item["item_unique"] == "ITEM1"


async def test_fetch_boarddocs_without_bodies_keeps_v1_behaviour() -> None:
    http = _FakeHttp()
    items = await fetch_boarddocs(_DISTRICT, http)  # type: ignore[arg-type]
    assert len(items) == 2
    # v1: text == title only; BD-GetAgendaItem never called.
    assert all("BD-GetAgendaItem" not in url for _, url, _ in http.calls)
    assert items[0]["text"] == "Student Device and Screen Time Policy"


# ===========================================================================
# Keyword prefilter
# ===========================================================================


def test_quick_relevance_hits() -> None:
    assert quick_relevance("New screen time limits for K-5")
    assert quick_relevance("District pauses AI pilot program")
    assert quick_relevance("Amira Learning renewal discussion")
    assert quick_relevance("bell-to-bell cell phone ban")


def test_quick_relevance_negatives() -> None:
    assert not quick_relevance("Universal dyslexia screening update")  # 'screening' != screentime
    assert not quick_relevance("Approve parking lot resurfacing contract")
    assert not quick_relevance("said the mountain")  # lowercase 'ai' inside words


def test_detect_topics_multi() -> None:
    topics = detect_topics("Screen time limits and the ChatGPT pilot were discussed")
    assert set(topics) == {"screentime", "ai_in_schools"}


# ===========================================================================
# LLM classifier (mocked adapter)
# ===========================================================================


async def test_classify_mention_good_json() -> None:
    adapter = _adapter_returning(_SCREENTIME_LLM_JSON)
    result = await classify_mention("Screen Time Policy", "body text", adapter=adapter)
    assert result is not None
    assert result.relevant is True
    assert result.topics == ["screentime"]
    assert result.sentiment == "positive"
    assert "screen time limits" in result.excerpt
    assert result.method == "llm"


async def test_classify_mention_fenced_json() -> None:
    adapter = _adapter_returning(f"```json\n{_SCREENTIME_LLM_JSON}\n```")
    result = await classify_mention("t", "b", adapter=adapter)
    assert result is not None and result.relevant


async def test_classify_mention_bad_json_returns_none() -> None:
    adapter = _adapter_returning("I think this is about screen time policies.")
    assert await classify_mention("t", "b", adapter=adapter) is None


async def test_classify_mention_unknown_topics_dropped() -> None:
    adapter = _adapter_returning(
        json.dumps({"relevant": True, "topics": ["screentime", "budget"], "sentiment": "neutral"})
    )
    result = await classify_mention("t", "b", adapter=adapter)
    assert result is not None
    assert result.topics == ["screentime"]


async def test_classify_mention_adapter_raises_returns_none() -> None:
    adapter = AsyncMock()
    adapter.complete.side_effect = RuntimeError("provider down")
    assert await classify_mention("t", "b", adapter=adapter) is None


def test_keyword_classification_fallback() -> None:
    result = keyword_classification("Cell phone ban", "Board adopted a bell-to-bell phone ban.")
    assert result is not None
    assert result.relevant and "screentime" in result.topics
    assert result.sentiment == "neutral"
    assert result.method == "keyword"
    assert result.excerpt


def test_keyword_classification_irrelevant_returns_none() -> None:
    assert keyword_classification("Facilities", "Parking lot resurfacing approved.") is None


# ===========================================================================
# Peer mapping
# ===========================================================================


def _item(title: str = "Regular Board Meeting — Screen Time Policy") -> dict[str, Any]:
    return {
        "title": title,
        "date": "2026-06-01",
        "source_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/goto?open&id=ITEM1",
        "text": "The board adopted screen time limits.",
        "speaker_attribution": None,
    }


def test_peer_item_to_finding_positive_tagged() -> None:
    cls = MentionClassification(
        relevant=True, topics=["screentime"], sentiment="positive", excerpt="limits adopted"
    )
    finding = peer_item_to_finding(_item(), _DISTRICT, cls)
    assert finding is not None
    assert finding["discoveredBy"] == "board_peer_validation_scout"
    assert "PEER_SCREENTIME_POLICY" in finding["reasonCodes"]
    assert "POLICY_EDTECH_TIME_LIMIT" in finding["reasonCodes"]
    assert finding["metadata"]["tags"] == ["peer_validation"]
    assert finding["metadata"]["sentiment"] == "positive"
    assert finding["urgency"] == "standard"


def test_peer_item_to_finding_amira_positive_is_hot() -> None:
    cls = MentionClassification(relevant=True, topics=["amira"], sentiment="positive")
    finding = peer_item_to_finding(_item("Amira Learning update"), _DISTRICT, cls)
    assert finding is not None
    assert finding["urgency"] == "hot"
    assert "PEER_AMIRA_MENTION" in finding["reasonCodes"]


def test_peer_item_to_finding_negative_not_tagged() -> None:
    cls = MentionClassification(relevant=True, topics=["ai_in_schools"], sentiment="negative")
    finding = peer_item_to_finding(_item("AI pilot paused"), _DISTRICT, cls)
    assert finding is not None
    assert finding["metadata"]["tags"] == []  # intel, not peer validation


def test_peer_item_to_finding_irrelevant_none() -> None:
    cls = MentionClassification(relevant=False)
    assert peer_item_to_finding(_item(), _DISTRICT, cls) is None
    assert peer_item_to_finding(_item(), _DISTRICT, None) is None


# ===========================================================================
# Customer exclusion
# ===========================================================================


async def test_static_exclusions_normalized() -> None:
    exclusions = StaticCustomerExclusions([" FL_Pinellas ", "TX_dallas"])
    ids = await exclusions.get_customer_district_ids()
    assert ids == {"fl_pinellas", "tx_dallas"}


async def test_salesforce_stub_returns_fallback() -> None:
    stub = SalesforceCustomerExclusions(fallback_ids=["TX_dallas"])
    assert await stub.get_customer_district_ids() == {"tx_dallas"}


# ===========================================================================
# BoardPeerValidationScout end-to-end (mocked HTTP + LLM)
# ===========================================================================


def _scout(**kwargs: Any) -> BoardPeerValidationScout:
    defaults: dict[str, Any] = {
        "config": ScoutConfig(),
        "watch_list": [_DISTRICT],
        "_http_client": _FakeHttp(),
        "_adapter": _adapter_returning(_SCREENTIME_LLM_JSON),
    }
    defaults.update(kwargs)
    return BoardPeerValidationScout(defaults.pop("config"), **defaults)


async def test_gather_findings_emits_peer_validation_finding() -> None:
    scout = _scout()
    findings = await scout._gather_findings()

    assert len(findings) == 1  # parking-lot item filtered by the prefilter
    finding = findings[0]
    assert finding["metadata"]["tags"] == ["peer_validation"]
    assert finding["metadata"]["topics"] == ["screentime"]
    assert finding["districtId"] == "FL_pinellas"

    # The finding survives canonical normalization AND the server validator.
    wire = Finding.from_raw(finding, scout_type=scout.scout_type).to_wire()
    assert _validate_finding(wire) == []
    assert wire["campaignFamily"]
    assert wire["sourceUrl"].startswith("https://go.boarddocs.com/")


async def test_gather_findings_prefilter_bounds_llm_calls() -> None:
    """Only keyword-relevant items reach the LLM (1 of 2 agenda items)."""
    adapter = _adapter_returning(_SCREENTIME_LLM_JSON)
    scout = _scout(_adapter=adapter)
    await scout._gather_findings()
    assert adapter.complete.await_count == 1


async def test_gather_findings_customer_district_excluded() -> None:
    scout = _scout(exclusions=StaticCustomerExclusions(["FL_pinellas"]))
    findings = await scout._gather_findings()
    assert findings == []
    # No HTTP fetch happened for the excluded district.
    assert scout._http.calls == []  # type: ignore[attr-defined]


async def test_gather_findings_exclusion_provider_failure_degrades() -> None:
    broken = AsyncMock()
    broken.get_customer_district_ids.side_effect = RuntimeError("salesforce down")
    scout = _scout(exclusions=broken)
    findings = await scout._gather_findings()
    assert len(findings) == 1  # run proceeded with empty exclusion set


async def test_gather_findings_llm_failure_falls_back_to_keyword() -> None:
    adapter = AsyncMock()
    adapter.complete.side_effect = RuntimeError("provider down")
    scout = _scout(_adapter=adapter)
    findings = await scout._gather_findings()
    assert len(findings) == 1
    assert findings[0]["metadata"]["classification_method"] == "keyword"
    assert findings[0]["metadata"]["sentiment"] == "neutral"


async def test_gather_findings_respects_district_cap() -> None:
    d2 = {**_DISTRICT, "district_id": "TX_dallas"}
    scout = _scout(watch_list=[_DISTRICT, d2], max_districts_per_run=1)
    await scout._gather_findings()
    urls = {url for _, url, _ in scout._http.calls}  # type: ignore[attr-defined]
    # Only one district's shell was fetched.
    assert len([u for u in urls if u.endswith("/Public")]) == 1


async def test_gather_findings_continues_on_district_error() -> None:
    d2 = {**_DISTRICT, "district_id": "TX_dallas"}

    async def _boom_then_ok(district: dict[str, Any], http: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if district["district_id"] == "FL_pinellas":
            raise RuntimeError("network error")
        return [_item()]

    with patch(
        "artemis.scouts.board_minutes.peer_scout.fetch_boarddocs", side_effect=_boom_then_ok
    ):
        scout = _scout(watch_list=[_DISTRICT, d2])
        findings = await scout._gather_findings()
    assert len(findings) == 1
    assert findings[0]["districtId"] == "TX_dallas"


# ===========================================================================
# Coverage list loading
# ===========================================================================


def test_default_watch_list_shape() -> None:
    assert _DEFAULT_PEER_WATCH_LIST
    for entry in _DEFAULT_PEER_WATCH_LIST:
        assert entry["district_id"] and entry["state"] and entry["boarddocs_url"]


def test_load_watch_list_roundtrip(tmp_path: Any) -> None:
    seed = [
        {"district_id": "CA_x", "state": "CA", "boarddocs_url": "https://go.boarddocs.com/ca/x/Board.nsf/Public"},
        {"district_id": "", "boarddocs_url": "https://bad"},  # malformed → skipped
        "not-a-dict",
    ]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed))
    loaded = load_watch_list(path)
    assert len(loaded) == 1
    assert loaded[0]["district_id"] == "CA_x"


def test_load_watch_list_missing_file() -> None:
    assert load_watch_list("/nonexistent/seed.json") == []
