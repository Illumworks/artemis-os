"""Verify and route Starbridge webhook deliveries.

Starbridge POSTs a bridge row the moment its columns finish processing, instead
of us polling a 4-hour scout. Deliveries are signed Ed25519 per the Standard
Webhooks spec, over ``{webhook-id}.{webhook-timestamp}.{raw_body}``.

**Two destinations, because the signals are two different things.** A live feed
of 35 classified findings was 20 board-meeting items against 15 procurement and
funding ones. Putting all of them in Josh's campaign queue would bury the Kansas
statewide screener RFP under twenty district strategic plans. So:

- **signal_queue** — procurement, funding and approved-vendor signals. These
  carry a buyer, a deadline and an action.
- **memory_observations** — meeting and leader-transition signals. These are
  context worth retrieving later, not a campaign trigger today.

Anything we cannot classify against Josh's registry is written nowhere and
counted, because a signal carrying a confident wrong label is worse than one we
never had.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

#: Reject anything older than this. Starbridge's own guidance, and the Standard
#: Webhooks default: without it a captured delivery can be replayed forever.
MAX_TIMESTAMP_AGE_SECONDS = 300

_SIGNATURE_PREFIX = "v1a,"
_PUBLIC_KEY_PREFIX = "whpk_"

#: Reason-code domains that belong in Josh's campaign queue rather than memory.
#: These are the signals with a buyer, a deadline and something to do about it.
ACTIONABLE_PREFIXES = ("PROCUREMENT_", "FUNDING_", "VENDOR_")


class WebhookVerificationError(Exception):
    """The delivery is not provably from Starbridge, or is too old.

    Raised rather than returned so no caller can accidentally treat an
    unverified payload as verified by ignoring a boolean.
    """


@dataclass(frozen=True)
class WebhookHeaders:
    webhook_id: str
    timestamp: str
    signature: str


def load_public_key(configured: str) -> Ed25519PublicKey:
    """Parse a ``whpk_``-prefixed key into something that can verify.

    The prefix is Starbridge's label, not part of the key, and the bytes behind
    it are DER-encoded X.509/SPKI rather than a raw 32-byte Ed25519 key.
    """
    raw = configured.strip()
    if raw.startswith(_PUBLIC_KEY_PREFIX):
        raw = raw[len(_PUBLIC_KEY_PREFIX) :]
    if not raw:
        raise WebhookVerificationError(
            "STARBRIDGE_WEBHOOK_PUBLIC_KEY is empty. Copy it from Starbridge "
            "Settings -> Webhook Keys; deliveries cannot be verified without it."
        )

    der = base64.b64decode(raw)
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    key = load_der_public_key(der)
    if not isinstance(key, Ed25519PublicKey):
        raise WebhookVerificationError(
            f"Configured webhook key is {type(key).__name__}, not Ed25519."
        )
    return key


def verify_delivery(
    *,
    raw_body: bytes,
    headers: WebhookHeaders,
    public_key: Ed25519PublicKey,
    now: float | None = None,
) -> None:
    """Raise unless this delivery is genuinely Starbridge's and recent.

    ``raw_body`` must be the bytes as received. Parsing and re-serialising the
    JSON changes whitespace and key order and breaks the signature -- Starbridge
    calls this out explicitly, and it is the single easiest way to get a
    verifier that rejects every real delivery.
    """
    try:
        sent_at = int(headers.timestamp)
    except (TypeError, ValueError) as exc:
        raise WebhookVerificationError(
            f"webhook-timestamp is not a unix timestamp: {headers.timestamp!r}"
        ) from exc

    age = (now if now is not None else time.time()) - sent_at
    if abs(age) > MAX_TIMESTAMP_AGE_SECONDS:
        raise WebhookVerificationError(
            f"webhook-timestamp is {int(age)}s away from now, outside the "
            f"{MAX_TIMESTAMP_AGE_SECONDS}s replay window."
        )

    signature = headers.signature.strip()
    if not signature.startswith(_SIGNATURE_PREFIX):
        raise WebhookVerificationError(
            f"webhook-signature does not carry the {_SIGNATURE_PREFIX!r} version prefix."
        )
    try:
        signature_bytes = base64.b64decode(signature[len(_SIGNATURE_PREFIX) :])
    except Exception as exc:
        raise WebhookVerificationError("webhook-signature is not valid base64.") from exc

    message = b".".join(
        [headers.webhook_id.encode("utf-8"), headers.timestamp.encode("utf-8"), raw_body]
    )
    try:
        public_key.verify(signature_bytes, message)
    except InvalidSignature as exc:
        raise WebhookVerificationError(
            "Ed25519 signature does not match. The delivery is not from Starbridge, "
            "or the raw body was re-serialised before verifying."
        ) from exc


def is_actionable(reason_codes: list[str]) -> bool:
    """True when a finding belongs in the campaign queue rather than memory."""
    return any(code.startswith(ACTIONABLE_PREFIXES) for code in reason_codes)
