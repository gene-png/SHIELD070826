"""risk_registers unique (client_id, version) - block duplicate versions (FIX E-3)

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-09 00:00:00

Additive + reversible: a UNIQUE constraint on (client_id, version) so a
concurrent double-click on POST /risk/clients/{cid}/register/generate cannot
persist two RiskRegister rows sharing a version (which would corrupt the
supersession chain). No backfill -- existing data already has one row per
(client, version). Mirrors uq_zt_assessments_service_version.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table so SQLite (which cannot ALTER TABLE ADD CONSTRAINT)
    # rebuilds the table; a no-op recreate on PostgreSQL.
    with op.batch_alter_table("risk_registers") as batch:
        batch.create_unique_constraint("uq_risk_registers_client_version", ["client_id", "version"])


def downgrade() -> None:
    with op.batch_alter_table("risk_registers") as batch:
        batch.drop_constraint("uq_risk_registers_client_version", type_="unique")
