"""client.redaction_preview_ack_* - review the egress before the first live run (FIX H-6)

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-09 00:00:00

The Master Spec (§12) promises the consultant can see what leaves the platform.
Redaction happened invisibly inside ``LLMClient.invoke``, so the first moment an
admin could inspect the outgoing payload was never.

The preview endpoints (``?preview=true``) answer "what would egress?". These
columns record that somebody actually looked, once per tenant, before the first
LIVE call. Not per run -- the point is a human reviewed redaction quality on
real client data, not that they click through a modal every time.

Additive, nullable, reversible. NULL = never acknowledged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("client") as batch:
        batch.add_column(sa.Column("redaction_preview_ack_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("redaction_preview_ack_by", sa.Uuid()))
        batch.create_foreign_key(
            "fk_client_redaction_preview_ack_by",
            "users",
            ["redaction_preview_ack_by"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("client") as batch:
        batch.drop_constraint("fk_client_redaction_preview_ack_by", type_="foreignkey")
        batch.drop_column("redaction_preview_ack_by")
        batch.drop_column("redaction_preview_ack_at")
