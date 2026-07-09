"""llm_calls.client_id - attribute AI spend to a tenant (FIX H-5)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-09 00:00:00

Additive only: a nullable, indexed FK to client so per-tenant AI usage and cost
can be reported (GET /admin/ai-usage). `service_id` alone could not answer
"how much did this client spend", and cross-tenant jobs (e.g. the Risk Register)
carry no service_id at all. No backfill (existing rows correctly read as
unattributed); reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.add_column(sa.Column("client_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_llm_calls_client_id",
            "client",
            ["client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_llm_calls_client_id", ["client_id"])


def downgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_index("ix_llm_calls_client_id")
        batch.drop_constraint("fk_llm_calls_client_id", type_="foreignkey")
        batch.drop_column("client_id")
