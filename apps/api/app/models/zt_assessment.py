"""Zero Trust assessment models (Phase 5 stage 1).

One assessment per (Service, framework, version). Service.kind drives
the framework choice at create time: kind=zero_trust_cisa -> CISA ZTMM
2.0; kind=zero_trust_dod -> DoD ZTRA. The `framework` column is
denormalized so the scoring engine + deliverable renderer can pick
their stage labels without re-querying the parent service.

Per Master Spec §11.1, `client_id` is denormalized on every business
row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin

# Portable JSON object (native JSONB on Postgres, generic JSON on SQLite tests).
_JSON_DICT = JSON().with_variant(JSONB, "postgresql")


class ZtAssessmentStatus(enum.StrEnum):
    DRAFT = "draft"
    # Client finished their self-assessment; awaiting admin review. Admins can
    # still edit in this state; clients cannot.
    SUBMITTED = "submitted"
    APPROVED = "approved"
    RELEASED = "released"


class ZtFramework(enum.StrEnum):
    CISA_ZTMM_2_0 = "cisa_ztmm_2_0"
    DOD_ZTRA = "dod_ztra"


class ZtAssessment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "zt_assessments"
    __table_args__ = (
        UniqueConstraint("service_id", "version", name="uq_zt_assessments_service_version"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    framework: Mapped[ZtFramework] = mapped_column(
        SAEnum(ZtFramework, name="zt_framework", native_enum=False, length=32),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Work Order C3: an AI run sets this true; finalize clears it.
    documents_stale: Mapped[bool] = mapped_column(default=False, nullable=False)
    # FIX E-4: ZT Run AI returns pillar narratives, an executive summary and a
    # roadmap summary that were shown once and never saved -- a reload lost
    # them, quietly encouraging repeat AI runs just to see them again. Persist
    # them (nullable, additive; migration 0032) so the work survives and can
    # feed the deliverable's executive sections.
    narratives: Mapped[dict | None] = mapped_column(_JSON_DICT)
    status: Mapped[ZtAssessmentStatus] = mapped_column(
        SAEnum(
            ZtAssessmentStatus,
            name="zt_assessment_status",
            native_enum=False,
            length=16,
        ),
        default=ZtAssessmentStatus.DRAFT,
        nullable=False,
    )

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ZtAnswer(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "zt_answers"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "capability_code",
            name="uq_zt_answers_assessment_capability",
        ),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("zt_assessments.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Framework-prefixed code (e.g. "CISA.ID.01" or "DOD.USR.01").
    # Validated against the in-memory catalog at the route boundary.
    capability_code: Mapped[str] = mapped_column(String(32), nullable=False)

    # 1..level_count(framework): CISA 1-4, DoD 1-3 (Work Order A4).
    # NULL = unscored. Validated per framework at the route boundary.
    maturity_stage: Mapped[int | None] = mapped_column(SmallInteger)
    # Work Order D3: per-capability target stage (gap = current < target).
    # NULL falls back to the assessment/engagement default target.
    target_stage: Mapped[int | None] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(Text)

    # Work Order C2: a locked row is never changed by a Run-AI rerun.
    locked: Mapped[bool] = mapped_column(default=False, nullable=False)
    evidence_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )

    answered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
