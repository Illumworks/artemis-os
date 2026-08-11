"""flag_catalog_gap — Kai's one side-effecting tool, and its authorization gate.

Owner decision (Jon, 2026-08-10): side-effecting actions only when the requester
is Jon or Missy; everyone else is information-only. The gate is enforced from the
Slack user id resolved off the inbound event, bound into the tool as a closure
value, so nothing the model emits can influence it.

The deny paths are tested adversarially: this is the only non-read capability a
deliberately read-only agent has, so a bug here is a security failure, not a bug.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.enablement import actions
from artemis.enablement.actions import (
    FLAG_CATALOG_GAP,
    build_gap_message,
    is_authorized_for_kai_actions,
)
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

JON = "U09F3EPJXSQ"
MISSY = "U07CHT0S7UK"
SARA = "U07926XP0FR"
AMANDA = "U0BBUF8R77G"
CHANNEL = "C0BB17EJLKC"


class _FakeSlackClient:
    """Records post_message calls instead of hitting Slack."""

    posts: list[dict[str, Any]] = []

    def __init__(self, token: str) -> None:
        self.token = token

    async def post_message(self, **kwargs: Any) -> dict[str, Any]:
        type(self).posts.append(kwargs)
        return {"ok": True, "ts": "1786400000.000100"}


class _FakeCfg:
    access_token = "xoxb-fake"


@pytest.fixture
def slack_spy(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSlackClient]:
    """Patch the Slack client + config resolution at their import sites."""
    _FakeSlackClient.posts = []

    import artemis.integrations.slack.client as slack_client
    import artemis.routes.integrations_slack_events as slack_events

    monkeypatch.setattr(slack_client, "SlackClient", _FakeSlackClient)

    async def _fake_resolve(*args: Any, **kwargs: Any) -> _FakeCfg:
        return _FakeCfg()

    monkeypatch.setattr(slack_events, "_resolve_agent_slack_config", _fake_resolve)
    return _FakeSlackClient


def _impl(speaker_id: str | None) -> Any:
    return actions._make_flag_catalog_gap(speaker_id)


VALID_INPUT = {
    "requested": "Evaluar User Guide",
    "search_summary": "No match. Closest were the Tutor and ISIP Assess manuals for SY2526.",
}


# ── The gate ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("user_id", [JON, MISSY])
def test_authorized_users_pass_the_gate(user_id: str) -> None:
    assert is_authorized_for_kai_actions(user_id) is True


@pytest.mark.parametrize(
    "user_id",
    [
        SARA,  # owns the catalog but is NOT authorized to trigger actions
        AMANDA,
        "U_UNKNOWN",
        "",
        "   ",
        None,
    ],
)
def test_everyone_else_is_denied(user_id: str | None) -> None:
    assert is_authorized_for_kai_actions(user_id) is False


def test_gate_fails_closed_on_non_string_identity() -> None:
    """A malformed identity must deny, never raise and never pass."""
    for junk in (0, 1, [], {}, object(), True):
        assert is_authorized_for_kai_actions(junk) is False  # type: ignore[arg-type]


def test_gate_denies_when_no_one_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "authorized_action_user_ids", lambda: frozenset())
    assert is_authorized_for_kai_actions(JON) is False


def test_sara_is_a_catalog_owner_but_not_an_action_trigger() -> None:
    """Sara gets tagged on the post; she cannot fire it. These are distinct lists."""
    assert SARA in actions.catalog_owner_user_ids()
    assert SARA not in actions.authorized_action_user_ids()


# ── Deny path posts nothing ───────────────────────────────────────────────────


async def test_unauthorized_requester_posts_nothing(slack_spy: type[_FakeSlackClient]) -> None:
    result = await _impl(SARA)(VALID_INPUT)
    assert "NOT_AUTHORIZED" in result
    assert slack_spy.posts == [], "an unauthorized request must not reach Slack at all"


async def test_absent_identity_posts_nothing(slack_spy: type[_FakeSlackClient]) -> None:
    """resume_after_confirm rebuilds the registry without a speaker: must deny."""
    result = await _impl(None)(VALID_INPUT)
    assert "NOT_AUTHORIZED" in result
    assert slack_spy.posts == []


async def test_deny_message_tells_kai_not_to_imply_it_was_recorded(
    slack_spy: type[_FakeSlackClient],
) -> None:
    result = await _impl(AMANDA)(VALID_INPUT)
    assert "no gap was filed" in result
    assert "Do not imply anything was recorded" in result


# ── Spoofing ──────────────────────────────────────────────────────────────────


async def test_model_cannot_spoof_the_requester_through_tool_input(
    slack_spy: type[_FakeSlackClient],
) -> None:
    """The classic escalation: the model asserts it is speaking for Jon."""
    spoofed = {
        **VALID_INPUT,
        "requested_by": JON,
        "speaker_id": JON,
        "user": JON,
        "authorized": True,
    }
    result = await _impl(SARA)(spoofed)
    assert "NOT_AUTHORIZED" in result
    assert slack_spy.posts == []


async def test_requester_line_comes_from_the_closure_not_the_input(
    slack_spy: type[_FakeSlackClient],
) -> None:
    """An authorized caller cannot post *as* someone else either."""
    result = await _impl(MISSY)({**VALID_INPUT, "requested_by": JON})
    assert "POSTED" in result
    (post,) = slack_spy.posts
    assert f"<@{MISSY}>" in post["text"], "attribution must be the real speaker"
    assert f"*Raised by:* <@{JON}>" not in post["text"]


def test_tool_schema_exposes_no_identity_parameter() -> None:
    """Identity must not be model-supplied. Absent from the schema is the guarantee."""
    props = FLAG_CATALOG_GAP.input_schema["properties"]
    for forbidden in ("requested_by", "speaker_id", "user", "user_id", "authorized", "as_user"):
        assert forbidden not in props
    assert set(props) == {"requested", "search_summary", "url"}


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_authorized_requester_posts_to_the_channel(
    slack_spy: type[_FakeSlackClient],
) -> None:
    result = await _impl(JON)({**VALID_INPUT, "url": "https://explore.amiralearning.com/x.pdf"})
    assert "POSTED" in result
    (post,) = slack_spy.posts
    assert post["channel"] == CHANNEL
    text = post["text"]
    assert f"<@{SARA}>" in text and f"<@{MISSY}>" in text, "must tag both catalog owners"
    assert "Evaluar User Guide" in text
    assert "https://explore.amiralearning.com/x.pdf" in text
    assert f"<@{JON}>" in text


async def test_success_result_forbids_overclaiming(slack_spy: type[_FakeSlackClient]) -> None:
    """Kai must not turn 'posted a message' into 'filed a ticket' (F1 with a tool)."""
    result = await _impl(JON)(VALID_INPUT)
    assert "no ticket" in result
    assert "no assignment" in result


# ── Input validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_input",
    [
        {"search_summary": "nothing found"},
        {"requested": "Evaluar guide"},
        {"requested": "  ", "search_summary": "nothing found"},
        {"requested": "Evaluar guide", "search_summary": "   "},
    ],
)
async def test_missing_fields_are_rejected_without_posting(
    slack_spy: type[_FakeSlackClient], bad_input: dict[str, Any]
) -> None:
    result = await _impl(JON)(bad_input)
    assert result.startswith("Error:")
    assert slack_spy.posts == []


async def test_missing_channel_config_fails_closed(
    slack_spy: type[_FakeSlackClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "enablement_library_channel_id", "", raising=False)
    result = await _impl(JON)(VALID_INPUT)
    assert "Nothing was filed" in result
    assert slack_spy.posts == []


async def test_slack_failure_is_reported_as_failure(
    slack_spy: type[_FakeSlackClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed post must never read as success — that is the F1 defect exactly."""

    class _Boom(_FakeSlackClient):
        async def post_message(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("channel_not_found")

    import artemis.integrations.slack.client as slack_client

    monkeypatch.setattr(slack_client, "SlackClient", _Boom)
    result = await _impl(JON)(VALID_INPUT)
    assert "Could not post" in result
    assert "Nothing was filed" in result
    assert "channel_not_found" in result


# ── Message shape ─────────────────────────────────────────────────────────────


def test_gap_message_respects_kai_style_rules() -> None:
    text = build_gap_message(
        requested="Evaluar User Guide",
        search_summary="No match.",
        requester_id=JON,
        url=None,
    )
    assert "—" not in text and "–" not in text, "Kai never uses em or en dashes"
    assert not any(ord(ch) > 0x2100 for ch in text), "Kai uses no emojis"


def test_gap_message_omits_the_url_line_when_none_supplied() -> None:
    text = build_gap_message(
        requested="Evaluar User Guide",
        search_summary="No match.",
        requester_id=JON,
    )
    assert "Link supplied" not in text


# ── Registry wiring ───────────────────────────────────────────────────────────


def test_speaker_id_reaches_the_kai_registry() -> None:
    registry = build_authorized_tool_registry(set(), agent_id="kai", speaker_id=JON)
    assert "flag_catalog_gap" in registry


def test_no_other_agent_gets_the_flag_tool() -> None:
    for agent in ("artemis", "callie", "ares"):
        registry = build_authorized_tool_registry(set(), agent_id=agent, speaker_id=JON)
        assert "flag_catalog_gap" not in registry, f"{agent} must not have Kai's tool"


def test_kai_still_has_no_general_purpose_tools() -> None:
    """Stream 2 adds ONE capability. Nothing else may have leaked in."""
    registry = build_authorized_tool_registry(set(), agent_id="kai", speaker_id=JON)
    for forbidden in (
        "query_memory",
        "send_slack_message",
        "send_slack_dm",
        "write_memory",
        "spawn_subagent",
        "create_event",
        "read_file",
    ):
        assert forbidden not in registry
