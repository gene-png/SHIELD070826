"""Risk Register schemas (Work Order E)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RiskSource(BaseModel):
    """One source assessment a Risk Register version was synthesized from."""

    kind: str  # attack | csf | zt
    version: int | None = None
    status: str | None = None
    approved: bool = False


class RiskGateStatus(BaseModel):
    """Whether the Risk Register can be generated for a client.

    FIX F-3: threshold is now APPROVAL, not mere existence. An APPROVED (or
    RELEASED) MITRE ATT&CK coverage mapping AND at least one APPROVED (or
    RELEASED) CSF or Zero Trust assessment. Existence alone (has_*) is exposed
    so the dashboard can explain "started but not approved".
    """

    unlocked: bool
    has_attack: bool
    has_csf: bool
    has_zt: bool
    attack_approved: bool = False
    csf_approved: bool = False
    zt_approved: bool = False
    missing: list[str]
    sources: list[RiskSource] = []


class RiskEntryPatch(BaseModel):
    """Admin edits to one Risk Register entry.

    Deliberately omits ``tier``: the tier is ALWAYS code-derived from the edited
    likelihood/impact (NIST 800-30 5x5), never accepted from the client. A
    ``tier`` key in the request body is ignored (extra fields dropped).
    """

    title: str | None = None
    description: str | None = None
    likelihood: str | None = None
    impact: str | None = None
    compensating_controls: str | None = None
    recommended_action: str | None = None
    rationale: str | None = None
    locked: bool | None = None


class RiskEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    axis: str | None
    source: str | None
    source_id: str | None
    linked_techniques: list[str] | None
    linked_controls: list[str] | None
    likelihood: str | None
    impact: str | None
    tier: str | None
    compensating_controls: str | None
    residual_risk: str | None
    recommended_action: str | None
    rationale: str | None
    origin: str
    trust: str | None
    locked: bool = False


class RiskRegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    version: int
    generated_by: uuid.UUID | None
    finalized_at: datetime | None
    approved_at: datetime | None = None
    approved_by: uuid.UUID | None = None
    created_at: datetime
    sources: list[RiskSource] = []
    xlsx_artifact_id: uuid.UUID | None = None
    pdf_artifact_id: uuid.UUID | None = None
    docx_artifact_id: uuid.UUID | None = None
    xlsx_filename: str | None = None
    pdf_filename: str | None = None
    docx_filename: str | None = None
    entries: list[RiskEntryResponse]
    # Dashboard rollups (code-computed).
    tier_counts: dict[str, int] = {}
    axis_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
