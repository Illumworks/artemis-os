"""Connector credential encryption.

Uses Python's cryptography.fernet with a key from ARTEMIS_CONNECTOR_KEY.

On startup, failure to load the key is FATAL — agents cannot run without
encryption capability. The key must be a URL-safe base64-encoded 32-byte
Fernet key. Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Credentials are stored in JSONB as a base64-encoded encrypted blob.
They are NEVER logged — this module enforces that.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_ENV_VAR = "ARTEMIS_CONNECTOR_KEY"
_DOT_ENV_PATH = Path.home() / ".artemis" / ".env"


class ConnectorEncryptionError(Exception):
    """Raised when encryption/decryption fails."""


class ConnectorKeyMissingError(ConnectorEncryptionError, RuntimeError):
    """The connector encryption key is not configured.

    This is a deployment/config problem, not a bad request, and it used to
    escape as a bare RuntimeError — so saving a connector returned a bare
    HTTP 500 with a stack trace, indistinguishable from a code bug. It stays a
    RuntimeError subclass so any existing `except RuntimeError` still catches
    it; routes catch this specific type to say what is actually wrong.
    """


def _load_key() -> bytes:
    """Load the connector encryption key. Fatal if missing."""
    raw = os.environ.get(_ENV_VAR)
    if raw:
        return raw.encode()

    # Try ~/.artemis/.env
    if _DOT_ENV_PATH.exists():
        for line in _DOT_ENV_PATH.read_text().splitlines():
            if line.startswith(f"{_ENV_VAR}="):
                val = line.split("=", 1)[1].strip().strip('"')
                os.environ[_ENV_VAR] = val
                return val.encode()

    raise ConnectorKeyMissingError(
        f"{_ENV_VAR} is not set. "
        'Generate a key with: python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())" '
        f"and add it to ~/.artemis/.env as {_ENV_VAR}=<key>"
    )


def _fernet() -> Fernet:
    key = _load_key()
    try:
        decoded = base64.urlsafe_b64decode(key + b"==")
        if len(decoded) != 32:
            raise ValueError("Fernet key must decode to exactly 32 bytes")
    except Exception as exc:
        raise ConnectorEncryptionError(f"Invalid {_ENV_VAR}: {exc}") from exc
    return Fernet(key)


def encrypt_credentials(payload: dict[str, str]) -> str:
    """JSON-encode payload, encrypt with Fernet, return as base64 string for JSONB storage."""
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    cipher = _fernet().encrypt(plaintext)
    return base64.urlsafe_b64encode(cipher).decode()


def decrypt_credentials(blob: str) -> dict[str, str]:
    """Decrypt a base64 Fernet blob back to the original dict."""
    try:
        cipher = base64.urlsafe_b64decode(blob.encode())
        plaintext = _fernet().decrypt(cipher)
        result: dict[str, str] = json.loads(plaintext)
        return result
    except InvalidToken as exc:
        raise ConnectorEncryptionError("Credential decryption failed — wrong key?") from exc
    except Exception as exc:
        raise ConnectorEncryptionError(f"Credential decryption error: {exc}") from exc


def mask_credentials(payload: dict[str, str]) -> dict[str, str]:
    """Return a copy of payload with all values masked to '***' for safe display."""
    return {k: "***" for k in payload}
