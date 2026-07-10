"""Client - a tenant (organization) served by this deployment.

Originally Master Spec §11 was single-tenant (one client row per deployment).
Migration 0013 turned this into a multi-tenant model: many clients per
deployment, every business row tagged with its `client_id`. Platform
admin/reviewer users have `User.client_id = NULL` and can switch between
clients via the X-Client-Id request header; client-role users are pinned
to their `User.client_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class Client(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "client"

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # FIX H-6: the first LIVE AI run for a tenant requires a recorded
    # acknowledgment that an admin previewed the redacted payload. Once per
    # client, never per run -- the point is that somebody looked at real client
    # data before it egressed, which is both the Master Spec's promise (§12) and
    # the first question a FedRAMP assessor asks. NULL = never acknowledged.
    redaction_preview_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redaction_preview_ack_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    dba_name: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(512))
    size_band: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(128))

    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(64))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(64))

    primary_poc_user_id: Mapped[uuid.UUID | None] = mapped_column()
    prompting_context: Mapped[str | None] = mapped_column(Text)

    # service_interests is a list-of-codes set at intake time. Stored as a
    # JSON array on SQLite for tests; native ARRAY(text) on Postgres.
    service_interests: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(32)).with_variant(JSONB, "sqlite")
    )

    # Master Spec §11: set when the intake wizard is submitted. Used by the
    # admin queue to surface new leads.
    intake_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
