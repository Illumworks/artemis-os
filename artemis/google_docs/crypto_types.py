"""SQLAlchemy TypeDecorator for encrypting Google OAuth tokens at rest.

Encrypts on write (process_bind_param) and decrypts on read (process_result_value)
using the same Fernet helper (ARTEMIS_CREDENTIALS_KEY) as the other integrations.
Stored as BYTEA in Postgres — consistent with integrations.models encrypted_credentials.
"""

from __future__ import annotations

from sqlalchemy import types
from sqlalchemy.dialects.postgresql import BYTEA


class EncryptedToken(types.TypeDecorator[str]):
    """Transparent encrypt-on-write / decrypt-on-read column type for OAuth tokens.

    Wraps a single string value in {"t": value} before encryption so the
    same encrypt_credentials / decrypt_credentials helpers (which expect a dict)
    can be reused without modification.

    Stored column type: BYTEA (raw Fernet ciphertext bytes).
    Python-side type: str (the original token string).
    """

    impl = BYTEA
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> bytes | None:
        if value is None:
            return None
        from artemis.integrations.crypto import encrypt_credentials

        return encrypt_credentials({"t": str(value)})

    def process_result_value(self, value: bytes | None, dialect: object) -> str | None:
        if value is None:
            return None
        from artemis.integrations.crypto import decrypt_credentials

        return str(decrypt_credentials(bytes(value))["t"])
