"""Risk Register routes (Work Order E).

A derived, admin-only, point-in-time deliverable. Admin-only and cross-tenant:
the client id is named in the path (like /admin/services/{id}); no X-Client-Id.

  GET    /risk/clients/{cid}/gate
  POST   /risk/clients/{cid}/register/generate
  GET    /risk/clients/{cid}/register/latest
  PATCH  /risk/entries/{entry_id}
  DELETE /risk/entries/{entry_id}
  POST   /risk/clients/{cid}/register/approve
  POST   /risk/clients/{cid}/register/export
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.engine import run_job
from app.ai.llm import LLMClient, LLMConfigurationError, LLMTimeoutError
from app.attack.catalog import all_codes as attack_all_codes
from app.audit import audit
from app.db.locks import RunInProgressError, run_lock
from app.db.session import get_db
from app.dependencies import require_role
from app.docx_export import DOCX_MIME
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.attack_assessment import (
    AttackAssessment,
    AttackAssessmentStatus,
    AttackCoverage,
)
from app.models.client import Client
from app.models.csf_assessment import CsfAnswer, CsfAssessment, CsfAssessmentStatus
from app.models.risk_register import RiskEntry, RiskRegister
from app.models.service import Service
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.models.zt_assessment import ZtAnswer, ZtAssessment, ZtAssessmentStatus
from app.risk import exporters as risk_exporters
from app.risk.engine import (
    Impact,
    Likelihood,
    RecommendedAction,
    RiskAxis,
    action_counts,
    axis_counts,
    tier_counts,
    tier_for,
)
from app.routes.artifacts import _storage_dep
from app.schemas.risk import (
    RiskEntryPatch,
    RiskEntryResponse,
    RiskGateStatus,
    RiskRegisterResponse,
    RiskSource,
)
from app.storage import StorageBackend
from app.tech_debt.filename import deliverable_filename

# FIX F-3: APPROVED-or-better states per source assessment kind. The gate opens
# only when the sources have actually been approved, not merely started.
_APPROVED_ATTACK = (AttackAssessmentStatus.APPROVED, AttackAssessmentStatus.RELEASED)
_APPROVED_CSF = (CsfAssessmentStatus.APPROVED, CsfAssessmentStatus.RELEASED)
_APPROVED_ZT = (ZtAssessmentStatus.APPROVED, ZtAssessmentStatus.RELEASED)
# Default CSF target tier when the client did not pin one at intake.
_DEFAULT_CSF_TARGET_TIER = 3

router = APIRouter(prefix="/risk", tags=["risk-register"])

_admin_required = Depends(require_role(UserRole.ADMIN))


def _llm_dep() -> LLMClient:
    # FIX A-5: surface a misconfigured live LLM as a typed error, not a 500.
    try:
        return LLMClient.from_settings()
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


def _latest(db: Session, model, client_id: uuid.UUID):
    return db.execute(
        select(model)
        .where(model.client_id == client_id)
        .order_by(model.version.desc(), model.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _sources(db: Session, client_id: uuid.UUID) -> list[RiskSource]:
    """The latest source assessment per kind, with its version + status.

    FIX F-3 item 7: labels the dashboard/export with what the register was
    synthesized from and whether each source is approved.
    """
    out: list[RiskSource] = []
    for kind, model, approved_states in (
        ("attack", AttackAssessment, _APPROVED_ATTACK),
        ("csf", CsfAssessment, _APPROVED_CSF),
        ("zt", ZtAssessment, _APPROVED_ZT),
    ):
        a = _latest(db, model, client_id)
        if a is None:
            continue
        status_value = a.status.value if hasattr(a.status, "value") else str(a.status)
        out.append(
            RiskSource(
                kind=kind,
                version=a.version,
                status=status_value,
                approved=a.status in approved_states,
            )
        )
    return out


def _gate(db: Session, client_id: uuid.UUID) -> RiskGateStatus:
    attack = _latest(db, AttackAssessment, client_id)
    csf = _latest(db, CsfAssessment, client_id)
    zt = _latest(db, ZtAssessment, client_id)
    has_attack = attack is not None
    has_csf = csf is not None
    has_zt = zt is not None
    # FIX F-3: the gate now requires APPROVAL, not mere existence. CSF/ZT
    # assessments auto-create at intake, so existence unlocked the register the
    # moment an ATT&CK assessment was started — a clean-bill-of-health export
    # before anyone scored anything.
    attack_approved = attack is not None and attack.status in _APPROVED_ATTACK
    csf_approved = csf is not None and csf.status in _APPROVED_CSF
    zt_approved = zt is not None and zt.status in _APPROVED_ZT
    unlocked = attack_approved and (csf_approved or zt_approved)
    missing: list[str] = []
    if not attack_approved:
        missing.append("an approved MITRE ATT&CK coverage mapping")
    if not (csf_approved or zt_approved):
        missing.append("an approved CSF or Zero Trust assessment")
    return RiskGateStatus(
        unlocked=unlocked,
        has_attack=has_attack,
        has_csf=has_csf,
        has_zt=has_zt,
        attack_approved=attack_approved,
        csf_approved=csf_approved,
        zt_approved=zt_approved,
        missing=missing,
        sources=_sources(db, client_id),
    )


def _csf_target_tier(db: Session, csf: CsfAssessment) -> int:
    """The CSF target tier the client pinned at intake (via the source request).

    Re-derives the same value routes/csf.py::_client_target_tier resolves, but
    keyed off the assessment (the risk route has no service_id in scope). Falls
    back to 3 (below-tier-3 is the platform's default gap threshold).
    """
    svc = db.get(Service, csf.service_id)
    if svc is None or svc.source_request_id is None:
        return _DEFAULT_CSF_TARGET_TIER
    sr = db.get(ServiceRequest, svc.source_request_id)
    if sr is None or sr.csf_target_tier is None:
        return _DEFAULT_CSF_TARGET_TIER
    return sr.csf_target_tier


def _require_client(db: Session, cid: uuid.UUID) -> Client:
    client = db.get(Client, cid)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    return client


@router.get(
    "/clients/{cid}/gate",
    response_model=RiskGateStatus,
    summary="Whether the Risk Register can be generated (admin)",
)
def gate(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskGateStatus:
    _require_client(db, cid)
    return _gate(db, cid)


def _gather_findings(db: Session, client_id: uuid.UUID) -> tuple[list[dict], set[str], set[str]]:
    """Findings (one per gap) + the valid technique/control link universes.

    valid_techniques = every technique in the client's ATT&CK assessment.
    valid_controls   = CSF subcategory codes + ZT capability codes present.
    """
    findings: list[dict] = []
    valid_techniques: set[str] = set()
    valid_controls: set[str] = set()

    attack = _latest(db, AttackAssessment, client_id)
    if attack is not None:
        rows = (
            db.execute(select(AttackCoverage).where(AttackCoverage.assessment_id == attack.id))
            .scalars()
            .all()
        )
        valid_techniques = {r.technique_code for r in rows} or set(attack_all_codes())
        for r in rows:
            if r.status in ("gap", "partial"):
                findings.append(
                    {
                        "source": "coverage_finding",
                        "source_id": r.technique_code,
                        "kind": "attack",
                        "label": f"ATT&CK {r.technique_code}: {r.status}",
                    }
                )

    csf = _latest(db, CsfAssessment, client_id)
    if csf is not None:
        # FIX F-3: harvest against the client's chosen target tier, not a fixed
        # below-tier-3 threshold. (ZT already honours its per-row target below,
        # so this fixed-threshold defect was CSF-specific.)
        csf_target = _csf_target_tier(db, csf)
        for r in (
            db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == csf.id)).scalars().all()
        ):
            valid_controls.add(r.subcategory_code)
            if r.maturity_tier is not None and r.maturity_tier < csf_target:
                findings.append(
                    {
                        "source": "questionnaire_response",
                        "source_id": r.subcategory_code,
                        "kind": "csf",
                        "label": f"CSF {r.subcategory_code}: tier {r.maturity_tier}",
                    }
                )

    zt = _latest(db, ZtAssessment, client_id)
    if zt is not None:
        for r in (
            db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == zt.id)).scalars().all()
        ):
            valid_controls.add(r.capability_code)
            tgt = r.target_stage if r.target_stage is not None else 3
            if r.maturity_stage is not None and r.maturity_stage < tgt:
                findings.append(
                    {
                        "source": "questionnaire_response",
                        "source_id": r.capability_code,
                        "kind": "zt",
                        "label": f"ZT {r.capability_code}: stage {r.maturity_stage}",
                    }
                )

    return findings, valid_techniques, valid_controls


def _enum_or_none(enum_cls, value):
    """Coerce ``value`` to a member of ``enum_cls``, normalizing display casing.

    The AI is asked for lowercase snake_case tokens, but drift (e.g. a prompt or
    model that emits display labels like "Very High" or "Very-High") must not
    silently null the field and, through it, the code-derived tier. So we lower,
    strip, and turn internal spaces/hyphens into underscores before coercing.

    It is a purely mechanical normalization: non-string values and genuinely
    unknown tokens still return None. It does NOT fuzzy-match or alias unknown
    vocabulary (e.g. "moderate" is not remapped to the "medium" likelihood, and
    "extremely_high" stays None) — it must never invent a value.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return enum_cls(normalized)
    except (ValueError, KeyError):
        return None


def _provided(value) -> bool:
    """Whether the AI actually supplied an enum value (vs. omitting it)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


class RiskRegisterGenerateResponse(RiskRegisterResponse):
    """generate() response: the register plus visibility into enum drift.

    Today a likelihood/impact token the engine doesn't recognize is silently
    coerced to None, blanking the tier with no signal to anyone. These fields
    make a future drift VISIBLE at the point of generation.
    """

    coercion_failures: int = 0
    warnings: list[str] = []


@router.post(
    "/clients/{cid}/register/generate",
    response_model=RiskRegisterGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new Risk Register version (admin)",
)
def generate(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
) -> RiskRegisterGenerateResponse:
    client = _require_client(db, cid)
    # FIX E-3: serialize concurrent generates for this client so a double-click
    # cannot mint two registers with the same version. The lock survives the
    # db.rollback() below (see app/db/locks.py); the unique constraint on
    # (client_id, version) is the DB-level backstop.
    try:
        with run_lock(db, "risk_generate", cid):
            return _generate_locked(db, llm, client, cid, admin)
    except RunInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Risk Register generation is already in progress for this client.",
        ) from exc


def _generate_locked(
    db: Session,
    llm: LLMClient,
    client: Client,
    cid: uuid.UUID,
    admin: User,
) -> RiskRegisterGenerateResponse:
    g = _gate(db, cid)
    if not g.unlocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Risk Register is locked. Missing: " + "; ".join(g.missing) + ".",
        )

    findings, valid_techniques, valid_controls = _gather_findings(db, cid)
    client_org = None if client.legal_name == "(pending intake)" else client.legal_name
    risk_inputs = {
        "findings": findings,
        "valid_techniques": sorted(valid_techniques),
        "valid_controls": sorted(valid_controls),
    }
    # FIX E-1a: inputs are materialized and there are no pending writes, so
    # return the pooled connection to the pool across the provider call; the
    # writes below re-acquire it. Capture admin.id BEFORE rollback (which expires
    # the ORM object); cid is already a plain path UUID.
    run_uid = admin.id
    db.rollback()
    try:
        result = run_job(
            db,
            llm,
            "risk_synthesize",
            inputs=risk_inputs,
            requested_by=run_uid,
            client_id=cid,
            client_org_name=client_org,
        )
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="the AI call timed out; nothing was changed",
        ) from exc
    data = result.data if isinstance(result.data, dict) else {}

    # New version; supersede the prior current one.
    prior = _latest(db, RiskRegister, cid)
    next_version = (prior.version + 1) if prior is not None else 1
    # FIX F-3 item 7: snapshot the source assessments this version synthesizes.
    sources_snapshot = [s.model_dump() for s in _sources(db, cid)]
    register = RiskRegister(
        client_id=cid,
        version=next_version,
        generated_by=admin.id,
        sources=sources_snapshot,
    )
    db.add(register)
    db.flush()
    if prior is not None:
        prior.superseded_by = register.id
        # FIX F-3: C2 lock semantics. Carry every locked (non-deleted) entry from
        # the prior version forward VERBATIM; only unlocked entries are redrafted
        # by the AI below. Same intent as csf/zt/attack Run-AI skipping locked
        # rows, adapted to this versioned deliverable.
        locked_prior = (
            db.execute(
                select(RiskEntry)
                .where(
                    RiskEntry.register_id == prior.id,
                    RiskEntry.locked.is_(True),
                    RiskEntry.deleted_at.is_(None),
                )
                .order_by(RiskEntry.created_at)
            )
            .scalars()
            .all()
        )
        for old in locked_prior:
            db.add(
                RiskEntry(
                    register_id=register.id,
                    client_id=cid,
                    title=old.title,
                    description=old.description,
                    axis=old.axis,
                    source=old.source,
                    source_id=old.source_id,
                    linked_techniques=old.linked_techniques,
                    linked_controls=old.linked_controls,
                    likelihood=old.likelihood,
                    impact=old.impact,
                    tier=old.tier,
                    compensating_controls=old.compensating_controls,
                    residual_risk=old.residual_risk,
                    recommended_action=old.recommended_action,
                    rationale=old.rationale,
                    origin=old.origin,
                    trust=old.trust,
                    locked=True,
                )
            )

    coercion_failures = 0
    for raw in data.get("entries", []):
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        lk = _enum_or_none(Likelihood, raw.get("likelihood"))
        im = _enum_or_none(Impact, raw.get("impact"))
        # Tier is ALWAYS code-derived, never AI-set.
        tier = tier_for(lk, im).value if (lk is not None and im is not None) else None
        techs = [t for t in (raw.get("linked_techniques") or []) if t in valid_techniques]
        controls = [c for c in (raw.get("linked_controls") or []) if c in valid_controls]
        axis = _enum_or_none(RiskAxis, raw.get("axis"))
        action = _enum_or_none(RecommendedAction, raw.get("recommended_action"))
        # An AI-supplied enum token the engine can't place -> None -> blank tier.
        # Count it so drift surfaces in the response instead of failing silently.
        if any(
            coerced is None and _provided(raw.get(key))
            for coerced, key in (
                (lk, "likelihood"),
                (im, "impact"),
                (axis, "axis"),
                (action, "recommended_action"),
            )
        ):
            coercion_failures += 1
        db.add(
            RiskEntry(
                register_id=register.id,
                client_id=cid,
                title=str(raw["title"])[:512],
                description=raw.get("description"),
                axis=axis.value if axis else None,
                source=raw.get("source"),
                source_id=raw.get("source_id"),
                linked_techniques=techs,
                linked_controls=controls,
                likelihood=lk.value if lk else None,
                impact=im.value if im else None,
                tier=tier,
                compensating_controls=raw.get("compensating_controls"),
                residual_risk=raw.get("residual_risk"),
                recommended_action=action.value if action else None,
                rationale=raw.get("rationale"),
                origin="ai_generated",
                trust="admin_assisted",
            )
        )

    audit(
        db,
        action="risk_register.generated",
        target_type="risk_register",
        target_id=register.id,
        actor_user_id=admin.id,
        details={
            "version": next_version,
            "findings": len(findings),
            "coercion_failures": coercion_failures,
        },
    )
    db.commit()

    warnings: list[str] = []
    if coercion_failures:
        noun = "entry" if coercion_failures == 1 else "entries"
        warnings.append(
            f"{coercion_failures} AI-drafted risk {noun} had a "
            "likelihood/impact/axis/action value the risk engine did not "
            "recognize; it was dropped and the tier may be blank. Check the AI "
            "prompt and app/risk/engine.py enums for vocabulary drift."
        )
    base = _serialize(db, register)
    return RiskRegisterGenerateResponse(
        **base.model_dump(),
        coercion_failures=coercion_failures,
        warnings=warnings,
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

    key = f"risk_register/{user.id}/{uuid.uuid4()}/{filename}"
    storage.put(key, data, content_type=mime_type)
    art = Artifact(
        client_id=client_id,
        title=filename,
        file_storage_key=key,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        origin=ArtifactOrigin.CONSULTANT_APPROVED,
        stage="risk_register.export",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/clients/{cid}/register/export",
    response_model=RiskRegisterResponse,
    summary="Render + store the current Risk Register as XLSX/PDF/Word (admin)",
)
def export(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> RiskRegisterResponse:
    client = _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generate a Risk Register before exporting.",
        )
    # FIX F-3 item 6: Export unlocks only AFTER the register version is approved.
    # Symmetric with every other service's approve->export gate.
    if reg.approved_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve the Risk Register version before exporting.",
        )
    entries = (
        db.execute(
            select(RiskEntry)
            .where(RiskEntry.register_id == reg.id, RiskEntry.deleted_at.is_(None))
            .order_by(RiskEntry.created_at)
        )
        .scalars()
        .all()
    )
    org = None if client.legal_name == "(pending intake)" else client.legal_name
    ctx = risk_exporters.build_context(client_legal_name=org, version=reg.version, entries=entries)

    # FIX B-7 (sub-fix 4): route Risk exports through the §15.5
    # deliverable_filename convention (Company_Service{MMDDYY}[_vN].ext) like the
    # other finalize flows, instead of raw f-strings that carried no company/date.
    from datetime import date as _date

    def _rr_name(extension: str) -> str:
        return deliverable_filename(
            company=org,
            service_slug="Risk_Register",
            extension=extension,
            day=_date.today(),
            version=reg.version,
        )

    xlsx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_rr_name("xlsx"),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=risk_exporters.render_xlsx(ctx),
    )
    pdf = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_rr_name("pdf"),
        mime_type="application/pdf",
        data=risk_exporters.render_pdf(ctx),
    )
    docx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_rr_name("docx"),
        mime_type=DOCX_MIME,
        data=risk_exporters.render_docx(ctx),
    )
    reg.xlsx_artifact_id = xlsx.id
    reg.pdf_artifact_id = pdf.id
    reg.docx_artifact_id = docx.id
    reg.finalized_at = utcnow()
    audit(
        db,
        action="risk_register.exported",
        target_type="risk_register",
        target_id=reg.id,
        actor_user_id=admin.id,
        details={"version": reg.version},
    )
    db.commit()
    return _serialize(db, reg)


@router.get(
    "/clients/{cid}/register/latest",
    response_model=RiskRegisterResponse,
    summary="The current Risk Register version (admin)",
)
def latest(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskRegisterResponse:
    _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Risk Register generated yet.",
        )
    return _serialize(db, reg)


def _editable_entry(db: Session, entry_id: uuid.UUID) -> RiskEntry:
    """Load a non-deleted entry whose register version is not yet approved.

    Once a version is approved it is locked (like an approved csf/zt/attack
    assessment); further edits must go through a regenerate into a new version.
    """
    entry = db.get(RiskEntry, entry_id)
    if entry is None or entry.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk entry not found.")
    reg = db.get(RiskRegister, entry.register_id)
    if reg is not None and reg.approved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This Risk Register version is approved and locked; "
                "regenerate a new version to edit."
            ),
        )
    return entry


@router.patch(
    "/entries/{entry_id}",
    response_model=RiskEntryResponse,
    summary="Edit one Risk Register entry; tier stays code-derived (admin)",
)
def patch_entry(
    entry_id: uuid.UUID,
    body: RiskEntryPatch,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskEntryResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    entry = _editable_entry(db, entry_id)
    if "title" in data and data["title"] is not None:
        entry.title = str(data["title"])[:512]
    if "description" in data:
        entry.description = data["description"]
    if "compensating_controls" in data:
        entry.compensating_controls = data["compensating_controls"]
    if "rationale" in data:
        entry.rationale = data["rationale"]
    if "recommended_action" in data:
        action = _enum_or_none(RecommendedAction, data["recommended_action"])
        entry.recommended_action = action.value if action else None
    if "likelihood" in data:
        lk = _enum_or_none(Likelihood, data["likelihood"])
        entry.likelihood = lk.value if lk else None
    if "impact" in data:
        im = _enum_or_none(Impact, data["impact"])
        entry.impact = im.value if im else None
    if "locked" in data and data["locked"] is not None:
        entry.locked = bool(data["locked"])
    # Tier is ALWAYS code-derived from the entry's (possibly edited) likelihood
    # and impact — never accepted from the client. A `tier` key in the request
    # body is dropped by RiskEntryPatch, and we re-derive here regardless.
    lk = _enum_or_none(Likelihood, entry.likelihood)
    im = _enum_or_none(Impact, entry.impact)
    entry.tier = tier_for(lk, im).value if (lk is not None and im is not None) else None
    audit(
        db,
        action="risk_register.entry_edited",
        target_type="risk_entry",
        target_id=entry.id,
        actor_user_id=admin.id,
        details={"fields": sorted(data.keys())},
    )
    db.commit()
    db.refresh(entry)
    return RiskEntryResponse.model_validate(entry, from_attributes=True)


@router.delete(
    "/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete one Risk Register entry (admin)",
)
def delete_entry(
    entry_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    entry = _editable_entry(db, entry_id)
    entry.deleted_at = utcnow()
    audit(
        db,
        action="risk_register.entry_deleted",
        target_type="risk_entry",
        target_id=entry.id,
        actor_user_id=admin.id,
        details={"register_id": str(entry.register_id)},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/clients/{cid}/register/approve",
    response_model=RiskRegisterResponse,
    summary="Approve (lock) the current Risk Register version (admin)",
)
def approve(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskRegisterResponse:
    """FIX F-3 item 6 (July 9 decision): Generate -> review + edit -> Approve
    (locks the version) -> Export. Idempotent: re-approving a locked version is a
    no-op that returns the current state."""
    _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generate a Risk Register before approving.",
        )
    if reg.approved_at is None:
        reg.approved_at = utcnow()
        reg.approved_by = admin.id
        audit(
            db,
            action="risk_register.approved",
            target_type="risk_register",
            target_id=reg.id,
            actor_user_id=admin.id,
            details={"version": reg.version},
        )
        db.commit()
        db.refresh(reg)
    return _serialize(db, reg)


def _serialize(db: Session, register: RiskRegister) -> RiskRegisterResponse:
    entries = (
        db.execute(
            select(RiskEntry)
            .where(RiskEntry.register_id == register.id, RiskEntry.deleted_at.is_(None))
            .order_by(RiskEntry.created_at)
        )
        .scalars()
        .all()
    )
    from app.risk.engine import RiskTier

    tiers = [RiskTier(e.tier) for e in entries if e.tier]
    axes = [RiskAxis(e.axis) for e in entries if e.axis]
    actions = [RecommendedAction(e.recommended_action) for e in entries if e.recommended_action]

    def _fn(aid: uuid.UUID | None) -> str | None:
        if aid is None:
            return None
        art = db.get(Artifact, aid)
        return art.title if art else None

    sources = [RiskSource(**s) for s in (register.sources or []) if isinstance(s, dict)]
    return RiskRegisterResponse(
        id=register.id,
        client_id=register.client_id,
        version=register.version,
        generated_by=register.generated_by,
        finalized_at=register.finalized_at,
        approved_at=register.approved_at,
        approved_by=register.approved_by,
        created_at=register.created_at,
        sources=sources,
        xlsx_artifact_id=register.xlsx_artifact_id,
        pdf_artifact_id=register.pdf_artifact_id,
        docx_artifact_id=register.docx_artifact_id,
        xlsx_filename=_fn(register.xlsx_artifact_id),
        pdf_filename=_fn(register.pdf_artifact_id),
        docx_filename=_fn(register.docx_artifact_id),
        entries=[RiskEntryResponse.model_validate(e, from_attributes=True) for e in entries],
        tier_counts=tier_counts(tiers),
        axis_counts=axis_counts(axes),
        action_counts=action_counts(actions),
    )
