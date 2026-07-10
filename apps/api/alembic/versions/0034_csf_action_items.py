"""csf_action_items - CSF action plan / POA&M (Playbook Step 10, FIX H-8)

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-09 00:00:00

The CSF deliverable promises an action plan with owners and dates (a POA&M).
No such row ever existed - gaps were computed on the fly with nowhere to hang
an owner or a due date, so consultants kept the real plan in a side
spreadsheet. This adds the persistent row: one commitment per subcategory, with
owner / due date / milestone / status, denormalizing client_id per §11.1.

Additive only, reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid(name: str, *, pk: bool = False, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite"),
        primary_key=pk,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "csf_action_items",
        _uuid("id", pk=True, nullable=False),
        _uuid("assessment_id", nullable=False),
        _uuid("client_id", nullable=False),
        sa.Column("subcategory_code", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("milestone", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_progress",
                "done",
                name="csf_action_item_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["csf_assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["client.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_csf_action_items_assessment_id", "csf_action_items", ["assessment_id"])
    op.create_index("ix_csf_action_items_client_id", "csf_action_items", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_csf_action_items_client_id", table_name="csf_action_items")
    op.drop_index("ix_csf_action_items_assessment_id", table_name="csf_action_items")
    op.drop_table("csf_action_items")
