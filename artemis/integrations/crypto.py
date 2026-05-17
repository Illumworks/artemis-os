"""Credential encryption/decryption for integration tokens.

Key source: ARTEMIS_CREDENTIALS_KEY env var (base64-url Fernet key).
If the var is missing on first run, a key is generated and written to
~/.artemis/.env with a prominent log warning — never committed to git.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_ENV_VAR = "ARTEMIS_CREDENTIALS_KEY"
_DOT_ENV_PATH = Path.home() / ".artemis" / ".env"


def _load_or_create_key() -> bytes:
    raw = os.environ.get(_ENV_VAR)
    if raw:
        return raw.encode()

    if _DOT_ENV_PATH.exists():
        for line in _DOT_ENV_PATH.read_text().splitlines():
            if line.startswith(f"{_ENV_VAR}="):
                val = line.split("=", 1)[1].strip().strip('"')
                os.environ[_ENV_VAR] = val
                return val.encode()

    key = Fernet.generate_key()
    _DOT_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DOT_ENV_PATH.open("a") as f:
        f.write(f'\n{_ENV_VAR}="{key.decode()}"\n')
    os.environ[_ENV_VAR] = key.decode()
    logger.warning(
        "ARTEMIS_CREDENTIALS_KEY was not set. Generated a new Fernet key and wrote it to %s. "
        "Copy this value into your environment for persistence.",
        _DOT_ENV_PATH,
    )
    return key


def _fernet() -> Fernet:
    key = _load_or_create_key()
    # Validate key format — raises ValueError if malformed.
    try:
        decoded = base64.urlsafe_b64decode(key + b"==")
        if len(decoded) != 32:
            raise ValueError("Fernet key must decode to exactly 32 bytes")
    except Exception as exc:
        raise ValueError(f"Invalid {_ENV_VAR}: {exc}") from exc
    return Fernet(key)


def encrypt_credentials(payload: dict[str, object]) -> bytes:
    """JSON-encode payload and encrypt with Fernet. Returns raw ciphertext bytes."""
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    return _fernet().encrypt(plaintext)


def decrypt_credentials(blob: bytes) -> dict[str, object]:
    """Decrypt Fernet ciphertext and JSON-decode back to a dict."""
    plaintext = _fernet().decrypt(blob)
    result: dict[str, object] = json.loads(plaintext)
    return result
