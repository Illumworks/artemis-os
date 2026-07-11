"""BoardPeerValidationScout — board-meeting minutes scout v2 (peer validation).

Scans NON-customer district board meetings (BoardDocs) for substantive
discussion of Amira / screentime policy / AI-in-schools, classifies mention
sentiment with an LLM, and emits peer-validation findings.  Feeds the exec
report: district board actions + state policy on screentime/AI, sourced.

Key differences from the v1 ``BoardMinutesScout``:
- Retrieves agenda-item BODY text (``fetch_bodies=True``) — mentions live in
  the item detail/minutes, not the title.
- LLM mention+sentiment classification (keyword prefilter bounds cost;
  deterministic keyword fallback when no adapter is available).
- Pluggable customer-exclusion input (Salesforce later; injected now).
- Configurable district coverage list — designed for a prioritized national
  seed list (~500–800 districts) supplied later via ``load_watch_list()``;
  ships with a small verified starter set.  Never crawls thousands live:
  per-run coverage is capped by ``max_districts_per_run``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.board_minutes.classifier import (
    MentionClassification,
    classify_mention,
    keyword_classification,
    quick_relevance,
)
from artemis.scouts.board_minutes.client import fetch_boarddocs
from artemis.scouts.board_minutes.customers import (
    CustomerExclusionProvider,
    StaticCustomerExclusions,
    normalize_district_id,
)
from artemis.scouts.board_minutes.mapping import peer_item_to_finding

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coverage — starter watch list
# ---------------------------------------------------------------------------
# Small REAL starter set. The original three (BoardDocs slugs verified
# 2026-06-16 in the v1 watch list) plus a 2026-07-10 broadening pass — each
# added slug was live-checked (HTTP 200 + page-title/body content match to the
# named district) as part of the Screen-Time Watch scout-coverage expansion,
# picking large districts across priority states that had no board-level
# coverage yet. The national prioritized seed list (~500–800 districts) is
# supplied later via ``load_watch_list(path)`` — same dict shape, one JSON file.
_DEFAULT_PEER_WATCH_LIST: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "state": "FL",
        "boarddocs_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
    },
    {
        "district_id": "TX_dallas",
        "state": "TX",
        "boarddocs_url": "https://go.boarddocs.com/tx/disd/Board.nsf/Public",
    },
    {
        "district_id": "IN_msd_pike",
        "state": "IN",
        "boarddocs_url": "https://go.boarddocs.com/in/pike/Board.nsf/Public",
    },
    # --- 2026-07-10 broadening: verified 200 + title/body match ---
    {
        "district_id": "CA_san_diego",
        "state": "CA",
        # San Diego Unified School District, Board of Education.
        "boarddocs_url": "https://go.boarddocs.com/ca/sandi/Board.nsf/Public",
    },
    {
        "district_id": "TX_humble",
        "state": "TX",
        # Humble Independent School District (Houston metro).
        "boarddocs_url": "https://go.boarddocs.com/tx/hisd/Board.nsf/Public",
    },
    {
        "district_id": "OH_columbus",
        "state": "OH",
        # Columbus City Schools.
        "boarddocs_url": "https://go.boarddocs.com/oh/columbus/Board.nsf/Public",
    },
    {
        "district_id": "VA_fauquier",
        "state": "VA",
        # Fauquier County Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/va/fcps/Board.nsf/Public",
    },
    {
        "district_id": "NY_buffalo",
        "state": "NY",
        # Buffalo City School District.
        "boarddocs_url": "https://go.boarddocs.com/ny/buffalo/Board.nsf/Public",
    },
    {
        "district_id": "LA_jefferson_parish",
        "state": "LA",
        # Jefferson Parish Public School System.
        "boarddocs_url": "https://go.boarddocs.com/la/jppss/Board.nsf/Public",
    },
    {
        "district_id": "CO_aurora",
        "state": "CO",
        # Aurora Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/co/aurora/Board.nsf/Public",
    },
    {
        "district_id": "UT_canyons",
        "state": "UT",
        # Canyons School District.
        "boarddocs_url": "https://go.boarddocs.com/ut/canyons/Board.nsf/Public",
    },
    {
        "district_id": "SC_charleston",
        "state": "SC",
        # Charleston County School District.
        "boarddocs_url": "https://go.boarddocs.com/sc/charleston/Board.nsf/Public",
    },
    {
        "district_id": "SC_horry",
        "state": "SC",
        # Horry County Schools.
        "boarddocs_url": "https://go.boarddocs.com/sc/horry/Board.nsf/Public",
    },
]

# Hard cap on districts scanned in one run — the national seed list will be
# larger than any single run should attempt.
_DEFAULT_MAX_DISTRICTS_PER_RUN = 25


def load_watch_list(path: str | Path) -> list[dict[str, Any]]:
    """Load a district coverage list from a JSON file.

    Expected shape: ``[{"district_id": ..., "state": ..., "boarddocs_url": ...}, ...]``.
    Entries missing ``district_id`` or ``boarddocs_url`` are skipped with a
    warning.  Returns ``[]`` on any file/parse error (fail-safe).
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        _logger.warning("peer watch list %s could not be loaded: %s", path, exc)
        return []
    if not isinstance(raw, list):
        _logger.warning("peer watch list %s is not a JSON array", path)
        return []
    result: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("district_id") and entry.get("boarddocs_url"):
            result.append(entry)
        else:
            _logger.warning("peer watch list %s: skipping malformed entry %r", path, entry)
    return result


class BoardPeerValidationScout(BaseScout):
    """Peer-validation board minutes scout (v2).

    Parameters
    ----------
    config:
        Standard scout runtime config.
    watch_list:
        District coverage list (dicts with district_id / state /
        boarddocs_url).  Defaults to the small verified starter set.
    exclusions:
        Customer-exclusion provider (``async get_customer_district_ids()``).
        Defaults to the empty static set — inject the Salesforce-backed
        provider when it lands.
    max_districts_per_run:
        Per-run cap on districts scanned (protects against a huge seed list).
    _http_client:
        Inject a pre-built ``ScoutHttpClient`` — tests only.
    _adapter:
        Inject a pre-resolved LLM adapter — tests only.  When ``None`` the
        scout resolves one lazily; if resolution fails it degrades to the
        deterministic keyword classifier.
    """

    scout_type: ClassVar[str] = "board_peer_validation_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        watch_list: list[dict[str, Any]] | None = None,
        exclusions: CustomerExclusionProvider | None = None,
        max_districts_per_run: int = _DEFAULT_MAX_DISTRICTS_PER_RUN,
        _http_client: ScoutHttpClient | None = None,
        _adapter: Any = None,
    ) -> None:
        super().__init__(config)
        self._watch_list: list[dict[str, Any]] = (
            watch_list if watch_list is not None else list(_DEFAULT_PEER_WATCH_LIST)
        )
        self._exclusions: CustomerExclusionProvider = exclusions or StaticCustomerExclusions()
        self._max_districts = max(1, max_districts_per_run)
        self._http: ScoutHttpClient = _http_client or ScoutHttpClient(rate_limit=2.0)
        self._adapter: Any = _adapter
        self._adapter_resolution_failed = False

    # ------------------------------------------------------------------
    # LLM adapter resolution (lazy, fail-safe)
    # ------------------------------------------------------------------

    async def _get_adapter(self) -> Any | None:
        """Resolve the LLM adapter once; degrade to keyword mode on failure."""
        if self._adapter is not None or self._adapter_resolution_failed:
            return self._adapter
        try:
            from artemis.db import SessionLocal
            from artemis.providers.resolver import resolve_adapter_async

            async with SessionLocal() as session:
                self._adapter = await resolve_adapter_async(
                    provider="claude-code",
                    feature_tag="scout_board_peer_validation",
                    session=session,
                )
        except Exception as exc:
            self._adapter_resolution_failed = True
            _logger.warning(
                "BoardPeerValidationScout: no LLM adapter available (%s) — "
                "falling back to keyword-only classification.",
                exc,
            )
        return self._adapter

    async def _classify(self, title: str, body: str) -> MentionClassification | None:
        adapter = await self._get_adapter()
        if adapter is not None:
            result = await classify_mention(title, body, adapter=adapter)
            if result is not None:
                return result
            # LLM errored on this item — fall through to keyword mode so a
            # flaky provider doesn't blind the whole run.
        return keyword_classification(title, body)

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Scan covered districts for peer-validation mentions.

        Pipeline per district: customer-exclusion gate → BoardDocs fetch with
        item bodies → keyword prefilter → LLM classification → mapping.
        Per-district errors are caught and logged; collection continues.
        """
        try:
            exclude = {
                normalize_district_id(d)
                for d in await self._exclusions.get_customer_district_ids()
            }
        except Exception:
            _logger.exception(
                "BoardPeerValidationScout: exclusion provider failed — "
                "proceeding with EMPTY exclusion set (customer hits possible)."
            )
            exclude = set()

        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        scanned = 0

        for district in self._watch_list:
            if scanned >= self._max_districts:
                _logger.info(
                    "BoardPeerValidationScout: per-run district cap (%d) reached; "
                    "%d districts deferred to the next run.",
                    self._max_districts,
                    len(self._watch_list) - scanned,
                )
                break

            district_id: str = district.get("district_id", "unknown")
            if normalize_district_id(district_id) in exclude:
                _logger.debug(
                    "BoardPeerValidationScout: %s is a customer — skipping (exclusion filter).",
                    district_id,
                )
                continue

            scanned += 1
            try:
                items = await fetch_boarddocs(district, self._http, fetch_bodies=True)
                for item in items:
                    title: str = item.get("title", "")
                    body: str = item.get("text", "")
                    combined = f"{title}\n{body}"

                    if not quick_relevance(combined):
                        continue

                    classification = await self._classify(title, body)
                    finding = peer_item_to_finding(item, district, classification)
                    if finding is None:
                        continue

                    dedup_key = (district_id, item.get("source_url", ""))
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    findings.append(finding)
            except Exception as exc:
                _logger.warning(
                    "BoardPeerValidationScout: error processing district %s — skipping: %s",
                    district_id,
                    exc,
                )

        _logger.info(
            "BoardPeerValidationScout: %d peer-validation findings across %d scanned districts.",
            len(findings),
            scanned,
        )
        return findings
