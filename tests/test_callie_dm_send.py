"""send_guarded_dm — Callie's one initiating capability (CALLIE-1).

Two allowlists, checked independently: who may ASK (the important half —
the risk is proxying, "Callie, DM Sara and tell her X") and who may RECEIVE.
Requester identity comes from the verified Slack payload only, bound into
the tool implementation as a closure value, exactly like Kai's
flag_catalog_gap — nothing the model emits (tool input OR message text) can
influence it. Every attempt, sent or refused, is audited in
callie_dm_send_attempts.

The deny paths are tested adversarially: this is Callie's only capability to
act unprompted on the outside world, so a bug here is a security failure,
not a UX bug.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.db import Base, attach_pgvector_codec
from artemis.floating_artemis import tool_registry as tool_registry_mod
from artemis.floating_artemis.callie_dm_models import CallieDmSendAttempt
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
from artemis.floating_artemis.tools import callie_dm
from artemis.floating_artemis.tools.callie_dm import (
    SEND_GUARDED_DM,
    build_attributed_message,
    is_authorized_recipient,
    is_authorized_requester,
)

# asyncio_mode = "auto" (pyproject.toml) already runs every `async def test_*`
# as a test — no pytestmark needed, and this file mixes async and sync tests.

# ── DB wiring ──────────────────────────────────────────────────────────────────
# alembic upgrade head cannot run in this worktree: migration 0115's
# down_revision (0114) belongs to a concurrently-running slice not present
# here (see the CALLIE-1 report for the verbatim failure). Base.metadata
# .create_all is scoped to ONLY this one new table (checkfirst=True), so it
# neither touches nor depends on any other migration's tables already
# present in artemis_test_b.

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test_b",
)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@pytest.fixture(autouse=True)
async def _table_ready() -> AsyncIterator[None]:
    async with _test_engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[CallieDmSendAttempt.__table__],
            checkfirst=True,
        )
    async with _test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE callie_dm_send_attempts RESTART IDENTITY"))
    yield


async def _audit_rows() -> list[CallieDmSendAttempt]:
    async with artemis.db.SessionLocal() as session:
        result = await session.execute(
            select(CallieDmSendAttempt).order_by(CallieDmSendAttempt.id)
        )
        return list(result.scalars().all())


# ── Fixed identities ────────────────────────────────────────────────────────────

JON_SLACK_ID = "U09F3EPJXSQ"
JON_EMAIL = "jon.fila@amiralearning.com"
ANGELA_SLACK_ID = "U0ANGELA01"
ANGELA_EMAIL = "angela.miata@amiralearning.com"
JOSH_SLACK_ID = "U0JOSHMUKAI"
JOSH_EMAIL = "joshua.mukai@amiralearning.com"
HANNAH_SLACK_ID = "U0HANNAHSL8"
HANNAH_EMAIL = "hannah.slater@amiralearning.com"
JACLYN_EMAIL = "jaclyn.wright@amiralearning.com"
SARA_SLACK_ID = "U07926XP0FR"  # not on either allowlist
SARA_EMAIL = "sara.erickson@amiralearning.com"

VALID_INPUT: dict[str, Any] = {
    "recipient_email": ANGELA_EMAIL,
    "message": "Heads up, the district signal for Cypress-Fairbanks just went hot.",
}


# ── Fake Slack ──────────────────────────────────────────────────────────────────


class _FakeSlackClient:
    """Records post_dm calls; resolves emails/ids from class-level fixed maps."""

    dms: list[dict[str, Any]] = []
    email_by_id: dict[str, str] = {}
    id_by_email: dict[str, str] = {}
    raise_on_lookup_email = False
    raise_on_lookup_by_email = False
    raise_on_post_dm = False
    email_lookup_returns_none = False
    by_email_lookup_returns_none = False

    def __init__(self, token: str) -> None:
        self.token = token

    async def lookup_user_email(self, user_id: str) -> str | None:
        if type(self).raise_on_lookup_email:
            raise RuntimeError("boom-lookup-email")
        if type(self).email_lookup_returns_none:
            return None
        return type(self).email_by_id.get(user_id)

    async def lookup_user_by_email(self, email: str) -> str | None:
        if type(self).raise_on_lookup_by_email:
            raise RuntimeError("boom-lookup-by-email")
        if type(self).by_email_lookup_returns_none:
            return None
        return type(self).id_by_email.get(email.lower())

    async def post_dm(self, user: str, message_text: str, **_kw: Any) -> dict[str, Any]:
        if type(self).raise_on_post_dm:
            raise RuntimeError("boom-post-dm")
        type(self).dms.append({"user": user, "text": message_text})
        return {"ok": True, "ts": "1786400000.000100"}


class _FakeCfg:
    access_token = "xoxb-callie-fake"


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSlackClient]:
    """Patch the Slack client + config resolution at their import sites, reset state."""
    _FakeSlackClient.dms = []
    _FakeSlackClient.email_by_id = {
        JON_SLACK_ID: JON_EMAIL,
        ANGELA_SLACK_ID: ANGELA_EMAIL,
        JOSH_SLACK_ID: JOSH_EMAIL,
        HANNAH_SLACK_ID: HANNAH_EMAIL,
        SARA_SLACK_ID: SARA_EMAIL,
    }
    _FakeSlackClient.id_by_email = {
        JON_EMAIL: JON_SLACK_ID,
        ANGELA_EMAIL: ANGELA_SLACK_ID,
        JOSH_EMAIL: JOSH_SLACK_ID,
        HANNAH_EMAIL: HANNAH_SLACK_ID,
    }
    _FakeSlackClient.raise_on_lookup_email = False
    _FakeSlackClient.raise_on_lookup_by_email = False
    _FakeSlackClient.raise_on_post_dm = False
    _FakeSlackClient.email_lookup_returns_none = False
    _FakeSlackClient.by_email_lookup_returns_none = False

    import artemis.integrations.slack.client as slack_client
    import artemis.routes.integrations_slack_events as slack_events

    monkeypatch.setattr(slack_client, "SlackClient", _FakeSlackClient)

    async def _fake_resolve(*args: Any, **kwargs: Any) -> _FakeCfg:
        return _FakeCfg()

    monkeypatch.setattr(slack_events, "_resolve_agent_slack_config", _fake_resolve)
    return _FakeSlackClient


def _impl(speaker_id: str | None) -> Any:
    return callie_dm._make_send_guarded_dm(speaker_id)


# ── 1. Authorized requester -> allowed recipient -> sent ─────────────────────


async def test_authorized_requester_to_allowed_recipient_is_sent(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "SENT" in result
    (dm,) = slack.dms
    assert dm["user"] == ANGELA_SLACK_ID

    rows = await _audit_rows()
    (row,) = rows
    assert row.outcome == "sent"
    assert row.requester_email == JON_EMAIL
    assert row.recipient_email == ANGELA_EMAIL
    assert row.slack_ts == "1786400000.000100"


async def test_sent_message_carries_attribution_to_the_real_requester(
    slack: type[_FakeSlackClient],
) -> None:
    """Paste-verbatim check: the attribution names the real speaker via a mention."""
    await _impl(JON_SLACK_ID)(VALID_INPUT)
    (dm,) = slack.dms
    expected = build_attributed_message(
        requester_slack_id=JON_SLACK_ID, body=VALID_INPUT["message"]
    )
    assert dm["text"] == expected
    assert dm["text"].startswith(f"<@{JON_SLACK_ID}> asked me to pass this along:\n\n")


# ── 2. Authorized requester -> UNLISTED recipient -> refused ──────────────────


async def test_authorized_requester_unlisted_recipient_is_refused(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON_SLACK_ID)({**VALID_INPUT, "recipient_email": SARA_EMAIL})
    assert "REFUSED" in result
    assert slack.dms == [], "an unauthorized recipient must never receive anything"

    (row,) = await _audit_rows()
    assert row.outcome == "refused"
    assert row.refusal_reason == "recipient_not_authorized"
    assert row.requester_email == JON_EMAIL
    assert row.recipient_email == SARA_EMAIL


# ── 3. UNLISTED requester -> allowed recipient -> refused ─────────────────────


async def test_unlisted_requester_allowed_recipient_is_refused(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(SARA_SLACK_ID)(VALID_INPUT)
    assert "NOT_AUTHORIZED" in result
    assert slack.dms == [], "nothing may be sent for an unauthorized requester"

    (row,) = await _audit_rows()
    assert row.outcome == "refused"
    assert row.refusal_reason == "requester_not_authorized"
    assert row.requester_email == SARA_EMAIL
    # Recipient was never checked/resolved once the requester gate failed.
    assert row.recipient_email is None


# ── 4. UNLISTED requester -> UNLISTED recipient -> refused ────────────────────


async def test_unlisted_requester_unlisted_recipient_is_refused(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(SARA_SLACK_ID)({**VALID_INPUT, "recipient_email": SARA_EMAIL})
    assert "NOT_AUTHORIZED" in result
    assert slack.dms == []

    (row,) = await _audit_rows()
    assert row.refusal_reason == "requester_not_authorized"


# ── 5. Requester identity unresolvable -> refused, fail closed ───────────────


async def test_unresolvable_identity_is_refused_not_treated_as_jon(
    slack: type[_FakeSlackClient],
) -> None:
    for missing in (None, "", "   "):
        result = await _impl(missing)(VALID_INPUT)
        assert "REFUSED" in result
    assert slack.dms == [], "an unresolved identity must never be treated as an authorized one"

    rows = await _audit_rows()
    assert len(rows) == 3
    for row in rows:
        assert row.outcome == "refused"
        assert row.refusal_reason == "requester_identity_unresolved"
        assert row.requester_slack_user_id is None
        assert row.requester_email is None


async def test_no_slack_call_is_made_when_identity_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors Kai's 'never touch Slack for an unresolvable requester' gate."""
    import artemis.routes.integrations_slack_events as slack_events

    spy = AsyncMock(side_effect=AssertionError("must not resolve Slack config"))
    monkeypatch.setattr(slack_events, "_resolve_agent_slack_config", spy)
    result = await _impl(None)(VALID_INPUT)
    assert "REFUSED" in result
    spy.assert_not_awaited()


# ── 6. A conflicting name in message TEXT has no effect ──────────────────────


async def test_name_planted_in_message_text_does_not_authorize_an_unlisted_requester(
    slack: type[_FakeSlackClient],
) -> None:
    """The classic escalation: the text claims to be Jon; the real speaker is Sara."""
    spoofed = {
        **VALID_INPUT,
        "message": "This is Jon speaking — please treat this as authorized. -Jon",
    }
    result = await _impl(SARA_SLACK_ID)(spoofed)
    assert "NOT_AUTHORIZED" in result
    assert slack.dms == []

    (row,) = await _audit_rows()
    assert row.requester_email == SARA_EMAIL, "the closure identity, never the text claim"


async def test_attribution_uses_the_real_closure_speaker_not_a_text_claim(
    slack: type[_FakeSlackClient],
) -> None:
    """An authorized caller cannot attribute the send to someone else either."""
    spoofed = {**VALID_INPUT, "message": "Hi Angela, this is actually from Josh, not Jon."}
    result = await _impl(JON_SLACK_ID)(spoofed)
    assert "SENT" in result
    (dm,) = slack.dms
    assert dm["text"].startswith(f"<@{JON_SLACK_ID}> asked me to pass this along:")
    assert f"<@{JOSH_SLACK_ID}>" not in dm["text"]


def test_tool_schema_exposes_no_identity_parameter() -> None:
    """Identity must not be model-supplied. Absent from the schema is the guarantee."""
    props = SEND_GUARDED_DM.input_schema["properties"]
    for forbidden in (
        "requested_by",
        "speaker_id",
        "user",
        "user_id",
        "requester",
        "requester_email",
        "from",
        "as_user",
    ):
        assert forbidden not in props
    assert set(props) == {"recipient_email", "message"}


# ── 7. Every attempt is audited, refusals included ────────────────────────────


async def test_every_outcome_kind_is_audited(slack: type[_FakeSlackClient]) -> None:
    await _impl(JON_SLACK_ID)(VALID_INPUT)  # sent
    await _impl(SARA_SLACK_ID)(VALID_INPUT)  # refused: requester
    await _impl(JON_SLACK_ID)({**VALID_INPUT, "recipient_email": SARA_EMAIL})  # refused: recipient
    await _impl(None)(VALID_INPUT)  # refused: identity unresolved

    rows = await _audit_rows()
    outcomes = [r.outcome for r in rows]
    assert outcomes == ["sent", "refused", "refused", "refused"]
    reasons = [r.refusal_reason for r in rows]
    assert reasons == [
        None,
        "requester_not_authorized",
        "recipient_not_authorized",
        "requester_identity_unresolved",
    ]


async def test_audit_write_failure_does_not_break_the_decision(
    slack: type[_FakeSlackClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken audit write must not read as, or cause, a send failure.

    Fails row CONSTRUCTION inside _record_attempt's own try/except (what
    that guard exists to survive), rather than the shared SessionLocal —
    the tool also opens a session earlier to resolve Callie's Slack config,
    and patching SessionLocal globally would break that unrelated call too.
    """

    class _BoomModel:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError("db is down")

    monkeypatch.setattr(callie_dm, "CallieDmSendAttempt", _BoomModel)
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "SENT" in result
    assert len(slack.dms) == 1


# ── 8. The would-be recipient is never notified of a refused attempt ─────────


async def test_refused_attempts_never_reach_slack_at_all(
    slack: type[_FakeSlackClient],
) -> None:
    scenarios = [
        (SARA_SLACK_ID, VALID_INPUT),
        (JON_SLACK_ID, {**VALID_INPUT, "recipient_email": SARA_EMAIL}),
        (None, VALID_INPUT),
        (JON_SLACK_ID, {**VALID_INPUT, "recipient_email": "not-an-email"}),
        (JON_SLACK_ID, {**VALID_INPUT, "recipient_email": f"{ANGELA_EMAIL},{HANNAH_EMAIL}"}),
    ]
    for speaker, payload in scenarios:
        await _impl(speaker)(payload)
    assert slack.dms == [], "no refused attempt may reach Slack in any form"


# ── 9. One recipient per call; multi-recipient input is rejected ─────────────


@pytest.mark.parametrize(
    "recipients",
    [
        f"{ANGELA_EMAIL},{HANNAH_EMAIL}",
        f"{ANGELA_EMAIL}; {HANNAH_EMAIL}",
        f"{ANGELA_EMAIL} and {HANNAH_EMAIL}",
        f"{ANGELA_EMAIL}\n{HANNAH_EMAIL}",
    ],
)
async def test_multi_recipient_input_is_rejected_not_fanned_out(
    slack: type[_FakeSlackClient], recipients: str
) -> None:
    result = await _impl(JON_SLACK_ID)({**VALID_INPUT, "recipient_email": recipients})
    assert "REFUSED" in result
    assert slack.dms == [], "must never fan out to multiple recipients"

    (row,) = await _audit_rows()
    assert row.refusal_reason == "recipient_input_invalid:multiple"


async def test_a_bare_name_is_rejected_as_not_email_shaped(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON_SLACK_ID)({**VALID_INPUT, "recipient_email": "Angela"})
    assert "REFUSED" in result
    assert slack.dms == []
    (row,) = await _audit_rows()
    assert row.refusal_reason == "recipient_input_invalid:not_email"


# ── 10. Empty allowlist setting -> nobody authorized, fail closed ────────────


def test_empty_requester_allowlist_denies_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(callie_dm, "authorized_requester_emails", lambda: frozenset())
    assert is_authorized_requester(JON_EMAIL) is False


def test_empty_recipient_allowlist_denies_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(callie_dm, "authorized_recipient_emails", lambda: frozenset())
    assert is_authorized_recipient(ANGELA_EMAIL) is False


async def test_empty_requester_allowlist_denies_at_the_tool_level(
    slack: type[_FakeSlackClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(callie_dm, "authorized_requester_emails", lambda: frozenset())
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "NOT_AUTHORIZED" in result
    assert slack.dms == []


# ── The gate, in isolation ──────────────────────────────────────────────────────


@pytest.mark.parametrize("email", [JON_EMAIL, ANGELA_EMAIL, JOSH_EMAIL])
def test_authorized_requester_emails_pass(email: str) -> None:
    assert is_authorized_requester(email) is True


@pytest.mark.parametrize("email", [HANNAH_EMAIL, JACLYN_EMAIL, SARA_EMAIL, "", "   ", None])
def test_everyone_else_is_denied_as_requester(email: str | None) -> None:
    assert is_authorized_requester(email) is False


@pytest.mark.parametrize(
    "email", [JON_EMAIL, ANGELA_EMAIL, JOSH_EMAIL, HANNAH_EMAIL, JACLYN_EMAIL]
)
def test_authorized_recipient_emails_pass(email: str) -> None:
    assert is_authorized_recipient(email) is True


@pytest.mark.parametrize("email", [SARA_EMAIL, "", "   ", None])
def test_everyone_else_is_denied_as_recipient(email: str | None) -> None:
    assert is_authorized_recipient(email) is False


def test_case_insensitive_email_matching() -> None:
    assert is_authorized_requester(JON_EMAIL.upper()) is True
    assert is_authorized_recipient(ANGELA_EMAIL.upper()) is True


def test_gate_fails_closed_on_non_string_identity() -> None:
    for junk in (0, 1, [], {}, object(), True):
        assert is_authorized_requester(junk) is False  # type: ignore[arg-type]
        assert is_authorized_recipient(junk) is False  # type: ignore[arg-type]


# ── Requester email cannot be resolved (data problem, not a permission one) ──


async def test_requester_email_lookup_failure_is_refused_and_distinguished(
    slack: type[_FakeSlackClient],
) -> None:
    slack.raise_on_lookup_email = True
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "REFUSED" in result
    assert slack.dms == []
    (row,) = await _audit_rows()
    assert row.refusal_reason == "requester_email_unresolved"
    assert row.requester_email is None


async def test_requester_email_lookup_returns_none_is_refused(
    slack: type[_FakeSlackClient],
) -> None:
    slack.email_lookup_returns_none = True
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "REFUSED" in result
    (row,) = await _audit_rows()
    assert row.refusal_reason == "requester_email_unresolved"


# ── Recipient allowlisted but Slack has no matching account ──────────────────


async def test_recipient_on_allowlist_but_no_slack_account_fails_closed_distinctly(
    slack: type[_FakeSlackClient],
) -> None:
    """Jaclyn is on the recipient allowlist but has no id in the fake Slack directory."""
    result = await _impl(JON_SLACK_ID)({**VALID_INPUT, "recipient_email": JACLYN_EMAIL})
    assert "REFUSED" in result
    assert slack.dms == []
    (row,) = await _audit_rows()
    assert row.outcome == "refused"
    assert row.refusal_reason == "recipient_lookup_failed"
    assert row.recipient_email == JACLYN_EMAIL
    assert row.recipient_slack_user_id is None


# ── Technical failures are "error", never misread as a policy decision ───────


async def test_recipient_lookup_exception_is_outcome_error_not_refused(
    slack: type[_FakeSlackClient],
) -> None:
    slack.raise_on_lookup_by_email = True
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "Could not send" in result
    assert slack.dms == []
    (row,) = await _audit_rows()
    assert row.outcome == "error"
    assert row.refusal_reason is not None and row.refusal_reason.startswith(
        "recipient_lookup_error:"
    )


async def test_post_dm_exception_is_outcome_error_and_reported_as_failure(
    slack: type[_FakeSlackClient],
) -> None:
    slack.raise_on_post_dm = True
    result = await _impl(JON_SLACK_ID)(VALID_INPUT)
    assert "Could not send" in result
    assert "Nothing went out" in result
    (row,) = await _audit_rows()
    assert row.outcome == "error"
    assert row.refusal_reason is not None and row.refusal_reason.startswith("post_dm_failed:")
    assert row.recipient_slack_user_id == ANGELA_SLACK_ID, "resolved before the send attempt"


# ── Input validation ────────────────────────────────────────────────────────────


async def test_missing_message_is_rejected_without_sending(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON_SLACK_ID)({"recipient_email": ANGELA_EMAIL, "message": "   "})
    assert result.startswith("Error:")
    assert slack.dms == []


async def test_missing_recipient_is_rejected_without_sending(
    slack: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON_SLACK_ID)({"recipient_email": "  ", "message": "hi"})
    assert "REFUSED" in result
    assert slack.dms == []


# ── Registry wiring ────────────────────────────────────────────────────────────


def test_send_guarded_dm_is_registered_for_callie_only() -> None:
    for agent in ("artemis", "kai", "ares", None):
        registry = build_authorized_tool_registry(set(), agent_id=agent, speaker_id=JON_SLACK_ID)
        assert "send_guarded_dm" not in registry, f"{agent} must not have Callie's DM tool"

    registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id=JON_SLACK_ID)
    assert "send_guarded_dm" in registry
    entry = registry.get("send_guarded_dm")
    assert entry is not None
    assert entry.layer == 2, "must be auto-invoke — see the module docstring on layer 3's leak"


def test_raw_send_slack_dm_is_removed_from_callie_but_kept_for_artemis() -> None:
    callie_registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id=None)
    assert "send_slack_dm" not in callie_registry, (
        "the raw, unguarded DM tool would make send_guarded_dm decorative for Callie"
    )
    # Callie keeps ordinary channel posting; only the raw DM tool is removed.
    assert "send_slack_message" in callie_registry

    artemis_registry = build_authorized_tool_registry(set(), agent_id="artemis", speaker_id=None)
    assert "send_slack_dm" in artemis_registry, "out of scope for CALLIE-1 to change Artemis"


def test_speaker_id_reaches_the_callie_registry() -> None:
    registry = build_authorized_tool_registry(set(), agent_id="callie", speaker_id=JON_SLACK_ID)
    entry = registry.get("send_guarded_dm")
    assert entry is not None


def test_register_slack_tools_include_dm_default_preserves_five_tools() -> None:
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.integrations.slack.tools import register_slack_tools

    reg = AuthorizedToolRegistry()
    register_slack_tools(reg)
    assert "send_slack_dm" in reg
    assert len(reg) == 5


def test_register_slack_tools_can_exclude_dm() -> None:
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.integrations.slack.tools import register_slack_tools

    reg = AuthorizedToolRegistry()
    register_slack_tools(reg, include_dm=False)
    assert "send_slack_dm" not in reg
    assert len(reg) == 4


def test_tool_registry_module_wires_callie_dm_tool() -> None:
    """Guards against the registration call being silently dropped in a future edit.

    CALLIE-2 moved Callie's registration out of the general fallthrough path
    and into her own early-return builder (``_build_callie_tool_registry``),
    in the shape of Kai's and Ares's -- see that function's docstring. The
    wiring call now lives there, not in ``build_authorized_tool_registry``
    itself.
    """
    import inspect

    source = inspect.getsource(tool_registry_mod._build_callie_tool_registry)
    assert "register_callie_dm_tool" in source
