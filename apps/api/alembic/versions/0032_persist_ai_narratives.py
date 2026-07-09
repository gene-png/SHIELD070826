"""Persist the AI narratives the platform paid for and threw away (FIX E-4)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-09 00:00:00

ZT Run AI returned pillar narratives, an executive summary and a roadmap
summary that were rendered once and never stored -- a page reload lost them,
which quietly encouraged consultants to re-run the AI just to see them again.
The `mitre_map` prompt likewise asks for `executive_summary` and
`top_blind_spots`, and the ATT&CK route consumed only `techniques`, discarding
the rest after paying for it in output tokens.

Both columns are nullable JSON, additive only, no backfill, reversible.
JSONB on PostgreSQL, generic JSON on the SQLite test database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("zt_assessments") as batch:
        batch.add_column(sa.Column("narratives", _JSON, nullable=True))
    with op.batch_alter_table("attack_assessments") as batch:
        batch.add_column(sa.Column("ai_summaries", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("attack_assessments") as batch:
        batch.drop_column("ai_summaries")
    with op.batch_alter_table("zt_assessments") as batch:
        batch.drop_column("narratives")
