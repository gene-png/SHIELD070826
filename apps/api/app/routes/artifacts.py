"""Artifact upload routes.

Master Spec §15 Phase 2: "Document upload with drag-drop, redaction
disclosure." The disclosure copy lives on the web side; the redactor
module itself (apps/api/app/ai/redact.py) lands in Phase 3 with the
first AI extraction.

Phase 2 only emits `client_upload` artifacts. v1 caps an individual
upload at 50 MB; multi-file batch upload is the caller's responsibility
(one POST per file).
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db.session import get_db
from app.dependencies import current_client, current_user
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.client import Client
from app.models.user import User, UserRole
from app.schemas.artifact import ArtifactListResponse, ArtifactResponse
from app.storage import StorageBackend, get_storage

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Allowed MIME types for intake uploads. Phase 2 is intake-scope only;
# Phase 3 may broaden this when the Tech Debt service ingests Excel.
ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/csv",
    "text/plain",
    "image/png",
    "image/jpeg",
    "application/zip",
}

# Legacy OLE2 Excel. Advertised by old exports but unreadable by openpyxl
# (the extraction path), so it would crash downstream. Reject at upload with
# an actionable message instead of silently accepting a file we can't parse.
LEGACY_XLS_MIME = "application/vnd.ms-excel"


def _safe_title(name: str) -> str:
    """Strip path separators and limit length so a malicious filename can't
    escape the storage key namespace or DoS the title column."""
    base = name.replace("\\", "/").split("/")[-1]
    base = re.sub(r"[^\w.\-]+", "_", base).strip("_")
    return base[:255] or "upload"


def _storage_dep() -> StorageBackend:
    return get_storage()


@router.post(
    "",
    response_model=ArtifactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an artifact (intake document)",
)
async def upload_artifact(
    file: Annotated[UploadFile, File(description="Document to upload")],
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
    notes: Annotated[str | None, Form()] = None,
) -> ArtifactResponse:
    mime = file.content_type or "application/octet-stream"
    if mime == LEGACY_XLS_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Legacy .xls is not supported; re-save the file as .xlsx and upload again.",
        )
    if mime not in ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"MIME type {mime!r} is not allowed for intake uploads.",
        )

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES} byte upload limit.",
        )

    title = _safe_title(file.filename or "upload")
    key = f"client_upload/{user.id}/{uuid.uuid4()}/{title}"
    stored = storage.put(key, data, content_type=mime)

    artifact = Artifact(
        client_id=client.id,
        title=title,
        file_storage_key=stored.key,
        mime_type=mime,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        origin=ArtifactOrigin.CLIENT_UPLOAD,
        stage="intake",
        uploaded_by=user.id,
        notes=notes,
    )
    db.add(artifact)
    db.flush()

    audit(
        db,
        action="artifact.uploaded",
        target_type="artifact",
        target_id=artifact.id,
        actor_user_id=user.id,
        details={
            "title": title,
            "mime_type": mime,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "origin": ArtifactOrigin.CLIENT_UPLOAD.value,
        },
    )
    db.commit()
    db.refresh(artifact)
    return ArtifactResponse.model_validate(artifact, from_attributes=True)


@router.get(
    "",
    response_model=ArtifactListResponse,
    summary="List artifacts uploaded by the current user",
)
def list_artifacts(
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ArtifactListResponse:
    # Admins see every artifact in the active tenant; client users
    # only see their own uploads inside that tenant.
    stmt = select(Artifact).where(Artifact.client_id == client.id)
    if user.role == UserRole.CLIENT:
        stmt = stmt.where(Artifact.uploaded_by == user.id)
    stmt = stmt.order_by(Artifact.uploaded_at.desc())
    rows = db.execute(stmt).scalars().all()
    return ArtifactListResponse(
        items=[ArtifactResponse.model_validate(r, from_attributes=True) for r in rows]
    )


@router.get(
    "/{artifact_id}",
    response_model=ArtifactResponse,
    summary="Artifact metadata",
)
def get_artifact(
    artifact_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ArtifactResponse:
    row = db.get(Artifact, artifact_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found.",
        )
    if user.role == UserRole.CLIENT and row.uploaded_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found.",
        )
    return ArtifactResponse.model_validate(row, from_attributes=True)


@router.get(
    "/{artifact_id}/download",
    summary="Stream the raw artifact bytes",
)
def download_artifact(
    artifact_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> Response:
    row = db.get(Artifact, artifact_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found.",
        )
    # Two permitted readers within the active tenant:
    #   1. the uploader;
    #   2. any admin (audit + ops).
    # Clients never download deliverables in-app (Work Order A1): deliverable
    # artifacts are admin-only and an admin shares them outside the app.
    is_uploader = row.uploaded_by == user.id
    is_staff = user.role == UserRole.ADMIN
    if not (is_uploader or is_staff):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found.",
        )
    try:
        data = storage.get(row.file_storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Artifact bytes no longer available.",
        ) from exc
    return Response(
        content=data,
        media_type=row.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{row.title}"'},
    )


@router.get(
    "/{artifact_id}/view",
    summary="Render an HTML artifact inline (e.g. the deliverable dashboard)",
)
def view_artifact(
    artifact_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> Response:
    """Serve an artifact for in-browser viewing (Content-Disposition: inline).

    Restricted to text/html so this can't be used to render arbitrary
    uploaded content inline (which would be an XSS/phishing vector); the
    HTML deliverable dashboards are consultant-generated, not client
    uploads. Same reader rules as /download: the uploader or any admin.
    """
    row = db.get(Artifact, artifact_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    is_uploader = row.uploaded_by == user.id
    is_staff = user.role == UserRole.ADMIN
    if not (is_uploader or is_staff):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if row.mime_type != "text/html":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only HTML artifacts can be viewed inline. Use /download instead.",
        )
    try:
        data = storage.get(row.file_storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Artifact bytes no longer available.",
        ) from exc
    # Lock down what the inline document can do: the dashboard is fully
    # self-contained (no scripts, no external assets), so a strict CSP costs
    # nothing and neutralizes any injection that slipped past HTML-escaping.
    return Response(
        content=data,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{row.title}"',
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
