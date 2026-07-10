"""Admin routes.

Master Spec §15 Phase 2 acceptance:
  - "Submitting intake reflects correctly in the admin queue with the
    new-lead timestamp."
  - "All intake data round-trips correctly: client enters X, admin reads X."

Phase 2 ships the read-only queue view. Phase 3+ adds the workflow surfaces
(attach reviewer, mark final, release deliverable) on top of this.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import get_settings
from app.db.session import get_db
from app.dependencies import require_role
from app.models._common import utcnow
from app.models.artifact import Artifact
from app.models.audit_entry import AuditEntry
from app.models.client import Client
from app.models.client_domain import ClientDomain
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest, ServiceType
from app.models.user import User, UserRole
from app.schemas.admin import (
    AdminAiStatus,
    AdminAiUsageResponse,
    AdminAiUsageRow,
    AdminArtifactRow,
    AdminAuditListResponse,
    AdminAuditRow,
    AdminClientCreateRequest,
    AdminClientListResponse,
    AdminClientSummary,
    AdminDomainCreateRequest,
    AdminDomainListResponse,
    AdminDomainRow,
    AdminIntakeQueueResponse,
    AdminServiceDetail,
    AdminServiceListResponse,
    AdminServiceRequestRow,
    AdminServiceRow,
    AdminUserCreateRequest,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserSummary,
    FulfillServiceRequestResponse,
)
from app.schemas.intake import ClientProfileResponse
from app.security.email_domains import domain_of, is_generic_provider
from app.security.password import PasswordPolicyError, hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_required = Depends(require_role(UserRole.ADMIN))

# Human-readable service titles used when a request graduates to a workspace.
_SERVICE_TITLES: dict[ServiceType, str] = {
    ServiceType.TECH_DEBT: "Technical Debt Review",
    ServiceType.ZERO_TRUST_CISA: "Zero Trust (CISA ZTMM 2.0)",
    ServiceType.ZERO_TRUST_DOD: "Zero Trust (DoD ZTRA)",
    ServiceType.NIST_CSF: "NIST CSF 2.0 Assessment",
    ServiceType.ATTACK_COVERAGE: "MITRE ATT&CK Coverage",
}


@router.get(
    "/intake-queue",
    response_model=AdminIntakeQueueResponse,
    summary="Intake queue (admin)",
)
def intake_queue(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    client_id: uuid.UUID | None = None,
) -> AdminIntakeQueueResponse:
    """Cross-tenant intake queue.

    Without `client_id` filter: shows requests/artifacts from all clients
    (consultant overview). The `client` field in the response is then the
    most-recently-created tenant for display continuity; treat it as advisory.
    With `client_id`: scopes to that tenant.
    """
    if client_id is not None:
        client = db.get(Client, client_id)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found.",
            )
    else:
        client = db.execute(
            select(Client).order_by(Client.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    sr_stmt = select(ServiceRequest, User).join(User, ServiceRequest.requested_by == User.id)
    if client_id is not None:
        sr_stmt = sr_stmt.where(ServiceRequest.client_id == client_id)
    sr_stmt = sr_stmt.order_by(ServiceRequest.requested_at.desc())
    rows = db.execute(sr_stmt).all()
    service_requests: list[AdminServiceRequestRow] = []
    for sr, requester in rows:
        service_requests.append(
            AdminServiceRequestRow(
                id=sr.id,
                service_type=sr.service_type,
                requested_at=sr.requested_at,
                requested_by=AdminUserSummary.model_validate(requester, from_attributes=True),
                notes=sr.notes,
                deadline=sr.deadline,
                csf_target_tier=sr.csf_target_tier,
                csf_profile=sr.csf_profile,
                zt_target_stage=sr.zt_target_stage,
                fulfilled_service_id=sr.fulfilled_service_id,
                declined_at=sr.declined_at,
                declined_reason=sr.declined_reason,
            )
        )

    art_stmt = select(Artifact)
    if client_id is not None:
        art_stmt = art_stmt.where(Artifact.client_id == client_id)
    art_stmt = art_stmt.order_by(Artifact.uploaded_at.desc())
    artifact_rows = db.execute(art_stmt).scalars().all()
    artifacts = [AdminArtifactRow.model_validate(a, from_attributes=True) for a in artifact_rows]

    user_stmt = select(func.count()).select_from(User)
    if client_id is not None:
        user_stmt = user_stmt.where(User.client_id == client_id)
    total_users = db.execute(user_stmt).scalar_one()

    return AdminIntakeQueueResponse(
        client=(
            ClientProfileResponse.model_validate(client, from_attributes=True)
            if client is not None
            else None
        ),
        intake_completed_at=client.intake_completed_at if client else None,
        service_requests=service_requests,
        artifacts=artifacts,
        total_users=total_users,
    )


# -----------------------------------------------------------------------------
# Users (admin-only account management)
# -----------------------------------------------------------------------------


def _active_admin_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    ).scalar_one()


@router.get(
    "/users",
    response_model=AdminUserListResponse,
    summary="List all user accounts (admin)",
)
def list_users(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserListResponse:
    """Cross-tenant list of every account, newest first."""
    rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return AdminUserListResponse(
        users=[AdminUserDetail.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/users",
    response_model=AdminUserDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user account (admin)",
)
def create_user(
    body: AdminUserCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserDetail:
    """Create an admin or client account directly (the only way to mint admins).

    Admins are cross-tenant (no client_id); client users must name an existing
    client tenant.
    """
    email = body.email.strip().lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for that email.",
        )

    if body.role == UserRole.CLIENT:
        if body.client_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="client_id is required when creating a client user.",
            )
        if db.get(Client, body.client_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No client with that id.",
            )
        client_id = body.client_id
    else:  # admin: cross-tenant, never pinned to a client
        if body.client_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Admin users are cross-tenant and must not have a client_id.",
            )
        client_id = None

    try:
        password_hash = hash_password(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    user = User(
        email=email,
        password_hash=password_hash,
        role=body.role,
        display_name=body.display_name,
        title=body.title,
        client_id=client_id,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        action="user.created",
        target_type="user",
        target_id=user.id,
        actor_user_id=admin.id,
        details={"role": body.role.value, "source": "admin", "client_id": str(client_id)},
    )
    db.commit()
    db.refresh(user)
    return AdminUserDetail.model_validate(user, from_attributes=True)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user account (admin)",
)
def deactivate_user(
    user_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Deactivate (not hard-delete) an account: blocks login and starts the
    retention clock. Guards against locking the platform out of admin access."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't deactivate your own account.",
        )
    if target.role == UserRole.ADMIN and target.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Can't deactivate the last active admin.",
        )

    if target.is_active:
        target.is_active = False
        target.deactivated_at = utcnow()
        audit(
            db,
            action="user.deactivated",
            target_type="user",
            target_id=target.id,
            actor_user_id=admin.id,
            details={"role": target.role.value},
        )
        db.commit()


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminUserDetail,
    summary="Reactivate a deactivated user account (admin)",
)
def reactivate_user(
    user_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserDetail:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if not target.is_active:
        target.is_active = True
        target.deactivated_at = None
        audit(
            db,
            action="user.reactivated",
            target_type="user",
            target_id=target.id,
            actor_user_id=admin.id,
            details={"role": target.role.value},
        )
        db.commit()
        db.refresh(target)
    return AdminUserDetail.model_validate(target, from_attributes=True)


@router.get(
    "/clients",
    response_model=AdminClientListResponse,
    summary="List all clients (admin)",
)
def list_clients(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientListResponse:
    rows = db.execute(select(Client).order_by(Client.created_at.desc())).scalars().all()
    return AdminClientListResponse(
        clients=[AdminClientSummary.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/clients",
    response_model=AdminClientSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new client tenant (admin)",
)
def create_client(
    body: AdminClientCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientSummary:
    legal_name = body.legal_name.strip()
    if not legal_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="legal_name is required.",
        )
    client = Client(
        legal_name=legal_name,
        dba_name=body.dba_name,
        industry=body.industry,
        size_band=body.size_band,
    )
    db.add(client)
    db.flush()
    audit(
        db,
        action="client.created",
        target_type="client",
        target_id=client.id,
        actor_user_id=admin.id,
        details={"legal_name": legal_name, "source": "admin"},
    )
    db.commit()
    db.refresh(client)
    return AdminClientSummary.model_validate(client, from_attributes=True)


@router.get(
    "/clients/{cid}",
    response_model=AdminClientSummary,
    summary="Client detail (admin)",
)
def get_client(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientSummary:
    client = db.get(Client, cid)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found.",
        )
    return AdminClientSummary.model_validate(client, from_attributes=True)


@router.get(
    "/clients/{cid}/domains",
    response_model=AdminDomainListResponse,
    summary="List a client's approved email domains (admin)",
)
def list_client_domains(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDomainListResponse:
    if db.get(Client, cid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    rows = (
        db.execute(
            select(ClientDomain).where(ClientDomain.client_id == cid).order_by(ClientDomain.domain)
        )
        .scalars()
        .all()
    )
    return AdminDomainListResponse(
        domains=[AdminDomainRow.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/clients/{cid}/domains",
    response_model=AdminDomainRow,
    status_code=status.HTTP_201_CREATED,
    summary="Approve an email domain for a client (admin)",
)
def add_client_domain(
    cid: uuid.UUID,
    body: AdminDomainCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDomainRow:
    if db.get(Client, cid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    # Accept either a bare domain or a full email; normalize to the domain.
    raw = body.domain.strip().lower()
    domain = domain_of(raw) if "@" in raw else raw
    if not domain or "." not in domain:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid domain, e.g. company.com.",
        )
    if is_generic_provider(domain):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Generic email providers can't be approved as a client domain.",
        )
    existing = db.execute(
        select(ClientDomain).where(ClientDomain.domain == domain)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That domain is already registered to a client.",
        )
    row = ClientDomain(client_id=cid, domain=domain, created_by=admin.id)
    db.add(row)
    db.flush()
    audit(
        db,
        action="client.domain.added",
        target_type="client",
        target_id=cid,
        actor_user_id=admin.id,
        details={"domain": domain},
    )
    db.commit()
    db.refresh(row)
    return AdminDomainRow.model_validate(row, from_attributes=True)


@router.delete(
    "/clients/{cid}/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an approved email domain (admin)",
)
def remove_client_domain(
    cid: uuid.UUID,
    domain_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    row = db.get(ClientDomain, domain_id)
    if row is None or row.client_id != cid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found.")
    db.delete(row)
    audit(
        db,
        action="client.domain.removed",
        target_type="client",
        target_id=cid,
        actor_user_id=admin.id,
        details={"domain": row.domain},
    )
    db.commit()


@router.get(
    "/services",
    response_model=AdminServiceListResponse,
    summary="List all services / engagements (admin)",
)
def list_services(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    client_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> AdminServiceListResponse:
    """Cross-tenant list of services (engagements), newest first.

    Archived services are hidden by default; pass include_archived=true to see
    them (e.g. for an "Archived" view).
    """
    stmt = select(Service)
    if client_id is not None:
        stmt = stmt.where(Service.client_id == client_id)
    if not include_archived:
        stmt = stmt.where(Service.status != ServiceStatus.ARCHIVED)
    stmt = stmt.order_by(Service.created_at.desc())
    rows = db.execute(stmt).scalars().all()
    return AdminServiceListResponse(
        services=[AdminServiceRow.model_validate(r, from_attributes=True) for r in rows]
    )


@router.delete(
    "/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (remove) a service (admin)",
)
def archive_service(
    service_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Soft-remove a service by archiving it. Data is retained per policy and
    the workspace drops out of active lists."""
    svc = db.get(Service, service_id)
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found.")
    svc.status = ServiceStatus.ARCHIVED
    audit(
        db,
        action="service.archived",
        target_type="service",
        target_id=svc.id,
        actor_user_id=admin.id,
        details={"client_id": str(svc.client_id), "kind": svc.kind.value},
    )
    db.commit()


@router.post(
    "/service-requests/{request_id}/fulfill",
    response_model=FulfillServiceRequestResponse,
    summary="Publish a service request for processing (admin)",
)
def fulfill_service_request(
    request_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> FulfillServiceRequestResponse:
    """Graduate a service request into a live engagement workspace.

    The admin reviews the client's inputs + uploads in the queue, then
    publishes: this opens the Service (status in_progress) so the consultant
    can run the assessment and the AI pipeline against vetted intake data.
    Idempotent — re-publishing returns the existing workspace.
    """
    sr = db.get(ServiceRequest, request_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service request not found.",
        )
    if sr.service_type == ServiceType.CONSULTATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consultation requests are handled directly, not published as a service.",
        )
    if sr.fulfilled_service_id is not None:
        existing = db.get(Service, sr.fulfilled_service_id)
        if existing is not None:
            return FulfillServiceRequestResponse(
                service_id=existing.id,
                service_type=sr.service_type,
                title=existing.title,
                already_fulfilled=True,
            )

    client = db.get(Client, sr.client_id)
    org = client.legal_name if client is not None else "Client"
    svc = Service(
        kind=ServiceKind(sr.service_type.value),
        status=ServiceStatus.IN_PROGRESS,
        title=f"{org} — {_SERVICE_TITLES[sr.service_type]}",
        client_id=sr.client_id,
        source_request_id=sr.id,
        opened_by=admin.id,
    )
    db.add(svc)
    db.flush()
    sr.fulfilled_service_id = svc.id
    audit(
        db,
        action="service_request.fulfilled",
        target_type="service",
        target_id=svc.id,
        actor_user_id=admin.id,
        details={"service_type": sr.service_type.value, "request_id": str(sr.id)},
    )
    db.commit()
    db.refresh(svc)
    return FulfillServiceRequestResponse(
        service_id=svc.id,
        service_type=sr.service_type,
        title=svc.title,
        already_fulfilled=False,
    )


@router.get(
    "/services/{service_id}",
    response_model=AdminServiceDetail,
    summary="Service detail (admin) - resolves a workspace's owning tenant",
)
def get_service(
    service_id: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminServiceDetail:
    """Look up a service by id, including its client_id.

    Cross-tenant on purpose (admin-only, no X-Client-Id): the workspace UI
    calls this to discover which client a service belongs to, then sets that
    as the active tenant before its tenant-scoped data calls.
    """
    svc = db.get(Service, service_id)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found.",
        )
    return AdminServiceDetail.model_validate(svc, from_attributes=True)


@router.get(
    "/ai-status",
    response_model=AdminAiStatus,
    summary="AI pipeline readiness (admin)",
)
def ai_status(_admin: Annotated[User, _admin_required]) -> AdminAiStatus:
    """Report whether AI features will actually run a live call.

    `ready` is true only when a real provider call will be made. It VALIDATES the
    live preconditions (FIX A-5): the API key is present and the provider SDK is
    importable, and lists the per-job model that will actually run (from the
    AIJob registry). It never performs a live ping and never returns the key.

    Fixture mode reports ready=false with the honest fixture-mode explanation
    (FIX E-5: fixtures are deterministic simulations, not "disabled").
    """
    from app.ai.engine import get_job, registered_jobs

    s = get_settings()
    mode = s.shield_llm_mode
    provider = s.shield_llm_provider
    model = s.shield_llm_model

    # Per-job models actually in effect (a job may pin a cheaper model; None
    # falls back to the configured default). Read from the registry, not guessed.
    job_models = {name: (get_job(name).model or model) for name in registered_jobs()}

    # Is the provider SDK importable? Checked lazily so fixture mode never makes
    # the SDK a hard dependency; a live deploy missing it must fail honestly here
    # rather than with a generic 500 on the first Run-AI click.
    sdk_importable = True
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except Exception:  # noqa: BLE001 - any import failure means "not importable"
            sdk_importable = False
    api_key_present = bool(s.anthropic_api_key)

    common = {
        "mode": mode,
        "provider": provider,
        "model": model,
        "api_key_present": api_key_present,
        "sdk_importable": sdk_importable,
        "job_models": job_models,
    }

    if mode != "live":
        return AdminAiStatus(
            ready=False,
            detail=(
                "AI suggestions are simulated (deterministic fixtures) for demo "
                "and testing; set SHIELD_LLM_MODE=live for real analysis."
            ),
            **common,
        )

    problems: list[str] = []
    if provider == "anthropic" and not api_key_present:
        problems.append("ANTHROPIC_API_KEY is not set")
    if not sdk_importable:
        problems.append("the 'anthropic' SDK cannot be imported")
    if problems:
        return AdminAiStatus(
            ready=False,
            detail="Live mode is on but " + "; ".join(problems) + ".",
            **common,
        )
    return AdminAiStatus(
        ready=True,
        detail=f"Live AI configured ({provider}/{model}).",
        **common,
    )


# ---------------------------------------------------------------------------
# AI usage + estimated cost (FIX H-5)
# ---------------------------------------------------------------------------

# STATIC estimate only — USD per 1,000,000 tokens, (input, output). This is a
# point-in-time price sheet kept in code intentionally (config.py is owned
# elsewhere and prices are not a security/runtime knob). Update on price changes.
# A model absent here yields a null cost that is reported as unestimated.
_MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = _MODEL_PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return None
    in_rate, out_rate = price
    return round(input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate, 6)


@router.get(
    "/ai-usage",
    response_model=AdminAiUsageResponse,
    summary="Per-tenant AI usage + estimated cost, by month + model (admin)",
)
def ai_usage(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminAiUsageResponse:
    """Calls, tokens, and estimated cost per client per month (broken out by
    model, since price is per-model). Cost is a STATIC estimate from an in-code
    price sheet; a model with no listed price reports a null cost.
    """
    from app.models.llm_call import LLMCall

    rows = db.execute(
        select(
            LLMCall.client_id,
            LLMCall.model,
            LLMCall.input_tokens,
            LLMCall.output_tokens,
            LLMCall.requested_at,
        )
    ).all()

    # Aggregate in Python (cross-dialect: no DB-specific date_trunc/strftime).
    agg: dict[tuple[uuid.UUID | None, str, str], dict[str, int]] = {}
    for client_id, model, in_tok, out_tok, requested_at in rows:
        month = requested_at.strftime("%Y-%m")
        key = (client_id, month, model)
        bucket = agg.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        bucket["calls"] += 1
        bucket["input_tokens"] += int(in_tok or 0)
        bucket["output_tokens"] += int(out_tok or 0)

    # Resolve client display names once.
    client_ids = {cid for (cid, _m, _model) in agg if cid is not None}
    names: dict[uuid.UUID, str] = {}
    if client_ids:
        for c in db.execute(select(Client).where(Client.id.in_(client_ids))).scalars().all():
            names[c.id] = c.legal_name

    out: list[AdminAiUsageRow] = []
    for (client_id, month, model), bucket in agg.items():
        cost = _estimate_cost_usd(model, bucket["input_tokens"], bucket["output_tokens"])
        out.append(
            AdminAiUsageRow(
                client_id=client_id,
                client_name=names.get(client_id) if client_id is not None else None,
                month=month,
                model=model,
                calls=bucket["calls"],
                input_tokens=bucket["input_tokens"],
                output_tokens=bucket["output_tokens"],
                estimated_cost_usd=cost,
                cost_estimated=cost is not None,
                note=(None if cost is not None else f"No price on file for model {model!r}."),
            )
        )
    # Stable, readable ordering: newest month first, then client, then model.
    out.sort(key=lambda r: (r.month, str(r.client_id), r.model), reverse=True)

    return AdminAiUsageResponse(
        rows=out,
        note=(
            "Costs are a static in-code estimate (USD per 1M tokens), not a billed "
            "figure; rows with an unlisted model report a null cost."
        ),
    )


# ---------------------------------------------------------------------------
# Audit log viewer (FIX H-7)
# ---------------------------------------------------------------------------

# Hard ceiling on a single CSV export so a filter that matches everything can't
# stream an unbounded response. Interactive paging uses `limit`/`offset`.
_AUDIT_CSV_MAX_ROWS = 10_000

# CSV column order, kept stable so downstream tooling can rely on it.
_AUDIT_CSV_COLUMNS = (
    "at",
    "action",
    "actor_user_id",
    "actor_email",
    "target_type",
    "target_id",
    "correlation_id",
    "details",
)


def _audit_filtered_stmt(
    *,
    action: str | None,
    actor_id: uuid.UUID | None,
    target_type: str | None,
    target_id: uuid.UUID | None,
    client_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
):
    """Build the filtered (unpaginated, unordered) AuditEntry SELECT.

    READ-ONLY: this only ever composes a SELECT. The audit endpoint never
    inserts, updates, or deletes an audit row (append-only invariant, enforced
    by both a Postgres trigger and an ORM before_flush listener).

    `client_id` scopes to rows that directly concern a client. audit_entries
    carries no client_id column, so this matches the client-targeted rows
    (target_type='client' AND target_id=<client_id>); broader per-tenant
    scoping would require a schema change to the append-only table.
    """
    stmt = select(AuditEntry)
    if action is not None:
        stmt = stmt.where(AuditEntry.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditEntry.actor_user_id == actor_id)
    if target_type is not None:
        stmt = stmt.where(AuditEntry.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditEntry.target_id == target_id)
    if client_id is not None:
        stmt = stmt.where(
            AuditEntry.target_type == "client",
            AuditEntry.target_id == client_id,
        )
    if start is not None:
        stmt = stmt.where(AuditEntry.at >= start)
    if end is not None:
        stmt = stmt.where(AuditEntry.at <= end)
    return stmt


def _resolve_actor_emails(db: Session, rows: list[AuditEntry]) -> dict[uuid.UUID, str]:
    """One batched lookup of actor id -> email for display."""
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    if not actor_ids:
        return {}
    users = db.execute(select(User).where(User.id.in_(actor_ids))).scalars().all()
    return {u.id: u.email for u in users}


@router.get(
    "/audit",
    response_model=AdminAuditListResponse,
    summary="Read the append-only audit trail (admin)",
)
def audit_log(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="json", pattern="^(json|csv)$"),
) -> Response | AdminAuditListResponse:
    """Cross-tenant, read-only view of the audit_entries trail (FIX H-7).

    The platform writes a genuinely append-only audit trail that previously
    had no reader without direct SQL access. This surfaces it to admins with
    filters (action, actor, target, client, date range), newest first, paged.
    Pass `format=csv` to download the filtered set (capped at
    `_AUDIT_CSV_MAX_ROWS`).

    This handler performs NO writes: it only issues SELECTs and never adds,
    updates, or deletes an audit row.
    """
    base = _audit_filtered_stmt(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        client_id=client_id,
        start=start,
        end=end,
    )
    # Newest first; id as a stable tiebreaker for equal timestamps.
    ordered = base.order_by(AuditEntry.at.desc(), AuditEntry.id.desc())

    if format == "csv":
        rows = db.execute(ordered.limit(_AUDIT_CSV_MAX_ROWS)).scalars().all()
        emails = _resolve_actor_emails(db, rows)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_AUDIT_CSV_COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    r.at.isoformat(),
                    r.action,
                    str(r.actor_user_id) if r.actor_user_id else "",
                    emails.get(r.actor_user_id, "") if r.actor_user_id else "",
                    r.target_type,
                    str(r.target_id) if r.target_id else "",
                    r.correlation_id or "",
                    json.dumps(r.details, default=str) if r.details else "",
                ]
            )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
        )

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    page = db.execute(ordered.limit(limit).offset(offset)).scalars().all()
    emails = _resolve_actor_emails(db, page)
    out = [
        AdminAuditRow(
            id=r.id,
            at=r.at,
            actor_user_id=r.actor_user_id,
            actor_email=emails.get(r.actor_user_id) if r.actor_user_id else None,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            details=r.details,
            correlation_id=r.correlation_id,
        )
        for r in page
    ]
    return AdminAuditListResponse(rows=out, total=total, limit=limit, offset=offset)
