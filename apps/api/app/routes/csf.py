"""NIST CSF 2.0 service routes (Phase 4 stage 2).

Endpoint surface:
  POST   /csf/services
         Open a CSF assessment service. Admin-only.
  GET    /csf/catalog
         Static reference data. Any signed-in role.
  POST   /csf/services/{service_id}/assessments
         Create a draft assessment for the service. Admin-only.
  GET    /csf/services/{service_id}/assessments/latest
         Most recent assessment (admin sees draft; client sees released).
  PATCH  /csf/answers/{answer_id}
         Inline update of one subcategory answer. Admin-only.
  POST   /csf/assessments/{assessment_id}/approve
         Flip status -> approved. Admin-only.
  GET    /csf/services/{service_id}/score
         Roll-up score for the latest assessment. Admin-only.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.diff import diff_keyed_rows
from app.ai.engine import run_job
from app.ai.llm import LLMClient, LLMConfigurationError, LLMTimeoutError
from app.audit import audit
from app.csf import playbook_export as csf_playbook_export
from app.csf.catalog import (
    CATEGORIES,
    FUNCTIONS,
    SUBCATEGORIES,
    all_codes,
    min_profile_for_category,
    subcategory_by_code,
)
from app.csf.exporters import build_context as build_csf_context
from app.csf.exporters import render_docx as render_csf_docx
from app.csf.exporters import render_pdf as render_csf_pdf
from app.csf.exporters import render_xlsx as render_csf_xlsx
from app.csf.gap import DEFAULT_TARGET_TIER
from app.csf.gap import analyze as analyze_gaps
from app.csf.maturity import TIER_DEFINITIONS
from app.csf.playbook import (
    DimensionScores,
    Tier,
    gap_priority,
    is_gap,
    score_tier,
    weighted_floor_rollup,
)
from app.csf.scoring import compute as compute_score
from app.db.locks import RunInProgressError, run_lock
from app.db.session import get_db
from app.dependencies import current_client, current_user, require_role
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.client import Client
from app.models.csf_assessment import (
    CsfAnswer,
    CsfAssessment,
    CsfAssessmentStatus,
)
from app.models.csf_profile import CsfDimensionScore
from app.models.deliverable import Deliverable
from app.models.questionnaire import Question
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.routes.artifacts import _storage_dep
from app.schemas.csf import (
    CatalogCategory,
    CatalogFunction,
    CatalogResponse,
    CatalogSubcategory,
    CatalogTier,
    CsfAnswerPatch,
    CsfAnswerResponse,
    CsfAssessmentResponse,
    CsfDimensionChange,
    CsfDimensionScorePatch,
    CsfDimensionScoreResponse,
    CsfPlaybookExportResponse,
    CsfProfileResponse,
    CsfQuestionnaireResponse,
    CsfRunAiResponse,
    CsfScoreSummary,
    CsfSelfAssessmentSubmit,
    CsfServiceCreateRequest,
    CsfServiceResponse,
    EnterpriseProfileResponse,
    EnterpriseSubcategory,
    ExportedArtifact,
    FunctionScore,
    GapAnalysisResponse,
    GapItem,
    InterviewQuestion,
    ProfileSeedRequest,
)
from app.schemas.tech_debt import DeliverableResponse
from app.storage import StorageBackend
from app.tech_debt.filename import (
    SERVICE_SLUG_NIST_CSF,
    deliverable_filename,
)
from app.tenant import (
    require_artifact_in_tenant,
    require_csf_assessment_in_tenant,
    require_service_in_tenant,
)

router = APIRouter(prefix="/csf", tags=["csf"])

_admin_required = Depends(require_role(UserRole.ADMIN))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_answers(rows: Iterable[CsfAnswer]) -> list[CsfAnswerResponse]:
    # Stable ordering: by NIST code so the workspace tab renders predictably.
    ordered = sorted(rows, key=lambda r: r.subcategory_code)
    return [CsfAnswerResponse.model_validate(r, from_attributes=True) for r in ordered]


def _client_target_tier(db: Session, service_id: uuid.UUID) -> int | None:
    """The CSF target tier the client chose at intake, via the source request.

    Lets the admin workspace default its gap target to the client's goal
    instead of a hardcoded tier.
    """
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.csf_target_tier if sr is not None else None


def _client_profile(db: Session, service_id: uuid.UUID) -> str | None:
    """The CSF impact profile the client chose at intake (LOW/MOD/HIGH)."""
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.csf_profile if sr is not None else None


def _serialize_assessment(db: Session, a: CsfAssessment) -> CsfAssessmentResponse:
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    return CsfAssessmentResponse(
        id=a.id,
        service_id=a.service_id,
        version=a.version,
        status=a.status,
        approved_at=a.approved_at,
        approved_by=a.approved_by,
        documents_stale=a.documents_stale,
        answers=_serialize_answers(rows),
        client_target_tier=_client_target_tier(db, a.service_id),
        client_profile=_client_profile(db, a.service_id),
    )


def _latest_assessment(db: Session, service_id: uuid.UUID) -> CsfAssessment | None:
    return db.execute(
        select(CsfAssessment)
        .where(CsfAssessment.service_id == service_id)
        .order_by(CsfAssessment.version.desc())
        .limit(1)
    ).scalar_one_or_none()


# Impact profile (set at intake) -> the interview-questionnaire framework_key
# loaded into the `questions` table. HIGH is the most complete questionnaire,
# so it's the fallback when no profile has been chosen yet.
_PROFILE_TO_TIER_KEY = {
    "LOW": "csf-tier-low",
    "MOD": "csf-tier-moderate",
    "HIGH": "csf-tier-high",
}
_DEFAULT_TIER_KEY = "csf-tier-high"


@router.get(
    "/services/{service_id}/questionnaire",
    response_model=CsfQuestionnaireResponse,
    summary="Interview prompts for the service's impact tier",
)
def get_interview_questionnaire(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfQuestionnaireResponse:
    """Tier-resolved interview prompts (read-only).

    Each prompt carries the CSF subcategories it informs so the workspace can
    surface it inline on those subcategory cards. Any signed-in role scoped to
    the tenant may read it.
    """
    require_service_in_tenant(db, service_id, client.id)
    profile = _client_profile(db, service_id)
    framework_key = _PROFILE_TO_TIER_KEY.get((profile or "").upper(), _DEFAULT_TIER_KEY)
    rows = (
        db.execute(
            select(Question)
            .where(Question.framework_key == framework_key)
            .order_by(Question.order_index)
        )
        .scalars()
        .all()
    )
    return CsfQuestionnaireResponse(
        framework_key=framework_key,
        profile=profile,
        questions=[
            InterviewQuestion(
                external_id=q.external_id,
                section_name=q.pillar,
                order_index=q.order_index,
                stem=q.stem,
                cues=list(q.cues or []),
                csf_subcategories=list(q.framework_activities or []),
            )
            for q in rows
        ],
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.post(
    "/services",
    response_model=CsfServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a CSF assessment service (admin)",
)
def create_csf_service(
    body: CsfServiceCreateRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfServiceResponse:
    if body.kind != ServiceKind.NIST_CSF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Service kind must be nist_csf for this endpoint.",
        )
    svc = Service(
        kind=ServiceKind.NIST_CSF,
        status=ServiceStatus.IN_PROGRESS,
        title=body.title,
        client_id=client.id,
        source_request_id=body.source_request_id,
        opened_by=user.id,
    )
    db.add(svc)
    db.flush()
    audit(
        db,
        action="csf.service.opened",
        target_type="service",
        target_id=svc.id,
        actor_user_id=user.id,
        details={"title": svc.title},
    )
    db.commit()
    db.refresh(svc)
    return CsfServiceResponse.model_validate(svc, from_attributes=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="NIST CSF 2.0 reference catalog",
)
def get_catalog(
    _user: Annotated[User, Depends(current_user)],
) -> CatalogResponse:
    functions: list[CatalogFunction] = []
    for fn in FUNCTIONS:
        categories: list[CatalogCategory] = []
        for cat in CATEGORIES:
            if cat.function != fn.code:
                continue
            subs = [
                CatalogSubcategory(
                    code=s.code,
                    function=s.function.value,
                    category=s.category,
                    name=s.name,
                    outcome=s.outcome,
                    min_profile=min_profile_for_category(s.category),
                )
                for s in SUBCATEGORIES
                if s.category == cat.code
            ]
            categories.append(
                CatalogCategory(
                    code=cat.code,
                    function=cat.function.value,
                    name=cat.name,
                    purpose=cat.purpose,
                    subcategories=subs,
                )
            )
        functions.append(
            CatalogFunction(
                code=fn.code.value,
                name=fn.name,
                purpose=fn.purpose,
                categories=categories,
            )
        )
    tiers = [
        CatalogTier(tier=int(t.tier), short_label=t.short_label, description=t.description)
        for t in TIER_DEFINITIONS
    ]
    return CatalogResponse(
        functions=functions,
        tiers=tiers,
        total_subcategories=len(SUBCATEGORIES),
    )


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


@router.post(
    "/services/{service_id}/assessments",
    response_model=CsfAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft assessment for the service (admin)",
)
def create_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    # FIX E-3: open-draft guard. If a draft already exists, return it instead of
    # minting a fresh version — a double-click / two tabs must not create a
    # second v1 (or supersede the working draft with an empty one). NB: CSF did
    # NOT already have this guard; it minted prior.version + 1 unconditionally.
    assessment, _created = _get_or_create_draft(db, svc, client.id, user.id)
    db.commit()
    db.refresh(assessment)
    return _serialize_assessment(db, assessment)


def _create_draft_assessment(
    db: Session, svc: Service, client_id: uuid.UUID, user_id: uuid.UUID
) -> CsfAssessment:
    """Mint a new draft assessment + its empty answer grid (no commit)."""
    prior = _latest_assessment(db, svc.id)
    version = (prior.version + 1) if prior else 1
    assessment = CsfAssessment(
        service_id=svc.id,
        client_id=client_id,
        version=version,
        status=CsfAssessmentStatus.DRAFT,
    )
    db.add(assessment)
    db.flush()
    # Pre-create empty answer rows so the workspace UI gets a deterministic
    # answer grid back from the very first GET. Cheap (~106 rows).
    for sc in SUBCATEGORIES:
        db.add(
            CsfAnswer(
                assessment_id=assessment.id,
                client_id=client_id,
                subcategory_code=sc.code,
            )
        )
    audit(
        db,
        action="csf.assessment.created",
        target_type="csf_assessment",
        target_id=assessment.id,
        actor_user_id=user_id,
        details={"service_id": str(svc.id), "version": version},
    )
    return assessment


def _get_or_create_draft(
    db: Session, svc: Service, client_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[CsfAssessment, bool]:
    """Return the current open draft (idempotent), else mint a new one.

    ``bool`` is True when a new draft was created. Only DRAFT counts as "open";
    a submitted/approved/released latest means the next create legitimately
    mints a fresh version.
    """
    prior = _latest_assessment(db, svc.id)
    if prior is not None and prior.status == CsfAssessmentStatus.DRAFT:
        return prior, False
    return _create_draft_assessment(db, svc, client_id, user_id), True


@router.get(
    "/services/{service_id}/assessments/latest",
    response_model=CsfAssessmentResponse,
    summary="Most recent assessment for the service",
)
def latest_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    # Phase 4 keeps assessment scoreboards admin-only until the
    # deliverable is released to the client (mirrors Phase 3 stage 9).
    if user.role != UserRole.ADMIN and assessment.status != CsfAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSF assessments are admin-only until released.",
        )
    return _serialize_assessment(db, assessment)


# ---------------------------------------------------------------------------
# Answer editing
# ---------------------------------------------------------------------------


@router.patch(
    "/answers/{answer_id}",
    response_model=CsfAnswerResponse,
    summary="Inline-update a single subcategory answer (admin)",
)
def patch_answer(
    answer_id: uuid.UUID,
    body: CsfAnswerPatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(CsfAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    # Refuse edits to approved or released assessments.
    a = db.get(CsfAssessment, row.assessment_id)
    if a is None or a.status in (
        CsfAssessmentStatus.APPROVED,
        CsfAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assessment is locked.",
        )
    # Validation: subcategory code already pinned at create-time, so we
    # only validate the tier values that arrive here.
    if "maturity_tier" in data and data["maturity_tier"] is not None:
        t = int(data["maturity_tier"])
        if not 1 <= t <= 4:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="maturity_tier must be 1-4.",
            )
        row.maturity_tier = t
    elif "maturity_tier" in data:
        row.maturity_tier = None
    if "notes" in data:
        row.notes = data["notes"]
    if "evidence_artifact_id" in data:
        aid = data["evidence_artifact_id"]
        # FIX C-8: validate the evidence link. An unchecked assignment accepted a
        # cross-tenant artifact id (then leaked it back) and 500'd on a
        # nonexistent id. Route it through the tenant check: 404 on missing or
        # cross-tenant (the platform's no-oracle convention).
        if aid is not None:
            require_artifact_in_tenant(db, aid, client.id)
        row.evidence_artifact_id = aid
    if data.get("locked") is not None:
        row.locked = bool(data["locked"])
    row.answered_by = user.id
    row.answered_at = utcnow()
    audit(
        db,
        action="csf.answer.updated",
        target_type="csf_answer",
        target_id=row.id,
        actor_user_id=user.id,
        details={
            "subcategory_code": row.subcategory_code,
            "fields": sorted(data.keys()),
        },
    )
    db.commit()
    db.refresh(row)
    return CsfAnswerResponse.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Client self-assessment (client fills their own draft, then submits for review)
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/self-assessment",
    response_model=CsfAssessmentResponse,
    summary="The client's own assessment for this service (any status)",
)
def get_self_assessment(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    """Read the client's own assessment so they can fill the questionnaire.

    Tenant-scoped (current_client), so a client only ever reaches their own.
    Unlike the admin `assessments/latest`, this is not gated on RELEASED - the
    client owns these answers. The score/gap/deliverable stay admin-only until
    the report is released.
    """
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    return _serialize_assessment(db, assessment)


@router.patch(
    "/self-assessment/answers/{answer_id}",
    response_model=CsfAnswerResponse,
    summary="Client updates one answer on their own draft self-assessment",
)
def patch_self_assessment_answer(
    answer_id: uuid.UUID,
    body: CsfAnswerPatch,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(CsfAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(CsfAssessment, row.assessment_id)
    if a is None or a.status != CsfAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your self-assessment is no longer editable.",
        )
    if "maturity_tier" in data and data["maturity_tier"] is not None:
        t = int(data["maturity_tier"])
        if not 1 <= t <= 4:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="maturity_tier must be 1-4.",
            )
        row.maturity_tier = t
    elif "maturity_tier" in data:
        row.maturity_tier = None
    if "notes" in data:
        row.notes = data["notes"]
    row.answered_by = user.id
    row.answered_at = utcnow()
    db.commit()
    db.refresh(row)
    return CsfAnswerResponse.model_validate(row, from_attributes=True)


@router.post(
    "/services/{service_id}/self-assessment/submit",
    response_model=CsfAssessmentResponse,
    summary="Client submits their self-assessment for admin review",
)
def submit_self_assessment(
    service_id: uuid.UUID,
    body: CsfSelfAssessmentSubmit,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if a.status != CsfAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This self-assessment has already been submitted.",
        )
    # Persist the (possibly adjusted) maturity target so the gap engine measures
    # against the client's goal.
    if body.target_tier is not None and svc.source_request_id is not None:
        sr = db.get(ServiceRequest, svc.source_request_id)
        if sr is not None:
            sr.csf_target_tier = body.target_tier
    a.status = CsfAssessmentStatus.SUBMITTED
    audit(
        db,
        action="csf.self_assessment.submitted",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"service_id": str(svc.id), "version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


@router.post(
    "/assessments/{assessment_id}/approve",
    response_model=CsfAssessmentResponse,
    summary="Approve the assessment (admin)",
)
def approve_assessment(
    assessment_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    a = require_csf_assessment_in_tenant(db, assessment_id, client.id)
    if a.status == CsfAssessmentStatus.APPROVED:
        return _serialize_assessment(db, a)
    if a.status == CsfAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already released.",
        )
    a.status = CsfAssessmentStatus.APPROVED
    a.approved_at = utcnow()
    a.approved_by = user.id
    audit(
        db,
        action="csf.assessment.approved",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/score",
    response_model=CsfScoreSummary,
    summary="Roll-up score for the latest assessment (admin)",
)
def score_latest(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfScoreSummary:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {r.subcategory_code: r.maturity_tier for r in rows}
    # Defensive: ignore unknown codes.
    valid = all_codes()
    answers = {k: v for k, v in answers.items() if k in valid}
    score = compute_score(answers)
    return CsfScoreSummary(
        assessment_id=a.id,
        version=a.version,
        total_subcategories=score.total_subcategories,
        answered_subcategories=score.answered_subcategories,
        coverage_pct=score.coverage_pct,
        average_tier=score.average_tier,
        overall_maturity_label=score.overall_maturity_label,
        by_function=[
            FunctionScore(
                function=fs.function.value,
                function_name=fs.function_name,
                subcategory_count=fs.subcategory_count,
                answered_count=fs.answered_count,
                average_tier=fs.average_tier,
                coverage_pct=fs.coverage_pct,
                weakest_subcategory_codes=list(fs.weakest_subcategory_codes),
            )
            for fs in score.by_function
        ],
    )


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/gap-analysis",
    response_model=GapAnalysisResponse,
    summary="Prioritized remediation gaps for the latest assessment (admin)",
)
def gap_analysis(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    target_tier: int = 3,
    top_n: int = 20,
) -> GapAnalysisResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    valid = all_codes()
    answers: dict[str, int | None] = {
        r.subcategory_code: r.maturity_tier for r in rows if r.subcategory_code in valid
    }
    notes: dict[str, str | None] = {
        r.subcategory_code: r.notes for r in rows if r.subcategory_code in valid
    }
    analysis = analyze_gaps(answers, notes=notes, target_tier=target_tier, top_n=top_n)
    return GapAnalysisResponse(
        assessment_id=a.id,
        version=a.version,
        target_tier=analysis.target_tier,
        target_label=analysis.target_label,
        total_gap_count=analysis.total_gap_count,
        unscored_count=len(analysis.unscored_codes),
        gap_count_by_function=analysis.gap_count_by_function,
        gaps=[
            GapItem(
                code=g.code,
                function=g.function.value,
                function_name=g.function_name,
                category=g.category,
                name=g.name,
                outcome=g.outcome,
                current_tier=g.current_tier,
                target_tier=g.target_tier,
                gap_size=g.gap_size,
                priority_score=g.priority_score,
                notes=g.notes,
            )
            for g in analysis.gaps
        ],
    )


# ---------------------------------------------------------------------------
# Full-Playbook tiered Working Profile (Work Order D4)
# ---------------------------------------------------------------------------

_VALID_TIERS = {t.value for t in Tier}

# FIX F-1: auto-seed default. The client's impact profile (set at intake) picks
# the tier to seed lazily on first Run AI; HIGH is the safe fallback (the most
# complete profile). Seeding NEVER stamps scored_at, so B-3's export gate keeps
# blocking until a real score (a human patch or a Run-AI pass) lands.
_PROFILE_TO_SEED_TIER = {"LOW": "low", "MOD": "moderate", "HIGH": "high"}


def _default_seed_tiers(db: Session, service_id: uuid.UUID) -> list[str]:
    profile = (_client_profile(db, service_id) or "").upper()
    return [_PROFILE_TO_SEED_TIER.get(profile, "high")]


def _seed_working_profile(
    db: Session, a: CsfAssessment, client_id: uuid.UUID, tiers: list[str]
) -> int:
    """Create the (tier, subcategory) dimension-score rows that don't yet exist.

    Idempotent (skips existing pairs) and deliberately leaves ``scored_at`` NULL
    so the B-3 export gate still counts every row as unscored. Returns how many
    rows were created.
    """
    existing = {
        (r.tier, r.subcategory_code)
        for r in db.execute(
            select(CsfDimensionScore.tier, CsfDimensionScore.subcategory_code).where(
                CsfDimensionScore.assessment_id == a.id
            )
        ).all()
    }
    created = 0
    for tier in tiers:
        for sc in SUBCATEGORIES:
            if (tier, sc.code) in existing:
                continue
            db.add(
                CsfDimensionScore(
                    assessment_id=a.id,
                    client_id=client_id,
                    tier=tier,
                    subcategory_code=sc.code,
                )
            )
            created += 1
    return created


def _dims(row: CsfDimensionScore) -> DimensionScores:
    return DimensionScores(
        governance=row.governance,
        policy=row.policy,
        implementation=row.implementation,
        monitoring=row.monitoring,
        improvement=row.improvement,
    )


def _score_response(row: CsfDimensionScore) -> CsfDimensionScoreResponse:
    result = score_tier(_dims(row), has_evidence=row.has_evidence)
    return CsfDimensionScoreResponse(
        id=row.id,
        tier=row.tier,
        subcategory_code=row.subcategory_code,
        governance=row.governance,
        policy=row.policy,
        implementation=row.implementation,
        monitoring=row.monitoring,
        improvement=row.improvement,
        in_scope=row.in_scope,
        rationale=row.rationale,
        what_we_found=row.what_we_found,
        has_evidence=row.has_evidence,
        target_level=row.target_level,
        locked=row.locked,
        total=result.total,
        level=result.level,
        evidence_capped=result.evidence_capped,
    )


@router.post(
    "/services/{service_id}/profiles/seed",
    response_model=list[str],
    summary="Seed the tiered Working Profile rows for the requested tiers (admin)",
)
def seed_profiles(
    service_id: uuid.UUID,
    body: ProfileSeedRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> list[str]:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Create an assessment first."
        )
    tiers = [t for t in body.tiers if t in _VALID_TIERS]
    if not tiers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No valid tiers (high/moderate/low).",
        )
    created = _seed_working_profile(db, a, client.id, tiers)
    audit(
        db,
        action="csf.profiles_seeded",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"tiers": tiers, "created": created},
    )
    db.commit()
    return tiers


@router.get(
    "/services/{service_id}/profile/{tier}",
    response_model=CsfProfileResponse,
    summary="The tiered Working Profile for one tier, with computed totals/levels (admin)",
)
def get_profile(
    service_id: uuid.UUID,
    tier: str,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfProfileResponse:
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tier.")
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    rows = (
        db.execute(
            select(CsfDimensionScore)
            .where(
                CsfDimensionScore.assessment_id == a.id,
                CsfDimensionScore.tier == tier,
            )
            .order_by(CsfDimensionScore.subcategory_code)
        )
        .scalars()
        .all()
    )
    return CsfProfileResponse(tier=tier, rows=[_score_response(r) for r in rows])


@router.patch(
    "/dimension-scores/{score_id}",
    response_model=CsfDimensionScoreResponse,
    summary="Set dimension scores / scope / target / lock on one row (admin)",
)
def patch_dimension_score(
    score_id: uuid.UUID,
    body: CsfDimensionScorePatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfDimensionScoreResponse:
    row = db.get(CsfDimensionScore, score_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Score row not found.")
    data = body.model_dump(exclude_unset=True)
    for f in (
        "governance",
        "policy",
        "implementation",
        "monitoring",
        "improvement",
        "in_scope",
        "rationale",
        "what_we_found",
        "has_evidence",
        "target_level",
        "locked",
    ):
        if f in data and data[f] is not None:
            setattr(row, f, data[f])
        elif f in data and f in ("rationale", "what_we_found", "target_level"):
            setattr(row, f, None)  # explicit clear allowed for nullable text/target
    # FIX B-3: a human scoring the row (any of the five dimensions, the evidence
    # flag, or the narrative) stamps scored_at so the export gate counts it as
    # assessed. Toggling only in_scope / lock / target is not "scoring".
    if any(f in data for f in (*_DIM_FIELDS, "has_evidence", "what_we_found")):
        row.scored_at = utcnow()
    db.commit()
    return _score_response(row)


@router.get(
    "/services/{service_id}/enterprise-profile",
    response_model=EnterpriseProfileResponse,
    summary="Roll the tiered profiles up to one Enterprise level per subcategory (admin)",
)
def enterprise_profile(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> EnterpriseProfileResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    out, tiers_in_use = _enterprise_subcategories(db, a)
    return EnterpriseProfileResponse(tiers_in_use=sorted(tiers_in_use), subcategories=out)


def _enterprise_subcategories(
    db: Session, a: CsfAssessment
) -> tuple[list[EnterpriseSubcategory], set[str]]:
    """The weighted-floor Enterprise roll-up per in-scope subcategory."""
    rows = (
        db.execute(select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id))
        .scalars()
        .all()
    )
    by_subcat: dict[str, dict[str, CsfDimensionScore]] = {}
    tiers_in_use: set[str] = set()
    for r in rows:
        if not r.in_scope:
            continue
        by_subcat.setdefault(r.subcategory_code, {})[r.tier] = r
        tiers_in_use.add(r.tier)

    out: list[EnterpriseSubcategory] = []
    for code in sorted(by_subcat):
        tier_rows = by_subcat[code]
        tier_levels = {
            tier: score_tier(_dims(row), has_evidence=row.has_evidence).level
            for tier, row in tier_rows.items()
        }
        rollup = weighted_floor_rollup(
            {Tier(t): lvl for t, lvl in tier_levels.items()},
            # IG core/supporting metadata isn't in the catalog yet; defaults keep
            # the roll-up on rules 1/3/4/6 until the IG import lands.
            is_core_primary=False,
            is_supporting_or_supplemental=False,
        )
        targets = [row.target_level for row in tier_rows.values() if row.target_level]
        target = max(targets) if targets else None
        gap = is_gap(rollup.score, target) if target is not None else False
        priority = (
            gap_priority(
                is_core=False,
                high_tier=Tier.HIGH.value in tier_rows,
                multi_system=len(tier_rows) > 1,
            )
            if gap
            else None
        )
        sc = subcategory_by_code(code)
        out.append(
            EnterpriseSubcategory(
                subcategory_code=code,
                name=getattr(sc, "name", code),
                function=str(getattr(sc, "function", "")),
                tier_levels=tier_levels,
                enterprise_level=rollup.score,
                rollup_rule=rollup.rule,
                target_level=target,
                gap=gap,
                priority=priority,
            )
        )
    return out, tiers_in_use


def _llm_dep() -> LLMClient:
    # FIX A-5: surface a misconfigured live LLM as a typed error, not a 500.
    try:
        return LLMClient.from_settings()
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


_DIM_FIELDS = ("governance", "policy", "implementation", "monitoring", "improvement")
_RUN_FIELDS = (*_DIM_FIELDS, "what_we_found")


@router.post(
    "/services/{service_id}/run-ai",
    response_model=CsfRunAiResponse,
    summary="Run the csf_score AI job: suggest dimension scores + narrative (admin)",
)
def run_ai(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
) -> CsfRunAiResponse:
    """The CSF full-Playbook 'Run AI'. Suggests the five dimension scores (0-2)
    + a 'what we found' narrative per (tier, subcategory). AI suggests; locked
    rows are untouched; code does the total/level/cap + Enterprise roll-up.
    Returns a 'what changed' list.
    """
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    # FIX E-3: hold the per-service run lock across the whole read-modify-write
    # (including the auto-seed + the provider call). It survives the db.rollback()
    # E-1 does before the provider call — see app/db/locks.py.
    try:
        with run_lock(db, "csf_run_ai", svc.id):
            return _csf_run_ai_locked(svc, user, client, db, llm)
    except RunInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Run AI is already in progress for this assessment.",
        ) from exc


def _csf_run_ai_locked(
    svc: Service,
    user: User,
    client: Client,
    db: Session,
    llm: LLMClient,
) -> CsfRunAiResponse:
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Create an assessment first."
        )
    if a.status in (CsfAssessmentStatus.APPROVED, CsfAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment is locked."
        )
    rows = {
        f"{r.tier}|{r.subcategory_code}": r
        for r in db.execute(
            select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id)
        )
        .scalars()
        .all()
    }
    if not rows:
        # FIX F-1: auto-seed the Working Profile lazily instead of 409'ing. The
        # manual seed endpoint stays for re-seeding after catalog changes. Seeding
        # leaves scored_at NULL, so B-3's export gate still blocks (verified in
        # tests). Commit the seeded rows so they survive the E-1 rollback below.
        _seed_working_profile(db, a, client.id, _default_seed_tiers(db, svc.id))
        db.commit()
        rows = {
            f"{r.tier}|{r.subcategory_code}": r
            for r in db.execute(
                select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id)
            )
            .scalars()
            .all()
        }
    locked_keys = frozenset(k for k, r in rows.items() if r.locked)

    def _snap() -> dict[str, dict]:
        return {k: {f: getattr(r, f) for f in _RUN_FIELDS} for k, r in rows.items()}

    before = _snap()
    client_org = None if client.legal_name == "(pending intake)" else client.legal_name

    # FIX A-2: ground the payload. The prompt promises the model "interview
    # answers, evidence summaries, and per-subcategory context", but the old
    # payload sent only bare tier strings + subcategory codes, so even a
    # schema-correct response was scored from nothing. Hand the model, per
    # (tier, subcategory_code): the client's own self-assessment answer (tier +
    # notes) from the CsfAnswer rows, the evidence flags, and the subcategory's
    # catalog context. Redaction runs downstream in LLMClient.invoke — we pass
    # the raw notes here and let it strip PII; we do not pre-redact.
    answers_by_code = {
        ans.subcategory_code: ans
        for ans in db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id))
        .scalars()
        .all()
    }

    def _ground(r: CsfDimensionScore) -> dict:
        ans = answers_by_code.get(r.subcategory_code)
        sc = subcategory_by_code(r.subcategory_code)
        return {
            "tier": r.tier,
            "subcategory_code": r.subcategory_code,
            "subcategory_name": getattr(sc, "name", None),
            "function": str(getattr(sc, "function", "")),
            "outcome": getattr(sc, "outcome", None),
            "in_scope": r.in_scope,
            "self_assessment_tier": ans.maturity_tier if ans else None,
            "self_assessment_notes": ans.notes if ans else None,
            "assessor_rationale": r.rationale,
            "prior_narrative": r.what_we_found,
            "has_evidence": r.has_evidence,
            "evidence_provided": bool(r.evidence_artifact_id)
            or (ans is not None and ans.evidence_artifact_id is not None),
            "target_level": r.target_level,
        }

    # FIX A-3: chunk by FIPS tier. csf_score is pinned to Haiku (64K max output);
    # one un-chunked call carries every seeded (tier x subcategory) row — up to
    # ~3 x 106 = 318 entries — which risks a truncated, unparseable response.
    # Each tier chunk holds at most len(SUBCATEGORIES) (~106) entries, well under
    # the cap, and is an INDEPENDENT run_job call, so redaction + one llm_calls
    # row per chunk still happen. `tier` is part of every row's key, so every
    # (tier, subcategory_code) pair lands in EXACTLY ONE chunk.
    chunks: dict[str, list[CsfDimensionScore]] = {}
    for r in rows.values():
        chunks.setdefault(r.tier, []).append(r)

    # FIX E-1a: materialize every chunk's grounded payload BEFORE releasing the
    # pooled connection, so no ORM read happens across a provider call. There are
    # no pending writes yet, so db.rollback() cleanly returns the connection; the
    # apply queries below re-acquire it.
    chunk_inputs = {
        tier: {"tier": tier, "items": [_ground(r) for r in chunks[tier]]} for tier in sorted(chunks)
    }
    # Capture the ids BEFORE rollback: db.rollback() expires these ORM objects,
    # so reading .id afterwards would reload them and re-check-out a connection
    # for the whole provider call — defeating the release.
    run_uid, run_sid, run_cid = user.id, svc.id, client.id
    db.rollback()

    merged_scores: list = []
    for tier in sorted(chunk_inputs):
        try:
            result = run_job(
                db,
                llm,
                "csf_score",
                inputs=chunk_inputs[tier],
                requested_by=run_uid,
                service_id=run_sid,
                client_id=run_cid,
                client_org_name=client_org,
            )
        except LLMTimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="the AI call timed out; nothing was changed",
            ) from exc
        except ValueError as exc:
            # CRITICAL: one bad chunk aborts the whole run and applies NOTHING.
            # No score row has been mutated yet (application happens only after
            # every chunk parses), so the transaction rolls back clean. A
            # half-applied assessment is worse than a failed one.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"AI scoring failed for the {tier!r} tier chunk; no scores "
                    "were applied. Re-run once the model returns valid JSON."
                ),
            ) from exc
        data = result.data if isinstance(result.data, dict) else {}
        merged_scores.extend(data.get("scores", []))

    # Apply only AFTER every chunk parsed.
    for sugg in merged_scores:
        if not isinstance(sugg, dict):
            continue
        row = rows.get(f"{sugg.get('tier')}|{sugg.get('subcategory_code')}")
        if row is None or row.locked:
            continue
        touched = False
        for dim in _DIM_FIELDS:
            if dim in sugg:
                try:
                    v = int(sugg[dim])
                except (TypeError, ValueError):
                    continue
                if 0 <= v <= 2:
                    setattr(row, dim, v)
                    touched = True
        if isinstance(sugg.get("what_we_found"), str):
            row.what_we_found = sugg["what_we_found"]
            touched = True
        if touched:
            row.scored_at = utcnow()  # FIX B-3: the AI wrote this row.

    db.flush()
    after = _snap()
    diffs = diff_keyed_rows(before, after, list(_RUN_FIELDS), locked_keys=locked_keys)
    changes: list[CsfDimensionChange] = []
    for d in diffs:
        tier, _, code = d.key.partition("|")
        for ch in d.changes:
            changes.append(
                CsfDimensionChange(
                    tier=tier, subcategory_code=code, field=ch.field, old=ch.old, new=ch.new
                )
            )

    a.documents_stale = True  # Work Order C3
    audit(
        db,
        action="csf.run_ai",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"changed_rows": len(diffs)},
    )
    db.commit()
    out_rows = [
        _score_response(r)
        for r in sorted(rows.values(), key=lambda r: (r.tier, r.subcategory_code))
    ]
    return CsfRunAiResponse(changed=changes, rows=out_rows, mode=llm.mode)


@router.post(
    "/services/{service_id}/playbook/export",
    response_model=CsfPlaybookExportResponse,
    summary="Render + store the CSF full-Playbook workbook (XLSX) (admin)",
)
def export_playbook(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> CsfPlaybookExportResponse:
    """An Enterprise Profile sheet (weighted-floor roll-up) + one sheet per tier
    with the five dimension scores and computed total/level/cap."""
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    all_rows = (
        db.execute(select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id))
        .scalars()
        .all()
    )
    if not all_rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seed the Working Profile before exporting.",
        )
    # FIX B-3: HARD gate. Seeding creates every row with all five dimensions at 0,
    # which the maturity math reads as a legitimate "Level 1", so exporting right
    # after "Seed Working Profiles" produced a full deliverable asserting L1
    # across every subcategory before anyone assessed anything (and cleared the
    # stale-documents flag as if the docs were current). Block unless EVERY
    # in-scope row has actually been scored (scored_at, set by a human patch or a
    # Run-AI pass) AND the assessment is approved. Product decision: no
    # draft-export path. Out-of-scope rows are excluded from the roll-up, so they
    # need no score. Nothing below this point (including clearing documents_stale)
    # runs unless the gate passes.
    in_scope_rows = [r for r in all_rows if r.in_scope]
    unscored = [r for r in in_scope_rows if r.scored_at is None]
    if unscored:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot export the Playbook: {len(unscored)} in-scope subcategory "
                "row(s) are still unscored. Score every row (Run AI or manual) and "
                "approve the assessment before exporting."
            ),
        )
    if a.status not in (CsfAssessmentStatus.APPROVED, CsfAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot export the Playbook: the assessment must be approved before exporting.",
        )
    enterprise_rows, _ = _enterprise_subcategories(db, a)
    # Belt and braces (FIX B-3 layer 3): tag every rendered row with whether it
    # was actually scored, so a null-scored row that somehow reaches an exporter
    # renders "Unscored" — never a bogus "Level 1". A code is "scored" only if
    # ALL of its in-scope tier rows are scored. Unreachable while the gate holds.
    code_scored: dict[str, bool] = {}
    for r in in_scope_rows:
        code_scored[r.subcategory_code] = code_scored.get(r.subcategory_code, True) and (
            r.scored_at is not None
        )
    enterprise_rows = [
        SimpleNamespace(**er.model_dump(), scored=code_scored.get(er.subcategory_code, True))
        for er in enterprise_rows
    ]
    tier_profiles: dict[str, list] = {}
    for tier in ("high", "moderate", "low"):
        trows = sorted(
            (r for r in all_rows if r.tier == tier),
            key=lambda r: r.subcategory_code,
        )
        if trows:
            tier_profiles[tier] = [
                SimpleNamespace(**_score_response(r).model_dump(), scored=r.scored_at is not None)
                for r in trows
            ]

    from app.docx_export import DOCX_MIME

    org = None if client.legal_name == "(pending intake)" else client.legal_name
    name = org or "Client"
    on = utcnow().strftime("%Y-%m-%d")
    today = utcnow().date()
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    pdf_mime = "application/pdf"

    # FIX B-7: route the Playbook filenames through the §15.5 deliverable_filename
    # convention (Company_Service{MMDDYY}[_vN].ext) like every other finalize
    # flow, instead of raw f-strings that carried no company or date.
    def _pb_name(service_slug: str, extension: str) -> str:
        return deliverable_filename(
            company=org,
            service_slug=service_slug,
            extension=extension,
            day=today,
            version=a.version,
        )

    specs = [
        (
            "xlsx",
            "Data workbook (XLSX)",
            _pb_name("CSF_Playbook", "xlsx"),
            xlsx_mime,
            csf_playbook_export.render_xlsx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                tier_profiles=tier_profiles,
            ),
        ),
        (
            "exec_pdf",
            "Executive briefing (PDF)",
            _pb_name("CSF_Playbook_Executive", "pdf"),
            pdf_mime,
            csf_playbook_export.render_exec_pdf(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "exec_docx",
            "Executive briefing (Word)",
            _pb_name("CSF_Playbook_Executive", "docx"),
            DOCX_MIME,
            csf_playbook_export.render_exec_docx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "full_pdf",
            "Full playbook (PDF)",
            _pb_name("CSF_Playbook_Full", "pdf"),
            pdf_mime,
            csf_playbook_export.render_full_pdf(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "full_docx",
            "Full playbook (Word)",
            _pb_name("CSF_Playbook_Full", "docx"),
            DOCX_MIME,
            csf_playbook_export.render_full_docx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
    ]
    artifacts: list[ExportedArtifact] = []
    for kind, label, filename, mime, data in specs:
        art = _write_artifact(
            db,
            storage=storage,
            user=user,
            client_id=client.id,
            filename=filename,
            mime_type=mime,
            data=data,
        )
        artifacts.append(
            ExportedArtifact(kind=kind, label=label, artifact_id=art.id, filename=art.title)
        )

    audit(
        db,
        action="csf.playbook_exported",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version, "artifacts": len(artifacts)},
    )
    a.documents_stale = False  # Work Order C3: exporting refreshes the documents
    db.commit()
    return CsfPlaybookExportResponse(artifacts=artifacts)


# ---------------------------------------------------------------------------
# Deliverables
# ---------------------------------------------------------------------------


def _serialize_deliverable(db: Session, deliv: Deliverable) -> DeliverableResponse:
    pdf_title = None
    xlsx_title = None
    docx_title = None
    if deliv.pdf_artifact_id:
        a = db.get(Artifact, deliv.pdf_artifact_id)
        pdf_title = a.title if a else None
    if deliv.xlsx_artifact_id:
        a = db.get(Artifact, deliv.xlsx_artifact_id)
        xlsx_title = a.title if a else None
    if deliv.docx_artifact_id:
        a = db.get(Artifact, deliv.docx_artifact_id)
        docx_title = a.title if a else None
    return DeliverableResponse(
        id=deliv.id,
        service_id=deliv.service_id,
        title=deliv.title,
        summary=deliv.summary,
        version=deliv.version,
        pdf_artifact_id=deliv.pdf_artifact_id,
        xlsx_artifact_id=deliv.xlsx_artifact_id,
        docx_artifact_id=deliv.docx_artifact_id,
        pdf_filename=pdf_title,
        xlsx_filename=xlsx_title,
        docx_filename=docx_title,
        finalized_at=deliv.finalized_at,
        finalized_by=deliv.finalized_by,
        superseded_by=deliv.superseded_by,
    )


def _write_artifact(
    db: Session,
    *,
    storage: StorageBackend,
    user: User,
    client_id: uuid.UUID,
    filename: str,
    mime_type: str,
    data: bytes,
) -> Artifact:
    from hashlib import sha256

    key = f"deliverable/{user.id}/{uuid.uuid4()}/{filename}"
    storage.put(key, data, content_type=mime_type)
    art = Artifact(
        client_id=client_id,
        title=filename,
        file_storage_key=key,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        origin=ArtifactOrigin.CONSULTANT_APPROVED,
        stage="csf.deliverable",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/services/{service_id}/deliverables/finalize",
    response_model=DeliverableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Render PDF + XLSX deliverable from the latest approved CSF assessment (admin)",
)
def finalize_csf_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> DeliverableResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if assessment.status not in (
        CsfAssessmentStatus.APPROVED,
        CsfAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment must be approved before finalizing the deliverable.",
        )
    answers = (
        db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == assessment.id))
        .scalars()
        .all()
    )
    valid = all_codes()
    tier_map: dict[str, int | None] = {
        r.subcategory_code: r.maturity_tier for r in answers if r.subcategory_code in valid
    }
    notes_map: dict[str, str | None] = {
        r.subcategory_code: r.notes for r in answers if r.subcategory_code in valid
    }
    score = compute_score(tier_map)
    # FIX B-2: honor the client's chosen target tier. The old call passed no
    # target, so finalize always measured against DEFAULT_TARGET_TIER (3) — a
    # client targeting T4 (or T2) got a Gap Plan that disagreed with the
    # workspace gap list. Resolve the target from the originating
    # ServiceRequest.csf_target_tier, falling back to the default; the summary
    # line below prints the resolved tier so the document states its assumption.
    resolved = _client_target_tier(db, svc.id)
    target_tier = resolved if (resolved is not None and 1 <= resolved <= 4) else DEFAULT_TARGET_TIER
    # B-4: the XLSX Gap Plan sheet must carry the FULL gap list, not a top-20
    # slice; top_n=None keeps every gap. The PDF/DOCX narratives slice to their
    # own top-N and title it "Top N of <total>".
    gap = analyze_gaps(tier_map, notes=notes_map, target_tier=target_tier, top_n=None)

    client_name = client.legal_name
    if client_name == "(pending intake)":
        client_name = None

    # Filename version: same-day re-finalize -> v2, v3, ...
    today = utcnow().date()
    existing_count = db.execute(select(Deliverable).where(Deliverable.service_id == svc.id)).all()
    next_version = len(existing_count) + 1

    pdf_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="pdf",
        day=today,
        version=next_version,
    )
    xlsx_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="xlsx",
        day=today,
        version=next_version,
    )
    docx_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="docx",
        day=today,
        version=next_version,
    )

    ctx = build_csf_context(
        client_legal_name=client_name,
        service_title=svc.title,
        assessment=assessment,
        answers=answers,
        score=score,
        gap=gap,
    )
    pdf_bytes = render_csf_pdf(ctx)
    xlsx_bytes = render_csf_xlsx(ctx)
    docx_bytes = render_csf_docx(ctx)

    pdf_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=pdf_name,
        mime_type="application/pdf",
        data=pdf_bytes,
    )
    xlsx_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=xlsx_name,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=xlsx_bytes,
    )
    from app.docx_export import DOCX_MIME

    docx_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=docx_name,
        mime_type=DOCX_MIME,
        data=docx_bytes,
    )

    summary_line = (
        f"Overall maturity: {score.overall_maturity_label}. "
        f"{score.answered_subcategories}/{score.total_subcategories} subcategories scored; "
        f"{gap.total_gap_count} gap(s) at target T{gap.target_tier}."
    )

    deliv = Deliverable(
        service_id=svc.id,
        title=f"{svc.title} v{next_version}",
        summary=summary_line,
        version=next_version,
        pdf_artifact_id=pdf_artifact.id,
        xlsx_artifact_id=xlsx_artifact.id,
        docx_artifact_id=docx_artifact.id,
        finalized_at=utcnow(),
        finalized_by=user.id,
    )
    db.add(deliv)
    db.flush()

    audit(
        db,
        action="csf.deliverable.finalized",
        target_type="deliverable",
        target_id=deliv.id,
        actor_user_id=user.id,
        details={
            "service_id": str(svc.id),
            "assessment_id": str(assessment.id),
            "assessment_version": assessment.version,
            "version": next_version,
            "overall_maturity_label": score.overall_maturity_label,
            "average_tier": score.average_tier,
            "coverage_pct": score.coverage_pct,
            "gap_count": gap.total_gap_count,
        },
    )
    assessment.documents_stale = False  # Work Order C3
    db.commit()
    db.refresh(deliv)
    return _serialize_deliverable(db, deliv)


@router.get(
    "/services/{service_id}/deliverables/latest",
    response_model=DeliverableResponse,
    summary="Most recent CSF deliverable for a service (admin)",
)
def latest_csf_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> DeliverableResponse:
    # Deliverables are admin-only (Work Order A1): clients never see or
    # download them in-app.
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    deliv = db.execute(
        select(Deliverable)
        .where(Deliverable.service_id == svc.id)
        .order_by(Deliverable.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if deliv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deliverable yet. Finalize one first.",
        )
    return _serialize_deliverable(db, deliv)
