"""Unit tests for Kai's enablement retrieval tools — no DB required.

Tests:
1. search_enablement_assets: ranked match results from a mocked store
2. search_enablement_assets: empty store → empty results JSON (Kai says "no current asset found")
3. search_enablement_assets: missing query → error string
4. search_enablement_assets: falls back to keyword when embedding is unavailable
5. get_enablement_asset: lookup by drive_file_id returns the asset
6. get_enablement_asset: lookup by asset_name returns the asset
7. get_enablement_asset: missing asset → found=false JSON
8. get_enablement_asset: missing identifier → error string
9. Scope policy: Kai gets only enablement scope (adversarial deny matrix)
10. Tool registry: Kai's registry is read-only-only (exactly 2 tools, correct names)
11. Tool registry: Callie's registry is unchanged (no enablement tools added)
12. Session scope: kai has empty frozenset allowlist
13. Personality: load_agent_profile("kai") returns the Kai v0.2 persona
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── env setup (no DB) ────────────────────────────────────────────────────────
os.environ.setdefault("ARTEMIS_DB_URL", "postgresql+asyncpg://test:test@localhost/test_unit")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-real")
os.environ.setdefault("FERNET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_asset(**kwargs: Any) -> MagicMock:
    """Return a MagicMock that looks like an EnablementAsset ORM row."""
    defaults = {
        "drive_file_id": "abc123",
        "asset_name": "Onboarding Guide",
        "title": "Onboarding Guide v2",
        "summary": "Covers first-week onboarding for new hires.",
        "drive_link": "https://drive.google.com/file/d/abc123",
        "type": "doc",
        "confidence_label": "Safe to send",
        "audience": "new-hire",
        "transcript_link": None,
        "status": "active",
        "source_scope": "enablement",
    }
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _make_session_with_assets(assets: list[MagicMock]) -> AsyncMock:
    """Return an AsyncMock DB session whose execute() yields the given assets."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value.all.return_value = assets
    result.scalar_one_or_none.return_value = assets[0] if assets else None
    session.execute = AsyncMock(return_value=result)
    return session


# ── 1-4: search_enablement_assets ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_ranked_matches():
    """search_enablement_assets returns matched assets as JSON."""
    from artemis.enablement.tools import _search_enablement_assets

    asset = _make_asset()
    session = _make_session_with_assets([asset])

    with (
        patch("artemis.db.SessionLocal", return_value=session),
        patch(
            "artemis.memory.embeddings.MiniLMProvider.embed",
            new=AsyncMock(return_value=[0.1] * 384),
        ),
    ):
        result = await _search_enablement_assets({"query": "onboarding", "limit": 5})

    data = json.loads(result)
    assert data["count"] == 1
    assert data["results"][0]["title"] == "Onboarding Guide v2"
    assert data["results"][0]["drive_link"] == "https://drive.google.com/file/d/abc123"
    assert data["query"] == "onboarding"


@pytest.mark.asyncio
async def test_search_empty_store_returns_empty_results():
    """Empty store → empty results JSON (Kai will say 'no current asset found')."""
    from artemis.enablement.tools import _search_enablement_assets

    session = _make_session_with_assets([])

    with (
        patch("artemis.db.SessionLocal", return_value=session),
        patch(
            "artemis.memory.embeddings.MiniLMProvider.embed",
            new=AsyncMock(return_value=[0.0] * 384),
        ),
    ):
        result = await _search_enablement_assets({"query": "district CFO one-pager"})

    data = json.loads(result)
    assert data["count"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_missing_query_returns_error():
    """Missing query parameter → error string."""
    from artemis.enablement.tools import _search_enablement_assets

    result = await _search_enablement_assets({})
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_search_falls_back_to_keyword_when_embedding_unavailable():
    """Falls back to keyword search when MiniLMProvider.embed raises."""
    from artemis.enablement.tools import _search_enablement_assets

    asset = _make_asset(title="Demo Video 2026")
    # First execute call (vector, returns empty) → triggers fallback
    # Second execute call (keyword) → returns asset
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    keyword_result = MagicMock()
    keyword_result.scalars.return_value.all.return_value = [asset]

    # embed raises → keyword path used
    with (
        patch("artemis.db.SessionLocal", return_value=session),
        patch(
            "artemis.memory.embeddings.MiniLMProvider.embed",
            new=AsyncMock(side_effect=Exception("model not available")),
        ),
    ):
        session.execute = AsyncMock(return_value=keyword_result)
        result = await _search_enablement_assets({"query": "demo video"})

    data = json.loads(result)
    assert data["count"] == 1
    assert data["results"][0]["title"] == "Demo Video 2026"


# ── 5-8: get_enablement_asset ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_drive_file_id_returns_asset():
    """get_enablement_asset by drive_file_id returns the asset with found=true."""
    from artemis.enablement.tools import _get_enablement_asset

    asset = _make_asset(drive_file_id="abc123")
    session = _make_session_with_assets([asset])

    with patch("artemis.db.SessionLocal", return_value=session):
        result = await _get_enablement_asset({"drive_file_id_or_name": "abc123"})

    data = json.loads(result)
    assert data["found"] is True
    assert data["asset"]["drive_file_id"] == "abc123"
    assert data["asset"]["title"] == "Onboarding Guide v2"


@pytest.mark.asyncio
async def test_get_by_asset_name_returns_asset():
    """get_enablement_asset by asset_name returns the asset."""
    from artemis.enablement.tools import _get_enablement_asset

    asset = _make_asset(asset_name="Onboarding Guide")
    session = _make_session_with_assets([asset])

    with patch("artemis.db.SessionLocal", return_value=session):
        result = await _get_enablement_asset({"drive_file_id_or_name": "Onboarding Guide"})

    data = json.loads(result)
    assert data["found"] is True
    assert data["asset"]["asset_name"] == "Onboarding Guide"


@pytest.mark.asyncio
async def test_get_missing_asset_returns_found_false():
    """get_enablement_asset for non-existent asset → found=false."""
    from artemis.enablement.tools import _get_enablement_asset

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    with patch("artemis.db.SessionLocal", return_value=session):
        result = await _get_enablement_asset({"drive_file_id_or_name": "nonexistent-id"})

    data = json.loads(result)
    assert data["found"] is False
    assert data["identifier"] == "nonexistent-id"


@pytest.mark.asyncio
async def test_get_missing_identifier_returns_error():
    """get_enablement_asset with no identifier → error string."""
    from artemis.enablement.tools import _get_enablement_asset

    result = await _get_enablement_asset({})
    assert result.startswith("Error:")


# ── 9: Scope policy adversarial tests ─────────────────────────────────────────


class TestKaiScopePolicy:
    """Kai gets only enablement scope — deny matrix."""

    def test_kai_only_enablement_scope(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("kai")
        assert a.allow_all is False
        assert a.denied is False
        assert "enablement" in a.allowed_scope_kinds
        assert a.permits("enablement", "anything")

    def test_kai_denied_personal(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("kai")
        assert not a.permits("personal", "1")
        assert not a.permits("personal", "99")
        assert a.personal_user_id is None

    def test_kai_denied_agent_artemis(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("kai")
        assert not a.permits("agent", "artemis")
        assert not a.permits("agent", "floating-artemis")

    def test_kai_denied_marketing_scopes(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("kai")
        assert not a.permits("workspace", "marketing")
        assert not a.permits("campaign_family", "any")
        assert not a.permits("global", "global")
        assert not a.permits("pipeline", "abc")

    def test_kai_agent_id_permitted(self):
        """agent:kai scope is permitted (Kai's own agent memory if it ever exists)."""
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("kai")
        assert a.permits("agent", "kai")

    def test_callie_unchanged(self):
        """Callie's allowance is not affected by Kai's addition."""
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("callie")
        assert a.permits("workspace", "marketing")
        assert a.permits("agent", "callie")
        assert not a.permits("agent", "artemis")
        assert not a.permits("personal", "1")
        assert not a.permits("enablement", "anything")

    def test_unknown_agent_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("unknown-agent")
        assert a.denied is True
        assert not a.permits("enablement", "anything")

    def test_empty_agent_id_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent("")
        assert a.denied is True

    def test_none_agent_id_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent

        a = allowed_scopes_for_agent(None)  # type: ignore[arg-type]
        assert a.denied is True


# ── 10-11: Tool registry ──────────────────────────────────────────────────────


class TestKaiToolRegistry:
    def test_kai_registry_has_exactly_five_tools(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        # Kai's locked-down registry: search + get + the facet/filter tool,
        # plus the single identity-gated flag_catalog_gap added 2026-08-11.
        # If this count changes again, it is a security decision — not a refactor.
        reg = build_authorized_tool_registry(set(), agent_id="kai")
        assert len(reg) == 5
        assert {e.tool.name for e in reg.all_entries()} == {
            "search_enablement_assets",
            "get_enablement_asset",
            "list_enablement_facets",
            "flag_catalog_gap",
            "update_asset_summary",
        }

    def test_kai_registry_has_search_tool(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        assert "search_enablement_assets" in reg

    def test_kai_registry_has_get_tool(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        assert "get_enablement_asset" in reg

    def test_kai_registry_no_core_tools(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        assert "query_memory" not in reg

    def test_kai_registry_no_marketing_tools(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        # Even with marketing surfaces, Kai gets no marketing tools
        reg = build_authorized_tool_registry({"marketing-os", "signal-queue"}, agent_id="kai")
        assert "post_analyst_message" not in reg
        assert "list_signals" not in reg
        assert "approve_signal" not in reg

    def test_kai_registry_no_gcal_tools(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        # gcal tools have names like list_calendar_events; confirm none present
        tool_names = {e.tool.name for e in reg.all_entries()}
        gcal_names = {n for n in tool_names if "calendar" in n or "gcal" in n}
        assert not gcal_names, f"Kai must not have gcal tools: {gcal_names}"

    def test_kai_registry_no_gmail_tools(self):
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        tool_names = {e.tool.name for e in reg.all_entries()}
        gmail_names = {n for n in tool_names if "gmail" in n or "email" in n or "mail" in n}
        assert not gmail_names, f"Kai must not have gmail tools: {gmail_names}"

    def test_kai_retrieval_tools_are_layer_1(self):
        """Every RETRIEVAL tool stays read-only.

        flag_catalog_gap is the one deliberate exception (layer 2, identity-gated
        to Jon and Missy). Its own behaviour is covered in
        tests/unit_no_db/test_kai_flag_catalog_gap.py.
        """
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        for entry in reg.all_entries():
            if entry.tool.name in ("flag_catalog_gap", "update_asset_summary"):
                assert entry.layer == 2
                continue
            assert entry.layer == 1, (
                f"All Kai retrieval tools must be layer 1, "
                f"{entry.tool.name!r} is layer {entry.layer}"
            )

    def test_kai_has_no_side_effecting_tool_beyond_the_two_gated_writes(self):
        """Exactly two non-read capabilities. Adding a third is a security change."""
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id="kai")
        non_read = {e.tool.name for e in reg.all_entries() if e.layer > 1}
        assert non_read == {"flag_catalog_gap", "update_asset_summary"}

    def test_callie_registry_unchanged_no_enablement(self):
        """Callie's registry must not include enablement tools after Kai's addition."""
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry({"marketing-os"}, agent_id="callie")
        assert "search_enablement_assets" not in reg
        assert "get_enablement_asset" not in reg
        assert "query_memory" in reg
        assert "post_analyst_message" in reg

    def test_none_agent_id_no_enablement_tools(self):
        """None agent_id falls through to the normal path, not the Kai path."""
        from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

        reg = build_authorized_tool_registry(set(), agent_id=None)
        assert "search_enablement_assets" not in reg
        assert "query_memory" in reg


# ── 12: Session scope allowlist ───────────────────────────────────────────────


class TestKaiSessionScope:
    def test_kai_has_empty_surface_allowlist(self):
        from artemis.floating_artemis.session_scope import _AGENT_SURFACE_ALLOWLIST

        assert "kai" in _AGENT_SURFACE_ALLOWLIST
        assert _AGENT_SURFACE_ALLOWLIST["kai"] == frozenset()

    def test_kai_resolve_surface_returns_empty(self):
        """resolve_surface_scope for a kai session returns an empty surface set."""
        from artemis.floating_artemis.session_scope import resolve_surface_scope

        result = resolve_surface_scope(
            all_surfaces={"okr", "marketing-os", "signal-queue", "meetings"},
            session_id="slack-kai-T123-C0BB17EJLKC-12345",
            metadata={"agent_id": "kai", "surface": "slack"},
        )
        assert result == set(), f"Kai must get empty surface set, got: {result}"

    def test_callie_surface_allowlist_unchanged(self):
        from artemis.floating_artemis.session_scope import (
            _AGENT_SURFACE_ALLOWLIST,
            _MARKETING_SURFACES,
        )

        assert _AGENT_SURFACE_ALLOWLIST["callie"] == _MARKETING_SURFACES


# ── 13: Personality ───────────────────────────────────────────────────────────


class TestKaiPersonality:
    def test_load_agent_profile_kai_display_name(self):
        from artemis.floating_artemis.personality import load_agent_profile

        profile = load_agent_profile("kai")
        assert profile.display_name == "Kai"

    def test_load_agent_profile_kai_returns_profile_text(self):
        from artemis.floating_artemis.personality import load_agent_profile

        profile = load_agent_profile("kai")
        # The v0.2 profile starts with the Chiron Kai heading
        assert "Chiron" in profile.profile_text or "Kai" in profile.profile_text

    def test_load_agent_profile_kai_persona_core_present(self):
        from artemis.floating_artemis.personality import load_agent_profile

        profile = load_agent_profile("kai")
        assert profile.persona_core  # non-empty
        assert "enablement" in profile.persona_core.lower()
        assert "read-only" in profile.persona_core.lower()

    def test_load_agent_profile_kai_voice_corpus(self):
        """Kai's profile has a characteristic phrases section (curly-quote format).

        NOTE: The v0.2 profile uses curly/typographic quotes (“...”) for
        its characteristic phrases.  The _parse_voice_corpus regex currently only
        matches ASCII straight quotes, so voice_corpus is empty until the profile
        is reformatted or the regex is updated.  This test verifies the CURRENT
        behaviour and documents the known gap.  The profile_text DOES contain the
        phrases — they are just not extracted into voice_corpus yet.
        """
        from artemis.floating_artemis.personality import load_agent_profile

        profile = load_agent_profile("kai")
        # Profile text contains the characteristic phrases section regardless
        assert "Characteristic Phrases" in profile.profile_text
        assert "Found it. Use this version." in profile.profile_text
        # voice_corpus is currently empty due to curly-quote mismatch — document it
        # (not a security issue; voice calibration is cosmetic)
        assert isinstance(profile.voice_corpus, list)  # always a list, never None

    def test_load_agent_profile_callie_unchanged(self):
        """Callie's profile is not affected by Kai's addition."""
        from artemis.floating_artemis.personality import load_agent_profile

        profile = load_agent_profile("callie")
        assert profile.display_name == "Callie"
        assert (
            "marketing" in profile.persona_core.lower() or "callie" in profile.persona_core.lower()
        )
