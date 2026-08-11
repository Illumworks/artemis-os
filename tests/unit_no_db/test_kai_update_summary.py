"""update_asset_summary — Kai accepting a correction from whoever owns the content.

Owner decision (Jon, 2026-08-11): nobody has time to review 400+ generated
summaries, so the bulk-review model is dropped. Instead, corrections happen live
in the channel: when Sara or Missy says a summary is wrong, Kai fixes it and the
new text is re-indexed immediately.

Authorization differs from flag_catalog_gap on purpose. Sara owns the catalog but
is NOT on the action allowlist for posting gap notes (owner decision 2026-08-10);
correcting a description of her own content is a smaller thing than posting to
the channel, and she is the library's heaviest user.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.enablement import actions
from artemis.enablement.actions import (
    UPDATE_ASSET_SUMMARY,
    is_authorized_to_edit_summary,
)

JON = "U09F3EPJXSQ"
MISSY = "U07CHT0S7UK"
SARA = "U07926XP0FR"
AMANDA = "U0BBUF8R77G"


def _impl(speaker_id: str | None) -> Any:
    return actions._make_update_asset_summary(speaker_id)


VALID = {
    "drive_file_id_or_name": "Exemplar Stories",
    "summary": "Exemplar classroom stories teachers use in coaching conversations.",
}


# ── Who may correct ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("user_id", [JON, SARA, MISSY])
def test_catalog_owners_and_jon_may_edit(user_id: str) -> None:
    assert is_authorized_to_edit_summary(user_id) is True


def test_sara_may_edit_even_though_she_cannot_flag_gaps() -> None:
    """The two allowlists are deliberately different. Pin both."""
    assert is_authorized_to_edit_summary(SARA) is True
    assert actions.is_authorized_for_kai_actions(SARA) is False


@pytest.mark.parametrize("user_id", [AMANDA, "U_UNKNOWN", "", "   ", None])
def test_everyone_else_is_denied(user_id: str | None) -> None:
    assert is_authorized_to_edit_summary(user_id) is False


def test_gate_fails_closed_on_junk_identity() -> None:
    for junk in (0, 1, [], {}, object(), True):
        assert is_authorized_to_edit_summary(junk) is False  # type: ignore[arg-type]


async def test_unauthorized_edit_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not even open a DB session for an unauthorized speaker."""

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unauthorized path touched the database")

    monkeypatch.setattr("artemis.db.SessionLocal", _boom)
    result = await _impl(AMANDA)(VALID)
    assert "NOT_AUTHORIZED" in result
    assert "nothing was changed" in result


async def test_deny_message_routes_to_the_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("artemis.db.SessionLocal", lambda *a, **k: None)
    result = await _impl(AMANDA)(VALID)
    assert "Sara or Missy" in result
    assert "Do not imply the catalog was updated" in result


# ── Input discipline ──────────────────────────────────────────────────────────


async def test_missing_summary_is_refused_with_instructions() -> None:
    """Kai must ask for their wording, not invent one and attribute it to them."""
    result = await _impl(SARA)({"drive_file_id_or_name": "Exemplar Stories"})
    assert result.startswith("Error:")
    assert "ask for one plain sentence" in result
    assert "Do not invent one" in result


async def test_missing_identifier_is_refused() -> None:
    result = await _impl(SARA)({"summary": "x" * 40})
    assert result.startswith("Error:")


async def test_overlong_summary_is_refused() -> None:
    result = await _impl(SARA)({**VALID, "summary": "x" * 401})
    assert result.startswith("Error:")
    assert "too long" in result


def test_schema_has_no_identity_parameter() -> None:
    """Same guarantee as flag_catalog_gap: the model cannot say who it is."""
    props = UPDATE_ASSET_SUMMARY.input_schema["properties"]
    assert set(props) == {"drive_file_id_or_name", "summary"}
    for forbidden in ("reviewer", "speaker_id", "user", "authorized"):
        assert forbidden not in props


def test_tool_description_ties_success_to_the_return_value() -> None:
    """The F1 lesson: only claim it saved when the tool says SAVED."""
    description = UPDATE_ASSET_SUMMARY.description
    assert "Only say it is saved when this returns SAVED" in description
    assert "NOT_FOUND" in description
    assert "re-indexed" in description
