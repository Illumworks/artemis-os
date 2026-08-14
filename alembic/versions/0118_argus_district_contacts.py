"""CONTACTS-1 -- district_contacts gains an Argus-sourced provenance path.

Revision ID: 0118
Revises: 0117
Create Date: 2026-08-14

Context: Argus's decision_makers research (14 real observations as of
2026-08-14, e.g. "Dr. Dyann Mack, newly appointed superintendent" for Harford
County) names real people in narrative prose only. Nothing can answer "who
runs Harford County" and no single person can be removed on request without
editing an observation -- which CLAUDE.md rule 3 forbids (drawers/observations
are never deleted, only superseded).

Resolution (see briefs/contacts-1-people-become-records.md): PII lives in
``district_contacts``, which stays genuinely deletable; observations are
never touched by this feature at all -- they keep exactly the prose they
always had. ``district_contacts`` already exists (SEND2-A, migration 0060)
with the right shape for a HUMAN-entered send-target (name, title, email,
phone, source, external_id, active), but two things in that shape assumed
every row would always have a deliverable email address, which is not true
for a person Argus merely learned the NAME and TITLE of:

1. ``email`` was ``NOT NULL``. The brief is explicit: never synthesize an
   email from a naming convention (that mistake already cost a wrong address
   this week -- josh.mukai@ vs joshua.mukai@). Made nullable.
2. ``source`` was constrained to ``'manual'`` or ``'salesforce'`` -- both
   channels that always carry a human-verified email. Added ``'argus'`` so
   the provenance is honestly labelled rather than misfiled under either
   existing value.

What this migration does
-------------------------
1. ``email`` -> nullable. The existing partial unique index
   ``uq_district_contacts_district_email`` (``WHERE active``, keyed on
   ``lower(email)``) is unaffected: Postgres unique indexes never treat two
   NULLs as a collision, so any number of email-less active rows can coexist
   per district.
2. ``ck_district_contacts_source`` widened to allow ``'argus'``.
3. ``source_observation_id`` (nullable BIGINT, FK -> memory_observations.id,
   no ON DELETE action) added -- the provenance pointer: "this row's name
   and title came from reading this specific observation." Deliberately a
   plain column, not a memory_evidence row: memory_evidence links evidence
   TOWARD an observation, and the direction needed here is the opposite (a
   district_contacts row pointing AT the observation it was read from). It
   is nullable because manual/salesforce rows have no observation to point
   at.  There is no ON DELETE behaviour to configure: memory_observations
   rows are never deleted (lossless rule), so this FK is never asked to
   handle a missing target.
4. A partial unique index, ``uq_district_contacts_argus_person``, on
   ``(district_id, lower(name)) WHERE source = 'argus' AND active`` --
   belt-and-suspenders for the brief's idempotency requirement ("re-running
   extraction does not duplicate a contact"). The application-level upsert
   in ``artemis.marketing.contacts.create_argus_contact`` already checks for
   an existing row before inserting; this index makes the same guarantee
   hold even under a concurrent double-run.

Downgrade note: restoring ``email NOT NULL`` will fail if any Argus rows
with a NULL email exist by then (expected -- most of them will). This is a
deliberate, honest failure rather than silently deleting data to make the
downgrade succeed; if you need to downgrade past this revision, remove or
backfill those rows first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0118"
down_revision: str | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("district_contacts", "email", nullable=True)

    op.drop_constraint("ck_district_contacts_source", "district_contacts", type_="check")
    op.create_check_constraint(
        "ck_district_contacts_source",
        "district_contacts",
        "source IN ('manual','salesforce','argus')",
    )

    op.add_column(
        "district_contacts",
        sa.Column("source_observation_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_district_contacts_source_observation",
        "district_contacts",
        "memory_observations",
        ["source_observation_id"],
        ["id"],
    )

    op.create_index(
        "uq_district_contacts_argus_person",
        "district_contacts",
        ["district_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("source = 'argus' AND active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_district_contacts_argus_person",
        table_name="district_contacts",
    )
    op.drop_constraint(
        "fk_district_contacts_source_observation",
        "district_contacts",
        type_="foreignkey",
    )
    op.drop_column("district_contacts", "source_observation_id")

    op.drop_constraint("ck_district_contacts_source", "district_contacts", type_="check")
    op.create_check_constraint(
        "ck_district_contacts_source",
        "district_contacts",
        "source IN ('manual','salesforce')",
    )

    op.alter_column("district_contacts", "email", nullable=False)
