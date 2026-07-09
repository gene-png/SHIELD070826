"""Risk Register routes (Work Order E).

A derived, admin-only, point-in-time deliverable. Admin-only and cross-tenant:
the client id is named in the path (like /admin/services/{id}); no X-Client-Id.

  GET  /risk/clients/{cid}/gate
  POST /risk/clients/{cid}/register/generate
  GET  /risk/clients/{cid}/register/latest
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.engine import run_job
from app.ai.llm import LLMClient, LLMConfigurationError, LLMTimeoutError
from app.attack.catalog import all_codes as attack_all_codes
from app.audit import audit
from app.db.session import get_db
from app.dependencies import require_role
from app.docx_export import DOCX_MIME
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.attack_assessment import AttackAssessment, AttackCoverage
from app.models.client import Client
from app.models.csf_assessment import CsfAnswer, CsfAssessment
from app.models.risk_register import RiskEntry, RiskRegister
from app.models.user import User, UserRole
from app.models.zt_assessment import ZtAnswer, ZtAssessment
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
    RiskEntryResponse,
    RiskGateStatus,
    RiskRegisterResponse,
)
from app.storage import StorageBackend

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


def _gate(db: Session, client_id: uuid.UUID) -> RiskGateStatus:
    has_attack = _latest(db, AttackAssessment, client_id) is not None
    has_csf = _latest(db, CsfAssessment, client_id) is not None
    has_zt = _latest(db, ZtAssessment, client_id) is not None
    unlocked = has_attack and (has_csf or has_zt)
    missing: list[str] = []
    if not has_attack:
        missing.append("a MITRE ATT&CK coverage mapping")
    if not (has_csf or has_zt):
        missing.append("a CSF or Zero Trust assessment")
    return RiskGateStatus(
        unlocked=unlocked,
        has_attack=has_attack,
        has_csf=has_csf,
        has_zt=has_zt,
        missing=missing,
    )


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
        for r in (
            db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == csf.id)).scalars().all()
        ):
            valid_controls.add(r.subcategory_code)
            if r.maturity_tier is not None and r.maturity_tier < 3:
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
    register = RiskRegister(client_id=cid, version=next_version, generated_by=admin.id)
    db.add(register)
    db.flush()
    if prior is not None:
        prior.superseded_by = register.id

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
    entries = (
        db.execute(
            select(RiskEntry).where(RiskEntry.register_id == reg.id).order_by(RiskEntry.created_at)
        )
        .scalars()
        .all()
    )
    org = None if client.legal_name == "(pending intake)" else client.legal_name
    ctx = risk_exporters.build_context(client_legal_name=org, version=reg.version, entries=entries)
    base = f"Risk_Register_v{reg.version}"
    xlsx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=f"{base}.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=risk_exporters.render_xlsx(ctx),
    )
    pdf = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=f"{base}.pdf",
        mime_type="application/pdf",
        data=risk_exporters.render_pdf(ctx),
    )
    docx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=f"{base}.docx",
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


def _serialize(db: Session, register: RiskRegister) -> RiskRegisterResponse:
    entries = (
        db.execute(
            select(RiskEntry)
            .where(RiskEntry.register_id == register.id)
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

    return RiskRegisterResponse(
        id=register.id,
        client_id=register.client_id,
        version=register.version,
        generated_by=register.generated_by,
        finalized_at=register.finalized_at,
        created_at=register.created_at,
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
