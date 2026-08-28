"""Tests for SFDC-1's Callie-facing tool: check_salesforce_activity.

Mocks artemis.db.SessionLocal (same pattern as test_g1_tools_marketing.py)
plus the two functions the tool calls (list_contacts_for_district,
check_suppression) at their SOURCE modules -- both are imported lazily
inside the tool implementation, so patching the source is what actually
takes effect, exactly like the existing jira/marketing tool tests in this
directory.

No real DB, no real Salesforce -- this suite only proves the tool's own
read-only orchestration and text formatting, including the fail-closed
"could not verify" wording when Salesforce is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from artemis.floating_artemis.tools.salesforce_tools import (
    CHECK_SALESFORCE_ACTIVITY,
    _check_salesforce_activity,
    register_salesforce_tools,
)


@dataclass(frozen=True)
class _FakeContact:
    name: str
    email: str | None


def _session_cm(district: object | None) -> AsyncMock:
    """A fake `async with SessionLocal() as session` context manager whose
    `session.get` and `session.execute(...).scalar_one_or_none()` both
    resolve to `district`."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=district)
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = district
    session.execute = AsyncMock(return_value=exec_result)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_missing_district_returns_error_string() -> None:
    result = await _check_salesforce_activity({})
    assert result.startswith("Error:")


async def test_no_district_found() -> None:
    with patch("artemis.db.SessionLocal", return_value=_session_cm(None)):
        result = await _check_salesforce_activity({"district_name": "Nowhere ISD"})
    # Wording changed 2026-08-28 when Salesforce moved to the front of the
    # answer: the tool now says which lookup came up empty, and must NOT send
    # the asker to fetch a Salesforce account name that cannot help.
    assert "no entry for" in result
    assert "Salesforce account name" not in result


async def test_no_email_bearing_contacts() -> None:
    district = SimpleNamespace(id=1, name="Empty District")
    with (
        patch("artemis.db.SessionLocal", return_value=_session_cm(district)),
        patch(
            "artemis.marketing.contacts.list_contacts_for_district",
            new=AsyncMock(return_value=[_FakeContact(name="No Email Guy", email=None)]),
        ),
    ):
        result = await _check_salesforce_activity({"district_id": 1})
    # "Nothing to check" must not read as "nothing to worry about" — the old
    # phrasing let Callie imply a district looked clear when it was never checked.
    assert "no contacts with email addresses on file" in result
    assert "NOT a clean" in result


async def test_reports_clear_and_suppressed_and_unavailable_contacts() -> None:
    from artemis.marketing.salesforce_suppression import SuppressionResult

    district = SimpleNamespace(id=2, name="Mixed District")
    contacts = [
        _FakeContact(name="Clear Carol", email="carol@ex.com"),
        _FakeContact(name="Customer Cara", email="cara@customer.org"),
        _FakeContact(name="Down Dana", email="dana@ex.com"),
    ]
    results = {
        "carol@ex.com": SuppressionResult(False, None, "no Salesforce Contact found"),
        "cara@customer.org": SuppressionResult(True, "existing_customer", "flagged as customer"),
        "dana@ex.com": SuppressionResult(True, "salesforce_unavailable", "auth failed"),
    }

    async def _fake_check_suppression(session, *, district_id, email, enrich):  # noqa: ARG001
        assert enrich is False, "Callie's tool must never enrich (layer-1 read-only contract)"
        return results[email]

    with (
        patch("artemis.db.SessionLocal", return_value=_session_cm(district)),
        patch(
            "artemis.marketing.contacts.list_contacts_for_district",
            new=AsyncMock(return_value=contacts),
        ),
        patch(
            "artemis.marketing.salesforce_suppression.check_suppression",
            new=_fake_check_suppression,
        ),
    ):
        result = await _check_salesforce_activity({"district_name": "Mixed District"})

    assert "Clear Carol" in result and "clear --" in result
    assert "Customer Cara" in result and "existing_customer" in result
    assert "Down Dana" in result and "could not verify" in result
    assert "UNVERIFIED" in result  # overall caveat when any contact is unavailable


def test_tool_registers_as_layer_1() -> None:
    from artemis.floating_artemis.authority import AuthorizedToolRegistry

    registry = AuthorizedToolRegistry()
    register_salesforce_tools(registry)
    entry = registry.get(CHECK_SALESFORCE_ACTIVITY)
    assert entry is not None
    assert entry.layer == 1
