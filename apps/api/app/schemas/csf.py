"""NIST CSF 2.0 route schemas (Phase 4 stage 2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.csf_assessment import CsfAssessmentStatus
from app.models.service import ServiceKind, ServiceStatus

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CatalogSubcategory(BaseModel):
    code: str
    function: str
    category: str
    name: str
    outcome: str
    # Minimum impact profile (LOW/MOD/HIGH) at which this outcome applies, so a
    # client questionnaire can filter to their profile.
    min_profile: str = "LOW"


class CatalogCategory(BaseModel):
    code: str
    function: str
    name: str
    purpose: str
    subcategories: list[CatalogSubcategory]


class CatalogFunction(BaseModel):
    code: str
    name: str
    purpose: str
    categories: list[CatalogCategory]


class CatalogTier(BaseModel):
    tier: int
    short_label: str
    description: str


class CatalogResponse(BaseModel):
    """Returned by GET /csf/catalog. Static reference data."""

    functions: list[CatalogFunction]
    tiers: list[CatalogTier]
    total_subcategories: int


# ---------------------------------------------------------------------------
# Interview questionnaire (rich prompts loaded into the `questions` table)
# ---------------------------------------------------------------------------


class InterviewQuestion(BaseModel):
    """One interview prompt extracted from the Kentro Step 1.x .docx files."""

    external_id: str
    section_name: str
    order_index: int
    stem: str
    cues: list[str]
    # CSF 2.0 subcategory ids the prompt informs, so the workspace can show it
    # inline on those subcategory cards.
    csf_subcategories: list[str]


class CsfQuestionnaireResponse(BaseModel):
    """Tier-specific interview prompts for a CSF service.

    Resolved from the service's impact profile (LOW/MOD/HIGH -> tier),
    defaulting to the HIGH questionnaire when no profile is set. Read-only.
    """

    framework_key: str
    profile: str | None = None
    questions: list[InterviewQuestion]


# ---------------------------------------------------------------------------
# Assessment + answers
# ---------------------------------------------------------------------------


class CsfServiceCreateRequest(BaseModel):
    kind: ServiceKind = ServiceKind.NIST_CSF
    title: str = Field(min_length=1, max_length=255)
    source_request_id: uuid.UUID | None = None


class CsfServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ServiceKind
    status: ServiceStatus
    title: str
    source_request_id: uuid.UUID | None
    opened_by: uuid.UUID
    released_at: datetime | None
    created_at: datetime


class CsfAnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assessment_id: uuid.UUID
    subcategory_code: str
    maturity_tier: int | None
    notes: str | None
    evidence_artifact_id: uuid.UUID | None
    locked: bool = False
    answered_by: uuid.UUID | None
    answered_at: datetime | None


class CsfAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    version: int
    status: CsfAssessmentStatus
    approved_at: datetime | None
    approved_by: uuid.UUID | None
    documents_stale: bool = False
    answers: list[CsfAnswerResponse]
    # Target tier the client picked at intake (2-4), or null if not set.
    client_target_tier: int | None = None
    # Impact profile the client picked at intake (LOW/MOD/HIGH), or null. Drives
    # which subcategories the client self-assessment shows.
    client_profile: str | None = None


class CsfAnswerPatch(BaseModel):
    """Partial-update body for a single subcategory answer.

    Sending `maturity_tier: null` clears the score (returns it to
    "unscored" for the unanswered-count math).
    """

    maturity_tier: int | None = Field(default=None, ge=1, le=4)
    notes: str | None = Field(default=None, max_length=8000)
    evidence_artifact_id: uuid.UUID | None = None
    # Work Order C2: lock/unlock this row against AI reruns (admin only).
    locked: bool | None = None


class CsfSelfAssessmentSubmit(BaseModel):
    """Client submits their self-assessment for admin review.

    `target_tier` lets the client confirm/adjust the maturity goal the gap
    engine measures against; persisted on the source request.
    """

    target_tier: int | None = Field(default=None, ge=1, le=4)


# ---------------------------------------------------------------------------
# Scoring summary
# ---------------------------------------------------------------------------


class FunctionScore(BaseModel):
    function: str
    function_name: str
    subcategory_count: int
    answered_count: int
    average_tier: float | None
    coverage_pct: float  # answered / total * 100
    weakest_subcategory_codes: list[str]


class CsfScoreSummary(BaseModel):
    assessment_id: uuid.UUID
    version: int
    total_subcategories: int
    answered_subcategories: int
    coverage_pct: float
    average_tier: float | None
    overall_maturity_label: str
    by_function: list[FunctionScore]


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


class GapItem(BaseModel):
    code: str
    function: str
    function_name: str
    category: str
    name: str
    outcome: str
    current_tier: int
    target_tier: int
    gap_size: int
    priority_score: float
    notes: str | None


class GapAnalysisResponse(BaseModel):
    assessment_id: uuid.UUID
    version: int
    target_tier: int
    target_label: str
    total_gap_count: int
    unscored_count: int
    gap_count_by_function: dict[str, int]
    gaps: list[GapItem]


# ---------------------------------------------------------------------------
# Full-Playbook tiered Working Profile (Work Order D4)
# ---------------------------------------------------------------------------


class CsfDimensionScoreResponse(BaseModel):
    id: uuid.UUID
    tier: str
    subcategory_code: str
    governance: int
    policy: int
    implementation: int
    monitoring: int
    improvement: int
    in_scope: bool
    rationale: str | None
    what_we_found: str | None
    has_evidence: bool
    target_level: int | None
    locked: bool
    # Code-computed (app/csf/playbook.py).
    total: int
    level: int
    evidence_capped: bool


class CsfProfileResponse(BaseModel):
    tier: str
    rows: list[CsfDimensionScoreResponse]


class CsfDimensionScorePatch(BaseModel):
    governance: int | None = Field(default=None, ge=0, le=2)
    policy: int | None = Field(default=None, ge=0, le=2)
    implementation: int | None = Field(default=None, ge=0, le=2)
    monitoring: int | None = Field(default=None, ge=0, le=2)
    improvement: int | None = Field(default=None, ge=0, le=2)
    in_scope: bool | None = None
    rationale: str | None = Field(default=None, max_length=8000)
    what_we_found: str | None = Field(default=None, max_length=8000)
    has_evidence: bool | None = None
    target_level: int | None = Field(default=None, ge=1, le=5)
    locked: bool | None = None


class ProfileSeedRequest(BaseModel):
    tiers: list[str] = ["high", "moderate", "low"]


class EnterpriseSubcategory(BaseModel):
    subcategory_code: str
    name: str
    function: str
    tier_levels: dict[str, int]
    enterprise_level: int
    rollup_rule: int
    target_level: int | None
    gap: bool
    priority: str | None


class EnterpriseProfileResponse(BaseModel):
    tiers_in_use: list[str]
    subcategories: list[EnterpriseSubcategory]


class CsfDimensionChange(BaseModel):
    """One field the csf_score AI run changed on a tiered row (Work Order D4/C2)."""

    tier: str
    subcategory_code: str
    field: str
    old: Any = None
    new: Any = None


class CsfRunAiResponse(BaseModel):
    """Result of a csf_score Run-AI: what changed + the refreshed rows."""

    changed: list[CsfDimensionChange]
    rows: list[CsfDimensionScoreResponse]
    # FIX E-5: "fixture" = deterministic simulated results; "live" = a real
    # provider call. The web UI badges simulated output on "fixture".
    mode: str = "fixture"


class ExportedArtifact(BaseModel):
    kind: str  # xlsx | exec_pdf | exec_docx | full_pdf | full_docx
    label: str
    artifact_id: uuid.UUID
    filename: str


class CsfPlaybookExportResponse(BaseModel):
    """The stored CSF Playbook artifacts — XLSX workbook + executive briefing +
    full playbook, each as a downloadable file (Work Order D4)."""

    artifacts: list[ExportedArtifact]
