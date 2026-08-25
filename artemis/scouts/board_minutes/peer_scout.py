"""BoardPeerValidationScout — board-meeting minutes scout v2 (peer validation).

Scans district board meetings (BoardDocs) for substantive discussion of
Amira / screentime policy / AI-in-schools, classifies mention sentiment with
an LLM, and emits findings.  Feeds the exec report: district board actions +
state policy on screentime/AI, sourced.

Two modes, same code path:
- GENERAL board-intel mode (current, since 2026-07-11): the exclusion
  provider's set is empty, so EVERY covered district's board mentions
  surface — customer and non-customer alike. This is how the scout runs
  today, live, ahead of the (delayed) Salesforce customer list.
- PEER-VALIDATION mode (deferred): once a customer list is injected into the
  exclusion provider (see ``customers.py``), customer districts are filtered
  out and only NON-customer mentions surface — the "even districts that
  don't use us are talking about this" signal. No code change needed to
  flip modes; only the injected ``exclusions`` provider changes.

Key differences from the v1 ``BoardMinutesScout``:
- Retrieves agenda-item BODY text (``fetch_bodies=True``) — mentions live in
  the item detail/minutes, not the title.
- LLM mention+sentiment classification (keyword prefilter bounds cost;
  deterministic keyword fallback when no adapter is available).
- Pluggable customer-exclusion input (Salesforce later; injected now — empty
  by default, meaning GENERAL mode as described above).
- Configurable district coverage list — designed for a prioritized national
  seed list (~500–800 districts) supplied later via ``load_watch_list()``;
  ships with a verified starter set (27 districts as of 2026-07-11, covering
  every priority state). Never crawls thousands live: per-run coverage is
  capped by ``max_districts_per_run``.
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
#
# 2026-07-11 second broadening pass (board-meeting scout go-live, seeded ahead
# of the delayed Salesforce customer list): added 14 more large districts —
# every one of Josh's spec priority_states (FL, IN, MD, MO, IL, TX) plus the
# legislative scout's broader priority set (CA, NY, GA, NC, OH) now has
# board-level coverage. Each added slug was live-checked the same way (HTTP
# 200 + page-title match) on 2026-07-11. Candidates that could NOT be verified
# (no live BoardDocs presence found — they run Legistar/Simbli/their own
# portal instead) were left OUT rather than guessed: Houston ISD (TX), Fort
# Worth ISD (TX), San Antonio ISD (TX), Fresno Unified (CA), Fort Wayne
# Community Schools (IN — the "nacs" slug found is a different district,
# Northwest Allen County Schools), DeKalb County Schools (GA — no BoardDocs
# site found).
_DEFAULT_PEER_WATCH_LIST: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "nces_id": "1201560",
        "state": "FL",
        "boarddocs_url": "https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public",
    },
    {
        "district_id": "TX_dallas",
        "nces_id": "4816230",
        "state": "TX",
        "boarddocs_url": "https://go.boarddocs.com/tx/disd/Board.nsf/Public",
    },
    {
        "district_id": "IN_msd_pike",
        "nces_id": "1808910",
        "state": "IN",
        "boarddocs_url": "https://go.boarddocs.com/in/pike/Board.nsf/Public",
    },
    # --- 2026-07-10 broadening: verified 200 + title/body match ---
    {
        "district_id": "CA_san_diego",
        "nces_id": "0634320",
        "state": "CA",
        # San Diego Unified School District, Board of Education.
        "boarddocs_url": "https://go.boarddocs.com/ca/sandi/Board.nsf/Public",
    },
    {
        "district_id": "TX_humble",
        "nces_id": "4823910",
        "state": "TX",
        # Humble Independent School District (Houston metro).
        "boarddocs_url": "https://go.boarddocs.com/tx/hisd/Board.nsf/Public",
    },
    {
        "district_id": "OH_columbus",
        "nces_id": "3904380",
        "state": "OH",
        # Columbus City Schools.
        "boarddocs_url": "https://go.boarddocs.com/oh/columbus/Board.nsf/Public",
    },
    {
        "district_id": "VA_fauquier",
        "nces_id": "5101320",
        "state": "VA",
        # Fauquier County Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/va/fcps/Board.nsf/Public",
    },
    {
        "district_id": "NY_buffalo",
        "nces_id": "3605850",
        "state": "NY",
        # Buffalo City School District.
        "boarddocs_url": "https://go.boarddocs.com/ny/buffalo/Board.nsf/Public",
    },
    {
        "district_id": "LA_jefferson_parish",
        "nces_id": "2200840",
        "state": "LA",
        # Jefferson Parish Public School System.
        "boarddocs_url": "https://go.boarddocs.com/la/jppss/Board.nsf/Public",
    },
    {
        "district_id": "CO_aurora",
        "nces_id": "0802340",
        "state": "CO",
        # Aurora Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/co/aurora/Board.nsf/Public",
    },
    {
        "district_id": "UT_canyons",
        "nces_id": "4900142",
        "state": "UT",
        # Canyons School District.
        "boarddocs_url": "https://go.boarddocs.com/ut/canyons/Board.nsf/Public",
    },
    {
        "district_id": "SC_charleston",
        "nces_id": "4501440",
        "state": "SC",
        # Charleston County School District.
        "boarddocs_url": "https://go.boarddocs.com/sc/charleston/Board.nsf/Public",
    },
    {
        "district_id": "SC_horry",
        "nces_id": "4502490",
        "state": "SC",
        # Horry County Schools.
        "boarddocs_url": "https://go.boarddocs.com/sc/horry/Board.nsf/Public",
    },
    # --- 2026-07-11 board-scout go-live broadening: verified 200 + title match ---
    {
        "district_id": "MD_prince_georges",
        "nces_id": "2400510",
        "state": "MD",
        # Prince George's County Board of Education (MABE-hosted BoardDocs).
        "boarddocs_url": "https://go.boarddocs.com/mabe/pgcps/Board.nsf/Public",
    },
    {
        "district_id": "MD_montgomery",
        "nces_id": "2400480",
        "state": "MD",
        # Montgomery County Board of Education (MABE-hosted BoardDocs).
        "boarddocs_url": "https://go.boarddocs.com/mabe/mcpsmd/Board.nsf/Public",
    },
    {
        "district_id": "MO_st_louis",
        "nces_id": "2929280",
        "state": "MO",
        # Board of Education of the City of St. Louis.
        "boarddocs_url": "https://go.boarddocs.com/mo/stlps/Board.nsf/Public",
    },
    {
        "district_id": "MO_kansas_city",
        "nces_id": "2916400",
        "state": "MO",
        # Kansas City Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/mo/kanscsd/Board.nsf/Public",
    },
    {
        "district_id": "IL_elgin_u46",
        "nces_id": "1713710",
        "state": "IL",
        # School District U-46 (Elgin, IL) — second-largest district in IL.
        "boarddocs_url": "https://go.boarddocs.com/il/u46/Board.nsf/Public",
    },
    {
        "district_id": "IL_rockford",
        "nces_id": "1734510",
        "state": "IL",
        # Rockford Public School District 205.
        "boarddocs_url": "https://go.boarddocs.com/il/rps205/Board.nsf/Public",
    },
    {
        "district_id": "FL_miami_dade",
        "nces_id": "1200390",
        "state": "FL",
        # Miami-Dade County Public Schools — largest district in FL.
        "boarddocs_url": "https://go.boarddocs.com/fl/sbmd/Board.nsf/Public",
    },
    {
        "district_id": "IN_indianapolis",
        "nces_id": "1804770",
        "state": "IN",
        # Indianapolis Public Schools.
        "boarddocs_url": "https://go.boarddocs.com/in/indps/Board.nsf/Public",
    },
    {
        "district_id": "OH_cleveland",
        "nces_id": "3904378",
        "state": "OH",
        # Cleveland Metropolitan School District.
        "boarddocs_url": "https://go.boarddocs.com/oh/cmsd/Board.nsf/Public",
    },
    {
        "district_id": "NY_rochester",
        "nces_id": "3624750",
        "state": "NY",
        # Rochester City School District.
        "boarddocs_url": "https://go.boarddocs.com/ny/rochny/Board.nsf/Public",
    },
    {
        "district_id": "NC_charlotte_mecklenburg",
        "nces_id": "3702970",
        "state": "NC",
        # Charlotte-Mecklenburg Schools.
        "boarddocs_url": "https://go.boarddocs.com/nc/cmsnc/Board.nsf/Public",
    },
    {
        "district_id": "NC_wake",
        "nces_id": "3704720",
        "state": "NC",
        # Wake County Public School System — largest district in NC.
        "boarddocs_url": "https://go.boarddocs.com/nc/wcpsnc/Board.nsf/Public",
    },
    {
        "district_id": "GA_gwinnett",
        "nces_id": "1302550",
        "state": "GA",
        # Gwinnett County Public Schools — largest district in GA.
        "boarddocs_url": "https://go.boarddocs.com/ga/gcps/Board.nsf/Public",
    },
    {
        "district_id": "GA_fulton",
        "nces_id": "1302280",
        "state": "GA",
        # Fulton County Schools (Atlanta metro).
        "boarddocs_url": "https://go.boarddocs.com/ga/fcss/Board.nsf/Public",
    },
    # --- Georgia crisis-coverage pass (2026-08-21) ---
    # Added because GA board coverage was Gwinnett + Fulton only, and Gwinnett
    # is not an Amira district (it screens with i-Ready), leaving effectively
    # one relevant board.  Both slugs below were live-checked on 2026-08-21
    # (HTTP 200 + address in page title matching the named district) with a
    # browser User-Agent; BoardDocs 404s a bare default UA.
    {
        "district_id": "GA_chatham",
        "nces_id": "1301020",
        "state": "GA",
        # Savannah-Chatham County Public Schools. Title check: "208 Bull Street
        # | Savannah, GA 31401".  Holds the only located GA board contract for
        # Amira (2023-04-12, HMH "AMIRA Suite" renewal, sole source, $352,000,
        # ESSER/CARES II funded -- that funding source has since expired).
        "boarddocs_url": "https://go.boarddocs.com/ga/sccs/Board.nsf/Public",
    },
    {
        "district_id": "GA_marietta",
        "nces_id": "1303510",
        "state": "GA",
        # Marietta City Schools. Title check: "250 Howard Street | Marietta, GA
        # 30060".  Not an Amira district as far as we know -- included because it
        # is the state's live AI flashpoint: a July 2026 board resolution states
        # AI is "not recommended for elementary classrooms" and the
        # superintendent went further on TV in August.  Whichever screener
        # Marietta names becomes the template other GA boards copy.
        "boarddocs_url": "https://go.boarddocs.com/ga/mcs/Board.nsf/Public",
    },
    # NOT on BoardDocs -- checked 2026-08-21, do not re-guess these slugs.
    # Probed and 404: aps, atlanta, cobb, ccsd, clayton, ccps, marietta, dekalb,
    # dcsd, henry, houston, bibb, muscogee, rcss.  Atlanta Public Schools,
    # Cobb, Clayton and DeKalb -- our four most exposed Georgia districts, and
    # APS is where the July 2026 story originated -- all run GSBA's Simbli /
    # eBOARDsolutions portal, which is not search-indexed and needs its own
    # adapter.  That is the largest single gap in Georgia board coverage.
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
        Defaults to the empty static set — that is GENERAL board-intel mode
        (all districts' mentions surface). Inject the Salesforce-backed
        provider (or a populated ``StaticCustomerExclusions``) once the
        customer list lands to switch to PEER-VALIDATION mode (non-customer
        mentions only) — no other code change required.
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
                normalize_district_id(d) for d in await self._exclusions.get_customer_district_ids()
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
            # Prefer the NCES district id. The Salesforce-backed exclusion
            # provider keys on it (Account.NCES_District_ID__c joins
            # districts.nces_id, both 7-digit), while `district_id` here is a
            # hand-authored slug like "FL_pinellas" that no external system
            # knows. Falling back to the slug keeps a StaticCustomerExclusions
            # set of slugs working for tests and manual injection.
            nces_id: str = str(district.get("nces_id") or "")
            match_key = nces_id or district_id
            if normalize_district_id(match_key) in exclude:
                _logger.debug(
                    "BoardPeerValidationScout: %s (nces=%s) is a customer — "
                    "skipping (exclusion filter).",
                    district_id,
                    nces_id or "-",
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
