"""Verifying and routing Starbridge webhook deliveries.

Deliveries are Ed25519-signed over `{webhook-id}.{webhook-timestamp}.{raw_body}`
per the Standard Webhooks spec. The signatures here are generated with a real
throwaway keypair rather than mocked, because a verifier that is only ever asked
to check a stubbed signature is a verifier nobody has checked.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from artemis.starbridge.webhook import (
    MAX_TIMESTAMP_AGE_SECONDS,
    WebhookHeaders,
    WebhookVerificationError,
    is_actionable,
    load_public_key,
    verify_delivery,
)

_PRIVATE = Ed25519PrivateKey.generate()


def _public_key_string() -> str:
    """Exactly the shape Starbridge shows in Settings: whpk_ + base64 DER SPKI."""
    der = _PRIVATE.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return "whpk_" + base64.b64encode(der).decode()


def _sign(body: bytes, webhook_id: str, timestamp: str) -> str:
    message = b".".join([webhook_id.encode(), timestamp.encode(), body])
    return "v1a," + base64.b64encode(_PRIVATE.sign(message)).decode()


def _delivery(body: bytes | None = None, *, age: int = 0) -> tuple[bytes, WebhookHeaders]:
    body = body if body is not None else json.dumps({"type": "bridge.row.created"}).encode()
    webhook_id = "msg_350d6ad7-13e1-47f8-8111-33f96313fabe"
    timestamp = str(int(time.time()) - age)
    return body, WebhookHeaders(webhook_id, timestamp, _sign(body, webhook_id, timestamp))


# ── the key format Starbridge actually hands you ─────────────────────────────


def test_the_whpk_prefix_is_stripped_before_parsing() -> None:
    """The prefix is Starbridge's label, not part of the key."""
    assert load_public_key(_public_key_string())


def test_a_key_pasted_without_its_prefix_still_works() -> None:
    """Someone will paste it either way; both are the same key."""
    assert load_public_key(_public_key_string().removeprefix("whpk_"))


def test_an_unconfigured_key_says_where_to_get_it() -> None:
    with pytest.raises(WebhookVerificationError, match="Settings -> Webhook Keys"):
        load_public_key("   ")


# ── verification ─────────────────────────────────────────────────────────────


def test_a_genuine_delivery_verifies() -> None:
    body, headers = _delivery()
    verify_delivery(
        raw_body=body, headers=headers, public_key=load_public_key(_public_key_string())
    )


def test_a_tampered_body_is_rejected() -> None:
    body, headers = _delivery()

    with pytest.raises(WebhookVerificationError, match="does not match"):
        verify_delivery(
            raw_body=body + b" ",
            headers=headers,
            public_key=load_public_key(_public_key_string()),
        )


def test_reserialising_the_json_breaks_verification() -> None:
    """The single easiest way to build a verifier that rejects every real call.

    Starbridge calls this out explicitly: re-serialising changes whitespace and
    key order. This test exists so nobody "tidies" the route to parse first.
    """
    body, headers = _delivery(json.dumps({"type": "bridge.row.created"}).encode())
    reserialised = json.dumps(json.loads(body), indent=2).encode()

    with pytest.raises(WebhookVerificationError):
        verify_delivery(
            raw_body=reserialised,
            headers=headers,
            public_key=load_public_key(_public_key_string()),
        )


def test_a_signature_from_another_key_is_rejected() -> None:
    other = Ed25519PrivateKey.generate()
    body = b'{"type":"bridge.row.created"}'
    wid, ts = "msg_1", str(int(time.time()))
    sig = "v1a," + base64.b64encode(other.sign(f"{wid}.{ts}.".encode() + body)).decode()

    with pytest.raises(WebhookVerificationError):
        verify_delivery(
            raw_body=body,
            headers=WebhookHeaders(wid, ts, sig),
            public_key=load_public_key(_public_key_string()),
        )


def test_an_old_delivery_is_rejected_as_a_replay() -> None:
    """A captured delivery must not be replayable forever."""
    body, headers = _delivery(age=MAX_TIMESTAMP_AGE_SECONDS + 60)

    with pytest.raises(WebhookVerificationError, match="replay window"):
        verify_delivery(
            raw_body=body, headers=headers, public_key=load_public_key(_public_key_string())
        )


def test_a_delivery_from_the_future_is_also_rejected() -> None:
    """Clock skew cuts both ways; an unbounded future stamp defeats the window."""
    body, headers = _delivery(age=-(MAX_TIMESTAMP_AGE_SECONDS + 60))

    with pytest.raises(WebhookVerificationError, match="replay window"):
        verify_delivery(
            raw_body=body, headers=headers, public_key=load_public_key(_public_key_string())
        )


def test_a_delivery_just_inside_the_window_is_accepted() -> None:
    body, headers = _delivery(age=MAX_TIMESTAMP_AGE_SECONDS - 30)
    verify_delivery(
        raw_body=body, headers=headers, public_key=load_public_key(_public_key_string())
    )


def test_a_missing_version_prefix_is_rejected() -> None:
    body, headers = _delivery()
    stripped = WebhookHeaders(headers.webhook_id, headers.timestamp, headers.signature[4:])

    with pytest.raises(WebhookVerificationError, match="version prefix"):
        verify_delivery(
            raw_body=body, headers=stripped, public_key=load_public_key(_public_key_string())
        )


def test_a_nonsense_timestamp_is_rejected_before_any_crypto() -> None:
    body, headers = _delivery()
    bad = WebhookHeaders(headers.webhook_id, "not-a-timestamp", headers.signature)

    with pytest.raises(WebhookVerificationError, match="not a unix timestamp"):
        verify_delivery(
            raw_body=body, headers=bad, public_key=load_public_key(_public_key_string())
        )


# ── which queue a signal belongs in ──────────────────────────────────────────


def test_procurement_and_funding_signals_are_actionable() -> None:
    assert is_actionable(["PROCUREMENT_LITERACY_RFP"])
    assert is_actionable(["FUNDING_LITERACY_GRANT"])
    assert is_actionable(["VENDOR_APPROVED_LIST"])


def test_board_meeting_context_is_not_a_campaign_trigger() -> None:
    """20 of 35 live findings were meeting items.

    Sending those to Josh's queue buries the Kansas statewide screener RFP under
    twenty district strategic plans.
    """
    assert not is_actionable(["DISTRICT_STRATEGIC_LITERACY"])
    assert not is_actionable(["LEADER_TRANSITION_FORMAL"])


def test_a_mixed_signal_is_actionable_if_any_code_is() -> None:
    assert is_actionable(["DISTRICT_STRATEGIC_LITERACY", "PROCUREMENT_LITERACY_RFP"])


def test_no_codes_is_not_actionable() -> None:
    assert not is_actionable([])
