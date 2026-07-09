"""Direct tests for tech_debt.parsers (FIX C-3).

parsers.py previously had ZERO direct tests. These cover the C-3 defects:
multi-sheet selection, duplicate-header uniquification, overflow-cell
retention, and the sheet/row metadata returned to the admin. The XLSX cases
build real workbooks with openpyxl so the sheet-selection logic is exercised.
"""

from __future__ import annotations

import io

import pytest
from app.tech_debt.parsers import (
    MAX_ROWS,
    SENTINEL_KEY,
    data_rows,
    parse_inventory,
    parse_inventory_detailed,
    was_truncated,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    # Remove the default sheet so we control ordering/names exactly.
    default = wb.active
    wb.remove(default)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.unit
def test_csv_uniquifies_duplicate_headers() -> None:
    # Two "Vendor" columns must NOT collapse to one (the DictReader bug).
    csv = b"Tool,Vendor,Vendor,Cost\nWiz,Wiz Inc,Reseller LLC,350000\n"
    detailed = parse_inventory_detailed(csv, "text/csv")
    row = data_rows(detailed.rows)[0]
    assert row["Vendor"] == "Wiz Inc"
    assert row["Vendor_2"] == "Reseller LLC"
    assert row["Tool"] == "Wiz"
    assert row["Cost"] == "350000"


@pytest.mark.unit
def test_csv_keeps_overflow_cells_under_generated_headers() -> None:
    # A data row wider than the header keeps its extra cells (not dropped).
    csv = b"Tool,Vendor\nWiz,Wiz Inc,extra-note,another\n"
    row = data_rows(parse_inventory_detailed(csv, "text/csv").rows)[0]
    assert row["Tool"] == "Wiz"
    assert row["Vendor"] == "Wiz Inc"
    # Overflow columns 3 and 4 surface under generated colN headers.
    assert row["col3"] == "extra-note"
    assert row["col4"] == "another"


@pytest.mark.unit
def test_csv_reports_metadata() -> None:
    csv = b"Tool,Cost\nWiz,1\n\nSplunk,2\n"  # one blank row skipped
    detailed = parse_inventory_detailed(csv, "text/csv")
    assert detailed.sheet_name is None
    assert detailed.sheet_count == 1
    assert detailed.rows_parsed == 2
    assert detailed.rows_skipped == 1
    assert detailed.truncated is False


@pytest.mark.unit
def test_xlsx_picks_data_sheet_over_cover_page() -> None:
    # Workbook opens on a cover page (wb.active == first sheet). The old code
    # read wb.active and extracted garbage; C-3 scans all sheets and picks the
    # one with the most data rows.
    data = _xlsx(
        {
            "Cover": [["SHIELD Inventory Export"], ["Prepared for Atlas"]],
            "Inventory": [
                ["Tool", "Vendor", "Cost"],
                ["Wiz", "Wiz Inc", 350000],
                ["Splunk", "Splunk", 480000],
                ["CrowdStrike", "CrowdStrike", 300000],
            ],
        }
    )
    detailed = parse_inventory_detailed(data, XLSX_MIME)
    assert detailed.sheet_name == "Inventory"
    assert detailed.sheet_count == 2
    rows = data_rows(detailed.rows)
    names = sorted(r["Tool"] for r in rows)
    assert names == ["CrowdStrike", "Splunk", "Wiz"]


@pytest.mark.unit
def test_xlsx_uniquifies_duplicate_headers() -> None:
    data = _xlsx({"S": [["Name", "Name", "Cost"], ["Wiz", "Reseller", 10]]})
    row = data_rows(parse_inventory_detailed(data, XLSX_MIME).rows)[0]
    assert row["Name"] == "Wiz"
    assert row["Name_2"] == "Reseller"


@pytest.mark.unit
def test_truncation_sentinel_preserved_and_not_a_data_row() -> None:
    lines = [b"Tool"] + [f"Cap {i}".encode() for i in range(MAX_ROWS + 5)]
    csv = b"\n".join(lines) + b"\n"
    detailed = parse_inventory_detailed(csv, "text/csv")
    assert detailed.truncated is True
    assert detailed.rows_parsed == MAX_ROWS
    # rows carries the sentinel as its LAST element; data_rows excludes it.
    assert was_truncated(detailed.rows) is True
    assert len(data_rows(detailed.rows)) == MAX_ROWS
    assert detailed.rows[-1][SENTINEL_KEY] is True


@pytest.mark.unit
def test_parse_inventory_backcompat_returns_rows_list() -> None:
    rows = parse_inventory(b"A,B\n1,2\n", "text/csv")
    assert isinstance(rows, list)
    assert rows[0] == {"A": "1", "B": "2"}
