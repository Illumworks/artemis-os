"""Tests for the D10 LinkedIn Observer Scout.

All LinkedIn API calls are mocked — no live network requests.

Coverage:
- mapping.py: post_to_finding, _week_key (≥12 tests)
- watch_list.py: _DEFAULT_WATCH_PROFILES (≥2 tests)
- scout.py: LinkedInObserverScout._gather_findings (≥8 tests)
- class meta: scout_type ClassVar (1 test)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from artemis.scouts.base import ScoutConfig
from artemis.scouts.linkedin.mapping import _week_key, post_to_finding
from artemis.scouts.linkedin.scout import LinkedInObserverScout
from artemis.scouts.linkedin.watch_list import _DEFAULT_WATCH_PROFILES

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PROFILE: dict[str, Any] = {
    "profile_id": "https://www.linkedin.com/in/sample-supe-fl-pinellas",
    "district_id": "FL_pinellas",
    "state": "FL",
    "role": "superintendent",
    "name": "Jane Smith",
}


def _make_post(
    *,
    text: str = "Our district is focused on improving literacy outcomes.",
    is_authored: bool = True,
    post_id: str = "post-001",
    posted_at: str = "2026-05-16T10:00:00Z",
    url: str = "https://www.linkedin.com/posts/sample-001",
    profile_id: str = _PROFILE["profile_id"],
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "post_id": post_id,
        "text": text,
        "posted_at": posted_at,
        "url": url,
        "is_authored": is_authored,
    }


def _make_mock_linkedin(
    *,
    api_key: str = "test-key",
    posts_by_profile: dict[str, list[dict[str, Any]]] | None = None,
) -> MagicMock:
    """Return a mock LinkedInScraperClient."""
    client = MagicMock()
    client._api_key = api_key

    posts_map = posts_by_profile or {}

    async def _fetch_posts(profile_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return posts_map.get(profile_id, [])

    client.fetch_posts = AsyncMock(side_effect=_fetch_posts)
    return client


# ===========================================================================
# 1. mapping.py — post_to_finding
# ===========================================================================


def test_post_to_finding_reshare_returns_none() -> None:
    """is_authored=False (reshare) must return None."""
    post = _make_post(is_authored=False, text="literacy reading curriculum")
    result = post_to_finding(post, _PROFILE)
    assert result is None


def test_post_to_finding_no_theme_keywords_returns_none() -> None:
    """Off-topic text with no campaign keywords must return None."""
    post = _make_post(text="Had a great weekend hiking in the mountains!")
    result = post_to_finding(post, _PROFILE)
    assert result is None


def test_post_to_finding_literacy_keyword_matches() -> None:
    """'literacy' in text → LINKEDIN_LEADER_ENGAGEMENT in reason_codes."""
    post = _make_post(text="We are committed to improving literacy for all students.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert "LINKEDIN_LEADER_ENGAGEMENT" in result["reasonCodes"]


def test_post_to_finding_esser_adds_topical_code() -> None:
    """'esser' in text → ESSER_CLIFF_REFERENCE in reason_codes."""
    post = _make_post(text="Our ESSER funds are helping close achievement gaps.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert "ESSER_CLIFF_REFERENCE" in result["reasonCodes"]


def test_post_to_finding_rfp_adds_reason_code() -> None:
    """'rfp' in text → BOARD_RFP_AUTHORIZATION in reason_codes AND standard urgency."""
    post = _make_post(text="We released an RFP for our new reading curriculum.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert "BOARD_RFP_AUTHORIZATION" in result["reasonCodes"]
    assert result["urgency"] == "standard"


def test_post_to_finding_vendor_standard_urgency() -> None:
    """'vendor' in text → urgency = 'standard'."""
    post = _make_post(text="We are evaluating vendor options for our literacy program.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert result["urgency"] == "standard"


def test_post_to_finding_default_enrichment() -> None:
    """'literacy' with no action keyword → urgency = 'enrichment'."""
    post = _make_post(text="Excited about our literacy initiatives this school year.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert result["urgency"] == "enrichment"


def test_post_to_finding_always_has_linkedin_leader_engagement() -> None:
    """LINKEDIN_LEADER_ENGAGEMENT must always be the first reason code."""
    post = _make_post(text="dyslexia screening is expanding to all grade levels.")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert result["reasonCodes"][0] == "LINKEDIN_LEADER_ENGAGEMENT"


def test_post_to_finding_district_id_from_profile() -> None:
    """districtId must come from the profile dict, not the post."""
    post = _make_post(text="literacy program expands this fall")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert result["districtId"] == "FL_pinellas"


def test_post_to_finding_discovered_by() -> None:
    """discoveredBy must always be 'linkedin_observer'."""
    post = _make_post(text="tutoring program pilot launched")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    assert result["discoveredBy"] == "linkedin_observer"


def test_post_to_finding_contact_hints_in_metadata() -> None:
    """metadata must contain a contact_hints dict with name, role, linkedin_url."""
    post = _make_post(text="curriculum alignment across our reading program")
    result = post_to_finding(post, _PROFILE)
    assert result is not None
    hints = result["metadata"]["contact_hints"]
    assert hints["name"] == "Jane Smith"
    assert hints["role"] == "superintendent"
    assert hints["linkedin_url"] == _PROFILE["profile_id"]


# ===========================================================================
# 2. mapping.py — _week_key
# ===========================================================================


def test_week_key_parses_iso_date() -> None:
    """2026-05-16 is in ISO week 20 of 2026."""
    assert _week_key("2026-05-16T10:00:00Z") == "2026-W20"


def test_week_key_empty_on_bad_date() -> None:
    """Non-parseable strings must return empty string."""
    assert _week_key("") == ""
    assert _week_key("not-a-date") == ""


# ===========================================================================
# 3. watch_list.py — _DEFAULT_WATCH_PROFILES
# ===========================================================================


def test_default_watch_profiles_has_entries() -> None:
    """_DEFAULT_WATCH_PROFILES must be non-empty."""
    assert len(_DEFAULT_WATCH_PROFILES) > 0


def test_default_watch_profiles_has_required_keys() -> None:
    """Every entry in _DEFAULT_WATCH_PROFILES must have all required keys."""
    required = {"profile_id", "district_id", "state", "role", "name"}
    for entry in _DEFAULT_WATCH_PROFILES:
        missing = required - entry.keys()
        assert not missing, f"Profile entry missing keys: {missing} — entry: {entry}"


# ===========================================================================
# 4. scout.py — LinkedInObserverScout class meta
# ===========================================================================


def test_linkedin_observer_scout_type_class_var() -> None:
    """scout_type class variable must equal 'linkedin_observer'."""
    assert LinkedInObserverScout.scout_type == "linkedin_observer"


# ===========================================================================
# 5. scout.py — _gather_findings behavior
# ===========================================================================


async def test_gather_findings_returns_empty_when_no_api_key() -> None:
    """No API key → _gather_findings returns []."""
    mock_client = _make_mock_linkedin(api_key="")
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_returns_list() -> None:
    """With a valid API key and matching posts → returns a list of findings."""
    post = _make_post(text="Our literacy program is growing.")
    mock_client = _make_mock_linkedin(
        posts_by_profile={_PROFILE["profile_id"]: [post]},
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert isinstance(findings, list)
    assert len(findings) == 1


async def test_gather_findings_deduplicates_by_week() -> None:
    """Two posts in the same ISO week for the same profile → only one finding."""
    post_a = _make_post(
        text="literacy intervention program week one",
        post_id="post-a",
        posted_at="2026-05-11T08:00:00Z",  # 2026-W20
    )
    post_b = _make_post(
        text="reading program update same week",
        post_id="post-b",
        posted_at="2026-05-15T16:00:00Z",  # also 2026-W20
    )
    mock_client = _make_mock_linkedin(
        posts_by_profile={_PROFILE["profile_id"]: [post_a, post_b]},
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert len(findings) == 1


async def test_gather_findings_continues_on_profile_error() -> None:
    """A fetch error for one profile must not stop other profiles being processed."""
    profile_a = dict(_PROFILE)
    profile_b = {
        "profile_id": "https://www.linkedin.com/in/sample-supe-tx-dallas",
        "district_id": "TX_dallas",
        "state": "TX",
        "role": "superintendent",
        "name": "Bob Jones",
    }
    post_b = _make_post(
        text="literacy scores improving across our district",
        post_id="post-b",
        profile_id=profile_b["profile_id"],
        posted_at="2026-05-16T10:00:00Z",
    )

    client = MagicMock()
    client._api_key = "test-key"

    call_count: list[int] = [0]

    async def _fetch(profile_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        call_count[0] += 1
        if profile_id == profile_a["profile_id"]:
            raise RuntimeError("scraper down")
        return [post_b]

    client.fetch_posts = AsyncMock(side_effect=_fetch)

    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[profile_a, profile_b],
        _linkedin_client=client,
    )
    findings = await scout._gather_findings()
    # profile_a raised but profile_b succeeded
    assert call_count[0] == 2
    assert len(findings) == 1
    assert findings[0]["districtId"] == "TX_dallas"


async def test_gather_findings_skips_reshares() -> None:
    """Posts with is_authored=False must not appear in findings."""
    reshare = _make_post(
        text="Great article about literacy and reading programs!",
        is_authored=False,
    )
    mock_client = _make_mock_linkedin(
        posts_by_profile={_PROFILE["profile_id"]: [reshare]},
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_skips_noncampaign_posts() -> None:
    """Off-topic posts (no campaign theme keywords) must not appear in findings."""
    post = _make_post(text="Just had a wonderful staff appreciation lunch today!")
    mock_client = _make_mock_linkedin(
        posts_by_profile={_PROFILE["profile_id"]: [post]},
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_multiple_profiles() -> None:
    """Findings are collected across all watch-list profiles."""
    profile_b = {
        "profile_id": "https://www.linkedin.com/in/sample-supe-tx-dallas",
        "district_id": "TX_dallas",
        "state": "TX",
        "role": "superintendent",
        "name": "Bob Jones",
    }
    post_a = _make_post(
        text="literacy program update",
        post_id="pa",
        posted_at="2026-05-16T10:00:00Z",
    )
    post_b = _make_post(
        text="reading assessment results are promising",
        post_id="pb",
        posted_at="2026-05-16T10:00:00Z",
        profile_id=profile_b["profile_id"],
    )
    mock_client = _make_mock_linkedin(
        posts_by_profile={
            _PROFILE["profile_id"]: [post_a],
            profile_b["profile_id"]: [post_b],
        }
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE, profile_b],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert len(findings) == 2
    district_ids = {f["districtId"] for f in findings}
    assert "FL_pinellas" in district_ids
    assert "TX_dallas" in district_ids


async def test_gather_findings_source_type_is_linkedin_post() -> None:
    """All findings must have sourceType == 'linkedin_post'."""
    post = _make_post(text="dyslexia screening mandate now in effect")
    mock_client = _make_mock_linkedin(
        posts_by_profile={_PROFILE["profile_id"]: [post]},
    )
    scout = LinkedInObserverScout(
        ScoutConfig(),
        watch_profiles=[_PROFILE],
        _linkedin_client=mock_client,
    )
    findings = await scout._gather_findings()
    assert all(f["sourceType"] == "linkedin_post" for f in findings)
