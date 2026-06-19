"""Argus research interface -- identifies gaps and fetches missing dimensions.

Real implementation:
  - artemis.tools.news.news_api.search  (Google News RSS)
    => vendor news, board decisions, leadership appointments, reading-score coverage
  - artemis.tools.board_minutes.board_minutes.fetch (BoardDocs)
    => procurement / RFP timing, curriculum adoption votes, decision-maker names
  - artemis.tools.procurement.procurement.search (SAM.gov + Bonfire + ESBD + eMMA)
    => active RFPs, contract awards, fiscal-year windows
  - artemis.tools.usaspending.usaspending.search (USASpending.gov, no key)
    => Title I / federal grant eligibility and funding levels
  - artemis.tools.state_doe (State DOE RSS)
    => DOE news, literacy initiatives, reading-score coverage by state

All five tool fetches run in parallel via asyncio.gather so the total
wall-clock time is bounded by the slowest single source, not their sum.

One LLM synthesis pass (claude-code via complete_with_fallback) turns the
raw tool results into DistrictFindings + the recommended angle.  Argus stays
on Claude (never Codex) because he uses tools and needs the tool-use path.

Tool failure on a dimension -- skip it, continue. Never crash the whole run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.argus.drawer import Dimension, DistrictFinding
from artemis.providers.fallback import complete_with_fallback
from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ── Dimension -> tool mapping ─────────────────────────────────────────────────

# Maps each GAP dimension to a list of tool identifiers to call.
# Multiple tools per dimension = richer coverage.
_DIM_TOOLS: dict[str, list[str]] = {
    Dimension.CURRENT_VENDOR: ["news_api", "board_minutes"],
    Dimension.PROCUREMENT_TIMING: ["procurement", "board_minutes"],
    Dimension.DISTRICT_PROFILE: ["usaspending", "state_doe"],
    Dimension.DECISION_MAKERS: ["news_api", "board_minutes"],
    Dimension.PRIOR_AMIRA_RELATIONSHIP: [],          # internal lookup; no external tool yet
    Dimension.COMPETITOR_COMMITMENTS: ["news_api", "board_minutes"],
    Dimension.RECOMMENDED_ANGLE: [],                 # synthesised from other dims; no fetch
}

# Model for synthesis.  Haiku-class is fast and cheap; synthesis is lightweight.
_SYNTHESIS_MODEL = "claude-haiku-4-5"

# ── Per-tool fetch helpers ─────────────────────────────────────────────────────


async def _fetch_news(district_key: str, signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fetch Google News RSS items for a district."""
    # Build a targeted query from district_key + signal headline
    state = (signal or {}).get("state", "")
    headline_snippet = (signal or {}).get("headline", "")
    query = f"{district_key} school district"
    if headline_snippet:
        # Take first 60 chars to keep query focused
        query += f" {headline_snippet[:60]}"

    try:
        import urllib.parse
        import xml.etree.ElementTree as ET

        encoded = urllib.parse.quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        async with ScoutHttpClient(timeout=20.0) as http:
            resp = await http.get(url)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        channel = root.find("channel") or root
        items: list[dict[str, Any]] = []
        for item_el in channel.findall("item")[:15]:
            title_el = item_el.find("title")
            link_el = item_el.find("link")
            pub_el = item_el.find("pubDate")
            items.append({
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "link": (link_el.text or "").strip() if link_el is not None else "",
                "published": (pub_el.text or "").strip() if pub_el is not None else "",
            })
        return items
    except Exception as exc:
        _logger.warning("Argus._fetch_news: error for district_key=%r -- %s", district_key, exc)
        return []


async def _fetch_board_minutes(district_key: str, signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fetch BoardDocs minutes for a district if boarddocs_url is known from signal."""
    boarddocs_url: str | None = None
    if signal and isinstance(signal.get("provenance"), dict):
        boarddocs_url = signal["provenance"].get("boarddocs_url")
    if not boarddocs_url and signal:
        boarddocs_url = signal.get("boarddocs_url")

    if not boarddocs_url:
        _logger.debug(
            "Argus._fetch_board_minutes: no boarddocs_url for district_key=%r -- skipping",
            district_key,
        )
        return []

    try:
        from artemis.scouts.board_minutes.client import fetch_boarddocs

        district_cfg = {"district_id": district_key, "boarddocs_url": boarddocs_url}
        async with ScoutHttpClient(timeout=30.0) as http:
            items = await fetch_boarddocs(district_cfg, http)
        trimmed = [
            {
                "title": it.get("title", ""),
                "date": it.get("date", ""),
                "source_url": it.get("source_url", ""),
                "text": (it.get("text", ""))[:1500],
            }
            for it in items[:10]
        ]
        return trimmed
    except Exception as exc:
        _logger.warning(
            "Argus._fetch_board_minutes: error for district_key=%r -- %s", district_key, exc
        )
        return []


async def _fetch_procurement(district_key: str, signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fetch procurement opportunities relevant to a district."""
    state = (signal or {}).get("state", "")
    keyword = f"{district_key} literacy reading"

    try:
        # Bonfire + ESBD + eMMA + OpenGov require no key; SAM.gov needs env key
        from artemis.scouts.procurement.bonfire import fetch_all_bonfire_opportunities

        async with ScoutHttpClient(timeout=30.0, rate_limit=1.0) as http:
            postings = await fetch_all_bonfire_opportunities(http)

        # Filter to roughly relevant items (by district name fragment or state)
        key_lower = district_key.lower().replace("-", " ").replace("_", " ")
        filtered = [
            p for p in postings
            if key_lower in (p.get("agency", "") + p.get("title", "")).lower()
            or (state and state.upper() == p.get("state", "").upper())
        ]
        return [
            {
                "title": p.get("title", ""),
                "agency": p.get("agency", ""),
                "posted_date": p.get("posted_date", ""),
                "due_date": p.get("due_date", ""),
                "url": p.get("source_url", ""),
                "description": (p.get("description", ""))[:500],
            }
            for p in filtered[:10]
        ]
    except Exception as exc:
        _logger.warning(
            "Argus._fetch_procurement: error for district_key=%r -- %s", district_key, exc
        )
        return []


async def _fetch_usaspending(district_key: str, signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fetch federal grant awards relevant to a district's state."""
    state = (signal or {}).get("state", "")
    states = [state.upper()] if state else []

    try:
        from datetime import date, timedelta

        from artemis.tools.usaspending import (
            _EDUCATION_CFDA,
            _GRANT_AWARD_TYPES,
            _SEARCH_URL,
            _build_recipient_locations,
            _build_time_period,
            _parse_result,
        )

        body: dict[str, Any] = {
            "filters": {
                "award_type_codes": _GRANT_AWARD_TYPES,
                "recipient_locations": _build_recipient_locations(states) if states else [{"country": "USA"}],
                "time_period": _build_time_period(365),
                "program_numbers": _EDUCATION_CFDA,
            },
            "fields": [
                "Award ID", "Recipient Name", "recipient_location_state_code",
                "Award Amount", "cfda_number", "cfda_program_title",
                "Start Date", "End Date", "Description",
            ],
            "sort": "Last Modified Date",
            "order": "desc",
            "limit": 10,
            "page": 1,
        }

        # Filter to recipient name containing district_key fragment if useful
        async with ScoutHttpClient(timeout=30.0, rate_limit=2.0) as http:
            resp = await http.post(_SEARCH_URL, json=body)

        if resp.status_code != 200:
            return []
        payload = resp.json()
        results = payload.get("results") or []
        awards: list[dict[str, Any]] = []
        key_lower = district_key.lower().replace("-", " ").replace("_", " ")
        for raw in results[:15]:
            if not isinstance(raw, dict):
                continue
            try:
                award = _parse_result(raw)
                # Only include if recipient name loosely matches district
                recipient = award.get("recipient_name", "").lower()
                if key_lower in recipient or (states and award.get("recipient_state", "") in states):
                    awards.append(award)
            except Exception:
                pass
        return awards[:8]
    except Exception as exc:
        _logger.warning(
            "Argus._fetch_usaspending: error for district_key=%r -- %s", district_key, exc
        )
        return []


async def _fetch_state_doe(district_key: str, signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fetch state DOE RSS items for a district's state."""
    state = (signal or {}).get("state", "").upper()
    if not state:
        return []
    try:
        from artemis.scouts.state_doe.sources import fetch_doe_rss

        async with ScoutHttpClient(timeout=20.0) as http:
            items = await fetch_doe_rss(state, http)
        return items[:10]
    except Exception as exc:
        _logger.warning(
            "Argus._fetch_state_doe: error for state=%r district_key=%r -- %s",
            state, district_key, exc,
        )
        return []


# ── Parallel fetch orchestrator ────────────────────────────────────────────────


async def _gather_tool_results(
    district_key: str,
    dimensions: list[str],
    signal: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Fetch all needed tools in parallel; return results keyed by tool name.

    Each tool is fetched at most once even if multiple dimensions need it.
    Tool failures are caught per-tool -- a failure in one never blocks others.
    """
    needed_tools: set[str] = set()
    for dim in dimensions:
        needed_tools.update(_DIM_TOOLS.get(dim, []))

    if not needed_tools:
        return {}

    _tool_fetchers = {
        "news_api": _fetch_news,
        "board_minutes": _fetch_board_minutes,
        "procurement": _fetch_procurement,
        "usaspending": _fetch_usaspending,
        "state_doe": _fetch_state_doe,
    }

    # Per-tool timeout: a single hanging external call must not stretch the whole
    # ~54s budget indefinitely.  15s is generous for an HTTP fetch; if a source
    # is down it returns empty in under 15s so the other tools can still complete.
    _TOOL_TIMEOUT_S = 15.0

    async def _safe_fetch(name: str) -> tuple[str, Any]:
        fetcher = _tool_fetchers.get(name)
        if fetcher is None:
            return name, []
        try:
            result = await asyncio.wait_for(
                fetcher(district_key, signal),
                timeout=_TOOL_TIMEOUT_S,
            )
            return name, result
        except asyncio.TimeoutError:
            _logger.warning(
                "Argus._gather_tool_results: tool=%r timed out after %.0fs (skipped)",
                name,
                _TOOL_TIMEOUT_S,
            )
            return name, []
        except Exception as exc:
            _logger.warning(
                "Argus._gather_tool_results: tool=%r raised -- %s (skipped)", name, exc
            )
            return name, []

    pairs = await asyncio.gather(*(_safe_fetch(t) for t in sorted(needed_tools)))
    return {name: result for name, result in pairs}


# ── LLM synthesis pass ────────────────────────────────────────────────────────


def _build_synthesis_prompt(
    district_key: str,
    dimensions: list[str],
    tool_results: dict[str, Any],
    signal: dict[str, Any] | None,
) -> str:
    """Build the prompt that asks the LLM to turn raw tool results into findings."""
    signal_section = ""
    if signal:
        headline = signal.get("headline", "")
        state = signal.get("state", "")
        signal_section = (
            f"Triggering signal:\n  headline: {headline}\n  state: {state}\n\n"
        )

    tool_section_parts: list[str] = []
    for tool_name, results in tool_results.items():
        if not results:
            continue
        try:
            excerpt = json.dumps(results, indent=2)[:3000]
        except Exception:
            excerpt = str(results)[:3000]
        tool_section_parts.append(f"### {tool_name}\n{excerpt}")
    tool_section = "\n\n".join(tool_section_parts) or "(no tool data returned)"

    dims_list = "\n".join(f"  - {d}" for d in dimensions if d != Dimension.RECOMMENDED_ANGLE)

    return f"""You are Argus, a dedicated marketing research agent for Amira Learning.
Amira Learning sells AI-powered literacy software (reading, phonics, dyslexia support) to K-12 school districts.

District under research: {district_key}
{signal_section}
Dimensions to research:
{dims_list}

Raw tool results:
{tool_section}

---
Task: Extract a concise DistrictFinding for each dimension listed above.
For EACH dimension, output a JSON object on its own line with these exact keys:
  dimension (string), value (string, 1-3 sentences), source (string), url (string or null)

Rules:
- dimension must be one of: current_vendor, procurement_timing, district_profile, decision_makers, prior_amira_relationship, competitor_commitments
- value must be specific and actionable, not vague. If data is insufficient, write "Insufficient data from available sources."
- source must be one of: "Argus/news_api", "Argus/board_minutes", "Argus/procurement", "Argus/usaspending", "Argus/state_doe", or "Argus" if synthesised from multiple sources
- url should be the most relevant link from the tool data, or null
- Only include dimensions from the list above; skip RECOMMENDED_ANGLE (handled separately)
- Output ONLY JSON lines, no prose, no markdown fences

Then, on a final line, output a single JSON object for the recommended angle:
  {{"dimension": "recommended_angle", "value": "<2-3 sentence recommended outreach angle for Amira's sales team>", "source": "Argus", "url": null}}

The recommended angle must mention: current vendor situation (if known), procurement timing (if known), and Amira's strongest positioning for this district.
"""


async def _run_synthesis(
    district_key: str,
    dimensions: list[str],
    tool_results: dict[str, Any],
    signal: dict[str, Any] | None,
    adapter: Any | None = None,
) -> list[DistrictFinding]:
    """Call the LLM to synthesise tool results into DistrictFindings.

    adapter is for tests only -- production uses complete_with_fallback.
    Returns an empty list on any LLM failure (logged as warning).
    """
    prompt = _build_synthesis_prompt(district_key, dimensions, tool_results, signal)
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        system=(
            "You are Argus, a precise marketing research agent. "
            "Output only valid JSON lines, no prose."
        ),
        model=_SYNTHESIS_MODEL,
        max_tokens=2048,
        cache_system=False,
        cache_tools=False,
    )

    try:
        if adapter is not None:
            resp = await adapter.complete(req)
        else:
            resp = await complete_with_fallback(
                req,
                primary="claude-code",
                fallback="claude-code",
            )
    except Exception as exc:
        _logger.warning(
            "Argus._run_synthesis: LLM call failed for district_key=%r -- %s", district_key, exc
        )
        return []

    raw_text = "".join(
        block.text for block in resp.message.content if isinstance(block, TextBlock)
    )

    return _parse_synthesis_output(raw_text, district_key)


def _parse_synthesis_output(raw_text: str, district_key: str) -> list[DistrictFinding]:
    """Parse LLM output into DistrictFindings.

    Tolerates partial output: lines that cannot be parsed are skipped.
    """
    now_date = datetime.now(UTC).date().isoformat()
    findings: list[DistrictFinding] = []
    seen_dims: set[str] = set()

    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        # Strip any markdown fences that sneak in
        if line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Try to extract a JSON object fragment
            start = line.find("{")
            end = line.rfind("}")
            if start >= 0 and end > start:
                try:
                    obj = json.loads(line[start:end + 1])
                except json.JSONDecodeError:
                    _logger.debug("Argus._parse_synthesis_output: skipping unparseable line: %r", line[:80])
                    continue
            else:
                continue

        dim = str(obj.get("dimension", "")).strip()
        value = str(obj.get("value", "")).strip()
        source = str(obj.get("source", "Argus")).strip()
        url = obj.get("url") or None
        if url:
            url = str(url).strip() or None

        if not dim or not value:
            continue
        if dim in seen_dims:
            continue

        # Ensure source always starts with "Argus"
        if not source.startswith("Argus"):
            source = f"Argus/{source}"

        seen_dims.add(dim)
        findings.append(
            DistrictFinding(
                dimension=dim,
                value=value,
                source=source,
                url=url,
                researched_at=now_date,
                raw_notes={"synthesis": "llm", "model": _SYNTHESIS_MODEL},
            )
        )

    return findings


# ── Main research function ────────────────────────────────────────────────────


async def research_dimensions(
    district_key: str,
    dimensions: list[str],
    signal: dict[str, Any] | None = None,
    *,
    _adapter: Any = None,
) -> list[DistrictFinding]:
    """Research the given gap dimensions for a district.

    Replaces stub_research_dimensions.

    Contract:
    - Accept (district_key, dimensions, signal) as positional args.
    - Return list[DistrictFinding]. Empty list = nothing found.
    - NEVER raise -- catches internally and logs; returns partial results.
    - Tags every finding source="Argus" (or "Argus/<tool-name>").

    _adapter: test-only injection; bypasses complete_with_fallback.

    Steps:
    1. Parallel fetch all needed tools (asyncio.gather).
    2. LLM synthesis pass: raw results -> DistrictFindings + recommended angle.
    3. Fill in any dimensions the LLM missed with an "insufficient data" stub so
       the drawer doesn't leave permanent gaps on noisy runs.
    """
    _logger.info(
        "Argus research_dimensions: district_key=%r, dimensions=%r",
        district_key,
        dimensions,
    )

    # ── 1. Parallel tool fetches ──────────────────────────────────────────────
    try:
        tool_results = await _gather_tool_results(district_key, dimensions, signal)
    except Exception as exc:
        _logger.warning(
            "Argus research_dimensions: _gather_tool_results raised -- %s (continuing with empty results)",
            exc,
        )
        tool_results = {}

    # ── 2. LLM synthesis ─────────────────────────────────────────────────────
    # Exclude RECOMMENDED_ANGLE and PRIOR_AMIRA_RELATIONSHIP from synthesis prompt
    # (angle is synthesised from other dims; prior relationship has no external tool yet).
    synth_dims = [
        d for d in dimensions
        if d not in (Dimension.RECOMMENDED_ANGLE, Dimension.PRIOR_AMIRA_RELATIONSHIP)
    ]

    findings: list[DistrictFinding] = []
    if synth_dims:
        try:
            findings = await _run_synthesis(
                district_key, synth_dims, tool_results, signal, adapter=_adapter
            )
        except Exception as exc:
            _logger.warning(
                "Argus research_dimensions: synthesis failed for district_key=%r -- %s (returning empty)",
                district_key,
                exc,
            )
            findings = []

    # ── 3. Fallback for dimensions the LLM missed ─────────────────────────────
    now_date = datetime.now(UTC).date().isoformat()
    found_dims = {f.dimension for f in findings}

    for dim in dimensions:
        if dim in found_dims:
            continue
        if dim == Dimension.RECOMMENDED_ANGLE:
            # Synthesised by flow.py -- skip here
            continue
        if dim == Dimension.PRIOR_AMIRA_RELATIONSHIP:
            # No external tool yet -- emit a clear "no data" finding so flow.py
            # doesn't re-research it next time (it will be stale after STALENESS_DAYS).
            findings.append(
                DistrictFinding(
                    dimension=dim,
                    value="No prior Amira relationship data available from external sources. Confirm via CRM.",
                    source="Argus",
                    url=None,
                    researched_at=now_date,
                    raw_notes={"note": "no_external_tool"},
                )
            )
            continue
        # Generic "insufficient data" fallback -- persisted so the dimension is
        # not re-researched on the same run if the LLM skipped it.
        findings.append(
            DistrictFinding(
                dimension=dim,
                value="Insufficient data from available sources for this research pass.",
                source="Argus",
                url=None,
                researched_at=now_date,
                raw_notes={"note": "synthesis_gap"},
            )
        )

    _logger.info(
        "Argus research_dimensions: district_key=%r -> %d findings from %d dimensions",
        district_key,
        len(findings),
        len(dimensions),
    )
    return findings


# ── Backwards compatibility -- stub still importable for tests ────────────────


async def stub_research_dimensions(
    district_key: str,
    dimensions: list[str],
    signal: dict[str, Any] | None = None,
) -> list[DistrictFinding]:
    """Preserved for test compatibility. Returns clearly-labelled stubs.

    The real implementation is research_dimensions above.
    """
    now_date = datetime.now(UTC).date().isoformat()
    return [
        DistrictFinding(
            dimension=dim,
            value=f"[STUB] No real research performed for dimension '{dim}' (district: {district_key}).",
            source="Argus/stub",
            url=None,
            researched_at=now_date,
            raw_notes={"stub": True, "signal": signal},
        )
        for dim in dimensions
    ]


# ── Gap identification ────────────────────────────────────────────────────────


# Dimensions that are always researched on a first pass. Callers can override.
PRIMARY_DIMENSIONS: list[str] = [
    Dimension.CURRENT_VENDOR,
    Dimension.PROCUREMENT_TIMING,
    Dimension.DISTRICT_PROFILE,
    Dimension.DECISION_MAKERS,
    Dimension.PRIOR_AMIRA_RELATIONSHIP,
    Dimension.COMPETITOR_COMMITMENTS,
    Dimension.RECOMMENDED_ANGLE,
]

# How many days before a finding is considered stale and should be refreshed.
STALENESS_DAYS = 90


def identify_gaps(
    existing: dict[str, DistrictFinding],
    *,
    required_dimensions: list[str] | None = None,
    as_of_date: str | None = None,
) -> list[str]:
    """Return a list of dimension names that are missing or stale.

    existing         -- dict[dimension -> DistrictFinding] from read_district_drawer.
    required_dims    -- which dimensions to check (defaults to PRIMARY_DIMENSIONS).
    as_of_date       -- ISO date string to compare against researched_at (default: today).

    A dimension is considered stale when its researched_at date is older than
    STALENESS_DAYS from as_of_date. Missing dimensions (not in existing) are
    always included in the gap list.
    """
    dims = required_dimensions if required_dimensions is not None else PRIMARY_DIMENSIONS
    today_str = as_of_date or datetime.now(UTC).date().isoformat()
    today = _parse_date(today_str)

    gaps: list[str] = []
    for dim in dims:
        if dim not in existing:
            gaps.append(dim)
            continue
        finding = existing[dim]
        researched_date = _parse_date(finding.researched_at)
        if researched_date is None:
            # Cannot parse date -- treat as stale
            gaps.append(dim)
            continue
        if today is not None and (today - researched_date).days > STALENESS_DAYS:
            _logger.debug(
                "Argus: dimension=%r for district_key is stale (researched_at=%r)",
                dim,
                finding.researched_at,
            )
            gaps.append(dim)
    return gaps


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date/datetime string, return None on failure."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            return datetime.strptime(date_str[:19], fmt[:len(date_str[:19])])
        except ValueError:
            continue
    return None
