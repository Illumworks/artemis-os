"""Encrypt Google OAuth tokens at rest (access_token, refresh_token → BYTEA).

Backfill-encrypts existing plaintext rows using the ARTEMIS_CREDENTIALS_KEY
Fernet helper (same key used by integrations/crypto.py for gcal/granola/slack).
Column type changes from Text → BYTEA; the EncryptedToken TypeDecorator makes
this transparent to all Python consumers.

Revision ID: 0090
Revises: 0089
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Import the crypto helper inside the function to avoid import-time side
    # effects during alembic history / check operations.
    from artemis.integrations.crypto import encrypt_credentials

    bind = op.get_bind()

    # 1. Add temporary BYTEA columns alongside the existing Text columns.
    op.add_column(
        "google_credentials",
        sa.Column("access_token_enc", sa.LargeBinary, nullable=True),
    )
    op.add_column(
        "google_credentials",
        sa.Column("refresh_token_enc", sa.LargeBinary, nullable=True),
    )

    # 2. Read every row and encrypt in Python, then write back to _enc columns.
    rows = bind.execute(
        text("SELECT id, access_token, refresh_token FROM google_credentials")
    ).fetchall()

    for row in rows:
        row_id = row[0]
        access_plaintext: str = row[1]
        refresh_plaintext: str | None = row[2]

        access_enc: bytes = encrypt_credentials({"t": access_plaintext})
        refresh_enc: bytes | None = (
            encrypt_credentials({"t": refresh_plaintext}) if refresh_plaintext is not None else None
        )

        bind.execute(
            text(
                "UPDATE google_credentials"
                " SET access_token_enc = :a, refresh_token_enc = :r"
                " WHERE id = :id"
            ),
            {"a": access_enc, "r": refresh_enc, "id": row_id},
        )

    # 3. Drop the old plaintext Text columns.
    op.drop_column("google_credentials", "access_token")
    op.drop_column("google_credentials", "refresh_token")

    # 4. Rename _enc columns to the original names.
    op.alter_column("google_credentials", "access_token_enc", new_column_name="access_token")
    op.alter_column("google_credentials", "refresh_token_enc", new_column_name="refresh_token")

    # 5. Enforce NOT NULL on access_token (was nullable during the migration window).
    op.alter_column("google_credentials", "access_token", nullable=False)


def downgrade() -> None:
    from artemis.integrations.crypto import decrypt_credentials

    bind = op.get_bind()

    # 1. Add temporary Text columns.
    op.add_column(
        "google_credentials",
        sa.Column("access_token_txt", sa.Text, nullable=True),
    )
    op.add_column(
        "google_credentials",
        sa.Column("refresh_token_txt", sa.Text, nullable=True),
    )

    # 2. Read every row and decrypt in Python, then write back to _txt columns.
    rows = bind.execute(
        text("SELECT id, access_token, refresh_token FROM google_credentials")
    ).fetchall()

    for row in rows:
        row_id = row[0]
        access_blob: bytes = bytes(row[1])
        refresh_blob: bytes | None = bytes(row[2]) if row[2] is not None else None

        access_plain: str = str(decrypt_credentials(access_blob)["t"])
        refresh_plain: str | None = (
            str(decrypt_credentials(refresh_blob)["t"]) if refresh_blob is not None else None
        )

        bind.execute(
            text(
                "UPDATE google_credentials"
                " SET access_token_txt = :a, refresh_token_txt = :r"
                " WHERE id = :id"
            ),
            {"a": access_plain, "r": refresh_plain, "id": row_id},
        )

    # 3. Drop the BYTEA columns.
    op.drop_column("google_credentials", "access_token")
    op.drop_column("google_credentials", "refresh_token")

    # 4. Rename _txt columns back to original names.
    op.alter_column("google_credentials", "access_token_txt", new_column_name="access_token")
    op.alter_column("google_credentials", "refresh_token_txt", new_column_name="refresh_token")

    # 5. Restore NOT NULL on access_token.
    op.alter_column("google_credentials", "access_token", nullable=False)
