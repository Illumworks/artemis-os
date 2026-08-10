"""Unit tests — Argus district research drawer integration with the campaign brief assembler.

All tests are UNIT tests: no DB, no env vars, no LLM calls.
read_district_drawer and the repository helpers are mocked throughout.

Test plan
---------
A1 — _resolve_district_key returns signal.district_id when present.
A2 — _resolve_district_key falls back to str(resolved_district_id) when district_id is absent.
A3 — _resolve_district_key returns None when both fields are absent.
A4 — _resolve_district_key returns None when primary_signal is None.
A5 — _read_argus_context returns None for a None district_key (no drawer read attempted).
A6 — _read_argus_context returns None when read_district_drawer returns an empty dict.
A7 — _read_argus_context returns a structured dict when findings exist.
A8 — _read_argus_context captures recommended_angle separately and excludes it from findings.
A9 — _read_argus_context returns None and logs a warning when read_district_drawer raises.
B1 — build_campaign_initiation_context includes argus_research key when drawer has findings.
B2 — build_campaign_initiation_context omits argus_research key when drawer is empty (no regression).
B3 — build_campaign_initiation_context omits argus_research when primary_signal has no district.
C1 — _build_campaign_initiation_prompt includes Argus section when argus_research is present.
C2 — _build_campaign_initiation_prompt produces same prompt as before when argus_research absent.
C3 — _build_campaign_initiation_prompt includes recommended_angle when present.
C4 — _build_campaign_initiation_prompt prompt does not include Argus section header when no research.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.marketing.brief_assembler import (
    _build_campaign_initiation_prompt,
    _read_argus_context,
    _resolve_district_key,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_signal(
    *,
    district_id: str | None = "TX-042",
    resolved_district_id: int | None = 42,
) -> MagicMock:
    sig = MagicMock()
    sig.district_id = district_id
    sig.resolved_district_id = resolved_district_id
    return sig


def _make_finding(
    *,
    dimension: str = "current_vendor",
    value: str = "Lexia Reading Core5",
    source: str = "Argus",
    researched_at: str = "2026-06-17",
) -> MagicMock:
    f = MagicMock()
    f.dimension = dimension
    f.value = value
    f.source = source
    f.researched_at = researched_at
    return f


def _make_context(*, with_argus: bool = True) -> dict[str, Any]:
    """Minimal context dict for prompt-builder tests."""
    ctx: dict[str, Any] = {
        "candidate": {
            "id": 1,
            "campaign_family": "obc",
            "decision_state": "approved",
            "predecessor_id": None,
        },
        "signals": [],
        "predecessor": None,
        "default_target_scope": {"base": "all"},
        "active_deliverable_type_slugs": ["outreach_email"],
        "default_recommended_deliverable_types": ["outreach_email"],
    }
    if with_argus:
        ctx["argus_research"] = {
            "district_key": "TX-042",
            "attributed_to": "Argus",
            "findings": {
                "current_vendor": {
                    "value": "Lexia Reading Core5",
                    "source": "Argus",
                    "researched_at": "2026-06-17",
                },
                "procurement_timing": {
                    "value": "RFP opens Q1 FY2027",
                    "source": "Argus",
                    "researched_at": "2026-06-17",
                },
            },
            "recommended_angle": "Position as complement to Lexia; target Q1 FY2027 window.",
        }
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# A-series: _resolve_district_key
# ─────────────────────────────────────────────────────────────────────────────


def test_a1_resolve_uses_district_id_string() -> None:
    """A1 — district_id string takes priority over resolved_district_id."""
    sig = _make_signal(district_id="TX-042", resolved_district_id=42)
    assert _resolve_district_key(sig) == "TX-042"


def test_a2_resolve_falls_back_to_resolved_district_id() -> None:
    """A2 — When district_id is None, falls back to str(resolved_district_id)."""
    sig = _make_signal(district_id=None, resolved_district_id=42)
    assert _resolve_district_key(sig) == "42"


def test_a2b_resolve_falls_back_to_resolved_when_district_id_blank() -> None:
    """A2b — Blank district_id string is treated as missing."""
    sig = _make_signal(district_id="   ", resolved_district_id=99)
    assert _resolve_district_key(sig) == "99"


def test_a3_resolve_returns_none_when_no_district() -> None:
    """A3 — Both fields absent → None."""
    sig = _make_signal(district_id=None, resolved_district_id=None)
    assert _resolve_district_key(sig) is None


def test_a4_resolve_returns_none_for_none_signal() -> None:
    """A4 — None primary_signal → None."""
    assert _resolve_district_key(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# A-series: _read_argus_context
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a5_read_returns_none_for_none_district_key() -> None:
    """A5 — None district_key → returns None without touching the DB."""
    session = AsyncMock()
    with patch("artemis.argus.drawer.read_district_drawer") as mock_read:
        result = await _read_argus_context(session, None)
    assert result is None
    mock_read.assert_not_called()


@pytest.mark.asyncio
async def test_a6_read_returns_none_for_empty_drawer() -> None:
    """A6 — Empty drawer → returns None."""
    session = AsyncMock()
    with patch("artemis.argus.drawer.read_district_drawer", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = {}
        result = await _read_argus_context(session, "TX-042")
    assert result is None


@pytest.mark.asyncio
async def test_a7_read_returns_structured_dict_with_findings() -> None:
    """A7 — Non-empty drawer → structured dict with findings payload."""
    session = AsyncMock()
    findings = {
        "current_vendor": _make_finding(dimension="current_vendor", value="Lexia"),
        "procurement_timing": _make_finding(dimension="procurement_timing", value="Q1 FY2027"),
    }
    with patch("artemis.argus.drawer.read_district_drawer", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = findings
        result = await _read_argus_context(session, "TX-042")

    assert result is not None
    assert result["district_key"] == "TX-042"
    assert result["attributed_to"] == "Argus"
    assert "current_vendor" in result["findings"]
    assert result["findings"]["current_vendor"]["value"] == "Lexia"
    assert result["recommended_angle"] is None  # no RECOMMENDED_ANGLE dimension present


@pytest.mark.asyncio
async def test_a8_read_separates_recommended_angle_from_findings() -> None:
    """A8 — recommended_angle dimension goes into its own key, not findings."""
    from artemis.argus.drawer import Dimension

    session = AsyncMock()
    findings = {
        "current_vendor": _make_finding(dimension="current_vendor", value="Lexia"),
        Dimension.RECOMMENDED_ANGLE: _make_finding(
            dimension=Dimension.RECOMMENDED_ANGLE,
            value="Position as complement.",
        ),
    }
    with patch("artemis.argus.drawer.read_district_drawer", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = findings
        result = await _read_argus_context(session, "TX-042")

    assert result is not None
    assert result["recommended_angle"] == "Position as complement."
    assert Dimension.RECOMMENDED_ANGLE not in result["findings"]
    assert "current_vendor" in result["findings"]


@pytest.mark.asyncio
async def test_a9_read_returns_none_on_exception(caplog: Any) -> None:
    """A9 — read_district_drawer raises → returns None (graceful), logs warning."""
    import logging

    session = AsyncMock()
    with patch("artemis.argus.drawer.read_district_drawer", new_callable=AsyncMock) as mock_read:
        mock_read.side_effect = RuntimeError("simulated DB failure")
        with caplog.at_level(logging.WARNING, logger="artemis.marketing.brief_assembler"):
            result = await _read_argus_context(session, "TX-042")

    assert result is None
    assert any("Argus drawer read failed" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# B-series: build_campaign_initiation_context
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_signal(
    *,
    district_id: str | None = "TX-042",
    resolved_district_id: int | None = 42,
    state: str | None = "TX",
) -> MagicMock:
    sig = MagicMock()
    sig.id = 1
    sig.headline = "Board approved literacy program"
    sig.summary = "District voted yes."
    sig.state = state
    sig.district_id = district_id
    sig.resolved_district_id = resolved_district_id
    sig.urgency_tier = "hot"
    sig.reason_codes = ["DISTRICT_VOTED_YES"]
    sig.source_url = "https://example.com"
    return sig


def _patch_repository(primary_signal: Any) -> Any:
    """Return a dict of patches for the repository functions used by build_campaign_initiation_context."""
    candidate = MagicMock()
    candidate.id = 1
    candidate.campaign_family = "obc"
    candidate.decision_state = "approved"
    candidate.predecessor_id = None

    deliverable = MagicMock()
    deliverable.slug = "outreach_email"

    return {
        "get_candidate": AsyncMock(return_value=candidate),
        "get_candidate_signal_rows": AsyncMock(return_value=[primary_signal]),
        "get_candidate_primary_signal": AsyncMock(return_value=primary_signal),
        "get_candidate_predecessor_context": AsyncMock(return_value=None),
        "list_deliverable_types": AsyncMock(return_value=[deliverable]),
        "get_district": AsyncMock(return_value=MagicMock(state="TX")),
    }


@pytest.mark.asyncio
async def test_b1_context_includes_argus_research_when_drawer_populated() -> None:
    """B1 — argus_research key present in context when drawer has findings."""
    from artemis.marketing.brief_assembler import build_campaign_initiation_context

    session = AsyncMock()
    primary_signal = _make_mock_signal(district_id="TX-042", resolved_district_id=42)
    repo_mocks = _patch_repository(primary_signal)

    findings = {
        "current_vendor": _make_finding(dimension="current_vendor", value="Lexia"),
    }

    with (
        patch(
            "artemis.marketing.brief_assembler._read_argus_context",
            new_callable=AsyncMock,
            return_value={
                "district_key": "TX-042",
                "attributed_to": "Argus",
                "findings": {
                    "current_vendor": {
                        "value": "Lexia",
                        "source": "Argus",
                        "researched_at": "2026-06-17",
                    }
                },
                "recommended_angle": "Position as complement.",
            },
        ),
        patch("artemis.marketing.repository.get_candidate", repo_mocks["get_candidate"]),
        patch(
            "artemis.marketing.repository.get_candidate_signal_rows",
            repo_mocks["get_candidate_signal_rows"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_primary_signal",
            repo_mocks["get_candidate_primary_signal"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_predecessor_context",
            repo_mocks["get_candidate_predecessor_context"],
        ),
        patch(
            "artemis.marketing.repository.list_deliverable_types",
            repo_mocks["list_deliverable_types"],
        ),
        patch("artemis.marketing.repository.get_district", repo_mocks["get_district"]),
    ):
        context = await build_campaign_initiation_context(session, candidate_id=1)

    assert "argus_research" in context
    assert context["argus_research"]["district_key"] == "TX-042"
    assert context["argus_research"]["attributed_to"] == "Argus"
    assert context["argus_research"]["recommended_angle"] == "Position as complement."
    assert "current_vendor" in context["argus_research"]["findings"]


@pytest.mark.asyncio
async def test_b2_context_omits_argus_research_when_drawer_empty() -> None:
    """B2 — Empty drawer → argus_research key absent (no regression to existing context shape)."""
    from artemis.marketing.brief_assembler import build_campaign_initiation_context

    session = AsyncMock()
    primary_signal = _make_mock_signal(district_id="TX-042", resolved_district_id=42)
    repo_mocks = _patch_repository(primary_signal)

    with (
        patch(
            "artemis.marketing.brief_assembler._read_argus_context",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("artemis.marketing.repository.get_candidate", repo_mocks["get_candidate"]),
        patch(
            "artemis.marketing.repository.get_candidate_signal_rows",
            repo_mocks["get_candidate_signal_rows"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_primary_signal",
            repo_mocks["get_candidate_primary_signal"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_predecessor_context",
            repo_mocks["get_candidate_predecessor_context"],
        ),
        patch(
            "artemis.marketing.repository.list_deliverable_types",
            repo_mocks["list_deliverable_types"],
        ),
        patch("artemis.marketing.repository.get_district", repo_mocks["get_district"]),
    ):
        context = await build_campaign_initiation_context(session, candidate_id=1)

    assert "argus_research" not in context
    # Core keys still present
    assert "candidate" in context
    assert "signals" in context
    assert "default_target_scope" in context


@pytest.mark.asyncio
async def test_b3_context_omits_argus_research_when_no_district() -> None:
    """B3 — Signal with no district → no argus_research key."""
    from artemis.marketing.brief_assembler import build_campaign_initiation_context

    session = AsyncMock()
    primary_signal = _make_mock_signal(district_id=None, resolved_district_id=None, state=None)
    repo_mocks = _patch_repository(primary_signal)

    with (
        patch(
            "artemis.marketing.brief_assembler._read_argus_context",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("artemis.marketing.repository.get_candidate", repo_mocks["get_candidate"]),
        patch(
            "artemis.marketing.repository.get_candidate_signal_rows",
            repo_mocks["get_candidate_signal_rows"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_primary_signal",
            repo_mocks["get_candidate_primary_signal"],
        ),
        patch(
            "artemis.marketing.repository.get_candidate_predecessor_context",
            repo_mocks["get_candidate_predecessor_context"],
        ),
        patch(
            "artemis.marketing.repository.list_deliverable_types",
            repo_mocks["list_deliverable_types"],
        ),
        patch("artemis.marketing.repository.get_district", repo_mocks["get_district"]),
    ):
        context = await build_campaign_initiation_context(session, candidate_id=1)

    assert "argus_research" not in context


# ─────────────────────────────────────────────────────────────────────────────
# C-series: _build_campaign_initiation_prompt
# ─────────────────────────────────────────────────────────────────────────────

# We mock _load_brief_assembler_prompt so the tests don't need the docs dir.
_STUB_SCAFFOLD = "## Campaign Brief Assembler\nProduce one CampaignInitiationProposal."


def _make_prompt(context: dict[str, Any]) -> str:
    with patch(
        "artemis.marketing.brief_assembler._load_brief_assembler_prompt",
        return_value=_STUB_SCAFFOLD,
    ):
        return _build_campaign_initiation_prompt(context)


def test_c1_prompt_includes_argus_section_when_research_present() -> None:
    """C1 — argus_research in context → Argus section appears in prompt."""
    prompt = _make_prompt(_make_context(with_argus=True))
    assert "## District Research (from Argus)" in prompt
    assert "TX-042" in prompt
    assert "Argus" in prompt
    assert "current_vendor" in prompt
    assert "Lexia Reading Core5" in prompt


def test_c2_prompt_no_argus_section_when_no_research() -> None:
    """C2 — No argus_research → prompt identical structure to pre-Argus prompt."""
    prompt = _make_prompt(_make_context(with_argus=False))
    assert "## District Research (from Argus)" not in prompt
    # Standard sections still present
    assert "## Runtime Task" in prompt
    assert "## Candidate Context" in prompt


def test_c3_prompt_includes_recommended_angle() -> None:
    """C3 — Recommended angle from Argus appears prominently in the prompt."""
    prompt = _make_prompt(_make_context(with_argus=True))
    assert "Position as complement to Lexia" in prompt
    assert "Recommended angle" in prompt


def test_c4_prompt_argus_section_precedes_runtime_task() -> None:
    """C4 — Argus research section comes before the Runtime Task section."""
    prompt = _make_prompt(_make_context(with_argus=True))
    argus_pos = prompt.index("## District Research (from Argus)")
    runtime_pos = prompt.index("## Runtime Task")
    assert argus_pos < runtime_pos, (
        "Argus research section must appear before the Runtime Task section"
    )


def test_c5_prompt_without_argus_runtime_task_present() -> None:
    """C5 — Even without Argus research, Runtime Task + Candidate Context both present."""
    prompt = _make_prompt(_make_context(with_argus=False))
    assert "## Runtime Task" in prompt
    assert "## Candidate Context" in prompt
