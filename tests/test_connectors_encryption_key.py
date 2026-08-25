"""The connector encryption key: a missing one must say so, not return HTTP 500.

`ARTEMIS_CONNECTOR_KEY` had never been set on this machine, because the API
Connectors section was unreachable in the UI so no connector was ever created.
The first save attempt therefore hit `_load_key`, which raised a bare
`RuntimeError` — FastAPI turned that into a plain 500 and the form showed
"HTTP 500", indistinguishable from a bug in saving.

That is the CLAUDE.md corollary in miniature: a path that fails closed but
cannot say *why* sends everyone hunting the wrong problem.
"""

from __future__ import annotations

import pathlib

import pytest

from artemis.connectors import encryption
from artemis.connectors.encryption import (
    ConnectorEncryptionError,
    ConnectorKeyMissingError,
    decrypt_credentials,
    encrypt_credentials,
)

SECRET_URL = "https://vistasocial.com/api/integration/mcp?api_key=ROUNDTRIPKEY"


# ── the missing-key error ─────────────────────────────────────────────────────


def test_missing_key_raises_the_specific_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("ARTEMIS_CONNECTOR_KEY", raising=False)
    monkeypatch.setattr(encryption, "_DOT_ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(ConnectorKeyMissingError):
        encryption._load_key()


def test_missing_key_error_says_how_to_fix_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The message is the whole point — it must name the var and the remedy."""
    monkeypatch.delenv("ARTEMIS_CONNECTOR_KEY", raising=False)
    monkeypatch.setattr(encryption, "_DOT_ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(ConnectorKeyMissingError) as excinfo:
        encryption._load_key()
    message = str(excinfo.value)
    assert "ARTEMIS_CONNECTOR_KEY" in message
    assert "Fernet.generate_key" in message


def test_missing_key_error_is_still_a_runtime_error() -> None:
    """Kept as a RuntimeError subclass so existing handlers keep working."""
    assert issubclass(ConnectorKeyMissingError, RuntimeError)
    assert issubclass(ConnectorKeyMissingError, ConnectorEncryptionError)


def test_key_is_read_from_the_dot_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """How the real deployment supplies it: ~/.artemis/.env, not the repo .env."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    env = tmp_path / ".env"
    env.write_text(f"# comment\nARTEMIS_CONNECTOR_KEY={key}\n")
    monkeypatch.delenv("ARTEMIS_CONNECTOR_KEY", raising=False)
    monkeypatch.setattr(encryption, "_DOT_ENV_PATH", env)
    assert encryption._load_key() == key.encode()


# ── round trip ────────────────────────────────────────────────────────────────


def test_credentials_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ARTEMIS_CONNECTOR_KEY", Fernet.generate_key().decode())
    blob = encrypt_credentials({"mcp_url": SECRET_URL})
    assert decrypt_credentials(blob) == {"mcp_url": SECRET_URL}


def test_ciphertext_does_not_contain_the_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    """At rest it must be opaque — the stored blob is what a DB dump exposes."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ARTEMIS_CONNECTOR_KEY", Fernet.generate_key().decode())
    blob = encrypt_credentials({"mcp_url": SECRET_URL})
    assert "ROUNDTRIPKEY" not in blob
    assert "vistasocial" not in blob


def test_invalid_key_is_reported_as_an_encryption_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTEMIS_CONNECTOR_KEY", "not-a-valid-fernet-key")
    with pytest.raises(ConnectorEncryptionError):
        encrypt_credentials({"a": "b"})
