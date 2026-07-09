"""csf_dimension_scores.scored_at - proof a row was scored (FIX B-3)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-08 00:00:00

Additive only: a nullable timestamp set whenever a human patch or a Run-AI pass
writes a dimension-score row. Seeding leaves it NULL. The Playbook export gate
blocks while any in-scope row is still NULL, so an all-zero seeded row can never
be exported as a bogus "Level 1". No backfill (existing seeded rows correctly
read as unscored); no data migration; reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("csf_dimension_scores") as batch:
        batch.add_column(sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("csf_dimension_scores") as batch:
        batch.drop_column("scored_at")
