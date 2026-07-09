"""Capability extraction - call the LLM with redacted inventory rows.

Master Spec §15 Phase 3 + §12. The flow:

  1. Load the source artifact bytes via the storage backend.
  2. Parse into row-dicts (tech_debt.parsers).
  3. Build a structured prompt + a payload of {"rows": [...], "context": {...}}.
  4. Call LLMClient.invoke(purpose="extract.capabilities"). The client
     redacts the payload before send and writes an llm_calls audit row.
  5. Parse the LLM's JSON response into ExtractedCapability rows. The
     route layer turns those into CapabilityItem ORM rows.

The prompt is versioned (`PROMPT_VERSION` constant) so a future change
to the prompt shape doesn't silently regress past extractions; the
llm_calls row records the version that ran.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm import LLMClient
from app.models.artifact import Artifact
from app.models.client import Client
from app.models.llm_call import LLMCall
from app.models.user import User
from app.storage import StorageBackend
from app.tech_debt.parsers import (
    EmptyInventoryError,
    data_rows,
    parse_inventory,
    was_truncated,
)

PROMPT_VERSION = "v1"

PROMPT = """You extract a structured capability list from a raw security \
tool inventory.

For each row in the JSON `rows` array, decide if it represents a security \
capability the organization is paying for (tool, platform, service, \
subscription). Skip rows that are notes, blank, or duplicates.

Return ONLY a JSON object of the form:

  {
    "items": [
      {
        "name": "<short name>",
        "vendor": "<vendor or null>",
        "category": "<category like CNAPP, EDR, SIEM, IAM, GRC, or null>",
        "function": "<one-line function the capability serves, or null>",
        "annual_cost_usd": <number or null>,
        "license_count": <integer or null>,
        "notes": "<short note, or null>",
        "confidence_pct": <integer 0-100>,
        "source_row_index": <integer index into rows[]>
      },
      ...
    ]
  }

Do not include any text outside the JSON object. Set confidence_pct \
honestly - 100 for unambiguous rows, lower when the row needs human \
review."""


@dataclass(frozen=True)
class ExtractedCapability:
    name: str
    vendor: str | None
    category: str | None
    function: str | None
    annual_cost_usd: float | None
    license_count: int | None
    notes: str | None
    confidence_pct: int | None
    source_row_index: int | None


@dataclass
class ExtractionResult:
    items: list[ExtractedCapability]
    llm_call: LLMCall


def _load_artifact_bytes(storage: StorageBackend, artifact: Artifact) -> bytes:
    """LocalFilesystemStorage exposes file paths; production S3 needs a
    `get_object` call. For v1, only the local backend is hit synchronously
    here - the S3 path is reached at deliverable-render time."""
    if hasattr(storage, "_path_for"):
        return storage._path_for(artifact.file_storage_key).read_bytes()  # type: ignore[attr-defined]
    # Fall back to the signed URL + a plain GET when the backend doesn't
    # expose a local path. (Tests always hit LocalFilesystemStorage so this
    # branch is exercised only when wired to S3 in Phase 6.)
    import urllib.request

    url = storage.signed_url(artifact.file_storage_key, ttl_seconds=120)
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - URL produced by our own StorageBackend
        return resp.read()


def _parse_response(content: str) -> list[ExtractedCapability]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        # Some providers wrap the JSON in prose despite the instruction.
        # Strip everything outside the outermost {...} and retry once.
        first = content.find("{")
        last = content.rfind("}")
        if first == -1 or last == -1 or last <= first:
            raise ValueError(f"LLM response was not parseable JSON: {exc}") from exc
        decoded = json.loads(content[first : last + 1])

    raw_items = decoded.get("items", []) if isinstance(decoded, dict) else []
    return [_coerce_item(item) for item in raw_items if isinstance(item, dict)]


def _coerce_item(item: dict[str, Any]) -> ExtractedCapability:
    def _opt_str(key: str) -> str | None:
        v = item.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _opt_int(key: str) -> int | None:
        v = item.get(key)
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _opt_float(key: str) -> float | None:
        v = item.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return ExtractedCapability(
        name=(_opt_str("name") or "Unknown capability"),
        vendor=_opt_str("vendor"),
        category=_opt_str("category"),
        function=_opt_str("function"),
        annual_cost_usd=_opt_float("annual_cost_usd"),
        license_count=_opt_int("license_count"),
        notes=_opt_str("notes"),
        confidence_pct=_opt_int("confidence_pct"),
        source_row_index=_opt_int("source_row_index"),
    )


def extract_capabilities(
    *,
    db: Session,
    storage: StorageBackend,
    artifact: Artifact,
    requested_by: User,
    service_id: uuid.UUID,
    client_org_name: str | None,
    name_hints: Iterable[str] = (),
    llm: LLMClient,
) -> ExtractionResult:
    """Top-level entry point used by the ingest route."""
    raw = _load_artifact_bytes(storage, artifact)
    parsed = parse_inventory(raw, artifact.mime_type)

    # Skip the truncation sentinel everywhere rows are used: it must never be
    # treated as a data row (or it becomes a phantom capability). It still
    # drives the "truncated" signal we pass to the model as context.
    rows = data_rows(parsed)
    truncated = was_truncated(parsed)

    # Zero-row guard: a header-only or unparseable-to-zero-rows file must NOT
    # reach the LLM. Calling the model with nothing to extract is wasteful and
    # invites fabricated output. Raise before any provider call.
    if not rows:
        raise EmptyInventoryError(
            "No data rows found in this file; check that the inventory is on "
            "the first sheet with a header row."
        )

    payload: dict[str, Any] = {
        "rows": rows,
        "context": {
            "source_filename": artifact.title,
            "source_mime": artifact.mime_type,
            "truncated": truncated,
        },
    }

    # Runs through the AI job registry (Work Order C1); the "tech_debt_extract"
    # job keeps the historical "extract.capabilities" llm purpose.
    from app.ai.engine import run_job

    result = run_job(
        db,
        llm,
        "tech_debt_extract",
        inputs=payload,
        requested_by=requested_by.id,
        service_id=service_id,
        client_org_name=client_org_name,
        name_hints=tuple(name_hints),
    )
    return ExtractionResult(items=result.data, llm_call=result.llm_call)


def name_hints_for_tenant(db: Session, client_id) -> list[str]:
    """Pull display_name + email-local-parts off every user in this tenant.

    The redactor uses these as a name dictionary so the inventory's
    "owner" / "POC" columns don't leak into the LLM payload. Multi-tenant:
    only the tenant's own user names are leaked into the dictionary so
    one client's names don't end up in another's redaction pass.
    """
    from sqlalchemy import select

    rows = db.execute(
        select(User.display_name, User.email).where(User.client_id == client_id)
    ).all()
    hints: list[str] = []
    for name, email in rows:
        if name:
            hints.append(name)
        if email and "@" in email:
            hints.append(email.split("@", 1)[0])
    return [h for h in hints if h and len(h) >= 2]


def client_org_name_for_tenant(db: Session, client_id) -> str | None:
    """Pull the named tenant's legal name (or None for placeholders)."""
    row = db.get(Client, client_id)
    if row is None:
        return None
    name = row.legal_name
    if not name or name == "(pending intake)":
        return None
    return name


# Back-compat shims so callers updated incrementally still resolve.
def name_hints_for_deployment(db: Session) -> list[str]:  # pragma: no cover
    raise RuntimeError(
        "name_hints_for_deployment is removed (multi-tenant). "
        "Use name_hints_for_tenant(db, client_id) instead."
    )


def client_org_name_for_deployment(db: Session) -> str | None:  # pragma: no cover
    raise RuntimeError(
        "client_org_name_for_deployment is removed (multi-tenant). "
        "Use client_org_name_for_tenant(db, client_id) instead."
    )
