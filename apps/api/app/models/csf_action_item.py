"""CSF action plan / POA&M rows (Playbook Step 10, FIX H-8).

The CSF deliverable promises an *action plan* — gaps with an owner and a date
by which they will be closed (a POA&M-style Plan of Action & Milestones). The
gap list is computed on the fly from the dimension scores and has no persistent
row to hang those fields on, so consultants kept the real action plan in a side
spreadsheet — exactly the out-of-band artifact this platform exists to remove.

One row per remediation commitment: the subcategory it closes, who owns it, the
date it is due, a milestone note, and a status. Deliberately simple (per the
plan): no notifications, no client visibility — just structured fields that
export into the Playbook.

Per Master Spec §11.1 `client_id` is denormalized on every business row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import (
    Date,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class CsfActionItemStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class CsfActionItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "csf_action_items"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("csf_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # The NIST subcategory this action closes (e.g. "GV.OC-01"), captured from
    # the gap row the consultant clicked. Validated at the API edge against
    # app.csf.catalog.all_codes() so a typo can't smuggle in a bogus row.
    subcategory_code: Mapped[str] = mapped_column(String(16), nullable=False)

    # Free-text owner (a name / role / team). Deliberately not a user FK: the
    # accountable owner is frequently on the client side and has no login here.
    owner: Mapped[str | None] = mapped_column(String(255))
    due_date: Mapped[date | None] = mapped_column(Date)
    # The concrete milestone / definition-of-done for the commitment.
    milestone: Mapped[str | None] = mapped_column(Text)

    status: Mapped[CsfActionItemStatus] = mapped_column(
        SAEnum(
            CsfActionItemStatus,
            name="csf_action_item_status",
            native_enum=False,
            length=16,
        ),
        default=CsfActionItemStatus.OPEN,
        nullable=False,
    )
