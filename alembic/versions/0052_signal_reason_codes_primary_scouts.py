"""SP — signal reason-code scout and campaign mappings.

Revision ID: 0052
Revises: 0051
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from artemis.marketing.josh_spec import parse_spec

revision: str = "0052"
down_revision: str = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal_reason_codes",
        sa.Column("primary_scouts", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "signal_reason_codes",
        sa.Column("campaign_families", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )

    spec = parse_spec()
    families_by_code: dict[str, list[str]] = {}
    for mapping in spec.campaign_type_mappings:
        for code in mapping.reason_codes:
            families_by_code.setdefault(code, []).append(mapping.campaign_type)

    conn = op.get_bind()
    for row in spec.reason_codes:
        conn.execute(
            sa.text(
                """
                UPDATE signal_reason_codes
                   SET primary_scouts = :primary_scouts,
                       campaign_families = :campaign_families
                 WHERE code = :code
                """
            ).bindparams(
                sa.bindparam("primary_scouts", type_=sa.ARRAY(sa.Text())),
                sa.bindparam("campaign_families", type_=sa.ARRAY(sa.Text())),
            ),
            {
                "code": row.code,
                "primary_scouts": list(row.primary_scouts),
                "campaign_families": families_by_code.get(row.code, []),
            },
        )


def downgrade() -> None:
    op.drop_column("signal_reason_codes", "campaign_families")
    op.drop_column("signal_reason_codes", "primary_scouts")
