"""Inventory file parsers - CSV and XLSX.

Master Spec §15 Phase 3: "Capability list ingest (Excel upload + AI
extraction with redaction)". This module turns the raw artifact bytes
into a row-shaped representation the LLM can reason about. The LLM does
the column-mapping; we just give it well-shaped rows.

Phase 3 only supports CSV + XLSX. PDF ingest is a Phase 6 hardening
target (table extraction is a different problem and the inventory
documents Eugene's customers ship are reliably tabular).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from typing import Any


class UnsupportedInventoryFormat(ValueError):
    """Raised when an artifact's MIME isn't a recognized inventory format."""


class EmptyInventoryError(ValueError):
    """Raised when an inventory file yields zero *data* rows to extract.

    Header-only sheets, blank files, or anything that parses down to no
    usable rows land here. Calling the LLM with nothing to extract is both
    a waste and the road to fabricated output, so the route turns this into
    a typed 422 *before* any provider call.
    """


class CorruptInventoryError(ValueError):
    """Raised when a file advertised as .xlsx can't be opened as one.

    openpyxl raises low-level ``zipfile.BadZipFile`` /
    ``openpyxl ... InvalidFileException`` for OLE2 legacy .xls bytes or a
    truncated/corrupt .xlsx. We convert those at the parse boundary so the
    route can return a typed 422 instead of a raw 500.
    """


# MIME types that the ingest endpoint accepts. Legacy .xls
# (``application/vnd.ms-excel``) is intentionally absent: openpyxl cannot
# read OLE2 .xls, so those are rejected at upload with an actionable message
# (re-save as .xlsx) rather than crashing extraction.
SUPPORTED_MIME = {
    "text/csv": "csv",
    "text/plain": "csv",  # treat .txt as CSV; most inventory exports save this way
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}

# Max rows we ship to the LLM. Above this and we'd either bust the context
# window or pay a fortune in tokens. v1 inventories are typically 50-300 rows.
MAX_ROWS = 500

# Internal marker key appended by parse_inventory when the input exceeds
# MAX_ROWS. It carries the "truncated" signal but is NOT a data row: it must
# never be shipped to the LLM or persisted as a capability.
SENTINEL_KEY = "__truncated__"


def is_sentinel_row(row: Any) -> bool:
    """True for the internal truncation marker (see SENTINEL_KEY)."""
    return isinstance(row, dict) and SENTINEL_KEY in row


def data_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The real data rows, with any truncation sentinel skipped."""
    return [row for row in rows if not is_sentinel_row(row)]


def was_truncated(rows: Iterable[dict[str, Any]]) -> bool:
    """Whether the parsed rows carry a truncation sentinel."""
    return any(is_sentinel_row(row) for row in rows)


def kind_for_mime(mime_type: str) -> str:
    try:
        return SUPPORTED_MIME[mime_type]
    except KeyError as exc:
        raise UnsupportedInventoryFormat(
            f"Inventory format {mime_type!r} not supported. Accept CSV or XLSX."
        ) from exc


def parse_inventory(data: bytes, mime_type: str) -> list[dict[str, Any]]:
    """Parse `data` into a list of row-dicts. Header row becomes the keys.

    Returns at most MAX_ROWS rows; the last row in the response is a
    sentinel `{"__truncated__": True}` marker when the input was longer.
    """
    kind = kind_for_mime(mime_type)
    if kind == "csv":
        rows = _parse_csv(data)
    elif kind == "xlsx":
        rows = _parse_xlsx(data)
    else:
        raise UnsupportedInventoryFormat(f"Unknown internal kind {kind!r}.")

    out = list(rows)
    truncated = len(out) > MAX_ROWS
    if truncated:
        out = out[:MAX_ROWS]
        out.append({SENTINEL_KEY: True, "__hint__": f"Input had > {MAX_ROWS} rows."})
    return out


def _parse_csv(data: bytes) -> Iterable[dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        # Strip whitespace from keys + values for stability.
        yield {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}


def _parse_xlsx(data: bytes) -> Iterable[dict[str, Any]]:
    # openpyxl is lazy-imported so test runs that don't touch XLSX don't
    # pay the import cost.
    from zipfile import BadZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        wb = load_workbook(filename=io.BytesIO(data), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException) as exc:
        # Legacy OLE2 .xls bytes or a truncated/corrupt .xlsx. Convert the
        # raw library error into a typed one so the route returns a 422, not
        # an unhandled 500.
        raise CorruptInventoryError(
            "This file could not be read as a valid .xlsx workbook. "
            "Re-save it as .xlsx and upload again."
        ) from exc
    ws = wb.active
    if ws is None:
        return
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return
    headers = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(header)]
    for raw in rows_iter:
        if raw is None or all(v is None or str(v).strip() == "" for v in raw):
            continue
        yield {
            headers[i]: ("" if v is None else str(v).strip())
            for i, v in enumerate(raw)
            if i < len(headers)
        }
