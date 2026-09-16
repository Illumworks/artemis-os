"""Gmail send path for the Champions digest.

Two things are covered here, and both exist because of a known failure mode:

1. The From/Reply-To/multi-recipient headers actually reach the MIME message.
   Gmail silently rewrites an unverified From rather than erroring, so the
   header we build is the only thing we can assert in a test -- whether Google
   honours it is confirmed by reading a delivered message, not here.

2. ``resolve_gmail_client`` distinguishes "not connected" from "connected but
   missing gmail.send". A fail-closed path that conflates the two reports a
   permissions problem for what is a re-authorization problem, which is exactly
   what sent people hunting the wrong bug on the crisis-content approvals.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from typing import Any

import pytest

from artemis.integrations.gmail.client import GmailClient
from artemis.integrations.gmail.sender import GmailSenderNotReadyError, resolve_gmail_client


class _CapturingClient(GmailClient):
    """A GmailClient that records the payload instead of calling Gmail."""

    def __init__(self) -> None:
        super().__init__(
            access_token="test-access",
            refresh_token="test-refresh",
            client_id="cid",
            client_secret="secret",
            expires_at=9_999_999_999.0,
        )
        self.sent_payload: dict[str, Any] | None = None

    async def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        self.sent_payload = json
        return {"id": "msg-1", "threadId": "thr-1", "labelIds": []}

    def parsed(self) -> Any:
        assert self.sent_payload is not None, "send_message never posted"
        raw = str(self.sent_payload["raw"])
        return message_from_bytes(base64.urlsafe_b64decode(raw))


@pytest.mark.asyncio
async def test_digest_headers_carry_hannah_and_all_recipients() -> None:
    client = _CapturingClient()
    await client.send_message(
        to=[
            "success@amiralearning.com",
            "jaclyn.wright@amiralearning.com",
            "hannah.slater@amiralearning.com",
        ],
        subject="Champions community digest",
        body="plain text",
        from_addr="Hannah Slater <hannah.slater@amiralearning.com>",
        reply_to="hannah.slater@amiralearning.com",
    )
    msg = client.parsed()

    assert msg["From"] == "Hannah Slater <hannah.slater@amiralearning.com>"
    assert msg["Reply-To"] == "hannah.slater@amiralearning.com"
    # All three recipients survive as one header, not just the first.
    for addr in (
        "success@amiralearning.com",
        "jaclyn.wright@amiralearning.com",
        "hannah.slater@amiralearning.com",
    ):
        assert addr in msg["To"]


@pytest.mark.asyncio
async def test_plain_string_recipient_still_works() -> None:
    """The existing personal-Gmail callers pass a bare string; do not break them."""
    client = _CapturingClient()
    await client.send_message(to="jon.fila@amiralearning.com", subject="s", body="b")
    msg = client.parsed()
    assert msg["To"] == "jon.fila@amiralearning.com"
    assert msg["From"] is None  # unset unless explicitly asked for


@pytest.mark.asyncio
async def test_html_alternative_is_attached() -> None:
    client = _CapturingClient()
    await client.send_message(to="a@b.com", subject="s", body="plain", html_body="<p>rich</p>")
    msg = client.parsed()
    assert msg.is_multipart()
    subtypes = {part.get_content_subtype() for part in msg.walk() if not part.is_multipart()}
    assert {"plain", "html"} <= subtypes


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self, credential: Any) -> None:
        self._credential = credential

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._credential)


@pytest.mark.asyncio
async def test_missing_credential_says_not_connected() -> None:
    with pytest.raises(GmailSenderNotReadyError) as exc:
        await resolve_gmail_client(_FakeSession(None), purpose="marketing")  # type: ignore[arg-type]
    message = str(exc.value)
    assert "No marketing Google credential is connected" in message
    assert "gmail.send" not in message, "must not blame the scope when nothing is connected"


@pytest.mark.asyncio
async def test_connected_without_send_scope_names_the_account_and_the_fix() -> None:
    class _Credential:
        connected_email = "amiracentral@amiralearning.com"
        scope = "https://www.googleapis.com/auth/drive.file openid"

    with pytest.raises(GmailSenderNotReadyError) as exc:
        await resolve_gmail_client(_FakeSession(_Credential()), purpose="marketing")  # type: ignore[arg-type]
    message = str(exc.value)
    # Names WHICH account, and says a refresh will not fix it -- the actual trap.
    assert "amiracentral@amiralearning.com" in message
    assert "gmail.send" in message
    assert "re-authorize" in message.lower()
    assert "refresh will not add it" in message
