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
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm import LLMClient
from app.ai.schemas import TECH_DEBT_EXTRACT_SHAPE
from app.models.artifact import Artifact
from app.models.client import Client
from app.models.llm_call import LLMCall
from app.models.user import User
from app.storage import StorageBackend
from app.tech_debt.parsers import (
    EmptyInventoryError,
    data_rows,
    parse_inventory_detailed,
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
    # FIX C-3: parse provenance the caller can report to the admin (which sheet
    # was read, how many rows parsed / were skipped, whether we truncated).
    sheet_name: str | None = None
    sheet_count: int = 1
    rows_parsed: int = 0
    rows_skipped: int = 0
    truncated: bool = False


def _load_artifact_bytes(storage: StorageBackend, artifact: Artifact) -> bytes:
    """Fetch the artifact bytes through the storage backend's own ``get()``.

    FIX C-7: the old code sniffed the private ``_path_for`` attribute and
    otherwise fetched a presigned URL with ``urllib.request.urlopen`` and NO
    timeout - a stalled MinIO could hang the worker forever. Both backends
    implement ``get()`` (LocalFilesystemStorage reads the file; S3Storage does
    a bounded ``get_object``), so we route every fetch through it. A missing
    object raises ``FileNotFoundError`` (-> 410); a storage outage raises
    ``StorageUnavailable`` (-> 503). Both propagate to the route unchanged.
    """
    return storage.get(artifact.file_storage_key)


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
        try:
            decoded = json.loads(content[first : last + 1])
        except json.JSONDecodeError as exc2:
            raise ValueError(f"LLM response was not parseable JSON: {exc2}") from exc2

    # FIX C-5: a valid-JSON but wrong-shape response (a top-level list, or a
    # dict lacking "items", or "items" not an array) must ERROR - not silently
    # yield [] and let the route mint an empty "Draft v2, 0 items" with no
    # failure signal. Validate against the documented TECH_DEBT_EXTRACT_SHAPE.
    if not isinstance(decoded, dict) or "items" not in decoded:
        raise ValueError(
            "LLM response was valid JSON but not the documented shape "
            "(expected an object with an 'items' array). Expected: "
            f"{TECH_DEBT_EXTRACT_SHAPE}"
        )
    raw_items = decoded["items"]
    if not isinstance(raw_items, list):
        raise ValueError(
            "LLM response 'items' was not an array. Expected: " f"{TECH_DEBT_EXTRACT_SHAPE}"
        )
    return [_coerce_item(item) for item in raw_items if isinstance(item, dict)]


# Magnitude suffixes we honor on a bare number (FIX C-4). "1.2M" -> 1_200_000,
# "500k" -> 500_000. Documented + supported; keeps abbreviated spend accurate.
_MAGNITUDE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _is_nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _parse_number(value: Any) -> float | None:
    """Tolerant money/count parser (FIX C-4).

    Handles "$120,000", "120,000", "EUR 1200", "500 seats", "1.2M". Strips
    currency symbols/words and thousands separators, parses the LEADING number,
    and applies a trailing k/m/b/t magnitude suffix. Returns None only when
    there is no leading number at all (e.g. "twelve"), so the caller can
    preserve the raw string for the human reviewer.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a cost/count
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _NUMBER_RE.search(s)
    if not m:
        return None
    try:
        num = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    suffix = s[m.end() :].lstrip()[:1].lower()
    if suffix in _MAGNITUDE:
        num *= _MAGNITUDE[suffix]
    return num


def _clamp_pct(value: int) -> int:
    """Clamp a confidence percentage into 0-100 (FIX C-4)."""
    return max(0, min(100, value))


def _coerce_item(item: dict[str, Any]) -> ExtractedCapability:
    def _opt_str(key: str) -> str | None:
        v = item.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    # Numeric fields: parse tolerantly, and when parsing STILL fails on a
    # non-empty value, preserve the raw string in notes so the human reviewer
    # never loses it (FIX C-4).
    notes_extra: list[str] = []

    raw_cost = item.get("annual_cost_usd")
    annual_cost_usd = _parse_number(raw_cost)
    if annual_cost_usd is None and _is_nonempty(raw_cost):
        notes_extra.append(f"cost: {str(raw_cost).strip()!r}")

    raw_licenses = item.get("license_count")
    licenses_num = _parse_number(raw_licenses)
    license_count = int(licenses_num) if licenses_num is not None else None
    if license_count is None and _is_nonempty(raw_licenses):
        notes_extra.append(f"licenses: {str(raw_licenses).strip()!r}")

    confidence_num = _parse_number(item.get("confidence_pct"))
    confidence_pct = _clamp_pct(int(confidence_num)) if confidence_num is not None else None

    source_num = _parse_number(item.get("source_row_index"))
    source_row_index = int(source_num) if source_num is not None else None

    notes = _opt_str("notes")
    if notes_extra:
        extra = "; ".join(notes_extra)
        notes = f"{notes} ({extra})" if notes else extra

    return ExtractedCapability(
        name=(_opt_str("name") or "Unknown capability"),
        vendor=_opt_str("vendor"),
        category=_opt_str("category"),
        function=_opt_str("function"),
        annual_cost_usd=annual_cost_usd,
        license_count=license_count,
        notes=notes,
        confidence_pct=confidence_pct,
        source_row_index=source_row_index,
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
    parsed = parse_inventory_detailed(raw, artifact.mime_type)

    # Skip the truncation sentinel everywhere rows are used: it must never be
    # treated as a data row (or it becomes a phantom capability). It still
    # drives the "truncated" signal we pass to the model as context.
    rows = data_rows(parsed.rows)
    truncated = was_truncated(parsed.rows)

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
            "source_sheet": parsed.sheet_name,
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

    # FIX C-5: zero extracted items from a NON-EMPTY input is a failure, not a
    # 0-item draft. We already guaranteed `rows` is non-empty above, so an empty
    # result means the model returned nothing for real input rows. Raise the
    # same ValueError the route maps to a typed 502.
    items: list[ExtractedCapability] = result.data
    if not items:
        raise ValueError(f"the model returned no items for {len(rows)} input rows")

    return ExtractionResult(
        items=items,
        llm_call=result.llm_call,
        sheet_name=parsed.sheet_name,
        sheet_count=parsed.sheet_count,
        rows_parsed=parsed.rows_parsed,
        rows_skipped=parsed.rows_skipped,
        truncated=truncated,
    )


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
