"""Risk Register governance: entry lock/soft-delete + register approve + sources (FIX F-3)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-09 00:00:00

Brings the Risk Register up to the platform's own governance pattern. Additive
only, reversible, no backfill:

  risk_entries
    * locked      NOT NULL default false -- C2 lock: carried forward verbatim on
                  regenerate, never redrafted by the AI (mirrors csf_answers /
                  zt_answers / attack_coverage.locked).
    * deleted_at  nullable timestamp -- soft delete; a deleted entry drops out of
                  the register view and every export but the row is retained.

  risk_registers
    * approved_at nullable timestamp -- the July 9 Approve step that locks the
                  version; export is refused until it is set.
    * approved_by nullable FK users -- who approved.
    * sources     nullable JSON (JSONB on PostgreSQL) -- snapshot of the source
                  assessments (kind, version, status) synthesized into this
                  version, for dashboard/export provenance.

batch_alter_table so the SQLite test database can rewrite the tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB, "postgresql")
_UUID_TYPE = postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("risk_entries") as batch:
        batch.add_column(
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("risk_registers") as batch:
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "approved_by",
                _UUID_TYPE,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("sources", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("risk_registers") as batch:
        batch.drop_column("sources")
        batch.drop_column("approved_by")
        batch.drop_column("approved_at")
    with op.batch_alter_table("risk_entries") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("locked")
