"""ATT&CK Coverage route schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.attack.coverage import CoverageStatus
from app.models.attack_assessment import AttackAssessmentStatus
from app.models.service import ServiceKind, ServiceStatus

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CatalogTactic(BaseModel):
    id: str
    shortname: str
    name: str
    description: str


class CatalogTechnique(BaseModel):
    id: str
    name: str
    tactics: list[str]
    parent_id: str | None
    is_sub_technique: bool


class CatalogCoverageDefinition(BaseModel):
    status: CoverageStatus
    short_label: str
    description: str


class CatalogResponse(BaseModel):
    tactics: list[CatalogTactic]
    techniques: list[CatalogTechnique]
    coverage_definitions: list[CatalogCoverageDefinition]
    total_techniques: int  # parent only
    total_sub_techniques: int


# ---------------------------------------------------------------------------
# Service + assessment
# ---------------------------------------------------------------------------


class AttackServiceCreateRequest(BaseModel):
    kind: ServiceKind = ServiceKind.ATTACK_COVERAGE
    title: str = Field(min_length=1, max_length=255)
    source_request_id: uuid.UUID | None = None


class AttackServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ServiceKind
    status: ServiceStatus
    title: str
    source_request_id: uuid.UUID | None
    opened_by: uuid.UUID
    released_at: datetime | None
    created_at: datetime


class AttackCoverageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    technique_code: str
    status: CoverageStatus | None
    notes: str | None
    evidence_artifact_id: uuid.UUID | None
    locked: bool = False
    # Work Order D2: tools providing detection / prevention / response.
    detection_tools: list[str] | None = None
    prevention_tools: list[str] | None = None
    response_tools: list[str] | None = None
    rationale: str | None = None
    answered_by: uuid.UUID | None
    answered_at: datetime | None


class AttackAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    version: int
    status: AttackAssessmentStatus
    approved_at: datetime | None
    approved_by: uuid.UUID | None
    documents_stale: bool = False
    coverage: list[AttackCoverageResponse]


class CoverageChange(BaseModel):
    """One field the mitre_map AI run changed on a technique (Work Order D2/C2)."""

    technique_code: str
    field: str
    old: Any = None
    new: Any = None


class AttackRunAiResponse(BaseModel):
    """Result of a mitre_map Run-AI: what changed + the refreshed coverage."""

    tools_available: int
    changed: list[CoverageChange]
    coverage: list[AttackCoverageResponse]
    # Under the fail-loud chunking rule (FIX A-3) a bad/missing batch aborts the
    # whole run, so a returned response always had every batch succeed and this
    # is always 0. Kept for response-shape stability.
    failed_batches: int = 0
    # Non-fatal advisories surfaced to the admin (FIX G-2). E.g. the client has
    # no APPROVED/RELEASED capability list, so the mapping can cite no tools.
    warnings: list[str] = Field(default_factory=list)


class AttackCoveragePatch(BaseModel):
    status: CoverageStatus | None = None
    notes: str | None = Field(default=None, max_length=8000)
    evidence_artifact_id: uuid.UUID | None = None
    # Work Order C2: lock/unlock this row against AI reruns.
    locked: bool | None = None
    # Work Order D2: D/P/R tool mappings + rationale.
    detection_tools: list[str] | None = None
    prevention_tools: list[str] | None = None
    response_tools: list[str] | None = None
    rationale: str | None = Field(default=None, max_length=8000)


# ---------------------------------------------------------------------------
# Heatmap analytics
# ---------------------------------------------------------------------------


class TacticHeatmapEntry(BaseModel):
    tactic_id: str
    tactic_name: str
    technique_count: int
    sub_technique_count: int
    covered: int
    partial: int
    gap: int
    not_applicable: int
    unscored: int
    coverage_pct: float


class AttackHeatmap(BaseModel):
    assessment_id: uuid.UUID
    version: int
    total_techniques: int
    total_sub_techniques: int
    scored_count: int
    unscored_count: int
    covered: int
    partial: int
    gap: int
    not_applicable: int
    coverage_pct: float
    by_tactic: list[TacticHeatmapEntry]
