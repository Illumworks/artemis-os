"""Allow multiple Google credentials per user by purpose.

Revision ID: 0084
Revises: 0083
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "google_credentials",
        sa.Column(
            "purpose",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'personal'"),
        ),
    )
    op.execute("UPDATE google_credentials SET purpose = 'personal' WHERE purpose IS NULL")
    op.drop_constraint("uq_google_credentials_user_id", "google_credentials", type_="unique")
    op.create_unique_constraint(
        "uq_google_credentials_user_purpose",
        "google_credentials",
        ["user_id", "purpose"],
    )
    op.alter_column("google_credentials", "purpose", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_google_credentials_user_purpose", "google_credentials", type_="unique")
    op.create_unique_constraint("uq_google_credentials_user_id", "google_credentials", ["user_id"])
    op.drop_column("google_credentials", "purpose")
