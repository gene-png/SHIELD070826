"""ATT&CK Coverage assessment models (Phase 5 stage 5).

One assessment per (Service, version) for service kind=attack_coverage.
Each technique gets a row in `attack_coverage` carrying the coverage
status (covered / partial / gap / not_applicable) plus optional notes
and an evidence pointer.
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

# Portable JSON list (native JSONB on Postgres, generic JSON on SQLite tests).
_JSON_LIST = JSON().with_variant(JSONB, "postgresql")
# Same, for an object rather than a list (FIX E-4).
_JSON_DICT = JSON().with_variant(JSONB, "postgresql")


class AttackAssessmentStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RELEASED = "released"


class AttackAssessment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "attack_assessments"
    __table_args__ = (
        UniqueConstraint("service_id", "version", name="uq_attack_assessments_service_version"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Work Order C3: an AI run sets this true; finalize clears it.
    documents_stale: Mapped[bool] = mapped_column(default=False, nullable=False)
    # FIX E-4: the mitre_map prompt asks for `executive_summary` and
    # `top_blind_spots`, and the route used to consume only `techniques` -- so
    # the narrative was generated, paid for in output tokens, and thrown away
    # on every run. Persist it (nullable, additive; migration 0032) so it
    # survives a reload and can feed the deliverable's executive section.
    ai_summaries: Mapped[dict | None] = mapped_column(_JSON_DICT)
    status: Mapped[AttackAssessmentStatus] = mapped_column(
        SAEnum(
            AttackAssessmentStatus,
            name="attack_assessment_status",
            native_enum=False,
            length=16,
        ),
        default=AttackAssessmentStatus.DRAFT,
        nullable=False,
    )

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class AttackCoverage(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "attack_coverage"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "technique_code",
            name="uq_attack_coverage_assessment_technique",
        ),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attack_assessments.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Technique code from app.attack.catalog (e.g. "T1003" or "T1003.001").
    technique_code: Mapped[str] = mapped_column(String(32), nullable=False)

    # Free-form short enum value (CoverageStatus). NULL = unscored.
    status: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    evidence_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )

    # Work Order C2: a locked row is never changed by a Run-AI rerun.
    locked: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Work Order D2: which listed tools provide detection / prevention /
    # response for this technique. Tool names drawn from the client's
    # capability list; AI suggests, admin curates. NULL = not yet mapped.
    detection_tools: Mapped[list | None] = mapped_column(_JSON_LIST)
    prevention_tools: Mapped[list | None] = mapped_column(_JSON_LIST)
    response_tools: Mapped[list | None] = mapped_column(_JSON_LIST)
    rationale: Mapped[str | None] = mapped_column(Text)

    answered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
