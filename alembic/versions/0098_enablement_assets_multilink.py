"""Widen enablement_assets for multi-link assets + source provenance + searchable text.

Revision ID: 0098
Revises: 0097
Create Date: 2026-06-18

The original enablement_assets table (0095) modelled a simple one-link asset
(ENABLEMENT_DB sheet: title/summary/tags/link/transcript). The real enablement
data (4 curated Google Sheets + an evergreen "Indexed Docs" folder, mapped in
``briefs/enablement-sheet-configs.md``) is richer: a single asset can carry
several *differently-scoped* links — a customer-facing web link, a PDF, an
internal-only editable handout, speaker-notes, a webinar — and Kai must never
confuse them. This adds:

- ``links``           JSONB list of structured link objects, each:
                      {role, label, url, visibility ("customer"|"internal"),
                       on_request (bool), make_copy (bool)}.
                      This is the surfacing-critical field: Kai's prompt rules
                      operate on the explicit visibility/on_request/make_copy
                      flags rather than guessing from column names.
- ``searchable_text`` TEXT — the indexable body (script-doc text, slide text,
                      full Google-Doc text, walkthrough notes) folded into the
                      embedding + keyword search. Separate from the human
                      ``summary``.
- ``source_sheet``    TEXT — provenance, e.g. "training_decks_sy26-27".
- ``source_row``      TEXT — row number or stable in-sheet id (debug/trace;
                      the idempotency anchor remains ``drive_file_id``, which the
                      Apps Script sets to a stable composite key per asset).
- ``requires_copy``   BOOLEAN — view-only decks/handouts the CSM must copy first.

All columns are nullable / defaulted so existing rows are untouched. The
idempotency key (``drive_file_id`` unique constraint) is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enablement_assets",
        sa.Column("links", JSONB(), nullable=True),
    )
    op.add_column(
        "enablement_assets",
        sa.Column("searchable_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "enablement_assets",
        sa.Column("source_sheet", sa.Text(), nullable=True),
    )
    op.add_column(
        "enablement_assets",
        sa.Column("source_row", sa.Text(), nullable=True),
    )
    op.add_column(
        "enablement_assets",
        sa.Column(
            "requires_copy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_enablement_assets_source_sheet",
        "enablement_assets",
        ["source_sheet"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_enablement_assets_source_sheet", table_name="enablement_assets")
    op.drop_column("enablement_assets", "requires_copy")
    op.drop_column("enablement_assets", "source_row")
    op.drop_column("enablement_assets", "source_sheet")
    op.drop_column("enablement_assets", "searchable_text")
    op.drop_column("enablement_assets", "links")
