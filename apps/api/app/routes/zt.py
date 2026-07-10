"""Zero Trust assessment routes (Phase 5 stage 2).

Mirrors the CSF route layout but parameterized by framework. The
framework is locked at service-create time via Service.kind:

  kind=zero_trust_cisa -> ZtFramework.CISA_ZTMM_2_0
  kind=zero_trust_dod  -> ZtFramework.DOD_ZTRA

Endpoint surface:
  POST   /zt/services
  GET    /zt/catalog?framework=cisa_ztmm_2_0|dod_ztra
  POST   /zt/services/{service_id}/assessments
  GET    /zt/services/{service_id}/assessments/latest
  PATCH  /zt/answers/{answer_id}
  POST   /zt/assessments/{assessment_id}/approve
  GET    /zt/services/{service_id}/score
  GET    /zt/services/{service_id}/gap-analysis
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.diff import diff_keyed_rows
from app.ai.engine import preview_job_payload, run_job
from app.ai.llm import LLMClient, LLMConfigurationError, LLMTimeoutError
from app.audit import audit
from app.db.locks import RunInProgressError, run_lock
from app.db.session import get_db
from app.dependencies import current_client, current_user, require_role
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.client import Client
from app.models.deliverable import Deliverable
from app.models.questionnaire import Question
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.models.zt_assessment import (
    ZtAnswer,
    ZtAssessment,
    ZtAssessmentStatus,
    ZtFramework,
)
from app.routes.artifacts import _storage_dep
from app.schemas.tech_debt import DeliverableResponse
from app.schemas.zt import (
    CatalogCapability,
    CatalogPillar,
    CatalogResponse,
    CatalogStage,
    GapAnalysisResponse,
    GapItem,
    PillarScore,
    RoadmapEntry,
    ZtAnswerPatch,
    ZtAnswerResponse,
    ZtAssessmentResponse,
    ZtCapabilityChange,
    ZtInterviewQuestion,
    ZtQuestionnaireResponse,
    ZtRunAiResponse,
    ZtScoreSummary,
    ZtSelfAssessmentSubmit,
    ZtServiceCreateRequest,
    ZtServiceResponse,
)
from app.storage import StorageBackend
from app.tech_debt.filename import (
    SERVICE_SLUG_ZT_CISA,
    SERVICE_SLUG_ZT_DOD,
    deliverable_filename,
)
from app.tenant import (
    require_artifact_in_tenant,
    require_service_in_tenant,
    require_zt_assessment_in_tenant,
)
from app.zt.catalog import (
    all_codes,
    capabilities,
    pillars,
)
from app.zt.exporters import build_context as build_zt_context
from app.zt.exporters import render_docx as render_zt_docx
from app.zt.exporters import render_html_dashboard as render_zt_html
from app.zt.exporters import render_pdf as render_zt_pdf
from app.zt.exporters import render_xlsx as render_zt_xlsx
from app.zt.maturity import ZtFrameworkCode, level_count, stage_definitions
from app.zt.scoring import DEFAULT_TARGET_STAGE, analyze_gaps, build_roadmap
from app.zt.scoring import compute as compute_score

router = APIRouter(prefix="/zt", tags=["zt"])

_admin_required = Depends(require_role(UserRole.ADMIN))


# ---------------------------------------------------------------------------
# Framework <-> ServiceKind mapping
# ---------------------------------------------------------------------------

_SERVICE_KIND_TO_FRAMEWORK: dict[ServiceKind, ZtFramework] = {
    ServiceKind.ZERO_TRUST_CISA: ZtFramework.CISA_ZTMM_2_0,
    ServiceKind.ZERO_TRUST_DOD: ZtFramework.DOD_ZTRA,
}


def _framework_for_kind(kind: ServiceKind) -> ZtFramework:
    try:
        return _SERVICE_KIND_TO_FRAMEWORK[kind]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Service kind must be zero_trust_cisa or zero_trust_dod.",
        ) from exc


def _to_catalog_framework(fw: ZtFramework) -> ZtFrameworkCode:
    return (
        ZtFrameworkCode.CISA_ZTMM_2_0
        if fw == ZtFramework.CISA_ZTMM_2_0
        else ZtFrameworkCode.DOD_ZTRA
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_answers(rows: Iterable[ZtAnswer]) -> list[ZtAnswerResponse]:
    ordered = sorted(rows, key=lambda r: r.capability_code)
    return [ZtAnswerResponse.model_validate(r, from_attributes=True) for r in ordered]


def _client_target_stage(db: Session, service_id: uuid.UUID) -> int | None:
    """The ZT target stage the client chose at intake, via the source request.

    Lets the admin workspace default its gap target to the client's goal
    instead of a hardcoded stage.
    """
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.zt_target_stage if sr is not None else None


def _resolve_gap_targets(
    db: Session,
    service_id: uuid.UUID,
    rows: Iterable[ZtAnswer],
    valid: set[str],
    *,
    target_stage: int | None = None,
) -> tuple[int, dict[str, int | None]]:
    """Resolve the two target inputs `analyze_gaps` consumes, identically for
    the dashboard gap endpoint and the deliverable finalize path so the two
    can never disagree.

    The B-1 defect was finalize computing gaps against the hardcoded default
    target while the dashboard used the client's real target; this single
    helper is the fix -- both call sites go through here.

    Returns ``(engagement_target_stage, per_capability_targets)``. Precedence
    for the engagement-level stage:

    1. an explicit ``target_stage`` override (the dashboard's query param),
    2. the client's chosen ZT target on the originating ``ServiceRequest``,
    3. ``DEFAULT_TARGET_STAGE``.

    Per-capability targets always override the engagement value inside
    ``analyze_gaps``.
    """
    targets: dict[str, int | None] = {
        r.capability_code: r.target_stage for r in rows if r.capability_code in valid
    }
    if target_stage is None:
        target_stage = _client_target_stage(db, service_id) or DEFAULT_TARGET_STAGE
    return target_stage, targets


def _serialize_assessment(db: Session, a: ZtAssessment) -> ZtAssessmentResponse:
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    return ZtAssessmentResponse(
        id=a.id,
        service_id=a.service_id,
        framework=a.framework,
        version=a.version,
        status=a.status,
        approved_at=a.approved_at,
        approved_by=a.approved_by,
        documents_stale=a.documents_stale,
        answers=_serialize_answers(rows),
        client_target_stage=_client_target_stage(db, a.service_id),
    )


def _latest_assessment(db: Session, service_id: uuid.UUID) -> ZtAssessment | None:
    return db.execute(
        select(ZtAssessment)
        .where(ZtAssessment.service_id == service_id)
        .order_by(ZtAssessment.version.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.post(
    "/services",
    response_model=ZtServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a Zero Trust assessment service (admin)",
)
def create_zt_service(
    body: ZtServiceCreateRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtServiceResponse:
    framework = _framework_for_kind(body.kind)
    svc = Service(
        kind=body.kind,
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
        action="zt.service.opened",
        target_type="service",
        target_id=svc.id,
        actor_user_id=user.id,
        details={"title": svc.title, "framework": framework.value},
    )
    db.commit()
    db.refresh(svc)
    return ZtServiceResponse.model_validate(svc, from_attributes=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Zero Trust reference catalog",
)
def get_catalog(
    _user: Annotated[User, Depends(current_user)],
    framework: Annotated[
        ZtFramework,
        Query(description="cisa_ztmm_2_0 or dod_ztra"),
    ] = ZtFramework.CISA_ZTMM_2_0,
) -> CatalogResponse:
    cat_fw = _to_catalog_framework(framework)
    pillar_rows: list[CatalogPillar] = []
    for p in pillars(cat_fw):
        caps = [
            CatalogCapability(
                code=c.code,
                pillar_code=c.pillar_code,
                name=c.name,
                outcome=c.outcome,
            )
            for c in capabilities(cat_fw)
            if c.pillar_code == p.code
        ]
        pillar_rows.append(
            CatalogPillar(
                code=p.code,
                name=p.name,
                purpose=p.purpose,
                capabilities=caps,
            )
        )
    stages = [
        CatalogStage(
            stage=int(d.stage),
            label=d.label,
            description=d.description,
        )
        for d in stage_definitions(cat_fw)
    ]
    total = sum(len(p.capabilities) for p in pillar_rows)
    return CatalogResponse(
        framework=framework,
        pillars=pillar_rows,
        stages=stages,
        total_capabilities=total,
    )


_FRAMEWORK_TO_QUESTION_KEY: dict[ZtFramework, str] = {
    ZtFramework.CISA_ZTMM_2_0: "zt-cisa",
    ZtFramework.DOD_ZTRA: "zt-dod",
}


@router.get(
    "/services/{service_id}/questionnaire",
    response_model=ZtQuestionnaireResponse,
    summary="Verbatim ZT interview prompts for the service's framework",
)
def get_interview_questionnaire(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtQuestionnaireResponse:
    """Framework-resolved verbatim interview prompts (read-only, Work Order C8).

    Each prompt carries the ZT capability hints it informs so the workspace can
    surface it alongside the per-capability scoring grid.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    framework = _framework_for_kind(svc.kind)
    framework_key = _FRAMEWORK_TO_QUESTION_KEY[framework]
    rows = (
        db.execute(
            select(Question)
            .where(Question.framework_key == framework_key)
            .order_by(Question.order_index)
        )
        .scalars()
        .all()
    )
    return ZtQuestionnaireResponse(
        framework_key=framework_key,
        framework=framework,
        questions=[
            ZtInterviewQuestion(
                external_id=q.external_id,
                section_name=q.pillar,
                order_index=q.order_index,
                stem=q.stem,
                cues=list(q.cues or []),
                capabilities=list(q.framework_activities or []),
            )
            for q in rows
        ],
    )


def _llm_dep() -> LLMClient:
    # FIX A-5: a misconfigured live LLM (missing key / unimplemented provider)
    # raises the typed LLMConfigurationError; surface it through the API's typed
    # error convention rather than a generic "internal error".
    try:
        return LLMClient.from_settings()
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post(
    "/services/{service_id}/run-ai",
    response_model=ZtRunAiResponse,
    summary="Run the zt_score AI job: suggest current + target per capability (admin)",
)
def run_ai(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    preview: bool = False,
) -> ZtRunAiResponse | JSONResponse:
    """The ZT 'Run AI'. Suggests a current and target maturity level per
    capability (on the framework's own scale) plus per-pillar narratives. AI
    suggests; locked rows are untouched; code does the pillar roll-up + roadmap.
    Returns a 'what changed' list.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Zero Trust service not found."
        )
    # FIX E-3: hold the per-service run lock across auto-create + read-modify-write
    # + the provider call. Survives the db.rollback() E-1 does (see app/db/locks.py).
    try:
        with run_lock(db, "zt_run_ai", svc.id):
            return _zt_run_ai_locked(svc, user, client, db, llm, preview=preview)
    except RunInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Run AI is already in progress for this assessment.",
        ) from exc


def _zt_run_ai_locked(
    svc: Service,
    user: User,
    client: Client,
    db: Session,
    llm: LLMClient,
    *,
    preview: bool = False,
) -> ZtRunAiResponse | JSONResponse:
    # FIX F-2: create the draft (seeding rows) on first Run AI when none exists,
    # exactly what "Start assessment" does — the manual Start button still works.
    # The open-draft guard (E-3) makes this idempotent, so a re-run does not mint
    # a second version.
    a, created = _get_or_create_draft(db, svc, client.id, user.id)
    if created:
        # Persist the fresh draft + rows BEFORE the E-1 rollback below, which
        # would otherwise discard them.
        db.commit()
        db.refresh(a)
    if a.status in (ZtAssessmentStatus.APPROVED, ZtAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment is locked."
        )

    cat_fw = _to_catalog_framework(a.framework)
    max_stage = level_count(cat_fw)
    valid = all_codes(cat_fw)
    rows = {
        r.capability_code: r
        for r in db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
        if r.capability_code in valid
    }
    locked_keys = frozenset(code for code, r in rows.items() if r.locked)

    def _snap() -> dict[str, dict]:
        return {
            code: {"maturity_stage": r.maturity_stage, "target_stage": r.target_stage}
            for code, r in rows.items()
        }

    before = _snap()

    def _coerce(v: object) -> int | None:
        try:
            iv = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return iv if 1 <= iv <= max_stage else None

    client_org = None if client.legal_name == "(pending intake)" else client.legal_name
    zt_inputs = {
        "framework": a.framework.value,
        "capabilities": sorted(rows),
        "answers": {
            code: {"notes": r.notes, "current": r.maturity_stage} for code, r in rows.items()
        },
    }
    # FIX H-6: a dry run. Build the exact payload, redact it, and hand it back
    # WITHOUT calling the provider and without writing an llm_calls row. This is
    # the only place an operator can answer "what client data leaves the
    # platform?" before it actually leaves -- the spec promises that, and it is
    # the first question a FedRAMP assessor asks. Returning a JSONResponse
    # bypasses `response_model`, so the preview shape need not pretend to be a
    # ZtRunAiResponse.
    if preview:
        return JSONResponse(
            preview_job_payload(
                "zt_score",
                inputs=zt_inputs,
                client_org_name=client_org,
            )
        )

    # FIX E-1a: inputs are fully materialized and there are no pending writes, so
    # return the pooled connection to the pool across the (synchronous) provider
    # call. Capture the ids BEFORE rollback (which expires the ORM objects), so
    # reading them as args doesn't reload + re-check-out a connection.
    run_uid, run_sid, run_cid = user.id, svc.id, client.id
    db.rollback()
    try:
        result = run_job(
            db,
            llm,
            "zt_score",
            inputs=zt_inputs,
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
    data = result.data if isinstance(result.data, dict) else {}

    for sugg in data.get("capabilities", []):
        if not isinstance(sugg, dict):
            continue
        row = rows.get(sugg.get("code"))
        if row is None or row.locked:
            continue
        cur = _coerce(sugg.get("current"))
        if cur is not None:
            row.maturity_stage = cur
        tgt = _coerce(sugg.get("target"))
        if tgt is not None:
            row.target_stage = tgt
        row.answered_by = user.id
        row.answered_at = utcnow()

    db.flush()
    after = _snap()
    diffs = diff_keyed_rows(
        before, after, ["maturity_stage", "target_stage"], locked_keys=locked_keys
    )
    changes = [
        ZtCapabilityChange(capability_code=d.key, field=ch.field, old=ch.old, new=ch.new)
        for d in diffs
        for ch in d.changes
    ]
    narratives = data.get("pillar_narratives")
    narratives = narratives if isinstance(narratives, dict) else {}

    # FIX E-4: persist the narrative work instead of rendering it once and
    # dropping it. A reload used to lose all of this, which pushed consultants
    # into re-running the AI (and re-paying for it) just to read it again.
    a.narratives = {
        "pillar_narratives": {str(k): str(v) for k, v in narratives.items()},
        "executive_summary": (data.get("executive_summary") or None),
        "roadmap_summary": (data.get("roadmap_summary") or None),
    }

    a.documents_stale = True  # Work Order C3
    audit(
        db,
        action="zt.run_ai",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"changed_rows": len(diffs)},
    )
    db.commit()

    answers_out = [
        ZtAnswerResponse.model_validate(r, from_attributes=True)
        for r in sorted(rows.values(), key=lambda r: r.capability_code)
    ]
    return ZtRunAiResponse(
        changed=changes,
        answers=answers_out,
        pillar_narratives={str(k): str(v) for k, v in narratives.items()},
        executive_summary=(data.get("executive_summary") or None),
        roadmap_summary=(data.get("roadmap_summary") or None),
        mode=llm.mode,
    )


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


@router.post(
    "/services/{service_id}/assessments",
    response_model=ZtAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft Zero Trust assessment (admin)",
)
def create_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    # FIX E-3: open-draft guard — a double-click returns the existing draft
    # instead of minting a fresh version.
    assessment, _created = _get_or_create_draft(db, svc, client.id, user.id)
    db.commit()
    db.refresh(assessment)
    return _serialize_assessment(db, assessment)


def _create_draft_assessment(
    db: Session, svc: Service, client_id: uuid.UUID, user_id: uuid.UUID
) -> ZtAssessment:
    """Mint a new draft ZT assessment + its per-capability answer grid (no commit)."""
    framework = _framework_for_kind(svc.kind)
    cat_fw = _to_catalog_framework(framework)
    prior = _latest_assessment(db, svc.id)
    version = (prior.version + 1) if prior else 1
    assessment = ZtAssessment(
        service_id=svc.id,
        client_id=client_id,
        framework=framework,
        version=version,
        status=ZtAssessmentStatus.DRAFT,
    )
    db.add(assessment)
    db.flush()
    for cap in capabilities(cat_fw):
        db.add(
            ZtAnswer(
                assessment_id=assessment.id,
                client_id=client_id,
                capability_code=cap.code,
            )
        )
    audit(
        db,
        action="zt.assessment.created",
        target_type="zt_assessment",
        target_id=assessment.id,
        actor_user_id=user_id,
        details={
            "service_id": str(svc.id),
            "version": version,
            "framework": framework.value,
        },
    )
    return assessment


def _get_or_create_draft(
    db: Session, svc: Service, client_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[ZtAssessment, bool]:
    """Return the current open draft (idempotent), else mint a new one.

    ``bool`` is True when a new draft was created. Only DRAFT is "open"; a
    submitted/approved/released latest means the next create mints a new version.
    """
    prior = _latest_assessment(db, svc.id)
    if prior is not None and prior.status == ZtAssessmentStatus.DRAFT:
        return prior, False
    return _create_draft_assessment(db, svc, client_id, user_id), True


@router.get(
    "/services/{service_id}/assessments/latest",
    response_model=ZtAssessmentResponse,
    summary="Most recent Zero Trust assessment",
)
def latest_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if user.role != UserRole.ADMIN and a.status != ZtAssessmentStatus.RELEASED:
        # FIX G-1: v1 delivers reports OUT OF BAND -- the consultant downloads the
        # deliverable and shares it outside the app. No route ever assigns
        # RELEASED (only scripts/seed_demo.py does), so this gate is permanently
        # closed to clients by design. The enum member is kept (removing it needs a
        # migration); the message no longer promises a release that cannot happen.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Zero Trust assessments are not viewable in-app; your consultant will "
                "deliver your report directly."
            ),
        )
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Answer editing
# ---------------------------------------------------------------------------


@router.patch(
    "/answers/{answer_id}",
    response_model=ZtAnswerResponse,
    summary="Inline-update a single capability answer (admin)",
)
def patch_answer(
    answer_id: uuid.UUID,
    body: ZtAnswerPatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(ZtAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(ZtAssessment, row.assessment_id)
    if a is None or a.status in (
        ZtAssessmentStatus.APPROVED,
        ZtAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assessment is locked.",
        )
    if "maturity_stage" in data and data["maturity_stage"] is not None:
        s = int(data["maturity_stage"])
        # Stages are 1..level_count for the framework (CISA 1-4, DoD 1-3).
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= s <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"maturity_stage must be 1-{max_stage}.",
            )
        row.maturity_stage = s
    elif "maturity_stage" in data:
        row.maturity_stage = None
    if "target_stage" in data and data["target_stage"] is not None:
        ts = int(data["target_stage"])
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= ts <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"target_stage must be 1-{max_stage}.",
            )
        row.target_stage = ts
    elif "target_stage" in data:
        row.target_stage = None
    if "notes" in data:
        row.notes = data["notes"]
    if "evidence_artifact_id" in data:
        aid = data["evidence_artifact_id"]
        # FIX C-8: validate the evidence link — 404 on a missing or cross-tenant
        # artifact id (no-oracle), instead of leaking it back / 500'ing.
        if aid is not None:
            require_artifact_in_tenant(db, aid, client.id)
        row.evidence_artifact_id = aid
    if data.get("locked") is not None:
        row.locked = bool(data["locked"])
    row.answered_by = user.id
    row.answered_at = utcnow()
    audit(
        db,
        action="zt.answer.updated",
        target_type="zt_answer",
        target_id=row.id,
        actor_user_id=user.id,
        details={
            "capability_code": row.capability_code,
            "fields": sorted(data.keys()),
        },
    )
    db.commit()
    db.refresh(row)
    return ZtAnswerResponse.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Client self-assessment (client fills their own draft, then submits for review)
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/self-assessment",
    response_model=ZtAssessmentResponse,
    summary="The client's own assessment for this service (any status)",
)
def get_self_assessment(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    """Read the client's own assessment so they can fill the questionnaire.

    Tenant-scoped, so a client only ever reaches their own. The score/gap/
    deliverable stay admin-only until the report is released.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    return _serialize_assessment(db, assessment)


@router.patch(
    "/self-assessment/answers/{answer_id}",
    response_model=ZtAnswerResponse,
    summary="Client updates one answer on their own draft self-assessment",
)
def patch_self_assessment_answer(
    answer_id: uuid.UUID,
    body: ZtAnswerPatch,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(ZtAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(ZtAssessment, row.assessment_id)
    if a is None or a.status != ZtAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your self-assessment is no longer editable.",
        )
    if "maturity_stage" in data and data["maturity_stage"] is not None:
        s = int(data["maturity_stage"])
        # Stages are 1..level_count for the framework (CISA 1-4, DoD 1-3).
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= s <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"maturity_stage must be 1-{max_stage}.",
            )
        row.maturity_stage = s
    elif "maturity_stage" in data:
        row.maturity_stage = None
    if "notes" in data:
        row.notes = data["notes"]
    row.answered_by = user.id
    row.answered_at = utcnow()
    db.commit()
    db.refresh(row)
    return ZtAnswerResponse.model_validate(row, from_attributes=True)


@router.post(
    "/services/{service_id}/self-assessment/submit",
    response_model=ZtAssessmentResponse,
    summary="Client submits their self-assessment for admin review",
)
def submit_self_assessment(
    service_id: uuid.UUID,
    body: ZtSelfAssessmentSubmit,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if a.status != ZtAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This self-assessment has already been submitted.",
        )
    if body.target_stage is not None and svc.source_request_id is not None:
        sr = db.get(ServiceRequest, svc.source_request_id)
        if sr is not None:
            sr.zt_target_stage = body.target_stage
    a.status = ZtAssessmentStatus.SUBMITTED
    audit(
        db,
        action="zt.self_assessment.submitted",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"service_id": str(svc.id), "version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


@router.post(
    "/assessments/{assessment_id}/approve",
    response_model=ZtAssessmentResponse,
    summary="Approve the Zero Trust assessment (admin)",
)
def approve_assessment(
    assessment_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    a = require_zt_assessment_in_tenant(db, assessment_id, client.id)
    if a.status == ZtAssessmentStatus.APPROVED:
        return _serialize_assessment(db, a)
    if a.status == ZtAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already released.",
        )
    a.status = ZtAssessmentStatus.APPROVED
    a.approved_at = utcnow()
    a.approved_by = user.id
    audit(
        db,
        action="zt.assessment.approved",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version, "framework": a.framework.value},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Scoring + gap
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/score",
    response_model=ZtScoreSummary,
    summary="Roll-up score for the latest Zero Trust assessment (admin)",
)
def score_latest(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtScoreSummary:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    cat_fw = _to_catalog_framework(a.framework)
    valid = all_codes(cat_fw)
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in rows if r.capability_code in valid
    }
    score = compute_score(cat_fw, answers)
    return ZtScoreSummary(
        assessment_id=a.id,
        version=a.version,
        framework=a.framework,
        total_capabilities=score.total_capabilities,
        answered_capabilities=score.answered_capabilities,
        coverage_pct=score.coverage_pct,
        average_stage=score.average_stage,
        maturity_pct=score.maturity_pct,
        overall_stage_label=score.overall_stage_label,
        by_pillar=[
            PillarScore(
                pillar_code=ps.pillar_code,
                pillar_name=ps.pillar_name,
                capability_count=ps.capability_count,
                answered_count=ps.answered_count,
                average_stage=ps.average_stage,
                maturity_pct=ps.maturity_pct,
                coverage_pct=ps.coverage_pct,
                weakest_capability_codes=list(ps.weakest_capability_codes),
            )
            for ps in score.by_pillar
        ],
    )


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
    target_stage: int | None = None,
    top_n: int = 20,
) -> GapAnalysisResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    cat_fw = _to_catalog_framework(a.framework)
    valid = all_codes(cat_fw)
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in rows if r.capability_code in valid
    }
    notes: dict[str, str | None] = {
        r.capability_code: r.notes for r in rows if r.capability_code in valid
    }
    target_stage, targets = _resolve_gap_targets(db, svc.id, rows, valid, target_stage=target_stage)
    analysis = analyze_gaps(
        cat_fw,
        answers,
        notes=notes,
        target_stage=target_stage,
        targets=targets,
        top_n=top_n,
    )
    roadmap = build_roadmap(analysis.gaps)
    return GapAnalysisResponse(
        assessment_id=a.id,
        version=a.version,
        framework=a.framework,
        target_stage=analysis.target_stage,
        target_label=analysis.target_label,
        total_gap_count=analysis.total_gap_count,
        unscored_count=len(analysis.unscored_codes),
        gap_count_by_pillar=analysis.gap_count_by_pillar,
        gaps=[
            GapItem(
                code=g.code,
                pillar_code=g.pillar_code,
                pillar_name=g.pillar_name,
                name=g.name,
                outcome=g.outcome,
                current_stage=g.current_stage,
                target_stage=g.target_stage,
                gap_size=g.gap_size,
                priority_score=g.priority_score,
                notes=g.notes,
            )
            for g in analysis.gaps
        ],
        roadmap=[
            RoadmapEntry(
                month=it.month,
                code=it.code,
                pillar_code=it.pillar_code,
                pillar_name=it.pillar_name,
                name=it.name,
                current_stage=it.current_stage,
                target_stage=it.target_stage,
                priority_score=it.priority_score,
            )
            for it in roadmap
        ],
    )


# ---------------------------------------------------------------------------
# Deliverables
# ---------------------------------------------------------------------------


_SERVICE_SLUG_BY_FRAMEWORK: dict[ZtFramework, str] = {
    ZtFramework.CISA_ZTMM_2_0: SERVICE_SLUG_ZT_CISA,
    ZtFramework.DOD_ZTRA: SERVICE_SLUG_ZT_DOD,
}


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
    html_title = None
    if deliv.html_artifact_id:
        a = db.get(Artifact, deliv.html_artifact_id)
        html_title = a.title if a else None
    return DeliverableResponse(
        id=deliv.id,
        service_id=deliv.service_id,
        title=deliv.title,
        summary=deliv.summary,
        version=deliv.version,
        pdf_artifact_id=deliv.pdf_artifact_id,
        xlsx_artifact_id=deliv.xlsx_artifact_id,
        docx_artifact_id=deliv.docx_artifact_id,
        html_artifact_id=deliv.html_artifact_id,
        pdf_filename=pdf_title,
        xlsx_filename=xlsx_title,
        docx_filename=docx_title,
        html_filename=html_title,
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
        stage="zt.deliverable",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/services/{service_id}/deliverables/finalize",
    response_model=DeliverableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Render PDF + XLSX deliverable from the latest approved ZT assessment (admin)",
)
def finalize_zt_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> DeliverableResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if assessment.status not in (
        ZtAssessmentStatus.APPROVED,
        ZtAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment must be approved before finalizing the deliverable.",
        )
    cat_fw = _to_catalog_framework(assessment.framework)
    valid = all_codes(cat_fw)
    answers = (
        db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == assessment.id)).scalars().all()
    )
    stage_map: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in answers if r.capability_code in valid
    }
    notes_map: dict[str, str | None] = {
        r.capability_code: r.notes for r in answers if r.capability_code in valid
    }
    score = compute_score(cat_fw, stage_map)
    # B-1: resolve the SAME target the dashboard resolves (per-capability
    # targets + the client's engagement-level goal) so the signed deliverable
    # can never contradict the gap count the consultant reviewed on screen.
    target_stage, targets = _resolve_gap_targets(db, svc.id, answers, valid)
    # B-4: the XLSX Gap Plan sheet must carry the FULL gap list, not a top-20
    # slice. top_n=None keeps every gap; the PDF/DOCX narratives slice to their
    # own top-N and title it "Top N of <total>".
    gap = analyze_gaps(
        cat_fw,
        stage_map,
        notes=notes_map,
        target_stage=target_stage,
        targets=targets,
        top_n=None,
    )
    # B-5: sequence the prioritized gaps into a remediation roadmap so the
    # executive deliverable carries the roadmap the spec promises.
    roadmap = build_roadmap(gap.gaps)

    client_name = client.legal_name
    if client_name == "(pending intake)":
        client_name = None

    today = utcnow().date()
    existing = db.execute(select(Deliverable).where(Deliverable.service_id == svc.id)).all()
    next_version = len(existing) + 1

    service_slug = _SERVICE_SLUG_BY_FRAMEWORK[assessment.framework]
    pdf_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="pdf",
        day=today,
        version=next_version,
    )
    xlsx_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="xlsx",
        day=today,
        version=next_version,
    )
    docx_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="docx",
        day=today,
        version=next_version,
    )
    html_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="html",
        day=today,
        version=next_version,
    )

    ctx = build_zt_context(
        client_legal_name=client_name,
        service_title=svc.title,
        framework=cat_fw,
        assessment=assessment,
        answers=answers,
        score=score,
        gap=gap,
        roadmap=roadmap,
    )
    pdf_bytes = render_zt_pdf(ctx)
    xlsx_bytes = render_zt_xlsx(ctx)
    docx_bytes = render_zt_docx(ctx)
    html_bytes = render_zt_html(ctx)

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
    html_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=html_name,
        mime_type="text/html",
        data=html_bytes,
    )

    summary_line = (
        f"Overall stage: {score.overall_stage_label}. "
        f"{score.answered_capabilities}/{score.total_capabilities} capabilities scored; "
        f"{gap.total_gap_count} gap(s) at target S{gap.target_stage}."
    )

    deliv = Deliverable(
        service_id=svc.id,
        title=f"{svc.title} v{next_version}",
        summary=summary_line,
        version=next_version,
        pdf_artifact_id=pdf_artifact.id,
        xlsx_artifact_id=xlsx_artifact.id,
        docx_artifact_id=docx_artifact.id,
        html_artifact_id=html_artifact.id,
        finalized_at=utcnow(),
        finalized_by=user.id,
    )
    db.add(deliv)
    db.flush()

    audit(
        db,
        action="zt.deliverable.finalized",
        target_type="deliverable",
        target_id=deliv.id,
        actor_user_id=user.id,
        details={
            "service_id": str(svc.id),
            "assessment_id": str(assessment.id),
            "framework": assessment.framework.value,
            "assessment_version": assessment.version,
            "version": next_version,
            "overall_stage_label": score.overall_stage_label,
            "average_stage": score.average_stage,
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
    summary="Most recent ZT deliverable for a service (admin)",
)
def latest_zt_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> DeliverableResponse:
    # Deliverables are admin-only (Work Order A1): clients never see or
    # download them in-app.
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
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
