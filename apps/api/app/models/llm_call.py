"""LLM call - one row per AI invocation.

Master Spec §11:
  llm_calls    id, service_id, purpose, prompt_version, model, mode
               (real/fixture), input_tokens, output_tokens, duration_ms,
               status (queued/running/completed/failed), error_message,
               response_artifact_id, requested_by, requested_at,
               completed_at.

The row is written BEFORE the provider call (status=running) so a crash
during the call still leaves a record. `redacted_counts` is JSON: counts
only (`{"email": 2, "phone": 1, ...}`), never payload content.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin, utcnow


class LLMCallStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LLMCallMode(enum.StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class LLMCall(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "llm_calls"

    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL")
    )
    # FIX H-5: attribute AI spend to a tenant so per-client usage + cost can be
    # reported. Nullable + indexed, additive (migration 0030); no backfill. When
    # a caller doesn't pass client_id explicitly, invoke derives it from
    # service_id so every job type still lands a tenant.
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client.id", ondelete="SET NULL"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[LLMCallMode] = mapped_column(
        SAEnum(LLMCallMode, name="llm_call_mode", native_enum=False, length=16),
        nullable=False,
    )

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[LLMCallStatus] = mapped_column(
        SAEnum(LLMCallStatus, name="llm_call_status", native_enum=False, length=16),
        default=LLMCallStatus.RUNNING,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    response_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )

    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Counts of items removed by the redactor for this call's input.
    # Counts only - never payload content (Master Spec §12.1).
    redacted_counts: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSONB, "postgresql"))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
