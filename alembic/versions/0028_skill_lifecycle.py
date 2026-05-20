"""skill_lifecycle — add status and category to skills.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-20

Ports the Node Skills lifecycle substrate: proposed/approved/archived status
and category grouping. The agent_skills join table already exists from 0023.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("category", sa.Text(), nullable=True))
    op.add_column(
        "skills",
        sa.Column("status", sa.Text(), nullable=False, server_default="approved"),
    )
    op.create_check_constraint(
        "ck_skills_status",
        "skills",
        "status IN ('proposed', 'approved', 'archived')",
    )
    op.create_index("idx_skills_status", "skills", ["status"])
    op.create_index("idx_skills_category", "skills", ["category"])


def downgrade() -> None:
    op.drop_index("idx_skills_category", table_name="skills")
    op.drop_index("idx_skills_status", table_name="skills")
    op.drop_constraint("ck_skills_status", "skills", type_="check")
    op.drop_column("skills", "status")
    op.drop_column("skills", "category")
