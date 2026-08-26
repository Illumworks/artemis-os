"""The target-list import tool.

The consequential failure is not a crash — it is replacing Josh's target
universe with the wrong thing, or letting the wrong person do it. Both are
pinned here.

Identity is bound as a CLOSURE at registration, never read from tool input,
because a model can be talked into putting any id in a JSON field. Layer 2, not
3: a layer-3 confirmation in a shared Slack channel is answered by whoever
replies next, so it would imply a safety that is not there.
"""

from __future__ import annotations

import pytest

from artemis.marketing.targets.tool import (
    IMPORT_TARGET_ACCOUNTS,
    _make_import_target_accounts,
    authorized_importer_ids,
)

JON = "U09F3EPJXSQ"
JOSH = "U07NYLNJY79"


def test_only_jon_and_josh_may_import() -> None:
    assert authorized_importer_ids() == {JON, JOSH}


@pytest.mark.asyncio
async def test_an_unlisted_speaker_is_refused_by_name() -> None:
    """The refusal must read as a permissions answer, not a malfunction."""
    run = _make_import_target_accounts("U0SOMEONEELSE")

    out = await run({"file_id": "F0BTF3J3HB2"})

    assert "Not permitted" in out
    assert "Jon and Josh" in out
    assert "do not attempt it another way" in out


@pytest.mark.asyncio
async def test_no_speaker_fails_closed() -> None:
    """An unresolved identity must deny, never default to allowed."""
    assert "Not permitted" in await _make_import_target_accounts(None)({"file_id": "F1"})
    assert "Not permitted" in await _make_import_target_accounts("")({"file_id": "F1"})


@pytest.mark.asyncio
async def test_identity_cannot_be_spoofed_through_tool_input() -> None:
    """The closure wins. A model that invents a speaker field gets nowhere."""
    run = _make_import_target_accounts("U0SOMEONEELSE")

    out = await run(
        {"file_id": "F0BTF3J3HB2", "speaker_id": JON, "imported_by": JON, "user": JON}
    )

    assert "Not permitted" in out


@pytest.mark.asyncio
async def test_a_missing_file_id_is_reported_not_guessed() -> None:
    run = _make_import_target_accounts(JOSH)

    out = await run({})

    assert "needs the Slack file_id" in out


def test_the_tool_tells_the_model_when_to_use_it() -> None:
    """Description carries the guardrails the model has to honour."""
    description = IMPORT_TARGET_ACCOUNTS.description
    assert "ONLY when explicitly asked" in description
    assert "never guess" in IMPORT_TARGET_ACCOUNTS.input_schema["properties"]["file_id"][
        "description"
    ]
    assert "stop being treated as live targets" in description
